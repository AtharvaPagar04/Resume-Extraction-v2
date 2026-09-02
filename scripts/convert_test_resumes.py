#!/usr/bin/env python3
"""Batch convert PDF resumes from test_resumes_sample (or custom path) to JSON.

Usage:
    python scripts/convert_test_resumes.py
    python scripts/convert_test_resumes.py --input test_resumes_sample --output output/raw
    python scripts/convert_test_resumes.py --overwrite --verbose
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# Ensure src is in sys.path when script is executed directly
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from resume_extractor.extractor import extract_pdf
from resume_extractor.pipeline import discover_pdfs, write_raw


def convert_resumes(
    input_path: Path | str = "test_resumes_sample",
    output_dir: Path | str = "output/raw",
    overwrite: bool = False,
    verbose: bool = False,
) -> dict:
    source_path = Path(input_path).resolve()
    target_dir = Path(output_dir).resolve()

    if not source_path.exists():
        raise FileNotFoundError(f"Input path does not exist: {source_path}")

    pdf_files = discover_pdfs(source_path)
    if not pdf_files:
        print(f"No PDF files discovered in: {source_path}")
        return {"attempted": 0, "successful": 0, "failed": 0, "skipped": 0, "results": []}

    target_dir.mkdir(parents=True, exist_ok=True)

    print(f"Found {len(pdf_files)} PDF(s) in {source_path}")
    print(f"Target output directory: {target_dir}")
    print("-" * 60)

    results = []
    successful = failed = skipped = 0
    total_start = time.perf_counter()

    for idx, pdf_file in enumerate(pdf_files, 1):
        output_file = target_dir / f"{pdf_file.stem}.json"
        if output_file.exists() and not overwrite:
            skipped += 1
            if verbose:
                print(f"[{idx}/{len(pdf_files)}] SKIP (exists): {pdf_file.name}")
            results.append({"file": pdf_file.name, "status": "SKIPPED", "output": str(output_file)})
            continue

        start_time = time.perf_counter()
        raw = extract_pdf(pdf_file)
        elapsed = time.perf_counter() - start_time

        write_raw(raw, output_file, overwrite=True)

        status = "SUCCESS" if raw.extraction.success else "FAILED"
        if raw.extraction.success:
            successful += 1
        else:
            failed += 1

        fields = raw.extracted_fields
        email_str = ", ".join(fields.emails) if fields.emails else "-"
        phone_str = ", ".join(fields.phone_numbers) if fields.phone_numbers else "-"
        warnings = f" (warnings: {', '.join(raw.extraction.warnings)})" if raw.extraction.warnings else ""

        results.append({
            "file": pdf_file.name,
            "status": status,
            "duration_s": round(elapsed, 4),
            "warnings": raw.extraction.warnings,
            "emails": fields.emails,
            "phone_numbers": fields.phone_numbers,
            "output": str(output_file),
        })

        prefix = "✓" if raw.extraction.success else "✗"
        print(f"[{idx}/{len(pdf_files)}] {prefix} {pdf_file.name} -> {output_file.name} ({elapsed * 1000:.1f}ms){warnings}")
        if verbose:
            print(f"    Emails: {email_str}")
            print(f"    Phones: {phone_str}")
            print(f"    Text Length: {len(raw.text)} chars across {raw.source.page_count} page(s)")

    total_time = time.perf_counter() - total_start
    print("-" * 60)
    print(
        f"Summary: total={len(pdf_files)} | successful={successful} | "
        f"failed={failed} | skipped={skipped} | time={total_time:.2f}s"
    )

    return {
        "attempted": len(pdf_files),
        "successful": successful,
        "failed": failed,
        "skipped": skipped,
        "total_duration_s": total_time,
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Convert PDF resumes from test_resumes_sample or custom directory to RAW JSON."
    )
    parser.add_argument(
        "-i",
        "--input",
        default="test_resumes_sample",
        help="Input directory or single PDF file (default: test_resumes_sample)",
    )
    parser.add_argument(
        "-o",
        "--output",
        default="output/raw",
        help="Target output directory for JSON files (default: output/raw)",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing JSON output files",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Show detailed per-file extracted contacts and page statistics",
    )
    args = parser.parse_args()

    summary = convert_resumes(
        input_path=args.input,
        output_dir=args.output,
        overwrite=args.overwrite,
        verbose=args.verbose,
    )
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
