from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path

from resume_extractor.extractor import extract_pdf
from resume_extractor.pipeline import discover_pdfs


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark deterministic PDF-to-RAW extraction.")
    parser.add_argument("input", help="PDF or directory (searched recursively).")
    args = parser.parse_args()
    durations: list[float] = []
    raw_bytes: list[int] = []
    ratios: list[float] = []
    successful = failed = partial = useful = links = emails = phones = urls = 0
    for pdf in discover_pdfs(Path(args.input)):
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
    report = {"pdf_count": len(durations), "successful": successful, "failed": failed, "partial": partial, "useful_text": useful, "hyperlinks": links, "emails": emails, "phones": phones, "urls": urls}
    if durations:
        report.update({"min_duration_s": min(durations), "max_duration_s": max(durations), "median_duration_s": statistics.median(durations), "average_duration_s": statistics.mean(durations), "p90_duration_s": statistics.quantiles(durations, n=10)[8] if len(durations) >= 10 else None, "resumes_per_minute": 60 / statistics.mean(durations), "average_raw_bytes": statistics.mean(raw_bytes), "text_to_json_ratio": {"min": min(ratios), "max": max(ratios), "median": statistics.median(ratios), "average": statistics.mean(ratios)}})
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
