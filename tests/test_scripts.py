from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.clean_json_data import (
    clean_single_resume_dict,
    clean_text_content,
    normalize_phone_number,
    process_clean_json,
    split_into_sections,
)
from scripts.clean_outputs import clean_outputs, collect_targets
from scripts.convert_test_resumes import convert_resumes


def test_convert_resumes_on_sample(tmp_path: Path):
    sample_dir = Path("test_resumes_sample")
    assert sample_dir.exists()

    output_dir = tmp_path / "raw"
    result = convert_resumes(
        input_path=sample_dir,
        output_dir=output_dir,
        overwrite=True,
        verbose=False,
    )

    assert result["attempted"] == 3
    assert result["successful"] == 3
    assert result["failed"] == 0

    json_files = list(output_dir.glob("*.json"))
    assert len(json_files) == 3

    for json_file in json_files:
        with json_file.open("r", encoding="utf-8") as f:
            data = json.load(f)
        assert data["schema_version"] == "4.0.0"
        assert data["extraction"]["success"] is True
        assert len(data["text"]) > 0


def test_clean_outputs_dry_run_and_execution(tmp_path: Path):
    dummy_out = tmp_path / "test_output"
    dummy_out.mkdir()
    f1 = dummy_out / "sample.json"
    f1.write_text('{"test": true}', encoding="utf-8")
    assert f1.exists()

    # Dry-run should not delete
    code = clean_outputs(target_dir=dummy_out, dry_run=True, yes=True)
    assert code == 0
    assert f1.exists()

    # Real run should delete
    code = clean_outputs(target_dir=dummy_out, dry_run=False, yes=True)
    assert code == 0
    assert not f1.exists()
    assert not dummy_out.exists()


def test_clean_outputs_protection(monkeypatch):
    root = Path(__file__).resolve().parent.parent
    with pytest.raises(ValueError, match="protected"):
        collect_targets(root / "src")


def test_clean_json_data_helpers():
    # Text sanitization
    raw = "Line 1   \r\n\r\n\r\nLine 2\u00a0with space\u200b\n\n\n\nLine 3"
    cleaned = clean_text_content(raw)
    assert "Line 1\n\nLine 2 with space\n\nLine 3" == cleaned

    # Phone normalization
    assert normalize_phone_number("9822182681") == "+91 98221 82681"
    assert normalize_phone_number("+919822182681") == "+91 98221 82681"

    # Section splitting
    doc_text = (
        "John Doe\njohn@example.com\n"
        "Professional Summary\nExperienced developer.\n"
        "Technical Skills\nPython, Docker.\n"
        "Work Experience\nCompany A - 2020 to 2024\n"
        "Education\nBS Computer Science\n"
    )
    sections = split_into_sections(doc_text)
    assert "header" in sections
    assert "summary" in sections
    assert "skills" in sections
    assert "experience" in sections
    assert "education" in sections
    assert "Experienced developer." in sections["summary"]


def test_clean_json_data_pipeline(tmp_path: Path):
    raw_dir = tmp_path / "raw"
    clean_dir = tmp_path / "cleaned"
    raw_dir.mkdir()

    raw_sample = {
        "source": {"file_name": "test.pdf", "page_count": 2},
        "extraction": {"success": True, "warnings": []},
        "text": "John Doe\n\nWork Experience\nEngineer at Acme\fEducation\nBS Math",
        "hyperlinks": [],
        "extracted_fields": {
            "emails": ["JOHN@Example.com"],
            "phone_numbers": ["9876543210"],
            "linkedin_urls": ["https://linkedin.com/in/johndoe"],
            "github_urls": [],
            "other_urls": [],
        },
        "schema_version": "4.0.0",
    }
    sample_file = raw_dir / "test.json"
    with sample_file.open("w", encoding="utf-8") as f:
        json.dump(raw_sample, f)

    ret = process_clean_json(input_path=raw_dir, output_dir=clean_dir, verbose=True)
    assert ret == 0

    cleaned_file = clean_dir / "test.json"
    assert cleaned_file.exists()

    with cleaned_file.open("r", encoding="utf-8") as f:
        data = json.load(f)

    assert data["schema_version"] == "4.0.0-cleaned"
    assert data["contact_info"]["emails"] == ["john@example.com"]
    assert data["contact_info"]["phone_numbers"] == ["+91 98765 43210"]
    assert len(data["pages"]) == 2
    assert "experience" in data["sections"]
    assert "education" in data["sections"]
