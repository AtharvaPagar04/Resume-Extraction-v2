from __future__ import annotations

import argparse

from .pipeline import run_pipeline


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract PDFs into compact RAW resume JSON.")
    parser.add_argument("input", help="A PDF or directory of PDFs (directories are searched recursively).")
    parser.add_argument("--output", help="Output directory, or a .json path for one input PDF.")
    parser.add_argument("--overwrite", action="store_true", help="Replace existing JSON files.")
    parser.add_argument("--verbose", action="store_true", help="Reserved for concise progress logging.")
    args = parser.parse_args()
    summary = run_pipeline(args.input, args.output, args.overwrite)
    print(f"attempted={summary.attempted} successful={summary.successful} failed={summary.failed} skipped={summary.skipped}")
    return 0 if summary.failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
