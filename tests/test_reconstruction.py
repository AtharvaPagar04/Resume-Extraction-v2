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
