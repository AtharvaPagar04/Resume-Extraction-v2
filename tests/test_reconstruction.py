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
