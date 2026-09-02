from dataclasses import replace

import pymupdf as fitz

import resume_extractor.extractor as extractor_module
from resume_extractor.layout_foundation import (
    LayoutLine,
    _line_from_dict,
    build_page_layout,
    cluster_x_anchors,
    dump_layout_debug,
    find_gutter_candidates,
    group_rows,
    validate_line_accounting,
    validate_span_accounting,
)


def make_pdf(tmp_path, name="layout.pdf", draw=None, text=()):
    path = tmp_path / name
    document = fitz.open()
    page = document.new_page()
    for value, point in text:
        page.insert_text(point, value, fontsize=12)
    if draw:
        draw(page)
    document.save(path)
    document.close()
    return path


def layout_for(path):
    document = fitz.open(path)
    try:
        return build_page_layout(document[0], 1)
    finally:
        document.close()


def synthetic_line(text, x0, y0, x1, y1, source_id):
    return LayoutLine(text, text, (x0, y0, x1, y1), 0, source_id, (0, source_id), (source_id,))


def test_page_layout_preserves_hierarchy_statistics_and_source_order(tmp_path):
    layout = layout_for(make_pdf(tmp_path, text=(("First", (72, 72)), ("Second", (72, 100)))))
    assert layout.width > 0 and layout.height > 0
    assert len(layout.blocks) == len(layout.lines) == 2
    assert len(layout.spans) >= 2 and layout.text_bbox is not None
    assert layout.median_line_height > 0 and layout.median_font_size == 12.0
    assert [line.source_order for line in layout.lines] == sorted(line.source_order for line in layout.lines)


def test_style_features_are_structural_not_semantic():
    raw = {"bbox": (0, 0, 100, 10), "spans": [{"text": "TITLE", "bbox": (0, 0, 50, 10), "font": "AnyFont", "size": 14, "flags": 16}, {"text": " mixed", "bbox": (51, 0, 100, 10), "font": "Other Italic", "size": 10, "flags": 2}]}
    line, spans = _line_from_dict(0, 0, raw, 0)
    assert len(spans) == 2 and line.dominant_font_size == 14
    assert line.bold_ratio > 0 and line.italic_ratio > 0 and 0 < line.uppercase_ratio < 1


def test_bullet_structural_features_do_not_assign_sections():
    bullet, _ = _line_from_dict(0, 0, {"bbox": (0, 0, 10, 10), "spans": [{"text": "• item", "bbox": (0, 0, 10, 10)}]}, 0)
    marker, _ = _line_from_dict(0, 1, {"bbox": (0, 0, 10, 10), "spans": [{"text": "•", "bbox": (0, 0, 10, 10)}]}, 1)
    assert bullet.starts_with_bullet and not bullet.is_bullet_only
    assert marker.is_bullet_only


def test_row_grouping_handles_baseline_variation_without_dropping_lines():
    lines = [synthetic_line("left", 10, 10, 60, 20, 0), synthetic_line("right", 120, 12, 170, 22, 1), synthetic_line("next", 10, 50, 60, 60, 2)]
    rows = group_rows(lines)
    assert [len(row.members) for row in rows] == [2, 1]
    assert validate_line_accounting(lines, rows) == ()


def test_anchor_clusters_and_gutters_are_page_relative():
    lines = [synthetic_line(f"L{i}", 10, 20 + 40 * i, 80, 30 + 40 * i, i) for i in range(3)] + [synthetic_line(f"R{i}", 180, 20 + 40 * i, 250, 30 + 40 * i, i + 3) for i in range(3)]
    anchors = cluster_x_anchors(lines, 300)
    gutters = find_gutter_candidates(lines, anchors, 300, 300)
    assert [anchor.support for anchor in anchors] == [3, 3]
    assert len(gutters) == 1 and gutters[0].vertical_coverage > 0


def test_hyperlink_geometry_maps_to_overlapping_text(tmp_path):
    path = make_pdf(tmp_path, text=(("website", (72, 72)),))
    document = fitz.open(path)
    document[0].insert_link({"kind": fitz.LINK_URI, "from": fitz.Rect(70, 58, 120, 76), "uri": "https://example.com"})
    document.save(tmp_path / "linked-layout.pdf")
    document.close()
    layout = layout_for(tmp_path / "linked-layout.pdf")
    link = layout.hyperlinks[0]
    assert layout.find_overlapping_lines(link.bbox)
    assert layout.nearest_line(link.bbox).text == "website"


def test_drawing_extraction_distinguishes_rules_rectangles_and_decoration(tmp_path):
    def draw(page):
        page.draw_line((50, 100), (400, 100), width=1)
        page.draw_line((100, 150), (100, 400), width=1)
        page.draw_rect(fitz.Rect(150, 150, 300, 300), width=1)
        page.draw_line((10, 10), (12, 10), width=1)

    layout = layout_for(make_pdf(tmp_path, draw=draw))
    kinds = {drawing.kind for drawing in layout.drawings}
    assert {"horizontal_rule", "vertical_rule", "rectangle"}.issubset(kinds)
    assert any(drawing.kind == "drawing" for drawing in layout.drawings)


def test_table_foundation_exposes_rule_and_native_candidates(tmp_path):
    def draw(page):
        for x in (80, 250, 420):
            page.draw_line((x, 100), (x, 320), width=1)
        for y in (100, 200, 320):
            page.draw_line((80, y), (420, y), width=1)

    layout = layout_for(make_pdf(tmp_path, draw=draw, text=(("A", (100, 150)), ("B", (270, 150)))))
    assert any(candidate.source == "rules" for candidate in layout.table_candidates)
    assert any(candidate.column_guides for candidate in layout.table_candidates if candidate.source == "rules")


def test_borderless_table_still_exposes_repeated_x_anchors(tmp_path):
    layout = layout_for(make_pdf(tmp_path, text=tuple((f"L{i}", (80, 80 + 40 * i)) for i in range(3)) + tuple((f"R{i}", (250, 80 + 40 * i)) for i in range(3))))
    assert sorted(anchor.support for anchor in layout.anchor_clusters) == [3, 3]
    assert validate_line_accounting(layout.lines, layout.regions) == ()


def test_four_column_and_wrapped_cell_geometry_stays_structural(tmp_path):
    text = tuple((f"H{column}", (70 + column * 120, 100)) for column in range(4))
    text += tuple((f"R{row}C{column}", (70 + column * 120, 150 + row * 45)) for row in range(2) for column in range(4))
    text += (("wrapped", (70, 260)), ("cell", (70, 275)))
    layout = layout_for(make_pdf(tmp_path, text=text))
    assert len([anchor for anchor in layout.anchor_clusters if anchor.support >= 3]) >= 4
    assert any(len(row.members) == 4 for row in layout.rows)


def test_source_accounting_detects_missing_span_and_rows_preserve_sources(tmp_path):
    layout = layout_for(make_pdf(tmp_path, text=(("one", (72, 72)), ("two", (72, 100)))))
    assert validate_span_accounting(layout.spans, layout.lines).valid
    missing = validate_span_accounting(layout.spans, [replace(layout.lines[0], spans=())])
    assert missing.missing and not missing.duplicated
    assert validate_line_accounting(layout.lines, layout.rows) == ()


def test_debug_dump_is_explicit_and_separate_from_raw(tmp_path):
    layout = layout_for(make_pdf(tmp_path, text=(("debug", (72, 72)),)))
    target = dump_layout_debug(layout, tmp_path / "debug" / "page.json")
    assert target.exists() and '"lines"' in target.read_text()


def test_foundation_failure_falls_back_to_basic_page_text(tmp_path, monkeypatch):
    path = make_pdf(tmp_path, text=(("survives", (72, 72)),))
    monkeypatch.setattr(extractor_module, "build_page_layout", lambda *args: (_ for _ in ()).throw(RuntimeError("layout")))
    raw = extractor_module.extract_pdf(path)
    assert raw.text == "survives" and "READING_ORDER_FALLBACK" in raw.extraction.warnings
