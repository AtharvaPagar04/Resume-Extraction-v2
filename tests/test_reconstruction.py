from resume_extractor.reconstruction import normalize_text, reconstruct_line_from_spans


def span(text, x0, x1, size=10):
    return {"text": text, "bbox": (x0, 0, x1, 10), "size": size}


def test_reconstructs_source_whitespace():
    assert reconstruct_line_from_spans([span("Hello ", 0, 20), span("world", 20, 45)]) == "Hello world"


def test_reconstructs_intervening_whitespace_span():
    assert reconstruct_line_from_spans([span("Hello", 0, 20), span(" ", 20, 21), span("world", 21, 45)]) == "Hello world"


def test_reconstructs_supported_geometric_gap():
    assert reconstruct_line_from_spans([span("Hello", 0, 20), span("world", 24, 45)]) == "Hello world"


def test_does_not_concatenate_arbitrary_words_without_evidence():
    assert reconstruct_line_from_spans([span("Hello", 0, 20), span("world", 20.5, 45)]) == "Helloworld"


def test_collapses_renderer_whitespace_and_preserves_punctuation():
    assert reconstruct_line_from_spans([span("Hello,   ", 0, 20), span("world!", 20, 45)]) == "Hello, world!"


def test_removes_cid_artifact_without_word_segmentation():
    assert reconstruct_line_from_spans([span("Py(cid:12)thon", 0, 40)]) == "Python"


def test_unicode_is_preserved():
    assert normalize_text("  Résumé\u00a0—  東京  ") == "Résumé — 東京"


def test_normalizes_legacy_symbol_font_bullet():
    from resume_extractor.layout_foundation import is_bullet_only, starts_with_bullet

    # Standalone
    standalone = normalize_text("\uf0b7")
    assert standalone == "•"
    assert reconstruct_line_from_spans([span("\uf0b7", 0, 10)]) == "•"
    assert is_bullet_only(standalone)
    assert starts_with_bullet(standalone)

    # Inline
    inline = normalize_text("\uf0b7 Delivered features")
    assert inline == "• Delivered features"
    assert reconstruct_line_from_spans([span("\uf0b7", 0, 10), span("Delivered features", 15, 100)]) == "• Delivered features"
    assert starts_with_bullet(inline)
    assert not is_bullet_only(inline)


def test_existing_bullet_markers_and_punctuation_unchanged():
    for marker in ["•", "●", "▪", "▫", "◦", "‣", "∙", "-", "–", "—", "*", "∗", "○"]:
        assert normalize_text(marker) == marker
    for punct in ["|", "/", "@", "&", ":", "(", ")", "1.", "a)"]:
        assert normalize_text(punct) == punct


def test_u200b_standalone_marker_normalizes_correctly():
    from resume_extractor.layout_foundation import is_bullet_only, starts_with_bullet

    norm = normalize_text("●\u200b")
    assert norm == "●"
    assert reconstruct_line_from_spans([span("●\u200b", 0, 10)]) == "●"
    assert is_bullet_only(norm)
    assert starts_with_bullet(norm)


def test_u200b_inline_marker_normalizes_correctly():
    from resume_extractor.layout_foundation import is_bullet_only, starts_with_bullet

    norm = normalize_text("●\u200b Designed API platform")
    assert norm == "● Designed API platform"
    assert reconstruct_line_from_spans([span("●\u200b", 0, 10), span("Designed API platform", 15, 120)]) == "● Designed API platform"
    assert starts_with_bullet(norm)
    assert not is_bullet_only(norm)


def test_u200b_trailing_colon_restores_colon_termination():
    norm = normalize_text("Description:\u200b")
    assert norm == "Description:"
    assert norm.endswith(":")


def test_u200b_standalone_becomes_empty():
    assert normalize_text("\u200b") == ""
    assert reconstruct_line_from_spans([span("\u200b", 0, 10)]) == ""


def test_u200b_ordinary_unicode_text_unchanged():
    samples = [
        "Engineering & Machine Learning — PyTorch, TensorFlow",
        "Résumé of François Müller (Senior AI Engineer)",
        "Contract value: $100k+ [99.9% uptime]",
    ]
    for sample in samples:
        assert normalize_text(sample) == sample


def test_unrelated_cf_characters_not_generically_removed():
    # Only U+200B is removed; other Cf characters (e.g. LTR/RTL marks, ZWNJ, ZWJ) are untouched
    assert "\u200e" in normalize_text("Text\u200eWithLTR")
    assert "\u200f" in normalize_text("Text\u200fWithRTL")
    assert "\u200c" in normalize_text("Text\u200cWithZWNJ")
    assert "\u200d" in normalize_text("Text\u200dWithZWJ")


# ==============================================================================
# PARTS 20-25: CANONICAL LINE PROJECTION AND PROVENANCE TESTS
# ==============================================================================

from resume_extractor.reconstruction import reconstruct_line_with_provenance, ReconstructedLineProjection
from resume_extractor.layout_foundation import CharacterEvidence


def test_part20_projection_text_and_char_map_length():
    """Part 20: Reconstructed text and char_map length invariant."""
    chars = tuple(CharacterEvidence(text=c, bbox=(i * 10.0, 0, (i + 1) * 10.0, 10)) for i, c in enumerate("Test"))
    s = {"text": "Test", "bbox": (0, 0, 40, 10), "size": 10, "chars": chars}
    proj = reconstruct_line_with_provenance([s])
    assert proj.text == "Test"
    assert len(proj.char_map) == len(proj.text)


def test_part21_direct_char_mapping():
    """Part 21: Direct character evidence mapping."""
    chars = tuple(CharacterEvidence(text=c, bbox=(i * 10.0, 0, (i + 1) * 10.0, 10)) for i, c in enumerate("ABC"))
    s = {"text": "ABC", "bbox": (0, 0, 30, 10), "size": 10, "chars": chars}
    proj = reconstruct_line_with_provenance([s])
    assert proj.text == "ABC"
    assert proj.char_map[0] is chars[0]
    assert proj.char_map[1] is chars[1]
    assert proj.char_map[2] is chars[2]


def test_part22_normalized_character_provenance():
    """Part 22: Normalized character (e.g. U+F0B7 -> •) points to original CharacterEvidence."""
    c_ev = CharacterEvidence(text="\uf0b7", bbox=(0, 0, 10, 10))
    s = {"text": "\uf0b7", "bbox": (0, 0, 10, 10), "size": 10, "chars": (c_ev,)}
    proj = reconstruct_line_with_provenance([s])
    assert proj.text == "•"
    assert len(proj.char_map) == 1
    assert proj.char_map[0] is c_ev


def test_part23_removed_character_has_no_slot():
    """Part 23: Removed character (e.g. U+200B) produces no output character and no char_map slot."""
    chars = (
        CharacterEvidence(text="A", bbox=(0, 0, 10, 10)),
        CharacterEvidence(text="\u200b", bbox=(10, 0, 10, 10)),
        CharacterEvidence(text="B", bbox=(10, 0, 20, 10)),
    )
    s = {"text": "A\u200bB", "bbox": (0, 0, 20, 10), "size": 10, "chars": chars}
    proj = reconstruct_line_with_provenance([s])
    assert proj.text == "AB"
    assert len(proj.char_map) == 2
    assert proj.char_map[0] is chars[0]
    assert proj.char_map[1] is chars[2]


def test_part24_synthetic_geometric_space_is_none():
    """Part 24: Synthetic inter-span space is represented as None in char_map."""
    c1 = (CharacterEvidence(text="A", bbox=(0, 0, 10, 10)),)
    c2 = (CharacterEvidence(text="B", bbox=(25, 0, 35, 10)),)
    s1 = {"text": "A", "bbox": (0, 0, 10, 10), "size": 10, "chars": c1}
    s2 = {"text": "B", "bbox": (25, 0, 35, 10), "size": 10, "chars": c2}
    proj = reconstruct_line_with_provenance([s1, s2])
    assert proj.text == "A B"
    assert len(proj.char_map) == 3
    assert proj.char_map[0] is c1[0]
    assert proj.char_map[1] is None  # Synthetic space
    assert proj.char_map[2] is c2[0]


def test_part25_collapsed_whitespace_is_none():
    """Part 25: Collapsed whitespace characters emit a single space with None char_map."""
    chars1 = tuple(CharacterEvidence(text=c, bbox=(i * 10.0, 0, (i + 1) * 10.0, 10)) for i, c in enumerate("A   "))
    chars2 = tuple(CharacterEvidence(text=c, bbox=(40 + i * 10.0, 0, 50 + i * 10.0, 10)) for i, c in enumerate("B"))
    s1 = {"text": "A   ", "bbox": (0, 0, 40, 10), "size": 10, "chars": chars1}
    s2 = {"text": "B", "bbox": (40, 0, 50, 10), "size": 10, "chars": chars2}
    proj = reconstruct_line_with_provenance([s1, s2])
    assert proj.text == "A B"
    assert len(proj.char_map) == 3
    assert proj.char_map[0] is chars1[0]
    assert proj.char_map[1] is None  # Collapsed whitespace
    assert proj.char_map[2] is chars2[0]
