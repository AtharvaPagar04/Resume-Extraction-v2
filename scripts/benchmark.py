from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path

import pymupdf as fitz

from resume_extractor.extractor import extract_pdf
from resume_extractor.layout_foundation import build_page_layout, validate_span_accounting
from resume_extractor.pipeline import discover_pdfs
from resume_extractor.tables import reconstruct_tables


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark deterministic PDF-to-RAW extraction.")
    parser.add_argument("input", help="PDF or directory (searched recursively).")
    args = parser.parse_args()
    durations: list[float] = []
    native_durations: list[float] = []
    foundation_durations: list[float] = []
    table_durations: list[float] = []
    raw_bytes: list[int] = []
    ratios: list[float] = []
    successful = failed = partial = useful = links = emails = phones = urls = pages = spans = lines = table_candidates = accepted_tables = rejected_tables = table_rows = table_cells = table_fallbacks = gutters = regions = accounting_failures = pymupdf_tables = geometry_tables = unresolved_table_spans = duplicate_table_emission_failures = 0
    for pdf in discover_pdfs(Path(args.input)):
        native_started = time.perf_counter()
        native = fitz.open(pdf)
        try:
            for page in native:
                page.get_text("dict", flags=fitz.TEXT_PRESERVE_LIGATURES | fitz.TEXT_PRESERVE_WHITESPACE)
        finally:
            native.close()
        native_durations.append(time.perf_counter() - native_started)
        foundation_started = time.perf_counter()
        foundation = fitz.open(pdf)
        try:
            page_layouts = [build_page_layout(page, index) for index, page in enumerate(foundation, 1)]
        finally:
            foundation.close()
        foundation_durations.append(time.perf_counter() - foundation_started)
        pages += len(page_layouts)
        spans += sum(len(layout.spans) for layout in page_layouts)
        lines += sum(len(layout.lines) for layout in page_layouts)
        table_candidates += sum(len(layout.table_candidates) for layout in page_layouts)
        table_started = time.perf_counter()
        table_results = [reconstruct_tables(layout) for layout in page_layouts]
        table_durations.append(time.perf_counter() - table_started)
        accepted_tables += sum(len(result.tables) for result in table_results)
        rejected_tables += sum(result.rejected_candidates for result in table_results)
        table_rows += sum(len(table.rows) for result in table_results for table in result.tables)
        table_cells += sum(len(row.cells) for result in table_results for table in result.tables for row in table.rows)
        table_fallbacks += sum("TABLE_RECONSTRUCTION_FALLBACK" in result.warnings for result in table_results)
        pymupdf_tables += sum(table.reconstruction_method == "pymupdf" for result in table_results for table in result.tables)
        geometry_tables += sum(table.reconstruction_method != "pymupdf" for result in table_results for table in result.tables)
        for result in table_results:
            for table in result.tables:
                assigned = [span.source_order for row in table.rows for cell in row.cells for span in cell.source_spans]
                unresolved_table_spans += len(table.source_spans) - len(assigned)
                duplicate_table_emission_failures += len(assigned) - len(set(assigned))
        gutters += sum(len(layout.gutters) for layout in page_layouts)
        regions += sum(len(layout.regions) for layout in page_layouts)
        accounting_failures += sum(not validate_span_accounting(layout.spans, layout.lines).valid for layout in page_layouts)
        started = time.perf_counter()
        raw = extract_pdf(pdf)
        durations.append(time.perf_counter() - started)
        payload = json.dumps(raw.to_dict(), ensure_ascii=False).encode()
        raw_bytes.append(len(payload))
        ratios.append(len(raw.text) / max(1, len(payload.decode())))
        successful += raw.extraction.success
        failed += not raw.extraction.success
        partial += "PARTIAL_PAGE_EXTRACTION" in raw.extraction.warnings
        useful += bool(raw.text.strip())
        links += len(raw.hyperlinks)
        emails += len(raw.extracted_fields.emails)
        phones += len(raw.extracted_fields.phone_numbers)
        urls += len(raw.extracted_fields.linkedin_urls) + len(raw.extracted_fields.github_urls) + len(raw.extracted_fields.other_urls)
    report = {"pdf_count": len(durations), "pages": pages, "successful": successful, "failed": failed, "partial": partial, "useful_text": useful, "spans": spans, "lines": lines, "source_accounting_failures": accounting_failures, "table_candidates": table_candidates, "accepted_tables": accepted_tables, "rejected_table_candidates": rejected_tables, "pymupdf_backed_tables": pymupdf_tables, "geometry_only_tables": geometry_tables, "reconstructed_table_rows": table_rows, "reconstructed_table_cells": table_cells, "table_fallbacks": table_fallbacks, "unresolved_table_spans": unresolved_table_spans, "duplicate_table_emission_failures": duplicate_table_emission_failures, "gutter_candidates": gutters, "region_candidates": regions, "hyperlinks": links, "emails": emails, "phones": phones, "urls": urls}
    if durations:
        def timings(values):
            return {"min_s": min(values), "max_s": max(values), "median_s": statistics.median(values), "average_s": statistics.mean(values), "p90_s": statistics.quantiles(values, n=10)[8] if len(values) >= 10 else None}

        report.update({"native_dict_extraction": timings(native_durations), "extraction_plus_layout_foundation": timings(foundation_durations), "table_reconstruction": timings(table_durations), "end_to_end_raw": timings(durations), "pages_per_second": pages / sum(foundation_durations), "resumes_per_minute": 60 / statistics.mean(durations), "average_raw_bytes": statistics.mean(raw_bytes), "text_to_json_ratio": {"min": min(ratios), "max": max(ratios), "median": statistics.median(ratios), "average": statistics.mean(ratios)}})
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
