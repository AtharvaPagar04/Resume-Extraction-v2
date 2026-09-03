from collections import Counter
from types import SimpleNamespace

import pymupdf as fitz

from resume_extractor.extractor import extract_pdf
from resume_extractor.layout_foundation import GutterCandidate, LayoutLine, LogicalRow, Span, TableCandidate, build_page_layout
from resume_extractor.tables import TableCell, TableRow, _aligned_candidates, _header_row, _is_cross_region_bridge, _is_layout_artifact, _logical_columns, _logical_rows, _reconstruct_visual_rows, _rule_separates, _serialize, reconstruct_tables


def table_pdf(tmp_path, rows, widths=(140, 140), header_bold=True, bold_rows=None, name="table.pdf", before=(), after=(), link=None, ruled=True):
    path = tmp_path / name
    document = fitz.open()
    page = document.new_page()
    x0, y0, row_height = 70, 120, 42
    for text, point in before:
        page.insert_text(point, text, fontname="hebo")
    xs = [x0]
    for width in widths:
        xs.append(xs[-1] + width)
    ys = [y0 + row * row_height for row in range(len(rows) + 1)]
    if ruled:
        for x in xs:
            page.draw_line((x, ys[0]), (x, ys[-1]), width=1)
        for y in ys:
            page.draw_line((xs[0], y), (xs[-1], y), width=1)
    for row_index, row in enumerate(rows):
        for column_index, value in enumerate(row):
            if not value:
                continue
            lines = value.split("\n")
            for line_index, line in enumerate(lines):
                page.insert_text((xs[column_index] + 8, ys[row_index] + 20 + line_index * 13), line, fontname="hebo" if (bold_rows is None and row_index == 0 and header_bold) or (bold_rows is not None and row_index in bold_rows) else "helv")
    for text, point in after:
        page.insert_text(point, text, fontname="hebo")
    if link:
        row, column, uri = link
        page.insert_link({"kind": fitz.LINK_URI, "from": fitz.Rect(xs[column] + 4, ys[row] + 4, xs[column + 1] - 4, ys[row + 1] - 4), "uri": uri})
    document.save(path)
    document.close()
    return path


def table_result(path):
    document = fitz.open(path)
    try:
        return reconstruct_tables(build_page_layout(document[0], 1))
    finally:
        document.close()


def aligned_candidate_pdf(tmp_path):
    path = tmp_path / "aligned_candidate.pdf"
    document = fitz.open()
    page = document.new_page()
    for x, y, text in (
        (70, 120, "Label A"), (220, 120, "Value A"),
        (70, 150, "Label B"), (115, 150, "fragment"), (220, 150, "Value B"),
        (70, 163, "continued"),
        (70, 190, "Label C"), (220, 190, "Value C"),
        (410, 215, "other"), (490, 215, "nearby"),
    ):
        page.insert_text((x, y), text)
    document.save(path)
    document.close()
    return path


def grid_cell(row, column, bbox, text=""):
    return TableCell(row, column, bbox, text=text)


def styled_cell(row, column, text, bold=False, fragments=1, size=10):
    spans = tuple(
        Span(text, text, (column * 100, row * 20, column * 100 + 50, row * 20 + 10), "", size, 0, bold, False, 0.0, row, column, index, (row, column, index))
        for index in range(fragments)
    )
    return TableCell(row, column, (column * 100, row * 20, column * 100 + 50, row * 20 + 10), spans, text=text)


def styled_row(row, styles):
    return tuple(styled_cell(row, column, f"cell-{row}-{column}", bold, fragments) for column, (bold, fragments) in enumerate(styles))


def source_line(block, index, bbox):
    return LayoutLine("", "", bbox, block, index, (block, index), ())


def aligned_line(text, x0, y0, width=45, block=0, index=0, bullet=False):
    return LayoutLine(text, text, (x0, y0, x0 + width, y0 + 10), block, index, (block, index), (), token_count=len(text.split()), text_length=len(text), starts_with_bullet=bullet)


def aligned_layout(rows, width=600, gutters=(), extra_lines=()):
    logical_rows = tuple(
        LogicalRow(tuple(members), (min(line.x0 for line in members), min(line.y0 for line in members), max(line.x1 for line in members), max(line.y1 for line in members)), sum(line.center_y for line in members) / len(members))
        for members in rows
    )
    return SimpleNamespace(rows=logical_rows, lines=tuple(line for members in rows for line in members) + tuple(extra_lines), width=width, median_line_height=10, gutters=gutters)


def test_basic_ruled_table_reconstructs_rows_and_source_ownership(tmp_path):
    result = table_result(table_pdf(tmp_path, (("Left", "Right"), ("A1", "B1"), ("A2", "B2"))))
    table = result.tables[0]
    assert table.text == "Left: A1 | Right: B1\nLeft: A2 | Right: B2"
    expected = Counter(span.source_order for span in table.source_spans)
    emitted = Counter(span.source_order for row in table.rows for cell in row.cells for span in cell.source_spans)
    assert expected == emitted and not table.warnings and not result.rejected_candidates


def test_four_column_table_is_row_major_not_column_major(tmp_path):
    result = table_result(table_pdf(tmp_path, (("A", "B", "C", "D"), ("r1a", "r1b", "r1c", "r1d"), ("r2a", "r2b", "r2c", "r2d")), (105, 105, 105, 105)))
    text = result.tables[0].text
    assert "A: r1a | B: r1b | C: r1c | D: r1d" in text
    assert text.index("r1d") < text.index("r2a")


def test_wrapped_cell_stays_in_its_row(tmp_path):
    result = table_result(table_pdf(tmp_path, (("Name", "Description"), ("A", "line one\nline two"), ("B", "other"))))
    assert "Name: A | Description: line one line two" in result.tables[0].text


def test_multiline_header_is_reconstructed_inside_its_cell(tmp_path):
    result = table_result(table_pdf(tmp_path, (("Languages &\nFrameworks", "Value"), ("One", "Two"))))
    assert result.tables[0].text == "Languages & Frameworks: One | Value: Two"


def test_empty_cell_does_not_shift_later_column_ownership(tmp_path):
    result = table_result(table_pdf(tmp_path, (("A", "B", "C"), ("one", "", "three"), ("four", "five", "six")), (120, 120, 120)))
    assert result.tables[0].text == "A: one | C: three\nA: four | B: five | C: six"


def test_empty_header_cell_preserves_its_body_value_without_empty_separator(tmp_path):
    result = table_result(table_pdf(tmp_path, (("A", "", "C"), ("one", "two", "three")), (120, 120, 120)))
    assert result.tables[0].text == "A: one | two | C: three"


def test_headerless_logical_rows_omit_empty_active_values(tmp_path):
    result = table_result(table_pdf(tmp_path, (("one", "", "three"), ("four", "five", "six")), (120, 120, 120), header_bold=False))
    assert result.tables[0].text == "one | three\nfour | five | six"


def test_header_role_is_invariant_to_wrapped_bold_label_fragments():
    body = (styled_row(1, ((True, 1), (False, 1))), styled_row(2, ((True, 1), (False, 1))))
    unwrapped = styled_row(0, ((True, 1), (False, 1)))
    wrapped = styled_row(0, ((True, 3), (False, 1)))
    assert not _header_row(unwrapped, body)
    assert _header_row(unwrapped, body) == _header_row(wrapped, body)
    rows = (TableRow(0, wrapped, (0, 0, 200, 10)), TableRow(1, body[0], (0, 20, 200, 30)), TableRow(2, body[1], (0, 40, 200, 50)))
    assert _serialize(rows).splitlines()[0] == "cell-0-0: cell-0-1"


def test_header_role_is_unchanged_when_a_later_label_wraps():
    first = styled_row(0, ((True, 1), (False, 1)))
    body = (styled_row(1, ((True, 3), (False, 1))), styled_row(2, ((True, 1), (False, 1))))
    assert not _header_row(first, body)


def test_true_headers_use_cell_normalized_style_not_fragment_count():
    header = styled_row(0, ((True, 3), (True, 1), (True, 1), (True, 1)))
    body = (styled_row(1, ((False, 1),) * 4), styled_row(2, ((False, 1),) * 4))
    assert _header_row(header, body)


def test_partial_header_emphasis_and_incidental_body_bold_are_preserved():
    header = styled_row(0, ((True, 1), (True, 1), (False, 1)))
    body = (styled_row(1, ((True, 1), (False, 1), (False, 1))), styled_row(2, ((False, 1), (False, 1), (False, 1))))
    assert _header_row(header, body)


def test_multicolumn_repeated_body_style_is_not_a_header():
    first = styled_row(0, ((True, 1), (False, 1), (True, 1)))
    body = (styled_row(1, ((True, 1), (False, 1), (True, 1))), styled_row(2, ((True, 1), (False, 1), (True, 1))))
    assert not _header_row(first, body)


def test_sparse_native_grid_is_not_accepted_as_a_table(tmp_path):
    result = table_result(table_pdf(tmp_path, (("A", "", "", ""), ("one", "", "", ""), ("two", "", "", "")), (90, 90, 90, 90)))
    assert not result.tables


def test_empty_interstitial_columns_do_not_inflate_density(tmp_path):
    result = table_result(table_pdf(tmp_path, (("H0", "", "", "H3", "", "", "H6"), ("a", "", "", "b", "", "", "c"), ("d", "", "", "e", "", "", "f")), (60, 60, 60, 60, 60, 60, 60)))
    table = result.tables[0]
    assert table.column_count == 3 and table.text == "H0: a | H3: b | H6: c\nH0: d | H3: e | H6: f"


def test_visual_columns_align_offset_header_and_body_raw_slots():
    header = TableRow(0, (
        grid_cell(0, 0, (50, 0, 160, 20)), grid_cell(0, 1, (60, 0, 158, 20), "Header A"),
        grid_cell(0, 2, (160, 0, 310, 20)), grid_cell(0, 3, (170, 0, 308, 20), "Header B"),
        grid_cell(0, 4, (310, 0, 440, 20)), grid_cell(0, 5, (320, 0, 438, 20), "Header C"),
    ), (50, 0, 440, 20))
    body = TableRow(1, (
        grid_cell(1, 0, (50, 20, 160, 40), "value A"), grid_cell(1, 1, (60, 20, 158, 40)),
        grid_cell(1, 2, (160, 20, 310, 40), "value B"), grid_cell(1, 3, (170, 20, 308, 40)),
        grid_cell(1, 4, (310, 20, 440, 40), "value C"), grid_cell(1, 5, (320, 20, 438, 40)),
    ), (50, 20, 440, 40))
    columns = _logical_columns([header, body])
    rows = _logical_rows([header, body], columns)
    assert [column.raw_column_indices for column in columns] == [(0, 1), (2, 3), (4, 5)]
    assert [cell.text for cell in rows[0].cells] == ["Header A", "Header B", "Header C"]
    assert [cell.text for cell in rows[1].cells] == ["value A", "value B", "value C"]
    assert _serialize((TableRow(rows[0].row_index, rows[0].cells, rows[0].bbox, True), rows[1])) == "Header A: value A | Header B: value B | Header C: value C"


def test_neighboring_visual_columns_are_not_overmerged():
    rows = [TableRow(0, (grid_cell(0, 0, (0, 0, 100, 20), "left"), grid_cell(0, 1, (99, 0, 199, 20), "right")), (0, 0, 199, 20))]
    assert [column.raw_column_indices for column in _logical_columns(rows)] == [(0,), (1,)]


def test_native_rows_reconcile_wrapped_label_with_spanning_value():
    rows = (
        TableRow(0, (grid_cell(0, 0, (50, 100, 150, 130)), grid_cell(0, 1, (160, 100, 300, 130), "Value")), (50, 100, 300, 130)),
        TableRow(1, (grid_cell(1, 0, (50, 100, 150, 110), "Label part one"), grid_cell(1, 1, (160, 100, 300, 110))), (50, 100, 300, 110)),
        TableRow(2, (grid_cell(2, 0, (50, 111, 150, 121), "part two"), grid_cell(2, 1, (160, 111, 300, 121))), (50, 111, 300, 121)),
    )
    candidate = TableCandidate((50, 100, 300, 130), "pymupdf", 1)
    logical = _reconstruct_visual_rows(SimpleNamespace(drawings=()), candidate, rows)
    assert len(logical) == 1
    assert [cell.text for cell in logical[0].cells] == ["Label part one part two", "Value"]
    assert _serialize(logical) == "Label part one part two: Value"


def test_native_rows_reconcile_multiline_value_against_spanning_label():
    rows = (
        TableRow(0, (grid_cell(0, 0, (50, 100, 150, 130), "Label"), grid_cell(0, 1, (160, 100, 300, 110), "Value part one")), (50, 100, 300, 130)),
        TableRow(1, (grid_cell(1, 0, (50, 111, 150, 121)), grid_cell(1, 1, (160, 111, 300, 121), "part two")), (50, 111, 300, 121)),
    )
    logical = _reconstruct_visual_rows(SimpleNamespace(drawings=()), TableCandidate((50, 100, 300, 130), "pymupdf", 1), rows)
    assert len(logical) == 1
    assert [cell.text for cell in logical[0].cells] == ["Label", "Value part one part two"]


def test_native_rows_reconcile_both_sides_wrapped_when_continuations_match():
    left_one, left_two = source_line(0, 0, (50, 100, 150, 110)), source_line(0, 1, (50, 111, 150, 121))
    right_one, right_two = source_line(1, 0, (160, 100, 300, 110)), source_line(1, 1, (160, 111, 300, 121))
    rows = (
        TableRow(0, (TableCell(0, 0, left_one.bbox, source_lines=(left_one,), text="Label one"), TableCell(0, 1, right_one.bbox, source_lines=(right_one,), text="Value one")), (50, 100, 300, 110)),
        TableRow(1, (TableCell(1, 0, left_two.bbox, source_lines=(left_two,), text="Label two"), TableCell(1, 1, right_two.bbox, source_lines=(right_two,), text="Value two")), (50, 111, 300, 121)),
    )
    logical = _reconstruct_visual_rows(SimpleNamespace(drawings=()), TableCandidate((50, 100, 300, 130), "pymupdf", 1), rows)
    assert len(logical) == 1
    assert [cell.text for cell in logical[0].cells] == ["Label one Label two", "Value one Value two"]


def test_native_row_reconstruction_respects_rule_and_independent_columns():
    rows = (
        TableRow(0, (grid_cell(0, 0, (50, 100, 150, 110), "Label A"), grid_cell(0, 1, (160, 100, 300, 110), "Value A")), (50, 100, 300, 110)),
        TableRow(1, (grid_cell(1, 0, (50, 111, 150, 121), "Label B"), grid_cell(1, 1, (160, 111, 300, 121), "Value B")), (50, 111, 300, 121)),
    )
    candidate = TableCandidate((50, 100, 300, 130), "pymupdf", 1)
    rule = SimpleNamespace(kind="horizontal_rule", bbox=(50, 110.5, 300, 110.5), length=250)
    logical = _reconstruct_visual_rows(SimpleNamespace(drawings=(rule,)), candidate, rows)
    assert _rule_separates(SimpleNamespace(drawings=(rule,)), candidate, rows[0].cells[0], rows[1].cells[0])
    assert len(logical) == 2


def test_boundary_empty_rows_do_not_displace_header_or_density(tmp_path):
    result = table_result(table_pdf(tmp_path, (("", ""), ("A", "B"), ("one", "two"), ("", "")), bold_rows=(1,)))
    table = result.tables[0]
    assert len(table.rows) == 2 and table.text == "A: one | B: two"


def test_internal_empty_row_is_not_trimmed(tmp_path):
    result = table_result(table_pdf(tmp_path, (("A", "B"), ("one", "two"), ("", ""), ("three", "four"))))
    table = result.tables[0]
    assert len(table.rows) == 4 and table.text == "A: one | B: two\nA: three | B: four"


def test_page_scale_unpaired_grid_is_rejected_before_density():
    rows = tuple(TableRow(index, (TableCell(index, 0, (0, index * 20, 100, index * 20 + 10), text="x" if index % 2 == 0 else ""), TableCell(index, 1, (100, index * 20, 200, index * 20 + 10), text="x" if index % 2 else "")), (0, index * 20, 200, index * 20 + 10)) for index in range(4))
    columns = _logical_columns(list(rows))
    assert _is_layout_artifact(SimpleNamespace(width=600, height=800, drawings=()), TableCandidate((0, 0, 600, 800), "pymupdf", 1), rows, columns)


def test_borderless_compact_label_value_rows_are_reconstructed(tmp_path):
    path = table_pdf(tmp_path, (("LabelA", "ValueA"), ("LabelB", "ValueB"), ("LabelC", "ValueC")), ruled=False, header_bold=False)
    result = table_result(path)
    assert result.tables and result.tables[0].text == "LabelA: ValueA\nLabelB: ValueB\nLabelC: ValueC"


def test_aligned_candidates_use_recurring_bands_and_attach_continuations():
    layout = aligned_layout((
        (aligned_line("Label A", 60, 100, block=0), aligned_line("Value A", 200, 100, block=1)),
        (aligned_line("Label B", 60, 130, block=0, index=1), aligned_line("fragment", 100, 130, block=0, index=2), aligned_line("Value B", 200, 130, block=1, index=1)),
        (aligned_line("continued", 60, 142, block=0, index=3),),
        (aligned_line("Label C", 60, 170, block=0, index=4), aligned_line("Value C", 200, 170, block=1, index=2)),
        (aligned_line("other", 380, 195, block=9), aligned_line("nearby", 450, 195, block=9, index=1)),
    ))
    candidates = _aligned_candidates(layout)
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.column_guides == (60, 200)
    assert len(candidate.row_guides) == 4 and candidate.bbox[3] == 180


def test_aligned_candidates_attach_right_continuations_but_not_noise_or_bullets():
    layout = aligned_layout((
        (aligned_line("Label A", 60, 100, block=0), aligned_line("Value A", 200, 100, block=1)),
        (aligned_line("Label B", 60, 130, block=0, index=1), aligned_line("Value B", 200, 130, block=1, index=1)),
        (aligned_line("continued", 200, 142, block=1, index=2),),
        (aligned_line("noise", 320, 145, block=8),),
        (aligned_line("Label C", 60, 170, block=0, index=2), aligned_line("Value C", 200, 170, block=1, index=3)),
    ))
    candidate, = _aligned_candidates(layout)
    assert candidate.column_guides == (60, 200)
    assert len(candidate.row_guides) == 4 and candidate.bbox[2] == 245
    bullet_layout = aligned_layout(tuple((aligned_line("marker", 60, y, width=8, bullet=True), aligned_line("content", 200, y, width=200, block=1)) for y in (100, 130, 160)))
    assert not _aligned_candidates(bullet_layout)


def test_local_aligned_table_crossing_a_gutter_is_preserved_without_bilateral_persistence():
    gutter = GutterCandidate(180, 360, 270, 10, 10, 0.8)
    rows = tuple(
        (aligned_line(f"Label {index}", 60, y, block=index), aligned_line(f"Value {index}", 380, y, width=80, block=index + 10))
        for index, y in enumerate((100, 130, 160))
    )
    layout = aligned_layout(rows, gutters=(gutter,), extra_lines=(aligned_line("before", 60, 40, width=200), aligned_line("after", 60, 240, width=200)))
    candidate, = _aligned_candidates(layout)
    assert candidate.column_guides == (60, 380)


def test_aligned_candidates_reject_short_persistent_cross_region_bridge():
    gutter = GutterCandidate(180, 390, 285, 10, 10, 0.8)
    rows = tuple(
        (aligned_line(f"Left {index}", 60, y, width=250, block=0), aligned_line("mark", 400, y, width=8, block=index + 10), aligned_line(f"Right {index}", 420, y, block=index + 10, index=1))
        for index, y in enumerate((100, 130, 160))
    )
    outside = (
        aligned_line("left above", 60, 40, width=250), aligned_line("left below", 60, 240, width=250),
        aligned_line("right above", 400, 40, width=8), aligned_line("right above item", 420, 40),
        aligned_line("right below", 400, 240, width=8), aligned_line("right below item", 420, 240),
    )
    assert not _aligned_candidates(aligned_layout(rows, gutters=(gutter,), extra_lines=outside))


def test_cross_region_bridge_requires_every_core_row_to_cross_the_persistent_gutter():
    gutter = GutterCandidate(180, 390, 285, 10, 10, 0.8)
    rows = aligned_layout((
        (aligned_line("left", 60, 100, width=80), aligned_line("right", 420, 100)),
        (aligned_line("left", 60, 130, width=80),),
        (aligned_line("left", 60, 160, width=80), aligned_line("right", 420, 160)),
    ), gutters=(gutter,), extra_lines=(aligned_line("left above", 60, 40), aligned_line("left below", 60, 240), aligned_line("right above", 420, 40), aligned_line("right below", 420, 240)))
    assert not _is_cross_region_bridge(rows, list(enumerate(rows.rows)))


def test_aligned_candidate_flows_to_existing_reconstruction_once(tmp_path):
    result = table_result(aligned_candidate_pdf(tmp_path))
    table, = result.tables
    assert table.reconstruction_method == "aligned"
    assert table.text.count("continued") == 1
    assert Counter(span.source_order for span in table.source_spans) == Counter(span.source_order for row in table.rows for cell in row.cells for span in cell.source_spans)


def test_true_two_column_flow_is_not_a_borderless_table(tmp_path):
    long = "This is an intentionally long independent flow sentence used for geometry only"
    path = table_pdf(tmp_path, ((long, "side"), (long, "side"), (long, "side")), widths=(300, 100), ruled=False, header_bold=False)
    assert not table_result(path).tables


def test_hyperlink_in_cell_preserves_visible_text_and_raw_link(tmp_path):
    path = table_pdf(tmp_path, (("Name", "Link"), ("A", "Open")), link=(1, 1, "https://example.com"))
    result = table_result(path)
    assert result.tables[0].rows[1].cells[1].hyperlinks
    raw = extract_pdf(path)
    assert "Link: Open" in raw.text and [(link.page, link.uri) for link in raw.hyperlinks] == [(1, "https://example.com")]


def test_heading_and_following_content_stay_outside_table(tmp_path):
    path = table_pdf(tmp_path, (("A", "B"), ("one", "two")), before=(("BEFORE", (70, 80)),), after=(("AFTER", (70, 280)),))
    raw = extract_pdf(path)
    assert raw.text.splitlines() == ["BEFORE", "A: one | B: two", "AFTER"]


def test_multiple_tables_reconstruct_once_each(tmp_path):
    path = tmp_path / "multiple.pdf"
    document = fitz.open()
    page = document.new_page()
    for y0, values in ((100, (("A", "B"), ("one", "two"))), (260, (("C", "D"), ("three", "four")))):
        xs, ys = (70, 210, 350), (y0, y0 + 42, y0 + 84)
        for x in xs:
            page.draw_line((x, ys[0]), (x, ys[-1]), width=1)
        for y in ys:
            page.draw_line((xs[0], y), (xs[-1], y), width=1)
        for row, values_row in enumerate(values):
            for column, value in enumerate(values_row):
                page.insert_text((xs[column] + 8, ys[row] + 20), value, fontname="hebo" if row == 0 else "helv")
    document.save(path)
    document.close()
    result = table_result(path)
    raw = extract_pdf(path)
    assert len(result.tables) == 2
    assert raw.text.count("A: one | B: two") == raw.text.count("C: three | D: four") == 1
