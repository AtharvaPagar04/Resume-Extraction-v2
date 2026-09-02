# PDF → RAW resume extraction

This project extracts a PDF deterministically into compact RAW JSON. It stops there: no CLEAN pipeline, OCR, LLM/SLM, semantic experience, projects, skills, sections, ownership inference, provenance, or geometry is serialized.

```text
PDF → PyMuPDF spans → reading-order normalization → RAW JSON
```

## Install and run

```bash
python -m pip install -r requirements.txt
# or: python -m pip install -e '.[test]'

# CLI extraction
resume-extract resume.pdf
resume-extract ./input --output ./output --overwrite
```

### Scripts

1. **Convert test resumes to JSON:**
   ```bash
   python scripts/convert_test_resumes.py
   # Options: --input <path> --output <dir> --overwrite --verbose
   ```

2. **Clean & structure generated JSON data:**
   ```bash
   python scripts/clean_json_data.py
   # Reads output/raw/*.json, sanitizes text, detects sections, and outputs to output/cleaned/
   # Options: --input <dir_or_file> --output <dir> --in-place --verbose
   ```

3. **Cleanup output files and caches:**
   ```bash
   python scripts/clean_outputs.py --dry-run
   python scripts/clean_outputs.py -y
   # Options: --all (also cleans caches and temp files) --json-only --dir <dir>
   ```

4. **Benchmark a corpus:**
   ```bash
   python scripts/benchmark.py ./test_resumes_sample
   ```

## RAW contract

```json
{
  "schema_version": "4.0.0",
  "source": {"file_name": "resume.pdf", "page_count": 1},
  "extraction": {"success": true, "warnings": []},
  "text": "Name\nExperience",
  "hyperlinks": [{"page": 1, "uri": "https://example.com"}],
  "extracted_fields": {
    "emails": ["person@example.com"],
    "phone_numbers": [],
    "linkedin_urls": [],
    "github_urls": [],
    "other_urls": []
  }
}
```

`text` is the only complete text representation. Page boundaries use form-feed (`\f`), so a two-page source is `page one\fpage two`; empty pages remain as empty positions.

## Guarantees and limits

The extractor uses PyMuPDF dictionary extraction, conservative span whitespace repair, geometry-based ordering with source-accounting fallback, optional deterministic table flattening, URI annotation retention, and syntactic contact/URL extraction. JSON is UTF-8 and atomically written.

Image-only PDFs receive `NEEDS_OCR`; OCR is not included. Pathological floating/overlapping layouts may receive `READING_ORDER_FALLBACK`. Tables are flattened and exact visual formatting is not preserved.
