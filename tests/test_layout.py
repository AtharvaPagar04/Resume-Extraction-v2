from resume_extractor.layout import LayoutLine, order_lines


def line(text, x0, y0, x1, y1, source_id):
    return LayoutLine(text, text, (x0, y0, x1, y1), 0, source_id, (0, source_id), (source_id,))


def texts(lines):
    return [item.text for item in lines]


def test_single_column_ordering():
    ordered, fallback = order_lines([line("B", 10, 30, 60, 40, 1), line("A", 10, 10, 60, 20, 0)], 200, 300)
    assert texts(ordered) == ["A", "B"] and not fallback


def test_multi_column_ordering_avoids_interleaving():
    source = [line(f"L{i}", 10, 10 + i * 30, 90, 20 + i * 30, i) for i in range(3)] + [line(f"R{i}", 130, 10 + i * 30, 210, 20 + i * 30, i + 3) for i in range(3)]
    ordered, fallback = order_lines(source, 240, 300)
    assert texts(ordered) == ["L0", "L1", "L2", "R0", "R1", "R2"] and not fallback


def test_sidebar_orders_wide_main_region_before_narrow_sidebar():
    source = [line(f"side{i}", 10, 10 + i * 30, 50, 20 + i * 30, i) for i in range(3)] + [line(f"main{i}", 100, 10 + i * 30, 220, 20 + i * 30, i + 3) for i in range(3)]
    ordered, _ = order_lines(source, 240, 300)
    assert texts(ordered) == ["main0", "main1", "main2", "side0", "side1", "side2"]


def test_right_metadata_stays_on_its_row():
    source = [line("Role", 10, 10, 100, 20, 0), line("2024", 150, 10, 190, 20, 1), line("Next", 10, 40, 100, 50, 2), line("2023", 150, 40, 190, 50, 3)]
    ordered, _ = order_lines(source, 240, 300)
    assert texts(ordered) == ["Role", "2024", "Next", "2023"]


def test_full_width_heading_starts_a_band():
    source = [line("Experience", 0, 0, 200, 10, 0)] + [line(f"L{i}", 10, 20 + i * 30, 90, 30 + i * 30, i + 1) for i in range(3)] + [line(f"R{i}", 130, 20 + i * 30, 210, 30 + i * 30, i + 4) for i in range(3)]
    ordered, _ = order_lines(source, 240, 300)
    assert texts(ordered)[0] == "Experience"


def test_standalone_bullet_pairs_with_content():
    ordered, _ = order_lines([line("•", 10, 10, 15, 20, 0), line("Delivered features", 20, 10, 120, 20, 1)], 200, 300)
    assert texts(ordered) == ["• Delivered features"]


def test_bullet_continuation_is_joined_only_when_geometry_matches():
    source = [line("•", 10, 10, 15, 20, 0), line("Delivered", 20, 10, 100, 20, 1), line("features", 20, 24, 100, 34, 2)]
    ordered, _ = order_lines(source, 200, 300)
    assert texts(ordered) == ["• Delivered features"]


def test_wrapped_lines_remain_physical_lines():
    ordered, _ = order_lines([line("first physical line", 10, 10, 100, 20, 0), line("second physical line", 10, 23, 100, 33, 1)], 200, 300)
    assert texts(ordered) == ["first physical line", "second physical line"]


def test_layout_source_accounting_invariant():
    source = [line("A", 10, 10, 30, 20, 0), line("B", 10, 30, 30, 40, 1)]
    ordered, _ = order_lines(source, 100, 100)
    assert sorted(source_id for item in ordered for source_id in item.source_ids) == [0, 1]


def test_layout_fallback_on_invalid_source_accounting():
    bad = [line("A", 10, 10, 30, 20, 0), line("B", 10, 30, 30, 40, 0)]
    ordered, fallback = order_lines(bad, 100, 100)
    assert fallback and texts(ordered) == ["A", "B"]


def test_empty_layout_is_stable():
    assert order_lines([], 100, 100) == ([], False)
