from __future__ import annotations

import contextlib
import io
import os
from pathlib import Path
from typing import Any

import pymupdf as fitz

from .layout import LayoutLine, order_lines
from .layout_foundation import PageLayout, build_page_layout
from .models import ExtractionStatus, RawHyperlink, RawResume, RawSource
from .primitives import extract_fields, normalize_url
from .reconstruction import reconstruct_line_from_spans


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


def _rect_area(rect: tuple[float, float, float, float]) -> float:
    return max(0.0, rect[2] - rect[0]) * max(0.0, rect[3] - rect[1])


def _excluded(block_bbox: tuple[float, float, float, float], table_bbox: tuple[float, float, float, float]) -> bool:
    x0, y0, x1, y1 = block_bbox
    tx0, ty0, tx1, ty1 = table_bbox
    ix0, iy0, ix1, iy1 = max(x0, tx0), max(y0, ty0), min(x1, tx1), min(y1, ty1)
    intersection = _rect_area((ix0, iy0, ix1, iy1))
    return (x0 >= tx0 and y0 >= ty0 and x1 <= tx1 and y1 <= ty1) or intersection > 0.10 * max(1.0, _rect_area(block_bbox)) or (ty0 - 2 <= y0 and y1 <= ty1 + 2 and ix1 > ix0)


def _table_lines(page: fitz.Page, page_width: float, table_candidates: tuple | None = None) -> tuple[list[LayoutLine], list[tuple[float, float, float, float]], bool]:
    try:
        if table_candidates is not None:
            tables = [candidate.native_table for candidate in table_candidates if candidate.source == "pymupdf" and candidate.native_table is not None]
        else:
            # Compatibility path for direct callers; normal extraction reuses foundation candidates.
            with contextlib.redirect_stdout(io.StringIO()):
                found = page.find_tables()
            tables = list(getattr(found, "tables", found))
    except Exception:
        return [], [], True
    output: list[LayoutLine] = []
    boxes: list[tuple[float, float, float, float]] = []
    for table_index, table in enumerate(tables):
        try:
            rows = table.extract()
            if len(rows) < 2 or len(rows[0]) < 2 or sum(bool((cell or "").strip()) for cell in rows[0]) < 2:
                continue
            def header(cell: object, index: int) -> str:
                value = str(cell or "").replace("\n", " ").strip()
                if value.casefold() in {"key impact", "impact"}:
                    return "Impact"
                if value.casefold() in {"app link", "link"}:
                    return "Link"
                return value or f"Col{index + 1}"

            headers = [header(cell, index) for index, cell in enumerate(rows[0])]
            bbox = tuple(table.bbox)
            boxes.append(bbox)
            row_height = max(1.0, (bbox[3] - bbox[1]) / max(1, len(rows)))
            for row_index, row in enumerate(rows[1:], 1):
                values = [((cell or "").replace("\n", " ").strip()) for cell in row]
                text = " | ".join(f"{headers[index]}: {value}" for index, value in enumerate(values) if value)
                if text:
                    output.append(LayoutLine(text, text, (bbox[0], bbox[1] + row_index * row_height, bbox[2], bbox[1] + (row_index + 1) * row_height), -1000 - table_index, row_index, (-1000 - table_index, row_index), (), is_table=True))
        except Exception:
            continue
    return output, boxes, False


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


def _page_lines(page: fitz.Page) -> tuple[list[LayoutLine], bool, bool]:
    try:
        page_layout: PageLayout = build_page_layout(page, 1)
    except Exception:
        return sorted(_basic_page_lines(page), key=lambda line: (line.y0, line.x0, line.block_index, line.line_index)), True, False
    table_lines, table_boxes, table_failed = _table_lines(page, page.rect.width, page_layout.table_candidates)
    block_boxes = {block.block_index: block.bbox for block in page_layout.blocks}
    lines = [line for line in page_layout.lines if not any(_excluded(block_boxes[line.block_index], box) for box in table_boxes)]
    source_id = len(lines)
    for table_line in table_lines:
        lines.append(LayoutLine(table_line.raw_text, table_line.reconstructed_text, table_line.bbox, table_line.block_index, table_line.line_index, table_line.source_order, (source_id,), is_table=True))
        source_id += 1
    ordered, fallback = order_lines(lines, page.rect.width, page.rect.height)
    return ordered, fallback, table_failed or "TABLE_EXTRACTION_FAILED" in page_layout.warnings


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
        for page_number in range(1, document.page_count + 1):
            try:
                page = document[page_number - 1]
                lines, fallback, table_failed = _page_lines(page)
                pages.append("\n".join(line.text for line in lines))
                if fallback and "READING_ORDER_FALLBACK" not in warnings:
                    warnings.append("READING_ORDER_FALLBACK")
                if table_failed and "TABLE_EXTRACTION_FAILED" not in warnings:
                    warnings.append("TABLE_EXTRACTION_FAILED")
                links.extend(_links(page, page_number, warnings))
            except Exception:
                pages.append("")
                if "PARTIAL_PAGE_EXTRACTION" not in warnings:
                    warnings.append("PARTIAL_PAGE_EXTRACTION")
        text = "\f".join(pages)
        if not text.strip():
            warnings.append("NEEDS_OCR" if document.page_count else "NO_EXTRACTABLE_TEXT")
        annotation_uris = [link.uri for link in links]
        return RawResume(source, ExtractionStatus(True, warnings), text, links, extract_fields(text, annotation_uris))
    except Exception as error:
        code = "ENCRYPTED_PDF" if "password" in str(error).lower() or "encrypt" in str(error).lower() else "CORRUPT_FILE"
        return RawResume(source, ExtractionStatus(False, [code]))
    finally:
        if document is not None:
            document.close()
