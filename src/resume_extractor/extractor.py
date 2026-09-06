from __future__ import annotations

import os
from pathlib import Path

import pymupdf as fitz

from .layout import LayoutLine, order_lines
from .layout_foundation import PageLayout, build_page_layout
from .models import ExtractionStatus, RawHyperlink, RawResume, RawSource
from .primitives import extract_fields, normalize_url
from .reconstruction import reconstruct_line_from_spans
from .tables import ReconstructedTable, reconstruct_tables
from .url_continuation import DerivedUrlCandidate, build_derived_url_candidates


def split_pages(text: str) -> list[str]:
    return text.split("\f")


def _validation_warning(path: Path) -> str | None:
    try:
        if not path.exists():
            return "FILE_NOT_FOUND"
        if not path.is_file() or not os.access(path, os.R_OK):
            return "PERMISSION_DENIED"
        if path.stat().st_size == 0:
            return "EMPTY_FILE"
        with path.open("rb") as source:
            if b"%PDF-" not in source.read(1024):
                return "INVALID_PDF_HEADER"
    except OSError:
        return "READ_ERROR"
    return None


def _basic_page_lines(page: fitz.Page) -> list[LayoutLine]:
    """Last-resort dict walk when derived geometry fails but text remains readable."""
    data = page.get_text("dict", flags=fitz.TEXT_PRESERVE_LIGATURES | fitz.TEXT_PRESERVE_WHITESPACE)
    output: list[LayoutLine] = []
    source_id = 0
    for block_index, block in enumerate(data.get("blocks", ())):
        if block.get("type") != 0:
            continue
        for line_index, raw_line in enumerate(block.get("lines", ())):
            text = reconstruct_line_from_spans(list(raw_line.get("spans", ())))
            if not text:
                continue
            output.append(LayoutLine(text, text, tuple(float(value) for value in raw_line.get("bbox", (0, 0, 0, 0))), block_index, line_index, (block_index, line_index), (source_id,)))
            source_id += 1
    return output


def _table_line(table: ReconstructedTable, table_index: int) -> LayoutLine:
    source_lines = tuple(sorted(table.source_lines, key=lambda line: line.source_order))
    source_ids = tuple(source_id for line in source_lines for source_id in line.source_ids)
    first = source_lines[0]
    return LayoutLine(table.text, table.text, table.bbox, -1000 - table_index, 0, first.source_order, source_ids, table.source_spans, first.dominant_font_size, first.median_font_size, first.dominant_font_name, first.dominant_flags, is_table=True)


def _page_lines(page: fitz.Page) -> tuple[list[LayoutLine], bool, tuple[str, ...], PageLayout | None]:
    try:
        page_layout: PageLayout = build_page_layout(page, 1)
    except Exception:
        return sorted(_basic_page_lines(page), key=lambda line: (line.y0, line.x0, line.block_index, line.line_index)), True, (), None
    reconstructed = reconstruct_tables(page_layout)
    lines = [line for line in page_layout.lines if line.source_order not in reconstructed.consumed_line_orders]
    lines.extend(_table_line(table, index) for index, table in enumerate(reconstructed.tables))
    ordered, fallback = order_lines(lines, page.rect.width, page.rect.height)
    return ordered, fallback, tuple(dict.fromkeys([*page_layout.warnings, *reconstructed.warnings])), page_layout


def _links(page: fitz.Page, page_number: int, warnings: list[str]) -> list[RawHyperlink]:
    output: list[RawHyperlink] = []
    seen: set[str] = set()
    try:
        links = page.get_links()
    except Exception:
        return output
    for link in links:
        raw = link.get("uri")
        if not raw:
            continue
        uri = normalize_url(str(raw))
        if uri is None:
            if "MALFORMED_LINK_SKIPPED" not in warnings:
                warnings.append("MALFORMED_LINK_SKIPPED")
            continue
        if uri.casefold() not in seen:
            seen.add(uri.casefold())
            output.append(RawHyperlink(page_number, uri))
    return output


def extract_pdf(path: str | Path) -> RawResume:
    source_path = Path(path)
    source = RawSource(source_path.name, 0)
    warning = _validation_warning(source_path)
    if warning:
        return RawResume(source, ExtractionStatus(False, [warning]))
    document: fitz.Document | None = None
    try:
        document = fitz.open(source_path)
        source.page_count = document.page_count
        if document.is_encrypted:
            return RawResume(source, ExtractionStatus(False, ["ENCRYPTED_PDF"]))
        pages: list[str] = []
        links: list[RawHyperlink] = []
        warnings: list[str] = []
        page_lines_list: list[list[LayoutLine]] = []
        page_layouts: list[PageLayout | None] = []
        for page_number in range(1, document.page_count + 1):
            try:
                page = document[page_number - 1]
                res = _page_lines(page)
                lines = res[0]
                fallback = res[1]
                page_warnings = res[2]
                page_layout = res[3] if len(res) > 3 else None
                pages.append("\n".join(line.text for line in lines))
                page_lines_list.append(lines)
                page_layouts.append(page_layout)
                if fallback and "READING_ORDER_FALLBACK" not in warnings:
                    warnings.append("READING_ORDER_FALLBACK")
                warnings.extend(warning for warning in page_warnings if warning not in warnings)
                links.extend(_links(page, page_number, warnings))
            except Exception:
                pages.append("")
                page_lines_list.append([])
                page_layouts.append(None)
                if "PARTIAL_PAGE_EXTRACTION" not in warnings:
                    warnings.append("PARTIAL_PAGE_EXTRACTION")
        text = "\f".join(pages)
        if not text.strip():
            warnings.append("NEEDS_OCR" if document.page_count else "NO_EXTRACTABLE_TEXT")

        derived_candidates: list[DerivedUrlCandidate] = []
        doc_page_start = 0
        for p_idx, (p_text, lines, p_layout) in enumerate(zip(pages, page_lines_list, page_layouts)):
            if p_layout is not None:
                page_derived, _ = build_derived_url_candidates(
                    lines,
                    p_layout.hyperlinks,
                    page_number=p_idx + 1,
                    page_document_start=doc_page_start,
                )
                derived_candidates.extend(page_derived)
            doc_page_start += len(p_text) + 1

        suppressed_occurrences = {
            (cand.prefix_document_start, cand.prefix_document_end)
            for cand in derived_candidates
            if cand.prefix_document_end > cand.prefix_document_start >= 0
        }
        annotation_uris = [link.uri for link in links]
        return RawResume(
            source,
            ExtractionStatus(True, warnings),
            text,
            links,
            extract_fields(
                text,
                annotation_uris,
                derived_url_candidates=derived_candidates,
                suppressed_url_occurrences=suppressed_occurrences,
            ),
        )
    except Exception as error:
        code = "ENCRYPTED_PDF" if "password" in str(error).lower() or "encrypt" in str(error).lower() else "CORRUPT_FILE"
        return RawResume(source, ExtractionStatus(False, [code]))
    finally:
        if document is not None:
            document.close()
