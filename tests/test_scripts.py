from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.clean_json_data import (
    SECTION_HEADING_FORMS,
    SECTION_PATTERNS,
    SECTION_WHITESPACE_FREE_MAP,
    _form_to_regex,
    clean_single_resume_dict,
    clean_text_content,
    normalize_heading_candidate,
    normalize_letter_spaced_heading_candidate,
    normalize_phone_number,
    process_clean_json,
    reconstruct_multi_word_letter_spaced_heading_candidate,
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


def test_normalize_heading_candidate():
    # Colon normalization
    assert normalize_heading_candidate("Summary:") == "Summary"
    assert normalize_heading_candidate("  Experience:  ") == "Experience"

    # Period normalization
    assert normalize_heading_candidate("Summary.") == "Summary"
    assert normalize_heading_candidate("  Skills. ") == "Skills"

    # Single character limit
    assert normalize_heading_candidate("Summary::") == "Summary:"
    assert normalize_heading_candidate("Skills...") == "Skills.."
    assert normalize_heading_candidate("Experience.:") == "Experience."

    # Unpunctuated
    assert normalize_heading_candidate("Summary") == "Summary"
    assert normalize_heading_candidate("  Work Experience  ") == "Work Experience"

    # Negative punctuation (must NOT be stripped)
    assert normalize_heading_candidate("Summary-") == "Summary-"
    assert normalize_heading_candidate("Summary–") == "Summary–"
    assert normalize_heading_candidate("Summary—") == "Summary—"
    assert normalize_heading_candidate("Summary|") == "Summary|"
    assert normalize_heading_candidate("Summary•") == "Summary•"
    assert normalize_heading_candidate("Summary;") == "Summary;"
    assert normalize_heading_candidate("Summary,") == "Summary,"
    assert normalize_heading_candidate("Summary!") == "Summary!"
    assert normalize_heading_candidate("Summary?") == "Summary?"


def test_split_into_sections_colon_headings():
    headings = [
        ("Summary:", "summary"),
        ("Experience:", "experience"),
        ("Education:", "education"),
        ("Skills:", "skills"),
        ("Certifications:", "certifications"),
    ]
    for heading, expected_sec in headings:
        text = f"Header info\n{heading}\nContent line 1\nContent line 2\n"
        sections = split_into_sections(text)
        assert expected_sec in sections, f"Failed to recognize heading '{heading}' as '{expected_sec}'"
        assert sections[expected_sec] == ["Content line 1", "Content line 2"]
        assert sections["header"] == ["Header info"]


def test_split_into_sections_period_headings():
    headings = [
        ("Summary.", "summary"),
        ("Education.", "education"),
        ("Skills.", "skills"),
    ]
    for heading, expected_sec in headings:
        text = f"Header info\n{heading}\nContent line 1\nContent line 2\n"
        sections = split_into_sections(text)
        assert expected_sec in sections, f"Failed to recognize heading '{heading}' as '{expected_sec}'"
        assert sections[expected_sec] == ["Content line 1", "Content line 2"]
        assert sections["header"] == ["Header info"]


def test_cleaned_text_preserves_original_heading_line():
    raw_sample = {
        "source": {"file_name": "sample.pdf", "page_count": 1},
        "extraction": {"success": True, "warnings": []},
        "text": "Name\nSummary:\nExperienced developer.\nSkills:\nPython\n",
        "hyperlinks": [],
        "extracted_fields": {},
        "schema_version": "4.0.0",
    }
    clean_dict = clean_single_resume_dict(raw_sample)

    # cleaned_text must contain the ORIGINAL line with trailing punctuation
    assert "Summary:" in clean_dict["cleaned_text"]
    assert "Skills:" in clean_dict["cleaned_text"]
    assert "Summary" not in clean_dict["cleaned_text"]

    # sections must contain content, not heading
    assert clean_dict["sections"]["summary"] == ["Experienced developer."]
    assert clean_dict["sections"]["skills"] == ["Python"]
    assert "Summary:" not in clean_dict["sections"]["summary"]


def test_negative_punctuation_headings_not_normalized():
    negative_headings = [
        "Summary-",
        "Summary–",
        "Summary—",
        "Summary|",
        "Summary;",
        "Summary,",
        "Summary!",
    ]
    for heading in negative_headings:
        text = f"Intro line\n{heading}\nBody line\n"
        sections = split_into_sections(text)
        # Should NOT recognize 'summary'; all lines remain under 'header'
        assert "summary" not in sections
        assert heading in sections["header"]
        assert "Body line" in sections["header"]


def test_single_character_limit_headings():
    multi_punct = [
        "Summary::",
        "Skills...",
        "Experience.:",
    ]
    for heading in multi_punct:
        text = f"Intro line\n{heading}\nBody line\n"
        sections = split_into_sections(text)
        assert "summary" not in sections
        assert "skills" not in sections
        assert "experience" not in sections
        assert heading in sections["header"]


def test_non_heading_label_colon():
    labels = [
        "Programming languages:",
        "Workflow Automation:",
        "Role:",
        "Project:",
        "Employer:",
        "Client:",
        "Location:",
        "Technologies:",
        "Responsibilities:",
    ]
    for label in labels:
        # Note: "technologies" is a skill synonym, so "Technologies:" maps to skills.
        # But non-vocab labels must NOT open sections.
        if label == "Technologies:":
            continue
        text = f"Intro line\n{label}\nDetails line\n"
        sections = split_into_sections(text)
        assert len(sections) == 1 and "header" in sections
        assert label in sections["header"]
        assert "Details line" in sections["header"]


def test_section_ownership_and_no_duplication_or_loss():
    doc = (
        "Header content\n"
        "Summary:\n"
        "Summary body\n"
        "Experience:\n"
        "Experience body\n"
        "Skills:\n"
        "Skills body\n"
    )
    sections = split_into_sections(doc)

    assert sections["header"] == ["Header content"]
    assert sections["summary"] == ["Summary body"]
    assert sections["experience"] == ["Experience body"]
    assert sections["skills"] == ["Skills body"]

    all_lines = [line for sec in sections.values() for line in sec]
    assert all_lines == [
        "Header content",
        "Summary body",
        "Experience body",
        "Skills body",
    ]


def test_section_transition_safety():
    doc = (
        "Experience:\n"
        "Experience line 1\n"
        "Experience line 2\n"
        "Education:\n"
        "Education line\n"
    )
    sections = split_into_sections(doc)

    assert "experience" in sections
    assert "education" in sections
    assert sections["experience"] == ["Experience line 1", "Experience line 2"]
    assert sections["education"] == ["Education line"]


def test_first_heading_header_fallback():
    doc = (
        "Candidate Name\n"
        "contact@example.com\n"
        "Summary:\n"
        "Passionate engineer.\n"
    )
    sections = split_into_sections(doc)

    assert sections["header"] == ["Candidate Name", "contact@example.com"]
    assert sections["summary"] == ["Passionate engineer."]


def test_body_text_with_period_and_colon():
    doc = (
        "Professional Summary\n"
        "Built scalable data pipelines.\n"
        "Programming languages:\n"
        "Python, SQL, Go\n"
    )
    sections = split_into_sections(doc)

    assert "summary" in sections
    assert sections["summary"] == [
        "Built scalable data pipelines.",
        "Programming languages:",
        "Python, SQL, Go",
    ]


def test_normalize_letter_spaced_heading_candidate_positives():
    assert normalize_letter_spaced_heading_candidate("S U M M A R Y") == "SUMMARY"
    assert normalize_letter_spaced_heading_candidate("S K I L L S") == "SKILLS"
    assert normalize_letter_spaced_heading_candidate("E D U C A T I O N") == "EDUCATION"
    assert normalize_letter_spaced_heading_candidate("E X P E R I E N C E") == "EXPERIENCE"
    assert normalize_letter_spaced_heading_candidate("s k i l l s") == "skills"


def test_normalize_letter_spaced_heading_candidate_negatives():
    # Whole-line rule: mixed words and single letters must not collapse
    assert normalize_letter_spaced_heading_candidate("A B Testing") == "A B Testing"
    assert normalize_letter_spaced_heading_candidate("R D Engineer") == "R D Engineer"
    assert normalize_letter_spaced_heading_candidate("B Tech") == "B Tech"
    assert normalize_letter_spaced_heading_candidate("M S Computer Science") == "M S Computer Science"
    assert normalize_letter_spaced_heading_candidate("Core S K I L L S") == "Core S K I L L S"
    assert normalize_letter_spaced_heading_candidate("S K I L L S Tools") == "S K I L L S Tools"

    # Two-token limit: len(tokens) < 3 must not collapse
    assert normalize_letter_spaced_heading_candidate("A B") == "A B"
    assert normalize_letter_spaced_heading_candidate("R D") == "R D"

    # Digits must not collapse
    assert normalize_letter_spaced_heading_candidate("2 0 2 6") == "2 0 2 6"
    assert normalize_letter_spaced_heading_candidate("1 2 3") == "1 2 3"

    # Punctuation bearing tokens must not collapse
    assert normalize_letter_spaced_heading_candidate("S K I L L S -") == "S K I L L S -"
    assert normalize_letter_spaced_heading_candidate("S K I L L S |") == "S K I L L S |"
    assert normalize_letter_spaced_heading_candidate("S K I L L S ;") == "S K I L L S ;"
    assert normalize_letter_spaced_heading_candidate("S. K. I. L. L. S.") == "S. K. I. L. L. S."


def test_split_into_sections_letter_spaced_positives():
    headings = [
        ("S U M M A R Y", "summary"),
        ("S K I L L S", "skills"),
        ("E D U C A T I O N", "education"),
        ("E X P E R I E N C E", "experience"),
    ]
    for heading, expected_sec in headings:
        text = f"Intro header\n{heading}\nLine 1\nLine 2\n"
        sections = split_into_sections(text)
        assert expected_sec in sections, f"Failed to match letter-spaced heading '{heading}'"
        assert sections[expected_sec] == ["Line 1", "Line 2"]
        assert sections["header"] == ["Intro header"]


def test_split_into_sections_letter_spaced_with_colon_and_period():
    # S K I L L S: -> colon stripped to S K I L L S -> letter collapsed to SKILLS -> skills
    text_colon = "Intro\nS K I L L S:\nPython\nSQL\n"
    sections_colon = split_into_sections(text_colon)
    assert "skills" in sections_colon
    assert sections_colon["skills"] == ["Python", "SQL"]

    # E D U C A T I O N. -> period stripped to E D U C A T I O N -> EDUCATION -> education
    text_period = "Intro\nE D U C A T I O N.\nBS Computer Science\n"
    sections_period = split_into_sections(text_period)
    assert "education" in sections_period
    assert sections_period["education"] == ["BS Computer Science"]


def test_split_into_sections_letter_spaced_repeated_punct_fails_closed():
    # Repeated punctuation must fail closed
    for heading in ["S K I L L S::", "S K I L L S..."]:
        text = f"Intro\n{heading}\nPython\n"
        sections = split_into_sections(text)
        assert "skills" not in sections
        assert heading in sections["header"]


def test_cleaned_text_preserves_original_letter_spaced_line():
    raw_sample = {
        "source": {"file_name": "sample.pdf", "page_count": 1},
        "extraction": {"success": True, "warnings": []},
        "text": "Header line\nS K I L L S\nPython\nSQL\n",
        "hyperlinks": [],
        "extracted_fields": {},
        "schema_version": "4.0.0",
    }
    clean_dict = clean_single_resume_dict(raw_sample)

    # cleaned_text must contain the ORIGINAL line with spaces
    assert "S K I L L S" in clean_dict["cleaned_text"]
    assert "SKILLS" not in clean_dict["cleaned_text"]

    # sections must contain content, not heading
    assert clean_dict["sections"]["skills"] == ["Python", "SQL"]
    assert "S K I L L S" not in clean_dict["sections"]["skills"]


def test_section_ownership_transition_letter_spaced():
    doc = (
        "Experience\n"
        "Job body line 1\n"
        "Job body line 2\n"
        "E D U C A T I O N\n"
        "Degree body line\n"
        "S K I L L S\n"
        "Python\n"
    )
    sections = split_into_sections(doc)

    assert sections["experience"] == ["Job body line 1", "Job body line 2"]
    assert sections["education"] == ["Degree body line"]
    assert sections["skills"] == ["Python"]

    all_lines = [l for sec in sections.values() for l in sec]
    assert all_lines == [
        "Job body line 1",
        "Job body line 2",
        "Degree body line",
        "Python",
    ]


def test_header_fallback_letter_spaced():
    doc = (
        "Candidate Header\n"
        "Contact Data\n"
        "S U M M A R Y\n"
        "Summary body line\n"
    )
    sections = split_into_sections(doc)

    assert sections["header"] == ["Candidate Header", "Contact Data"]
    assert sections["summary"] == ["Summary body line"]


def test_surface_form_derivation_and_equivalence():
    # Verify authoritative single source
    assert len(SECTION_HEADING_FORMS) == 8
    total_forms = sum(len(forms) for forms in SECTION_HEADING_FORMS.values())
    assert total_forms == 40

    # Verify SECTION_PATTERNS derived from SECTION_HEADING_FORMS
    assert len(SECTION_PATTERNS) == 8
    for sec_name, pattern in SECTION_PATTERNS:
        assert sec_name in SECTION_HEADING_FORMS
        # Every form in SECTION_HEADING_FORMS must match its derived pattern
        for form in SECTION_HEADING_FORMS[sec_name]:
            assert pattern.match(form), f"Form '{form}' did not match derived pattern for {sec_name}"

    # Verify whitespace-free map derived from SECTION_HEADING_FORMS
    assert len(SECTION_WHITESPACE_FREE_MAP) == 40
    for sec_name, forms in SECTION_HEADING_FORMS.items():
        for form in forms:
            key = form.replace(" ", "").lower()
            assert key in SECTION_WHITESPACE_FREE_MAP
            assert SECTION_WHITESPACE_FREE_MAP[key] == form


def test_reconstruct_multi_word_letter_spaced_heading_candidate_positives():
    assert reconstruct_multi_word_letter_spaced_heading_candidate("P R O F E S S I O N A L S U M M A R Y") == "professional summary"
    assert reconstruct_multi_word_letter_spaced_heading_candidate("C O R E C O M P E T E N C I E S") == "core competencies"
    assert reconstruct_multi_word_letter_spaced_heading_candidate("P R O F E S S I O N A L E X P E R I E N C E") == "professional experience"
    assert reconstruct_multi_word_letter_spaced_heading_candidate("K E Y P R O J E C T S") == "key projects"


def test_split_into_sections_multi_word_positives():
    headings = [
        ("P R O F E S S I O N A L S U M M A R Y", "summary"),
        ("C O R E C O M P E T E N C I E S", "skills"),
        ("P R O F E S S I O N A L E X P E R I E N C E", "experience"),
        ("K E Y P R O J E C T S", "projects"),
    ]
    for heading, expected_sec in headings:
        doc = f"Intro header\n{heading}\nContent line 1\nContent line 2\n"
        sections = split_into_sections(doc)
        assert expected_sec in sections, f"Failed to match multi-word letter-spaced heading '{heading}'"
        assert sections[expected_sec] == ["Content line 1", "Content line 2"]
        assert sections["header"] == ["Intro header"]


def test_cleaned_text_preserves_original_multi_word_letter_spaced_line():
    raw_sample = {
        "source": {"file_name": "sample.pdf", "page_count": 1},
        "extraction": {"success": True, "warnings": []},
        "text": "Header line\nP R O F E S S I O N A L S U M M A R Y\nExperienced developer.\n",
        "hyperlinks": [],
        "extracted_fields": {},
        "schema_version": "4.0.0",
    }
    clean_dict = clean_single_resume_dict(raw_sample)

    # cleaned_text must contain the ORIGINAL line with spaces
    assert "P R O F E S S I O N A L S U M M A R Y" in clean_dict["cleaned_text"]
    assert "professional summary" not in clean_dict["cleaned_text"]
    assert "Professional Summary" not in clean_dict["cleaned_text"]

    # sections must contain content, not heading
    assert clean_dict["sections"]["summary"] == ["Experienced developer."]
    assert "P R O F E S S I O N A L S U M M A R Y" not in clean_dict["sections"]["summary"]


def test_multi_word_section_transitions():
    doc = (
        "P R O F E S S I O N A L S U M M A R Y\n"
        "Summary body line 1\n"
        "Summary body line 2\n"
        "P R O F E S S I O N A L E X P E R I E N C E\n"
        "Job body line\n"
        "K E Y P R O J E C T S\n"
        "Project body line\n"
    )
    sections = split_into_sections(doc)

    assert sections["summary"] == ["Summary body line 1", "Summary body line 2"]
    assert sections["experience"] == ["Job body line"]
    assert sections["projects"] == ["Project body line"]

    all_lines = [l for sec in sections.values() for l in sec]
    assert all_lines == [
        "Summary body line 1",
        "Summary body line 2",
        "Job body line",
        "Project body line",
    ]


def test_multi_word_unmatched_letter_spaced_line():
    doc = (
        "Intro header\n"
        "D A T A S C I E N C E\n"
        "Machine Learning\n"
    )
    sections = split_into_sections(doc)
    assert len(sections) == 1 and "header" in sections
    assert "D A T A S C I E N C E" in sections["header"]
    assert "Machine Learning" in sections["header"]


def test_multi_word_repeated_punctuation_fails_closed():
    for heading in ["P R O F E S S I O N A L S U M M A R Y::", "K E Y P R O J E C T S..."]:
        doc = f"Intro header\n{heading}\nContent line\n"
        sections = split_into_sections(doc)
        assert len(sections) == 1 and "header" in sections
        assert heading in sections["header"]


def test_multi_word_mixed_token_form_fails_closed():
    mixed = [
        "Professional S U M M A R Y",
        "Core C O M P E T E N C I E S",
        "P R O F E S S I O N A L Experience",
    ]
    for heading in mixed:
        # None of these are single-word or pure single-letter tokens, and neither is in vocabulary
        doc = f"Intro header\n{heading}\nContent line\n"
        sections = split_into_sections(doc)
        assert len(sections) == 1 and "header" in sections
        assert heading in sections["header"]


def test_negative_structural_letter_spaced_cases():
    negatives = [
        "J O H N D O E",
        "D A T A E N G I N E E R",
        "P R O F E S S I O N A L",
        "T E C H N I C A L",
        "K E Y",
        "H I G H L I G H T S",
    ]
    for heading in negatives:
        doc = f"Intro header\n{heading}\nContent line\n"
        sections = split_into_sections(doc)
        assert len(sections) == 1 and "header" in sections
        assert heading in sections["header"]


def test_multi_word_colon_period_composition():
    cases = [
        ("P R O F E S S I O N A L S U M M A R Y:", "summary"),
        ("C O R E C O M P E T E N C I E S.", "skills"),
        ("P R O F E S S I O N A L E X P E R I E N C E:", "experience"),
        ("K E Y P R O J E C T S.", "projects"),
    ]
    for heading, expected_sec in cases:
        doc = f"Intro\n{heading}\nContent\n"
        sections = split_into_sections(doc)
        assert expected_sec in sections, f"Failed for {heading}"
        assert sections[expected_sec] == ["Content"]


def test_form_to_regex_exact_token_compiler_semantics():
    # Ordinary words use \s+
    assert _form_to_regex("professional summary") == r"professional\s+summary"
    assert _form_to_regex("academic background") == r"academic\s+background"

    # Exact "&" and "and" connectors use \s*
    assert _form_to_regex("skills & abilities") == r"skills\s*\&\s*abilities"
    assert _form_to_regex("skills and abilities") == r"skills\s*and\s*abilities"
    assert _form_to_regex("licenses & certifications") == r"licenses\s*\&\s*certifications"
    assert _form_to_regex("licenses and certifications") == r"licenses\s*and\s*certifications"
    assert _form_to_regex("awards & achievements") == r"awards\s*\&\s*achievements"
    assert _form_to_regex("awards and achievements") == r"awards\s*and\s*achievements"

    # Substring "and" safety: words containing "and" must NOT be treated as connectors
    assert _form_to_regex("standard tools") == r"standard\s+tools"
    assert _form_to_regex("android development") == r"android\s+development"
    assert _form_to_regex("handbook skills") == r"handbook\s+skills"
    assert _form_to_regex("candidate background") == r"candidate\s+background"


def test_historical_connector_whitespace_language_positives():
    # Historical connector language accepts zero-space, one-sided zero-space, and spaced variants
    skills_variants = [
        "skills&abilities",
        "skills & abilities",
        "skills  &  abilities",
        "skills\t&\tabilities",
        "skillsandabilities",
        "skillsand abilities",
        "skills andabilities",
        "skills and abilities",
        "skills  and  abilities",
        "skills\tand\tabilities",
    ]
    for variant in skills_variants:
        doc = f"Intro\n{variant}\nPython details\n"
        sections = split_into_sections(doc)
        assert "skills" in sections, f"Failed to match skills variant: {variant!r}"
        assert sections["skills"] == ["Python details"]

    cert_variants = [
        "licenses&certifications",
        "licenses & certifications",
        "licensesandcertifications",
        "licenses and certifications",
        "licensesand certifications",
        "licenses andcertifications",
    ]
    for variant in cert_variants:
        doc = f"Intro\n{variant}\nAWS Certified\n"
        sections = split_into_sections(doc)
        assert "certifications" in sections, f"Failed to match certifications variant: {variant!r}"
        assert sections["certifications"] == ["AWS Certified"]

    award_variants = [
        "awards&achievements",
        "awards & achievements",
        "awardsandachievements",
        "awards and achievements",
        "awardsand achievements",
        "awards andachievements",
    ]
    for variant in award_variants:
        doc = f"Intro\n{variant}\nFirst Prize\n"
        sections = split_into_sections(doc)
        assert "awards" in sections, f"Failed to match awards variant: {variant!r}"
        assert sections["awards"] == ["First Prize"]


def test_connector_case_variants():
    case_variants = [
        ("SKILLSANDABILITIES", "skills"),
        ("SkillsAndAbilities", "skills"),
        ("skillsANDabilities", "skills"),
        ("SKILLS & ABILITIES", "skills"),
        ("LICENSESANDCERTIFICATIONS", "certifications"),
        ("LicensesAndCertifications", "certifications"),
        ("AWARDSANDACHIEVEMENTS", "awards"),
        ("AwardsAndAchievements", "awards"),
    ]
    for variant, expected_sec in case_variants:
        doc = f"Intro\n{variant}\nContent\n"
        sections = split_into_sections(doc)
        assert expected_sec in sections, f"Failed for case variant {variant!r}"
        assert sections[expected_sec] == ["Content"]


def test_connector_malformed_negatives_rejected():
    malformed = [
        "skillsandability",
        "skillandabilities",
        "skillsorabilities",
        "skillsand",
        "andabilities",
        "skills & capability",
        "licensesandcertificate",
        "awardsandachievement",
    ]
    for variant in malformed:
        doc = f"Intro\n{variant}\nContent\n"
        sections = split_into_sections(doc)
        assert len(sections) == 1 and "header" in sections
        assert variant in sections["header"]


def test_ordinary_multi_word_unletterspaced_rejected_without_space():
    # Ordinary multi-word headings without connectors require whitespace
    doc_unspaced = "Intro\nprofessionalsummary\nContent\n"
    sec_unspaced = split_into_sections(doc_unspaced)
    assert len(sec_unspaced) == 1 and "header" in sec_unspaced
    assert "professionalsummary" in sec_unspaced["header"]

    doc_spaced = "Intro\nprofessional summary\nContent\n"
    sec_spaced = split_into_sections(doc_spaced)
    assert "summary" in sec_spaced
    assert sec_spaced["summary"] == ["Content"]


def test_cleaned_text_preserves_connector_heading_line():
    raw_sample = {
        "source": {"file_name": "sample.pdf", "page_count": 1},
        "extraction": {"success": True, "warnings": []},
        "text": "Header line\nSkillsAndAbilities\nPython\n",
        "hyperlinks": [],
        "extracted_fields": {},
        "schema_version": "4.0.0",
    }
    clean_dict = clean_single_resume_dict(raw_sample)
    assert "SkillsAndAbilities" in clean_dict["cleaned_text"]
    assert clean_dict["sections"]["skills"] == ["Python"]



