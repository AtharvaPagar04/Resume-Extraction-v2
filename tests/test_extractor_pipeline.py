import json
from pathlib import Path

import pymupdf as fitz
import pytest

import resume_extractor.extractor as extractor_module
from resume_extractor.extractor import _excluded, _table_lines, extract_pdf, split_pages
from resume_extractor.layout import LayoutLine
from resume_extractor.pipeline import discover_pdfs, run_pipeline, write_raw


def pdf(path: Path, pages=("Hello, world!",)) -> Path:
    document = fitz.open()
    for text in pages:
        page = document.new_page()
        if text:
            page.insert_text((72, 72), text, fontsize=12)
    document.save(path)
    document.close()
    return path


def test_basic_pdf_validation_missing_file(tmp_path):
    raw = extract_pdf(tmp_path / "missing.pdf")
    assert not raw.extraction.success and raw.extraction.warnings == ["FILE_NOT_FOUND"]


def test_invalid_header_fails_safely(tmp_path):
    path = tmp_path / "not.pdf"
    path.write_text("not a pdf")
    assert extract_pdf(path).extraction.warnings == ["INVALID_PDF_HEADER"]


def test_one_page_extraction_preserves_line_content(tmp_path):
    raw = extract_pdf(pdf(tmp_path / "resume.pdf", ("Punctuation: hello, world!",)))
    assert raw.extraction.success and "Punctuation: hello, world!" in raw.text and raw.source.page_count == 1


def test_multi_page_uses_form_feed_and_keeps_page_order(tmp_path):
    raw = extract_pdf(pdf(tmp_path / "resume.pdf", ("first", "second")))
    assert raw.text == "first\fsecond" and split_pages(raw.text) == ["first", "second"]


def test_empty_page_preserves_form_feed_position(tmp_path):
    raw = extract_pdf(pdf(tmp_path / "resume.pdf", ("first", "", "third")))
    assert raw.text.count("\f") == 2 and split_pages(raw.text) == ["first", "", "third"]


def test_image_only_pdf_requests_ocr(tmp_path):
    raw = extract_pdf(pdf(tmp_path / "scan.pdf", ("",)))
    assert raw.extraction.success and "NEEDS_OCR" in raw.extraction.warnings


def test_zero_page_pdf_is_safe(tmp_path, monkeypatch):
    path = pdf(tmp_path / "zero.pdf")

    class ZeroPageDocument:
        page_count = 0
        is_encrypted = False

        @staticmethod
        def close():
            pass

    monkeypatch.setattr(extractor_module.fitz, "open", lambda _: ZeroPageDocument())
    raw = extract_pdf(path)
    assert raw.extraction.success and raw.source.page_count == 0 and "NO_EXTRACTABLE_TEXT" in raw.extraction.warnings


def test_encrypted_pdf_is_rejected(tmp_path):
    document = fitz.open()
    document.new_page().insert_text((72, 72), "secret")
    path = tmp_path / "encrypted.pdf"
    document.save(path, encryption=fitz.PDF_ENCRYPT_AES_256, user_pw="password", owner_pw="owner")
    document.close()
    raw = extract_pdf(path)
    assert not raw.extraction.success and raw.extraction.warnings == ["ENCRYPTED_PDF"]


def test_page_failure_keeps_other_page_positions(tmp_path, monkeypatch):
    path = pdf(tmp_path / "resume.pdf", ("one", "two", "three"))
    calls = 0

    def flaky_page_lines(page):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("synthetic page failure")
        return [LayoutLine(str(calls), str(calls), (0, 0, 10, 10), 0, 0, (0, 0), (0,))], False, False

    monkeypatch.setattr(extractor_module, "_page_lines", flaky_page_lines)
    raw = extract_pdf(path)
    assert raw.extraction.success and raw.text == "1\f\f3" and "PARTIAL_PAGE_EXTRACTION" in raw.extraction.warnings


def test_hyperlink_annotation_survives_without_visible_overlap(tmp_path):
    path = pdf(tmp_path / "resume.pdf", ("plain text",))
    document = fitz.open(path)
    document[0].insert_link({"kind": fitz.LINK_URI, "from": fitz.Rect(1, 1, 2, 2), "uri": "https://example.com/annotation"})
    document.save(tmp_path / "linked.pdf")
    document.close()
    raw = extract_pdf(tmp_path / "linked.pdf")
    assert [(link.page, link.uri) for link in raw.hyperlinks] == [(1, "https://example.com/annotation")]


def test_duplicate_annotation_links_are_deduplicated_per_page(tmp_path):
    path = pdf(tmp_path / "resume.pdf", ("plain",))
    document = fitz.open(path)
    for y in (1, 4):
        document[0].insert_link({"kind": fitz.LINK_URI, "from": fitz.Rect(1, y, 10, y + 2), "uri": "https://example.com"})
    document.save(tmp_path / "linked.pdf")
    document.close()
    assert len(extract_pdf(tmp_path / "linked.pdf").hyperlinks) == 1


def test_malformed_and_javascript_links_are_skipped(tmp_path):
    path = pdf(tmp_path / "resume.pdf", ("plain",))
    document = fitz.open(path)
    document[0].insert_link({"kind": fitz.LINK_URI, "from": fitz.Rect(1, 1, 10, 4), "uri": "javascript:alert(1)"})
    document.save(tmp_path / "linked.pdf")
    document.close()
    raw = extract_pdf(tmp_path / "linked.pdf")
    assert raw.hyperlinks == [] and "MALFORMED_LINK_SKIPPED" in raw.extraction.warnings


def test_table_flattening_and_exclusion_helpers():
    class Table:
        bbox = (0, 0, 100, 40)

        @staticmethod
        def extract():
            return [["Skill", "Level"], ["Python", "Advanced"]]

    class Page:
        rect = type("Rect", (), {"width": 200})()

        @staticmethod
        def find_tables():
            return type("Found", (), {"tables": [Table()]})()

    lines, boxes, failed = _table_lines(Page(), 200)
    assert not failed and lines[0].text == "Skill: Python | Level: Advanced" and boxes == [(0, 0, 100, 40)]
    assert _excluded((10, 10, 90, 30), boxes[0])


def test_table_failure_falls_back_without_document_failure(tmp_path, monkeypatch):
    path = pdf(tmp_path / "resume.pdf")
    monkeypatch.setattr(extractor_module, "_table_lines", lambda *args: ([], [], True))
    raw = extract_pdf(path)
    assert raw.extraction.success and "TABLE_EXTRACTION_FAILED" in raw.extraction.warnings


def test_raw_schema_is_exact_and_has_no_duplicate_text(tmp_path):
    raw = extract_pdf(pdf(tmp_path / "resume.pdf"))
    payload = raw.to_dict()
    assert set(payload) == {"schema_version", "source", "extraction", "text", "hyperlinks", "extracted_fields"}
    prohibited = {"pages", "lines", "sections", "layout", "provenance", "semantic_profile", "diagnostics", "full_text", "raw_text", "full_clean_text", "cleaned_text", "name_candidates", "location_candidates", "dates_found"}
    assert not prohibited.intersection(payload) and set(payload["extracted_fields"]) == {"emails", "phone_numbers", "linkedin_urls", "github_urls", "other_urls"}


def test_atomic_json_write_and_overwrite_protection(tmp_path):
    raw = extract_pdf(pdf(tmp_path / "resume.pdf"))
    target = tmp_path / "out.json"
    write_raw(raw, target)
    assert json.loads(target.read_text())["text"] == raw.text
    with pytest.raises(FileExistsError):
        write_raw(raw, target)


def test_discovery_is_recursive_deterministic_and_skips_hidden(tmp_path):
    pdf(tmp_path / "b.pdf")
    (tmp_path / "nested").mkdir()
    pdf(tmp_path / "nested" / "a.pdf")
    pdf(tmp_path / ".hidden.pdf")
    pdf(tmp_path / "upper.PDF")
    assert [file.name for file in discover_pdfs(tmp_path)] == ["b.pdf", "a.pdf", "upper.PDF"]


def test_batch_failure_isolation_and_output_naming(tmp_path):
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    pdf(inputs / "good.pdf")
    (inputs / "bad.pdf").write_text("bad")
    output = tmp_path / "output"
    summary = run_pipeline(inputs, output)
    assert (output / "good.json").exists() and (output / "bad.json").exists()
    assert (summary.attempted, summary.successful, summary.failed) == (2, 1, 1)


def test_pipeline_does_not_overwrite_without_flag(tmp_path):
    source = pdf(tmp_path / "resume.pdf")
    output = tmp_path / "result.json"
    run_pipeline(source, output)
    assert run_pipeline(source, output).skipped == 1
