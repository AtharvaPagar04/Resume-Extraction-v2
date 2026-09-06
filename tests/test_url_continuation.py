"""Unit and integration tests for Contact Phase 1A: Geometry & Provenance Strict Hardening.

Verifies:
- Zero contact geometry thresholds
- Zero proportional character / substring bbox estimation
- Exact source-span ownership (Case A through F)
- Exact canonical-text occurrence range identity for phantom prefix suppression
- Duplicate occurrence resolution without value-based or regex claiming
- Complete isolation from corpus-specific permanent test values
"""

import fitz
import pytest

from resume_extractor.extractor import extract_pdf
from resume_extractor.geometry import contains
from resume_extractor.layout import LayoutLine
from resume_extractor.layout_foundation import CharacterEvidence, LayoutHyperlink, Span
from resume_extractor.primitives import _URL, extract_fields
from resume_extractor.url_continuation import (
    DerivedUrlCandidate,
    build_derived_url_candidates,
    character_hyperlink_uri,
    entity_completion_prefix,
    fragment_hyperlink_uri,
    percent_completion_prefix,
    source_chars_for_line_fragment,
)


from resume_extractor.reconstruction import reconstruct_line_with_provenance


def _make_span(
    text: str,
    bbox=(10.0, 10.0, 100.0, 20.0),
    block=0,
    line=0,
    span=0,
    normalized_text: str | None = None,
    chars: tuple[CharacterEvidence, ...] | None = None,
) -> Span:
    norm = text if normalized_text is None else normalized_text
    if chars is None:
        if not text:
            chars_tuple: tuple[CharacterEvidence, ...] = ()
        else:
            w = (bbox[2] - bbox[0]) / max(1, len(text))
            chars_tuple = tuple(
                CharacterEvidence(
                    text=ch,
                    bbox=(bbox[0] + i * w, bbox[1], bbox[0] + (i + 1) * w, bbox[3]),
                )
                for i, ch in enumerate(text)
            )
    else:
        chars_tuple = chars
    return Span(
        raw_text=text,
        normalized_text=norm,
        bbox=bbox,
        font_name="Helvetica",
        font_size=10.0,
        flags=0,
        bold=False,
        italic=False,
        uppercase_ratio=0.0,
        block_index=block,
        line_index=line,
        span_index=span,
        source_order=(block, line, span),
        chars=chars_tuple,
    )


def _make_line(
    text: str,
    bbox=(10.0, 10.0, 100.0, 20.0),
    block=0,
    line=0,
    source_ids=(1,),
    spans: tuple[Span, ...] | list[Span] | None = None,
    char_map: tuple[CharacterEvidence | None, ...] | None = None,
) -> LayoutLine:
    if spans is None:
        spans = (_make_span(text, bbox=bbox, block=block, line=line, span=0),)
    spans_tuple = tuple(spans)
    if char_map is None:
        proj = reconstruct_line_with_provenance(spans_tuple)
        char_map = proj.char_map
    return LayoutLine(text, text, bbox, block, line, (block, line), tuple(source_ids), spans_tuple, char_map=char_map)


# ==============================================================================
# A. OLD GREEDY RECONSTRUCTION REGRESSIONS
# ==============================================================================

def test_independent_adjacent_urls_remain_separate():
    lines = [
        _make_line("https://example.com/first", bbox=(10, 10, 100, 20), block=0, line=0),
        _make_line("https://example.org/second", bbox=(10, 25, 100, 35), block=1, line=0),
    ]
    derived, _ = build_derived_url_candidates(lines, ())
    assert derived == []
    text = "\n".join(l.text for l in lines)
    fields = extract_fields(text, derived_url_candidates=derived)
    assert fields.other_urls == ["https://example.com/first", "https://example.org/second"]


def test_url_followed_by_ordinary_text_remains_unchanged():
    lines = [
        _make_line("https://example.com/profile", bbox=(10, 10, 100, 20), block=0, line=0),
        _make_line("Software Engineer with Python skills", bbox=(10, 25, 100, 35), block=0, line=1),
    ]
    derived, _ = build_derived_url_candidates(lines, ())
    assert derived == []
    fields = extract_fields("\n".join(l.text for l in lines), derived_url_candidates=derived)
    assert fields.other_urls == ["https://example.com/profile"]


def test_url_followed_by_bullet_remains_unchanged():
    lines = [
        _make_line("https://example.com/project", bbox=(10, 10, 100, 20), block=0, line=0),
        _make_line("● Built modern web architecture", bbox=(10, 25, 100, 35), block=1, line=0),
    ]
    derived, _ = build_derived_url_candidates(lines, ())
    assert derived == []
    fields = extract_fields("\n".join(l.text for l in lines), derived_url_candidates=derived)
    assert fields.other_urls == ["https://example.com/project"]


def test_url_trailing_slash_followed_by_heading_does_not_join():
    lines = [
        _make_line("https://example.com/team/", bbox=(10, 10, 100, 20), block=0, line=0),
        _make_line("EDUCATION", bbox=(10, 25, 100, 35), block=1, line=0),
    ]
    derived, _ = build_derived_url_candidates(lines, ())
    assert derived == []
    fields = extract_fields("\n".join(l.text for l in lines), derived_url_candidates=derived)
    assert fields.other_urls == ["https://example.com/team/"]


def test_url_trailing_hyphen_followed_by_text_does_not_join():
    lines = [
        _make_line("https://example.com/project-", bbox=(10, 10, 100, 20), block=0, line=0),
        _make_line("management system", bbox=(10, 25, 100, 35), block=0, line=1),
    ]
    derived, _ = build_derived_url_candidates(lines, ())
    assert derived == []
    fields = extract_fields("\n".join(l.text for l in lines), derived_url_candidates=derived)
    assert fields.other_urls == ["https://example.com/project-"]


# ==============================================================================
# B. PERCENT COMPLETION CONTRACT
# ==============================================================================

def test_percent_completion_single_hex():
    res = percent_completion_prefix("https://example.com/%2", "0")
    assert res == ("0", "")


def test_percent_completion_single_hex_leaves_suffix_unconsumed():
    res = percent_completion_prefix("https://example.com/%2", "0Python")
    assert res == ("0", "Python")

    lines = [
        _make_line("https://example.com/%2", bbox=(10, 10, 100, 20)),
        _make_line("0Python", bbox=(10, 25, 100, 35)),
    ]
    derived, consumed = build_derived_url_candidates(lines, ())
    assert len(derived) == 1
    assert derived[0].value == "https://example.com/%20"
    fields = extract_fields("\n".join(l.text for l in lines), derived_url_candidates=derived)
    assert fields.other_urls == ["https://example.com/%20"]


def test_percent_completion_two_hex_leaves_suffix_unconsumed():
    res = percent_completion_prefix("https://example.com/%", "20Python")
    assert res == ("20", "Python")

    lines = [
        _make_line("https://example.com/%", bbox=(10, 10, 100, 20)),
        _make_line("20Python", bbox=(10, 25, 100, 35)),
    ]
    derived, consumed = build_derived_url_candidates(lines, ())
    assert len(derived) == 1
    assert derived[0].value == "https://example.com/%20"
    fields = extract_fields("\n".join(l.text for l in lines), derived_url_candidates=derived)
    assert fields.other_urls == ["https://example.com/%20"]


def test_complete_percent_escape_does_not_join():
    assert percent_completion_prefix("https://example.com/%20", "Python") is None
    lines = [
        _make_line("https://example.com/%20", bbox=(10, 10, 100, 20)),
        _make_line("Python", bbox=(10, 25, 100, 35)),
    ]
    derived, _ = build_derived_url_candidates(lines, ())
    assert derived == []


def test_invalid_percent_escape_does_not_join():
    assert percent_completion_prefix("https://example.com/%", "GG") is None
    assert percent_completion_prefix("https://example.com/%G", "0") is None


# ==============================================================================
# C. ENTITY COMPLETION CONTRACT
# ==============================================================================

def test_entity_completion_exact():
    res = entity_completion_prefix("https://example.com/?x=&am", "p;")
    assert res == ("p;", "")


def test_entity_completion_leaves_suffix_unconsumed_without_annotation():
    res = entity_completion_prefix("https://example.com/?x=&am", "p;rest")
    assert res == ("p;", "rest")

    lines = [
        _make_line("https://example.com/?x=&am", bbox=(10, 10, 100, 20)),
        _make_line("p;rest", bbox=(10, 25, 100, 35)),
    ]
    derived, consumed = build_derived_url_candidates(lines, ())
    assert len(derived) == 1
    assert derived[0].value == "https://example.com/?x=&amp;"
    fields = extract_fields("\n".join(l.text for l in lines), derived_url_candidates=derived)
    assert fields.other_urls == ["https://example.com/?x=&"]


def test_entity_completion_quot():
    res = entity_completion_prefix("https://example.com/?title=&quo", "t;rest")
    assert res == ("t;", "rest")


def test_unrecognized_entity_does_not_join():
    assert entity_completion_prefix("https://example.com/?x=&abc", "123") is None
    assert entity_completion_prefix("https://example.com/?x=&am", "Python") is None


# ==============================================================================
# D. SHARED HYPERLINK CONTRACT
# ==============================================================================

def test_shared_annotation_reconstructs_ambiguous_alphanumeric_wrap():
    l1 = _make_line("https://example.com/profile/abc", bbox=(10.0, 10.0, 100.0, 20.0))
    l2 = _make_line("def123", bbox=(10.0, 25.0, 60.0, 35.0))
    h1 = LayoutHyperlink("https://example.com/profile/abcdef123", 1, (10.0, 10.0, 100.0, 20.0), 0)
    h2 = LayoutHyperlink("https://example.com/profile/abcdef123", 1, (10.0, 25.0, 60.0, 35.0), 1)

    derived, consumed = build_derived_url_candidates([l1, l2], (h1, h2), page_number=1)
    assert len(derived) == 1
    assert derived[0].value == "https://example.com/profile/abcdef123"
    assert "https://example.com/profile/abc" in consumed

    fields = extract_fields(f"{l1.text}\n{l2.text}", derived_url_candidates=derived)
    assert fields.other_urls == ["https://example.com/profile/abcdef123"]


def test_hyperlink_covering_only_line1_does_not_consume_line2():
    l1 = _make_line("https://example.com/profile/abc", bbox=(10.0, 10.0, 100.0, 20.0))
    l2 = _make_line("def123", bbox=(10.0, 25.0, 60.0, 35.0))
    h1 = LayoutHyperlink("https://example.com/profile/abc", 1, (10.0, 10.0, 100.0, 20.0), 0)

    derived, _ = build_derived_url_candidates([l1, l2], (h1,), page_number=1)
    assert derived == []
    fields = extract_fields(f"{l1.text}\n{l2.text}", derived_url_candidates=derived)
    assert fields.other_urls == ["https://example.com/profile/abc"]


def test_different_hyperlinks_do_not_join():
    l1 = _make_line("https://example.com/a", bbox=(10.0, 10.0, 100.0, 20.0))
    l2 = _make_line("https://example.org/b", bbox=(10.0, 25.0, 100.0, 35.0))
    h1 = LayoutHyperlink("https://example.com/a", 1, (10.0, 10.0, 100.0, 20.0), 0)
    h2 = LayoutHyperlink("https://example.org/b", 1, (10.0, 25.0, 100.0, 35.0), 1)

    derived, _ = build_derived_url_candidates([l1, l2], (h1, h2), page_number=1)
    assert derived == []


def test_trailing_slash_without_shared_annotation_does_not_join():
    l1 = _make_line("https://example.com/path/", bbox=(10.0, 10.0, 100.0, 20.0))
    l2 = _make_line("subpath", bbox=(10.0, 25.0, 60.0, 35.0))
    h1 = LayoutHyperlink("https://example.com/path/", 1, (10.0, 10.0, 100.0, 20.0), 0)

    derived, _ = build_derived_url_candidates([l1, l2], (h1,), page_number=1)
    assert derived == []


# ==============================================================================
# E. SUFFIX AUTHORIZATION & INDEPENDENT CONTACT PRESERVATION
# ==============================================================================

def test_percent_completion_preserves_subsequent_url():
    lines = [
        _make_line("https://example.com/%2", bbox=(10, 10, 100, 20)),
        _make_line("0https://example.org/valid", bbox=(10, 25, 100, 35)),
    ]
    derived, consumed = build_derived_url_candidates(lines, ())
    assert len(derived) == 1
    assert derived[0].value == "https://example.com/%20"
    fields = extract_fields("\n".join(l.text for l in lines), derived_url_candidates=derived)
    assert "https://example.com/%20" in fields.other_urls
    assert "https://example.org/valid" in fields.other_urls


def test_entity_completion_preserves_subsequent_email():
    lines = [
        _make_line("https://example.com/?x=&am", bbox=(10, 10, 100, 20)),
        _make_line("p;user@example.com", bbox=(10, 25, 100, 35)),
    ]
    derived, consumed = build_derived_url_candidates(lines, ())
    assert len(derived) == 1
    assert derived[0].value == "https://example.com/?x=&amp;"
    fields = extract_fields("\n".join(l.text for l in lines), derived_url_candidates=derived)
    assert "https://example.com/?x=&" in fields.other_urls
    assert fields.emails == ["user@example.com"]


# ==============================================================================
# F. PAGE BOUNDARY SAFETY
# ==============================================================================

def test_cross_page_lexical_continuation_is_prohibited(tmp_path):
    doc = fitz.open()
    p1 = doc.new_page()
    p1.insert_text((50, 50), "https://example.com/%2")
    p2 = doc.new_page()
    p2.insert_text((50, 50), "0file")
    pdf_path = tmp_path / "cross_page.pdf"
    doc.save(pdf_path)
    doc.close()

    raw = extract_pdf(pdf_path)
    assert "https://example.com/%20" not in raw.extracted_fields.other_urls
    assert raw.text.split("\f")[0].strip() == "https://example.com/%2"
    assert raw.text.split("\f")[1].strip() == "0file"


# ==============================================================================
# G. RAW TEXT IMMUTABILITY & SYNTHETIC PDF ISOLATION (PART 25)
# ==============================================================================

def test_raw_text_and_source_ids_are_strictly_immutable(tmp_path):
    doc = fitz.open()
    p1 = doc.new_page()
    p1.insert_text((50, 50), "https://www.linkedin.com/in/synthetic-user-")
    p1.insert_text((50, 70), "profile12345")
    # Exact enclosing link rectangles
    rect1 = fitz.Rect(45, 35, 300, 55)
    rect2 = fitz.Rect(45, 55, 300, 75)
    p1.insert_link({"kind": fitz.LINK_URI, "from": rect1, "uri": "https://www.linkedin.com/in/synthetic-user-profile12345"})
    p1.insert_link({"kind": fitz.LINK_URI, "from": rect2, "uri": "https://www.linkedin.com/in/synthetic-user-profile12345"})
    pdf_path = tmp_path / "immutable_test.pdf"
    doc.save(pdf_path)
    doc.close()

    raw = extract_pdf(pdf_path)
    expected_text = "https://www.linkedin.com/in/synthetic-user-\nprofile12345"
    assert raw.text == expected_text
    assert raw.extracted_fields.linkedin_urls == ["https://www.linkedin.com/in/synthetic-user-profile12345"]


# ==============================================================================
# H. NEGATIVE STRUCTURAL GUARDS
# ==============================================================================

def test_same_block_and_consecutive_lines_do_not_authorize_continuation():
    l1 = _make_line("https://example.com/profile", block=3, line=0)
    l2 = _make_line("Python Engineer", block=3, line=1)
    derived, _ = build_derived_url_candidates([l1, l2], ())
    assert derived == []


def test_trailing_question_mark_does_not_join():
    l1 = _make_line("https://example.com/search?", block=0, line=0)
    l2 = _make_line("Experience", block=0, line=1)
    derived, _ = build_derived_url_candidates([l1, l2], ())
    assert derived == []


def test_trailing_equals_does_not_join():
    l1 = _make_line("https://example.com/search?q=", block=0, line=0)
    l2 = _make_line("Location: Remote", block=0, line=1)
    derived, _ = build_derived_url_candidates([l1, l2], ())
    assert derived == []


# ==============================================================================
# PART 20: DUPLICATE OCCURRENCE TESTS (A through F)
# ==============================================================================

def test_part20_test_a_duplicate_pair_second_authorized():
    """Test A: Duplicate pair, second authorized. First prefix NOT suppressed, second IS suppressed."""
    l1 = _make_line("https://example.com/profile/abc", bbox=(10, 10, 100, 20), line=0)
    l2 = _make_line("def123", bbox=(10, 25, 60, 35), line=1)
    l3 = _make_line("Experience section separator", bbox=(10, 40, 150, 50), line=2)
    l4 = _make_line("https://example.com/profile/abc", bbox=(10, 55, 100, 65), line=3)
    l5 = _make_line("def123", bbox=(10, 70, 60, 80), line=4)

    # Only pair 2 (l4, l5) has shared hyperlink evidence
    h4 = LayoutHyperlink("https://example.com/profile/abcdef123", 1, (10, 55, 100, 65), 0)
    h5 = LayoutHyperlink("https://example.com/profile/abcdef123", 1, (10, 70, 60, 80), 1)

    lines = [l1, l2, l3, l4, l5]
    text = "\n".join(l.text for l in lines)
    matches = list(_URL.finditer(text))
    assert len(matches) == 2
    occ1 = (matches[0].start(), matches[0].end())
    occ2 = (matches[1].start(), matches[1].end())

    derived, _ = build_derived_url_candidates(lines, (h4, h5), page_number=1, page_document_start=0)
    assert len(derived) == 1
    suppressed_range = (derived[0].prefix_document_start, derived[0].prefix_document_end)

    # Assert exact occurrence suppression
    assert occ1 != suppressed_range, "Occurrence 1 must NOT be suppressed"
    assert occ2 == suppressed_range, "Occurrence 2 MUST be suppressed"

    fields = extract_fields(text, derived_url_candidates=derived)
    assert "https://example.com/profile/abc" in fields.other_urls
    assert "https://example.com/profile/abcdef123" in fields.other_urls


def test_part20_test_b_duplicate_pair_first_authorized():
    """Test B: Duplicate pair, first authorized. First prefix IS suppressed, second is NOT."""
    l1 = _make_line("https://example.com/profile/abc", bbox=(10, 10, 100, 20), line=0)
    l2 = _make_line("def123", bbox=(10, 25, 60, 35), line=1)
    l3 = _make_line("Experience section separator", bbox=(10, 40, 150, 50), line=2)
    l4 = _make_line("https://example.com/profile/abc", bbox=(10, 55, 100, 65), line=3)
    l5 = _make_line("def123", bbox=(10, 70, 60, 80), line=4)

    # Only pair 1 (l1, l2) has shared hyperlink evidence
    h1 = LayoutHyperlink("https://example.com/profile/abcdef123", 1, (10, 10, 100, 20), 0)
    h2 = LayoutHyperlink("https://example.com/profile/abcdef123", 1, (10, 25, 60, 35), 1)

    lines = [l1, l2, l3, l4, l5]
    text = "\n".join(l.text for l in lines)
    matches = list(_URL.finditer(text))
    assert len(matches) == 2
    occ1 = (matches[0].start(), matches[0].end())
    occ2 = (matches[1].start(), matches[1].end())

    derived, _ = build_derived_url_candidates(lines, (h1, h2), page_number=1, page_document_start=0)
    assert len(derived) == 1
    suppressed_range = (derived[0].prefix_document_start, derived[0].prefix_document_end)

    assert occ1 == suppressed_range, "Occurrence 1 MUST be suppressed"
    assert occ2 != suppressed_range, "Occurrence 2 must NOT be suppressed"

    fields = extract_fields(text, derived_url_candidates=derived)
    assert "https://example.com/profile/abc" in fields.other_urls
    assert "https://example.com/profile/abcdef123" in fields.other_urls


def test_part20_test_c_three_identical_pairs_only_middle_authorized():
    """Test C: Three identical pairs (A independent, B continuation-backed, C independent)."""
    l1 = _make_line("https://example.com/profile/abc", bbox=(10, 10, 100, 20), line=0)
    l2 = _make_line("def123", bbox=(10, 25, 60, 35), line=1)
    l3 = _make_line("Sep 1", bbox=(10, 40, 60, 50), line=2)
    l4 = _make_line("https://example.com/profile/abc", bbox=(10, 55, 100, 65), line=3)
    l5 = _make_line("def123", bbox=(10, 70, 60, 80), line=4)
    l6 = _make_line("Sep 2", bbox=(10, 85, 60, 95), line=5)
    l7 = _make_line("https://example.com/profile/abc", bbox=(10, 100, 100, 110), line=6)
    l8 = _make_line("def123", bbox=(10, 115, 60, 125), line=7)

    # Only pair B (l4, l5) is continuation-backed
    h4 = LayoutHyperlink("https://example.com/profile/abcdef123", 1, (10, 55, 100, 65), 0)
    h5 = LayoutHyperlink("https://example.com/profile/abcdef123", 1, (10, 70, 60, 80), 1)

    lines = [l1, l2, l3, l4, l5, l6, l7, l8]
    text = "\n".join(l.text for l in lines)
    matches = list(_URL.finditer(text))
    assert len(matches) == 3
    occ_a = (matches[0].start(), matches[0].end())
    occ_b = (matches[1].start(), matches[1].end())
    occ_c = (matches[2].start(), matches[2].end())

    derived, _ = build_derived_url_candidates(lines, (h4, h5), page_number=1, page_document_start=0)
    assert len(derived) == 1
    suppressed_range = (derived[0].prefix_document_start, derived[0].prefix_document_end)

    assert occ_a != suppressed_range
    assert occ_b == suppressed_range
    assert occ_c != suppressed_range


def test_part20_test_d_identical_pairs_on_different_pages(tmp_path):
    """Test D: Identical pair on Page 1 and Page 2. Only Page 2 is continuation-backed."""
    doc = fitz.open()
    p1 = doc.new_page()
    p1.insert_text((50, 50), "https://example.com/profile/abc")
    p1.insert_text((50, 70), "def123")

    p2 = doc.new_page()
    p2.insert_text((50, 50), "https://example.com/profile/abc")
    p2.insert_text((50, 70), "def123")
    r1 = fitz.Rect(45, 35, 250, 55)
    r2 = fitz.Rect(45, 55, 250, 75)
    p2.insert_link({"kind": fitz.LINK_URI, "from": r1, "uri": "https://example.com/profile/abcdef123"})
    p2.insert_link({"kind": fitz.LINK_URI, "from": r2, "uri": "https://example.com/profile/abcdef123"})

    pdf_path = tmp_path / "diff_pages.pdf"
    doc.save(pdf_path)
    doc.close()

    raw = extract_pdf(pdf_path)
    matches = list(_URL.finditer(raw.text))
    assert len(matches) == 2
    # The derived URL is reconstructed
    assert "https://example.com/profile/abcdef123" in raw.extracted_fields.other_urls
    # The unbacked Page 1 occurrence remains extracted
    assert "https://example.com/profile/abc" in raw.extracted_fields.other_urls


def test_part20_test_e_identical_prefix_different_suffix():
    """Test E: A has prefix \n alpha, B has prefix \n beta. Only B is continuation-backed."""
    l1 = _make_line("https://example.com/prefix", bbox=(10, 10, 100, 20), line=0)
    l2 = _make_line("alpha", bbox=(10, 25, 50, 35), line=1)
    l3 = _make_line("Separator line", bbox=(10, 40, 100, 50), line=2)
    l4 = _make_line("https://example.com/prefix", bbox=(10, 55, 100, 65), line=3)
    l5 = _make_line("beta", bbox=(10, 70, 50, 80), line=4)

    h4 = LayoutHyperlink("https://example.com/prefixbeta", 1, (10, 55, 100, 65), 0)
    h5 = LayoutHyperlink("https://example.com/prefixbeta", 1, (10, 70, 50, 80), 1)

    lines = [l1, l2, l3, l4, l5]
    text = "\n".join(l.text for l in lines)
    matches = list(_URL.finditer(text))
    assert len(matches) == 2
    occ_a = (matches[0].start(), matches[0].end())
    occ_b = (matches[1].start(), matches[1].end())

    derived, _ = build_derived_url_candidates(lines, (h4, h5), page_number=1, page_document_start=0)
    assert len(derived) == 1
    suppressed_range = (derived[0].prefix_document_start, derived[0].prefix_document_end)

    assert occ_a != suppressed_range
    assert occ_b == suppressed_range

    fields = extract_fields(text, derived_url_candidates=derived)
    assert "https://example.com/prefix" in fields.other_urls
    assert "https://example.com/prefixbeta" in fields.other_urls


def test_part20_test_f_multiple_urls_in_same_line():
    """Test F: Line has two URLs; only second participates in continuation."""
    url1 = "https://example.com/first"
    url2 = "https://example.org/second"
    line1_text = f"{url1} {url2}"
    s1 = _make_span(url1, bbox=(10, 10, 80, 20), line=0, span=0)
    s2 = _make_span(url2, bbox=(85, 10, 160, 20), line=0, span=1)
    l1 = _make_line(line1_text, bbox=(10, 10, 160, 20), line=0, spans=[s1, s2])

    l2 = _make_line("suffix123", bbox=(85, 25, 140, 35), line=1)
    h_link2 = LayoutHyperlink("https://example.org/secondsuffix123", 1, (85, 10, 160, 20), 0)
    h_l2 = LayoutHyperlink("https://example.org/secondsuffix123", 1, (85, 25, 140, 35), 1)

    lines = [l1, l2]
    text = f"{line1_text}\n{l2.text}"
    matches = list(_URL.finditer(text))
    assert len(matches) == 2
    occ_url1 = (matches[0].start(), matches[0].end())
    occ_url2 = (matches[1].start(), matches[1].end())

    derived, _ = build_derived_url_candidates(lines, (h_link2, h_l2), page_number=1, page_document_start=0)
    assert len(derived) == 1
    assert derived[0].value == "https://example.org/secondsuffix123"

    suppressed_range = (derived[0].prefix_document_start, derived[0].prefix_document_end)
    assert occ_url1 != suppressed_range, "First URL in same line must NOT be suppressed"
    assert occ_url2 == suppressed_range, "Second URL participating in continuation MUST be suppressed"

    fields = extract_fields(text, derived_url_candidates=derived)
    assert "https://example.com/first" in fields.other_urls
    assert "https://example.org/secondsuffix123" in fields.other_urls


# ==============================================================================
# PART 21 / PARTS AD through AR: CHARACTER-BACKED HYPERLINK OWNERSHIP TESTS
# ==============================================================================

def test_part_ad_single_character_ownership():
    """Part AD: Character center inside hyperlink rectangle is owned by normalized URI."""
    c = CharacterEvidence(text="a", bbox=(10.0, 10.0, 20.0, 20.0))  # center: (15.0, 15.0)
    h = LayoutHyperlink("https://example.com/target", 1, (10.0, 10.0, 20.0, 20.0), 0)
    uri = character_hyperlink_uri(c, [h])
    assert uri == "https://example.com/target"


def test_part_ae_character_bbox_extends_outside_link_center_inside():
    """Part AE: Character bbox extends outside link, but CENTER lies inside -> OWNED.

    Proves the model does not accidentally revert to bbox containment.
    """
    # Character bbox extends 2.0 pt outside link at top and bottom (ascender/descender)
    c = CharacterEvidence(text="g", bbox=(10.0, 8.0, 20.0, 22.0))  # center: (15.0, 15.0)
    h = LayoutHyperlink("https://example.com/target", 1, (10.0, 10.0, 20.0, 20.0), 0)
    uri = character_hyperlink_uri(c, [h])
    assert uri == "https://example.com/target"


def test_part_af_center_outside_link():
    """Part AF: Character bbox intersects link, but CENTER lies outside -> NOT owned.

    Proves rectangle intersection is not used.
    """
    # Character bbox is (10.0, 10.0, 30.0, 20.0), center is (20.0, 15.0)
    # Hyperlink is (10.0, 10.0, 18.0, 20.0) -> intersects [10, 18], but center 20.0 is outside
    c = CharacterEvidence(text="x", bbox=(10.0, 10.0, 30.0, 20.0))
    h = LayoutHyperlink("https://example.com/target", 1, (10.0, 10.0, 18.0, 20.0), 0)
    uri = character_hyperlink_uri(c, [h])
    assert uri is None


def test_part_ag_multiple_chars_same_uri():
    """Part AG: Fragment contains several characters, all owned by link(s) with same URI."""
    chars = [
        CharacterEvidence(text=ch, bbox=(10.0 + i * 10.0, 10.0, 20.0 + i * 10.0, 20.0))
        for i, ch in enumerate("abc")
    ]
    h = LayoutHyperlink("https://example.com/target", 1, (10.0, 10.0, 40.0, 20.0), 0)
    uri = fragment_hyperlink_uri(chars, [h])
    assert uri == "https://example.com/target"


def test_part_ah_one_character_unowned_fails_closed():
    """Part AH: All but one character owned -> fragment ownership fails closed (None)."""
    chars = [
        CharacterEvidence(text="a", bbox=(10.0, 10.0, 20.0, 20.0)),
        CharacterEvidence(text="b", bbox=(20.0, 10.0, 30.0, 20.0)),
        CharacterEvidence(text="c", bbox=(30.0, 10.0, 40.0, 20.0)),  # center 35.0
    ]
    # Hyperlink only covers up to x1=28.0 (chars 'a' and 'b')
    h = LayoutHyperlink("https://example.com/target", 1, (10.0, 10.0, 28.0, 20.0), 0)
    uri = fragment_hyperlink_uri(chars, [h])
    assert uri is None


def test_part_ai_conflicting_uris_fails_closed():
    """Part AI: Fragment characters owned by different URIs -> fails closed (None)."""
    chars = [
        CharacterEvidence(text="a", bbox=(10.0, 10.0, 20.0, 20.0)),
        CharacterEvidence(text="b", bbox=(20.0, 10.0, 30.0, 20.0)),
    ]
    h1 = LayoutHyperlink("https://example.com/first", 1, (10.0, 10.0, 20.0, 20.0), 0)
    h2 = LayoutHyperlink("https://example.org/second", 1, (20.0, 10.0, 30.0, 20.0), 1)
    uri = fragment_hyperlink_uri(chars, [h1, h2])
    assert uri is None


def test_part_aj_overlapping_annotations_same_uri():
    """Part AJ: Character center in two annotations with the SAME normalized URI -> succeeds."""
    c = CharacterEvidence(text="a", bbox=(10.0, 10.0, 20.0, 20.0))  # center: (15.0, 15.0)
    h1 = LayoutHyperlink("https://example.com/target", 1, (5.0, 5.0, 25.0, 25.0), 0)
    h2 = LayoutHyperlink("https://example.com/target", 1, (10.0, 10.0, 20.0, 20.0), 1)
    uri = character_hyperlink_uri(c, [h1, h2])
    assert uri == "https://example.com/target"


def test_part_ak_overlapping_annotations_different_uri_ambiguous():
    """Part AK: Character center in two annotations with DIFFERENT normalized URIs -> ambiguous, fails closed."""
    c = CharacterEvidence(text="a", bbox=(10.0, 10.0, 20.0, 20.0))  # center: (15.0, 15.0)
    h1 = LayoutHyperlink("https://example.com/first", 1, (5.0, 5.0, 25.0, 25.0), 0)
    h2 = LayoutHyperlink("https://example.org/second", 1, (10.0, 10.0, 20.0, 20.0), 1)
    uri = character_hyperlink_uri(c, [h1, h2])
    assert uri is None


def test_part_al_partial_span_ownership():
    """Part AL: Span contains prefix text + URL fragment + suffix text.

    Only URL characters are linked. source_chars_for_line_fragment returns only URL characters;
    ownership succeeds and excludes unrelated characters.
    """
    prefix = "Visit: "
    url = "https://example.com/path"
    suffix = " now"
    full_text = prefix + url + suffix

    w = 5.0
    chars = [
        CharacterEvidence(text=ch, bbox=(i * w, 10.0, (i + 1) * w, 20.0))
        for i, ch in enumerate(full_text)
    ]
    span = _make_span(full_text, bbox=(0.0, 10.0, len(full_text) * w, 20.0), chars=tuple(chars))
    line = _make_line(full_text, bbox=(0.0, 10.0, len(full_text) * w, 20.0), spans=[span])

    url_start = len(prefix)
    url_end = url_start + len(url)

    # Hyperlink only covers the URL characters
    h = LayoutHyperlink(
        "https://example.com/path",
        1,
        (url_start * w, 10.0, url_end * w, 20.0),
        0,
    )

    frag_chars = source_chars_for_line_fragment(line, url_start, url_end)
    assert frag_chars is not None
    assert len(frag_chars) == len(url)
    assert "".join(c.text for c in frag_chars) == url

    uri = fragment_hyperlink_uri(frag_chars, [h])
    assert uri == "https://example.com/path"


def test_part_am_multi_span_fragment():
    """Part AM: URL fragment crosses two contiguous source spans; both share same URI."""
    p1 = "https://example.com/dir/"
    p2 = "subpath"
    s1 = _make_span(p1, bbox=(10.0, 10.0, 80.0, 20.0), span=0)
    s2 = _make_span(p2, bbox=(80.0, 10.0, 120.0, 20.0), span=1)
    full_url = p1 + p2
    line = _make_line(full_url, bbox=(10.0, 10.0, 120.0, 20.0), spans=[s1, s2])

    frag_chars = source_chars_for_line_fragment(line, 0, len(full_url))
    assert frag_chars is not None
    assert len(frag_chars) == len(full_url)

    h1 = LayoutHyperlink("https://example.com/dir/subpath", 1, (10.0, 10.0, 80.0, 20.0), 0)
    h2 = LayoutHyperlink("https://example.com/dir/subpath", 1, (80.0, 10.0, 120.0, 20.0), 1)
    uri = fragment_hyperlink_uri(frag_chars, [h1, h2])
    assert uri == "https://example.com/dir/subpath"


def test_part_an_multi_rectangle_same_uri():
    """Part AN: Line 1 chars in rectangle A, Line 2 chars in rectangle B, both same URI."""
    l1 = _make_line("https://example.com/prefix-", bbox=(10.0, 10.0, 100.0, 20.0))
    l2 = _make_line("suffix123", bbox=(10.0, 25.0, 60.0, 35.0))
    h1 = LayoutHyperlink("https://example.com/prefix-suffix123", 1, (10.0, 10.0, 100.0, 20.0), 0)
    h2 = LayoutHyperlink("https://example.com/prefix-suffix123", 1, (10.0, 25.0, 60.0, 35.0), 1)

    derived, consumed = build_derived_url_candidates([l1, l2], [h1, h2], page_number=1)
    assert len(derived) == 1
    assert derived[0].value == "https://example.com/prefix-suffix123"
    assert derived[0].evidence_kind == "shared_annotation_continuation"


def test_part_ao_malformed_target_mismatch():
    """Part AO: Annotation URI is same for both fragments, but target does not equal visible text.

    Shared ownership is established, BUT derived value remains reconstructed visible text,
    not annotation target replacement.
    """
    l1 = _make_line("https://example.com/visible/part1-", bbox=(10.0, 10.0, 100.0, 20.0))
    l2 = _make_line("part2", bbox=(10.0, 25.0, 60.0, 35.0))
    # Target is truncated or different
    h1 = LayoutHyperlink("https://example.com/target", 1, (10.0, 10.0, 100.0, 20.0), 0)
    h2 = LayoutHyperlink("https://example.com/target", 1, (10.0, 25.0, 60.0, 35.0), 1)

    derived, consumed = build_derived_url_candidates([l1, l2], [h1, h2], page_number=1)
    assert len(derived) == 1
    assert derived[0].value == "https://example.com/visible/part1-part2"
    assert derived[0].value != h1.uri


def test_part_ap_character_range_mapping():
    """Part AP: Exact mapping when fragment begins inside span, ends inside span, or crosses boundary."""
    s1 = _make_span("prefix_https://example.com/", bbox=(0.0, 10.0, 50.0, 20.0), span=0)
    s2 = _make_span("path_suffix", bbox=(50.0, 10.0, 100.0, 20.0), span=1)
    full_text = "prefix_https://example.com/path_suffix"
    line = _make_line(full_text, bbox=(0.0, 10.0, 100.0, 20.0), spans=[s1, s2])

    url_start = full_text.find("https://")
    url_end = full_text.find("_suffix")

    frag_chars = source_chars_for_line_fragment(line, url_start, url_end)
    assert frag_chars is not None
    assert "".join(c.text for c in frag_chars) == "https://example.com/path"


def test_part_aq_duplicate_identical_text():
    """Part AQ: Two identical URL fragments in different lines; only one has shared hyperlink evidence.

    Only that occurrence creates a derived continuation; exact document-range suppression targets only it.
    """
    l1 = _make_line("See https://example.com/wrap-", bbox=(10.0, 10.0, 100.0, 20.0), line=0)
    l2 = _make_line("ped1", bbox=(10.0, 25.0, 60.0, 35.0), line=1)
    l3 = _make_line("Other https://example.com/wrap-", bbox=(10.0, 40.0, 100.0, 50.0), line=2)
    l4 = _make_line("ped2", bbox=(10.0, 55.0, 60.0, 65.0), line=3)

    # Only lines 1 and 2 have shared hyperlink
    h1 = LayoutHyperlink("https://example.com/wrapped1", 1, (10.0, 10.0, 100.0, 20.0), 0)
    h2 = LayoutHyperlink("https://example.com/wrapped1", 1, (10.0, 25.0, 60.0, 35.0), 1)

    derived, _ = build_derived_url_candidates([l1, l2, l3, l4], [h1, h2], page_number=1)
    assert len(derived) == 1
    assert derived[0].value == "https://example.com/wrap-ped1"
    assert derived[0].prefix_line_index == 0


def test_part_ar_no_source_characters_fails_closed():
    """Part AR: Synthetic or sourceless LayoutLine has empty chars -> fails closed (None)."""
    line = LayoutLine("https://example.com", "https://example.com", (0, 0, 10, 10), 0, 0, (0, 0), (1,), ())
    chars = source_chars_for_line_fragment(line, 0, len(line.text))
    assert chars is None


def test_part26_contact_exact_range():
    """Part 26: Given canonical line 'Prefix https://example.test/path suffix' with provenance map,
    exact URL slice returns only the source CharacterEvidence corresponding to the URL."""
    line_text = "Prefix https://example.test/path suffix"
    line = _make_line(line_text, bbox=(10.0, 10.0, 200.0, 20.0))
    url = "https://example.test/path"
    url_start = line_text.find(url)
    url_end = url_start + len(url)

    chars = source_chars_for_line_fragment(line, url_start, url_end)
    assert chars is not None
    assert len(chars) == len(url)
    assert "".join(c.text for c in chars) == url
    assert chars == line.char_map[url_start:url_end]


def test_part27_synthetic_space_in_range():
    """Part 27: If a requested source fragment range includes a char_map None,
    source_chars_for_line_fragment() -> None / fails closed."""
    s1 = _make_span("prefix", bbox=(10.0, 10.0, 40.0, 20.0))
    s2 = _make_span("suffix", bbox=(60.0, 10.0, 90.0, 20.0))
    proj = reconstruct_line_with_provenance([s1, s2])
    assert " " in proj.text
    space_idx = proj.text.find(" ")
    assert proj.char_map[space_idx] is None

    line = LayoutLine(proj.text, proj.text, (10.0, 10.0, 90.0, 20.0), 0, 0, (0, 0), (1,), (s1, s2), char_map=proj.char_map)
    chars = source_chars_for_line_fragment(line, space_idx - 1, space_idx + 2)
    assert chars is None


def test_part28_partial_span_url():
    """Part 28: One Span contains 'label + URL + suffix'.
    Canonical provenance map must allow exact URL range to select only URL chars.
    Character-backed hyperlink ownership must still succeed."""
    span_text = "See https://example.org/my-profile here"
    url = "https://example.org/my-profile"
    url_start = span_text.find(url)
    url_end = url_start + len(url)

    span = _make_span(span_text, bbox=(10.0, 10.0, 210.0, 20.0))
    line = _make_line(span_text, spans=[span])

    url_chars = source_chars_for_line_fragment(line, url_start, url_end)
    assert url_chars is not None
    assert len(url_chars) == len(url)
    assert "".join(c.text for c in url_chars) == url

    min_x = min(c.bbox[0] for c in url_chars)
    max_x = max(c.bbox[2] for c in url_chars)
    min_y = min(c.bbox[1] for c in url_chars)
    max_y = max(c.bbox[3] for c in url_chars)

    h = LayoutHyperlink("https://example.org/my-profile", 1, (min_x, min_y, max_x, max_y), 0)
    owner = fragment_hyperlink_uri(url_chars, [h])
    assert owner == "https://example.org/my-profile"

    full_chars = source_chars_for_line_fragment(line, 0, len(span_text))
    assert fragment_hyperlink_uri(full_chars, [h]) is None

