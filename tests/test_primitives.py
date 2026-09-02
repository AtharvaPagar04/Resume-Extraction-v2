from resume_extractor.primitives import extract_fields, normalize_url, reconstruct_broken_urls


def test_normalizes_url_host_and_html_escape():
    assert normalize_url("HTTPS://Example.COM:443/a?x=1&amp;y=2.") == "https://example.com/a?x=1&y=2"


def test_rejects_javascript_uri():
    assert normalize_url("javascript:alert(1)") is None


def test_rejects_malformed_url():
    assert normalize_url("https://bad host.example") is None


def test_reconstructs_url_shaped_broken_text_only():
    assert reconstruct_broken_urls("https://example.com/a\nb?x=1") == "https://example.com/ab?x=1"


def test_extracts_and_normalizes_email():
    assert extract_fields("USER@Example.COM").emails == ["USER@example.com"]


def test_extracts_phone_with_extension():
    assert extract_fields("Call +91 (98765) 43210 ext 12").phone_numbers == ["+919876543210 x12"]


def test_phone_extraction_masks_urls_and_dates():
    assert extract_fields("https://example.com/2024-2025 2020-2024").phone_numbers == []


def test_phone_rejects_repeated_digits_and_bad_prefix():
    assert extract_fields("0000000000 1111111111").phone_numbers == []


def test_classifies_linkedin_syntactically():
    assert extract_fields("https://www.linkedin.com/in/example").linkedin_urls == ["https://www.linkedin.com/in/example"]


def test_classifies_github_syntactically():
    assert extract_fields("https://github.com/example").github_urls == ["https://github.com/example"]


def test_keeps_generic_urls_without_portfolio_ownership():
    fields = extract_fields("https://example.org/work https://example.org/work")
    assert fields.other_urls == ["https://example.org/work"] and not hasattr(fields, "portfolio_urls")


def test_annotation_mailto_contributes_email():
    assert extract_fields("", ["mailto:person@example.com"]).emails == ["person@example.com"]
