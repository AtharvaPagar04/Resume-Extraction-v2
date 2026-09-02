# PDF-to-RAW Resume Extraction Pipeline — Complete Technical Handoff

Audit only, 2026-09-02. This document describes deterministic PDF-to-RAW behavior; it deliberately excludes semantic profile, CLEAN, SLM, and LLM behavior.

## 1. Active runtime flow

`main.py:main()` parses CLI arguments, builds `PipelineConfig`, calls `logger.setup_logger`, then `pipeline.run_pipeline`. `run_pipeline` recursively discovers PDFs with `input_dir.rglob("*.pdf")`, filters hidden/temp/lock-like names, and invokes `process_single_pdf` sequentially. The one-PDF RAW path is:

```text
main -> run_pipeline -> process_single_pdf -> extract_pdf_data
 -> PyMuPDFResumeExtractor.extract -> _process_page (each page)
 -> reconstruct_line_from_spans -> layout.analyze_page_layout
 -> regex_extractor.extract_all_fields -> raw_evidence.build_raw_evidence
 -> json_writer.write_resume_outputs -> output/raw/<stem>.json
```

The current pipeline also calls `section_parser.parse_sections`, semantic assembly, and CLEAN writing. RAW evidence is built before semantic assembly, but a new compact raw-only extractor must not copy those downstream stages.

```python
def extract(path):
    validate_file(path)                         # existence/readability/size/%PDF-
    doc = safe_fitz_open_or_failed_status(path)
    if doc.is_encrypted: return password_protected_status()
    pages = [extract_page(page, n) for n, page in enumerate(doc, 1)]
    text = "\f".join(p.text for p in pages)      # recommended replacement for old joins
    fields = extract_primitives(text.replace("\f", "\n\n"), pages, all_links(pages))
    return compact_raw(metadata, pages, text, fields)
```

## 2. Repository map

| Path | Active role and key symbols | Port recommendation |
|---|---|---|
| `main.py` | `build_arg_parser`, `main` | Simplify/port CLI |
| `resume_pipeline/config.py` | `PipelineConfig`, patterns, thresholds | Port relevant values |
| `resume_pipeline/extractor.py` | `PyMuPDFResumeExtractor`, page/table/link/reconstruction | Must port |
| `resume_pipeline/layout.py` | `LayoutLine`, rows/regions, `analyze_page_layout` | Must port |
| `resume_pipeline/regex_extractor.py` | all deterministic candidates | Should port |
| `resume_pipeline/location_dictionary.py` | gazetteer validation | Optional; copy data or weaken location recall |
| `resume_pipeline/raw_evidence.py` | RAW v3 IDs/references | Do not port |
| `resume_pipeline/models.py` | status/page/primitive/RAW Pydantic models | Keep concepts; simplify |
| `resume_pipeline/json_writer.py` | atomic JSON writer | Port mechanics |
| `resume_pipeline/pipeline.py` | discovery, timing, isolation | Port simplified |
| `resume_pipeline/logger.py` | rotating logger | Optional |
| `section_parser.py`, `semantic_extractor.py` | semantic consumer logic | Do not port |
| `slm_formatter/*`, SLM scripts | model consumer logic | Do not port |
| `cleaner.py` | legacy wordninja cleaner helpers | Do not port |
| `tests/test_extractor.py`, `test_layout.py`, `test_reconstruction.py` | extraction/layout regression | Port equivalent tests |
| `tests/test_regex_extractor.py`, `test_field_extraction_foundation.py` | primitive/nonmutation regression | Port equivalent tests |
| `tests/test_output_writer.py`, `test_pipeline_integration.py` | RAW/write/batch behavior | Port equivalent tests |

## 3. Dependencies

Pinned dependencies are `pymupdf>=1.24.0`, `pydantic>=2.0.0`, `wordninja>=2.0.0`, `tqdm>=4.66.0`, `pytest>=8.0.0`, and `cryptography>=42.0.0`. Only PyMuPDF is indispensable; it is imported exactly as `import pymupdf as fitz`. Pydantic is a typed boundary convenience. Extraction uses stdlib `re`, `html`, `unicodedata`, `urllib.parse`, `hashlib`, `datetime`, `os`, and `pathlib`. `wordninja` is not called by the normal reconstruction path; do not port it. There is no date library, OCR, NLP, LLM, embedding, or remote dependency.

## 4. Opening, validation, and errors

`validate_pdf_basic(path)` checks existence, readability, `stat`, nonzero size, and `%PDF-` within the first 1024 bytes. Statuses are `FILE_NOT_FOUND`, `PERMISSION_DENIED`, `STAT_ERROR`, `EMPTY_FILE`, `INVALID_PDF_HEADER`, `READ_ERROR`, and `VALID`. `fitz.open(path)` exceptions become `CORRUPT_FILE`, except message text containing `password` or `encrypt`, which becomes `PASSWORD_PROTECTED`. A document with `doc.is_encrypted` is rejected without authentication. Zero-page/image-only documents are `NEEDS_OCR` after zero extracted characters; no OCR is attempted. Empty pages make otherwise successful extraction `PARTIAL`.

`extract_pdf_data` always calls `extractor.close()` in `finally`. Page errors are not isolated: they become document extraction errors. `process_single_pdf` wraps extraction errors in `DocumentProcessingError("EXTRACTION_ERROR", exc)`; `run_pipeline` catches it, logs traceback with `logger.exception`, emits a failed file summary, and continues. No fabricated output is written after a stage exception.

## 5. Page API and source model

```python
text_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_LIGATURES | fitz.TEXT_PRESERVE_WHITESPACE)
native_links = page.get_links()
tables = page.find_tables()
```

Only `block["type"] == 0` is text. Blocks expose `bbox`/`lines`; lines expose `bbox`/`spans`; spans expose `text`, `bbox`, and potentially `size`, `font`, `flags`. Style data is used only for compatible bullet/cell continuation joins. The system does not use `get_text("text")`, `rawdict`, word API, `page.annots`, images, or OCR.

```python
LayoutLine(raw_text, cleaned_text, bbox=(x0,y0,x1,y1), block_index, line_index,
           spans=[], is_table=False, source_ids=())
```

`LogicalRow` is a y-compatible set of lines. `LayoutRegion` is a left/right flow. These are internal and need not enter compact RAW.

## 6. Whitespace and line reconstruction

`reconstruct_line_from_spans` sorts spans by `bbox[0]`; nonempty text is stripped; whitespace-only spans set `pending_whitespace`. It inserts one space before the next fragment if source whitespace exists, a whitespace-only span intervened, or `gap > 1.0 and gap/max(previous.size,current.size,1.0) > 0.18`. Final raw/clean text uses `re.sub(r"\s+", " ", text).strip()`. Clean additionally removes `(cid:<digits>)` per span. It does not spell-correct, segment words, dehyphenate, case-convert, or join ordinary physical lines. The old `cleaner.py` wordninja path is inactive.

## 7. Reading order and multi-column algorithm

`analyze_page_layout(lines, page_width, page_height)` is deterministic and source-accounting-safe: if ordering loses or duplicates any source fragment, it discards the classification and emits `(y0, x0, block_index, line_index)` sorted lines.

1. Full width: `line.width >= .68*page_width`; such one-line rows split bands.
2. Same row: vertical overlap/smaller height `>=.45` or center distance `<=.45*max(median height,line heights)`.
3. Standalone bullet pairing: right gap `<=.12*width`, ambiguous score gap must exceed `.15`; up to four continuation lines require same block/style, indent delta `<=.08*width`, vertical gap `<=1.8*median height`, no backtrack `>.01*width`, and not full width.
4. Wrapped local cells: previous multi-cell row; gap `<=.45*median height`, anchor delta `<=.06*width`, nearest owner unambiguous by `.15*width`, parent fill `>=.60` available width.
5. Gutter: >=6 x starts, largest adjacent x gap `>=.08*width`, >=3 lines each side; midpoint is gutter.
6. Right metadata: pair ratio `>=.55` plus compact/narrow conditions; preserve row order. Independent secondary needs vertical coverage `>=.16*height` and unpaired ratio `>=.45` or density `>.25`.
7. Independent regions rank by text width, width, x; primary emits before secondary. Narrow/wide ratio `<=.55` gives `sidebar`, otherwise `multi_column`. Full-width plus independent bands gives `mixed`.

Layout labels are `single_column`, `ambiguous`, `single_column_with_right_metadata`, `local_secondary`, `sidebar`, `multi_column`, and `mixed`. They are diagnostic; order is the important result. Known failures: floating/overlapping text, <3-line sidebars, narrow/unstable gutters, and graphics. The fallback preserves coverage, not ideal visual order.

## 8. Table handling

`page.find_tables()` is used. A table needs >=2 rows and a first row with >=2 nonempty cells; that row becomes headers. Later rows flatten to `Header: value | Header: value`; cell newlines become spaces; `key impact`/`impact` -> `Impact`, `app link`/`link` -> `Link`, missing headers -> `ColN`. A text block is excluded if contained by the table, intersects it by >10% of block area, or is vertically contained with x overlap (2pt y slack). Synthetic rows get evenly divided table-y geometry and run through normal layout. Any table exception is swallowed. This is valuable for structured records but lossy: cells/geometry do not survive.

## 9. Hyperlinks and URL behavior

`_validate_and_format_links` receives `page.get_links()`. It rejects missing URI and `javascript:`, HTML-unescapes, strips punctuation, collapses whitespace around `?`/`&`, repairs exact terminal `&am`/`&mt=` artifacts, deduplicates per-page URI, rejects bbox width `<15pt` or height `<5pt`, and requires visible-span overlap. In bottom 10% of page it additionally requires contact-like overlapping text. Retain only `mailto:` or validated web URL. Emit URI and rounded `x0/top/x1/bottom`; map nearest intersecting ordered line by vertical-center distance or leave annotation-only.

`_normalize_url` NFKC-normalizes, HTML-unescapes, removes zero-width/NBSP, strips outer punctuation, repairs malformed schemes, validates hostname <=253 with labels <=63 and alphabetic TLD 2--24, rejects credentials/invalid ports, lowercases host, removes default ports, and emits HTTPS while keeping path/query/fragment. `reconstruct_broken_urls` joins only URL-shaped newlines and decodes `&amp;`; ordinary prose is untouched. URL categories are syntactic only: LinkedIn, GitHub, known platform -> other, otherwise portfolio. They are not ownership facts.

## 10. Primitive extraction recipe

Order: stitch URL-shaped text; emails; dates on original text; annotation and visible URLs; mask URL/email/date/App Store IDs/hex hashes/alphanumeric handles; phones; locations from page lines; names from page one first eight lines.

Email regex: `\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b`; validate hostname and reject image/document/code-like suffixes. Lowercase domain only. Phone requires 10--15 digits and >2 distinct digits, rejects year ranges and bare numeric 0/1 starts; normalized output strips separators, preserves `+`, uses ` xN` extension. Date candidates support month/year, `MM/YYYY`, full numeric date, year, ranges with `- – — to until`, and open endpoints Present/Current/Ongoing/Now/Till Date; years 1900--2100, two-digit pivot 50; bare years only alone/labelled/compact pipe metadata <=8 tokens. They are text, not ISO facts.

Locations use capitalized comma shape, explicit labels, Remote/Hybrid, nearby date metadata within 2 lines, and location dictionary validation. Reject >65 chars, >8 tokens, URL/contact text, non-location keywords. Names are 2--5 title/ALL-CAPS tokens, 3--60 chars, no digits/contact punctuation/comma/blacklist/location/organization suffix, only page-one first eight lines, confidence >=.75. Both are candidates only.

## 11. Current RAW v3 versus future compact RAW

Current `RawDocument` schema is `3.0.0`: `source`, `document`, `extraction`, canonical `lines`, hyperlinks, referenced sections, referenced primitives, layout, diagnostics. `build_raw_evidence` makes `p<page>:l<index>` IDs, casefold-dedupes primitives and merges line references, and validates every reference. This is old provenance architecture.

Do not port line/source IDs, RawSection, evidence graphs, semantic provenance, semantic profile, SLM preprocessors, compatibility adapters, or duplicate buffers. Recommended compact schema:

```json
{"schema_version":"4.0.0","source":{"file_name":"resume.pdf","page_count":1},"extraction":{"success":true,"warnings":[]},"text":"...\f...","pages":[{"page":1,"text":"..."}],"hyperlinks":[{"page":1,"uri":"https://example.test"}],"extracted_fields":{"emails":[],"phone_numbers":[],"linkedin_urls":[],"github_urls":[],"portfolio_urls":[],"other_urls":[],"name_candidates":[],"location_candidates":[],"dates_found":[]}}
```

`json_writer._write_json` uses UTF-8 `json.dump(indent=2, ensure_ascii=False)`, same-directory `NamedTemporaryFile(delete=False)`, then `Path.replace()` atomically; no key sort. Preserve that behavior.

## 12. Configuration, tests, performance, porting priority

Extraction config is `input_dir`, `output_dir`, `log_file=logs/parser.log`, `overwrite=False`, `verbose=False`. Batch is sequential; a `fitz.Document` stays open through its page loop and page/span objects are retained, so memory scales approximately linearly. No thread/process concurrency contract exists; use one document per worker if adding parallelism.

Relevant tests: `test_extractor.py` (validation/failures/links/tables), `test_layout.py` (rows/columns/bullets/fallback), `test_reconstruction.py` (span whitespace), `test_regex_extractor.py` and `test_field_extraction_foundation.py` (primitives/nonmutation), `test_output_writer.py` (RAW/atomic/empty), `test_pipeline_integration.py` (batch/skip/isolation). Core invariants: preserve emitted source or fall back; preserve page order; do not merge ordinary prose; retain valid annotations; mask contacts before phone matching; one bad PDF cannot stop batch.

Measured on the 14-PDF available corpus with `PyMuPDFResumeExtractor.extract()`: 14/14 success, 1,103 lines, 42 retained links, 9 warning-bearing documents; min 70.86ms, max 408.26ms, median 131.69ms, mean 160.06ms, p90 310.39ms, 374.9 resumes/minute. No stage split is instrumented.

**Must port:** PyMuPDF dict extraction, span whitespace reconstruction, geometry order/fallback, visible hyperlink filtering, URL-aware phone masking, per-document isolation, atomic UTF-8 JSON. **Should port:** emails/phones/dates and clearly labelled candidate locations/names. **Optional:** table flattening, SHA256, producer metadata, quality score, gazetteer, progress/log rotation. **Do not port:** semantic/CLEAN/SLM layers and RAW v3 evidence storage.

## 13. Bootstrap checklist and known limits

1. Install PyMuPDF/pytest. 2. Implement validation/status and batch continuation. 3. Port span reconstruction. 4. Port rows, bands, gutters, region order, fallback. 5. Add validated tables only if needed. 6. Add annotation links and URL normalization. 7. Add primitives in documented order as candidates. 8. Emit page text and form-feed joined text. 9. Add synthetic whitespace/column/table/link/masking/failure tests. 10. Benchmark against Section 12.

Known limits: image-only PDFs require separate OCR; floating text may fall back to y/x order; table flattening loses cells; prose wraps remain physical; candidate location/name recall is heuristic; tiny legitimate links can be filtered; repeated headers/footers are preserved. The least reproducible component is `location_dictionary.py`: port its data verbatim or intentionally emit weaker unvalidated location candidates.

