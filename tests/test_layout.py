from resume_extractor.layout import LayoutLine, order_lines


def line(text, x0, y0, x1, y1, source_id, block=0, font_size=10.0, flags=0, bold_ratio=0.0, line_idx=0):
    from resume_extractor.layout_foundation import is_bullet_only, starts_with_bullet

    return LayoutLine(
        text,
        text,
        (x0, y0, x1, y1),
        block,
        line_idx,
        (block, line_idx),
        (source_id,),
        dominant_font_size=font_size,
        dominant_flags=flags,
        bold_ratio=bold_ratio,
        ends_with_colon=text.strip().endswith(":"),
        is_bullet_only=is_bullet_only(text),
        starts_with_bullet=starts_with_bullet(text),
    )


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


def test_multiple_consecutive_detached_bullets_do_not_collapse():
    source = [
        line("•", 10, 10, 15, 20, 0, block=5),
        line("Item 1", 20, 10, 100, 20, 1, block=5),
        line("•", 10, 24, 15, 34, 2, block=5),
        line("Item 2", 20, 24, 100, 34, 3, block=5),
        line("•", 10, 38, 15, 48, 4, block=5),
        line("Item 3", 20, 38, 100, 48, 5, block=5),
    ]
    ordered, _ = order_lines(source, 200, 300)
    assert texts(ordered) == ["• Item 1", "• Item 2", "• Item 3"]


def test_inline_bullet_with_wrapped_continuation():
    source = [
        line("• Built feature X that performs", 10, 10, 150, 20, 0),
        line("additional detail here", 20, 24, 150, 34, 1),
        line("and final detail.", 20, 38, 120, 48, 2),
    ]
    ordered, _ = order_lines(source, 200, 300)
    assert texts(ordered) == ["• Built feature X that performs additional detail here and final detail."]


def test_detached_bullet_with_style_shift():
    source = [
        line("•", 10, 10, 15, 20, 0),
        line("Heading text", 20, 10, 100, 20, 1, flags=16),
        line("plain continuation", 20, 24, 110, 34, 2, flags=0),
    ]
    ordered, _ = order_lines(source, 200, 300)
    assert texts(ordered) == ["• Heading text plain continuation"]


def test_continuation_must_stop_at_next_bullet():
    source = [
        line("•", 10, 10, 15, 20, 0),
        line("Bullet 1 start", 20, 10, 120, 20, 1),
        line("Bullet 1 wrapped continuation", 20, 24, 120, 34, 2),
        line("• Bullet 2 inline", 10, 38, 120, 48, 3),
        line("Bullet 2 continuation", 20, 52, 120, 62, 4),
    ]
    ordered, _ = order_lines(source, 200, 300)
    assert texts(ordered) == [
        "• Bullet 1 start Bullet 1 wrapped continuation",
        "• Bullet 2 inline Bullet 2 continuation",
    ]


def test_cross_column_sidebar_bullet_safety():
    source = [
        line("•", 10, 10, 15, 20, 0),
        line("Side skill", 20, 10, 60, 20, 1),
        line("Main exp 1", 100, 10, 220, 20, 2),
        line("Main exp 2", 100, 30, 220, 40, 3),
        line("Main exp 3", 100, 50, 220, 60, 4),
        line("Side skill 2", 10, 30, 60, 40, 5),
        line("Side skill 3", 10, 50, 60, 60, 6),
    ]
    ordered, _ = order_lines(source, 240, 300)
    side_lines = [t for t in texts(ordered) if "Side" in t]
    assert "• Side skill" in side_lines


def test_u2217_bullet_recognition():
    from resume_extractor.layout_foundation import is_bullet_only, starts_with_bullet

    # Standalone
    assert is_bullet_only("∗")
    assert starts_with_bullet("∗")

    # Inline
    assert not is_bullet_only("∗ Designed multi-stage parsing pipeline")
    assert starts_with_bullet("∗ Designed multi-stage parsing pipeline")


def test_parent_bullet_followed_by_nested_u2217_stays_separated():
    source = [
        line("◦ Parent bullet start", 10, 10, 150, 20, 0),
        line("parent continuation", 20, 24, 150, 34, 1),
        line("∗ Nested item 1", 20, 38, 150, 48, 2),
        line("∗ Nested item 2", 20, 52, 150, 62, 3),
    ]
    ordered, _ = order_lines(source, 200, 300)
    assert texts(ordered) == [
        "◦ Parent bullet start parent continuation",
        "∗ Nested item 1",
        "∗ Nested item 2",
    ]


def test_nested_bullet_continuations_attach_correctly():
    source = [
        line("◦ Parent bullet start", 10, 10, 150, 20, 0),
        line("∗ Nested item 1 lead", 20, 24, 150, 34, 1),
        line("nested 1 continuation", 20, 38, 150, 48, 2),
        line("∗ Nested item 2 lead", 20, 52, 150, 62, 3),
    ]
    ordered, _ = order_lines(source, 200, 300)
    assert texts(ordered) == [
        "◦ Parent bullet start",
        "∗ Nested item 1 lead nested 1 continuation",
        "∗ Nested item 2 lead",
    ]


def test_bullet_stops_before_standalone_subheading():
    source = [
        line("•", 10, 10, 15, 20, 0),
        line("Cloud & AI: Azure Services, Azure OpenAI", 20, 10, 150, 20, 1, bold_ratio=0.1),
        line("Business & Professional Skills:", 20, 24, 120, 34, 2, bold_ratio=1.0),
        line("•", 10, 38, 15, 48, 3),
        line("Business & Strategy: Management", 20, 38, 150, 48, 4, bold_ratio=0.1),
    ]
    ordered, _ = order_lines(source, 200, 300)
    assert texts(ordered) == [
        "• Cloud & AI: Azure Services, Azure OpenAI",
        "Business & Professional Skills:",
        "• Business & Strategy: Management",
    ]


def test_ordinary_wrapped_continuation_with_bold_lead_in_not_stopped():
    source = [
        line("•", 10, 10, 15, 20, 0),
        line("Conducting API testing using Postman", 20, 10, 150, 20, 1, bold_ratio=0.8),
        line("validating status codes and responses.", 20, 24, 150, 34, 2, bold_ratio=0.0),
    ]
    ordered, _ = order_lines(source, 200, 300)
    assert texts(ordered) == [
        "• Conducting API testing using Postman validating status codes and responses."
    ]


def test_ordinary_colon_body_text_not_stopped():
    source = [
        line("• Built pipeline with components:", 10, 10, 150, 20, 0, bold_ratio=0.0),
        line("ingestion, transformation, and storage.", 20, 24, 150, 34, 1, bold_ratio=0.0),
    ]
    ordered, _ = order_lines(source, 200, 300)
    assert texts(ordered) == [
        "• Built pipeline with components: ingestion, transformation, and storage."
    ]


def test_bullet_stops_before_non_colon_bold_standalone_label():
    source = [
        line("– Built monitoring dashboards tracking WMAPE drift", 16, 10, 150, 20, 0, block=1, line_idx=0, bold_ratio=0.2),
        line("Snacc Item Rating", 18, 24, 100, 34, 1, block=2, line_idx=0, bold_ratio=1.0),
        line("– Built and shipped Snacc rating system", 16, 38, 150, 48, 2, block=2, line_idx=1, bold_ratio=0.5),
    ]
    ordered, _ = order_lines(source, 200, 300)
    assert texts(ordered) == [
        "– Built monitoring dashboards tracking WMAPE drift",
        "Snacc Item Rating",
        "– Built and shipped Snacc rating system",
    ]


def test_non_colon_label_at_same_x_as_bullet_content_remains_independent():
    source = [
        line("•", 16, 10, 20, 20, 0, block=1, line_idx=0),
        line("First bullet content line", 25, 10, 150, 20, 1, block=1, line_idx=1, bold_ratio=0.1),
        line("Standalone Bold Title", 25, 24, 120, 34, 2, block=2, line_idx=0, bold_ratio=1.0),
        line("•", 16, 38, 20, 48, 3, block=2, line_idx=1),
        line("Second bullet content line", 25, 38, 150, 48, 4, block=2, line_idx=2, bold_ratio=0.1),
    ]
    ordered, _ = order_lines(source, 200, 300)
    assert texts(ordered) == [
        "• First bullet content line",
        "Standalone Bold Title",
        "• Second bullet content line",
    ]


def test_new_block_non_colon_label_with_bullet_restart_recognized():
    source = [
        line("– Built LightGBM reranker with calibration", 16, 10, 150, 20, 0, block=7, line_idx=4, bold_ratio=0.3),
        line("Analytics, Pipelines & Automation", 18, 24, 160, 34, 1, block=8, line_idx=0, bold_ratio=1.0),
        line("– Streamlined end-to-end ML workflows", 16, 38, 150, 48, 2, block=8, line_idx=1, bold_ratio=0.2),
    ]
    ordered, _ = order_lines(source, 200, 300)
    assert texts(ordered) == [
        "– Built LightGBM reranker with calibration",
        "Analytics, Pipelines & Automation",
        "– Streamlined end-to-end ML workflows",
    ]


def test_bold_bullet_wrapping_to_bold_continuation_remains_attached():
    source = [
        line("• Senior Platform Engineer and Lead", 16, 10, 150, 20, 0, block=1, line_idx=0, bold_ratio=1.0),
        line("Architect for Infrastructure Systems", 25, 24, 150, 34, 1, block=1, line_idx=1, bold_ratio=1.0),
        line("• Next role", 16, 38, 100, 48, 2, block=1, line_idx=2, bold_ratio=1.0),
    ]
    ordered, _ = order_lines(source, 200, 300)
    assert texts(ordered) == [
        "• Senior Platform Engineer and Lead Architect for Infrastructure Systems",
        "• Next role",
    ]


def test_bold_lead_in_wrapping_to_plain_continuation_remains_attached():
    source = [
        line("• Technical Lead: Spearheaded microservice", 16, 10, 150, 20, 0, block=1, line_idx=0, bold_ratio=0.8),
        line("migration achieving 99.99% system uptime.", 25, 24, 150, 34, 1, block=1, line_idx=1, bold_ratio=0.0),
        line("• Next bullet", 16, 38, 100, 48, 2, block=1, line_idx=2, bold_ratio=0.0),
    ]
    ordered, _ = order_lines(source, 200, 300)
    assert texts(ordered) == [
        "• Technical Lead: Spearheaded microservice migration achieving 99.99% system uptime.",
        "• Next bullet",
    ]


def test_short_unbolded_continuation_remains_attached():
    source = [
        line("– Built model with LightGBM and Prophet", 16, 10, 150, 20, 0, block=1, line_idx=0, bold_ratio=0.2),
        line("ensemble.", 25, 24, 60, 34, 1, block=1, line_idx=1, bold_ratio=0.0),
        line("– Next model", 16, 38, 100, 48, 2, block=1, line_idx=2, bold_ratio=0.2),
    ]
    ordered, _ = order_lines(source, 200, 300)
    assert texts(ordered) == [
        "– Built model with LightGBM and Prophet ensemble.",
        "– Next model",
    ]


def test_candidate_bold_label_without_following_bullet_not_split():
    source = [
        line("• Key leadership achievements include", 16, 10, 150, 20, 0, block=1, line_idx=0, bold_ratio=0.1),
        line("Executive Direction", 25, 24, 120, 34, 1, block=2, line_idx=0, bold_ratio=1.0),
    ]
    ordered, _ = order_lines(source, 200, 300)
    assert texts(ordered) == [
        "• Key leadership achievements include Executive Direction"
    ]


def test_new_block_without_strong_style_transition_not_split():
    source = [
        line("– Developed processing platform using Apache Beam to", 16, 10, 150, 20, 0, block=3, line_idx=0, bold_ratio=0.0),
        line("orchestrate document processing across distributed services.", 25, 24, 150, 34, 1, block=4, line_idx=0, bold_ratio=0.0),
        line("– Next bullet", 16, 38, 100, 48, 2, block=4, line_idx=1, bold_ratio=0.0),
    ]
    ordered, _ = order_lines(source, 200, 300)
    assert texts(ordered) == [
        "– Developed processing platform using Apache Beam to orchestrate document processing across distributed services.",
        "– Next bullet",
    ]


def test_u25cb_standalone_is_recognized_as_bullet_only():
    from resume_extractor.layout_foundation import is_bullet_only, starts_with_bullet

    assert is_bullet_only("○")
    assert starts_with_bullet("○")


def test_u25cb_inline_starts_with_bullet():
    from resume_extractor.layout_foundation import is_bullet_only, starts_with_bullet

    assert starts_with_bullet("○ Software Engineer at Tech Company")
    assert not is_bullet_only("○ Software Engineer at Tech Company")


def test_detached_u25cb_pairs_with_adjacent_content_using_existing_logic():
    source = [
        line("○", 90, 10, 96, 20, 0),
        line("Web Dream Works India Pvt Ltd — Software Engineer", 108, 10, 375, 20, 1),
        line("○", 90, 25, 96, 35, 2),
        line("Techiche Pvt Ltd — Senior Software Engineer", 108, 25, 350, 35, 3),
        line("○", 90, 40, 96, 50, 4),
        line("Toprock India Pvt Ltd — Senior Software Engineer", 108, 40, 360, 50, 5),
    ]
    ordered, _ = order_lines(source, 400, 300)
    assert texts(ordered) == [
        "○ Web Dream Works India Pvt Ltd — Software Engineer",
        "○ Techiche Pvt Ltd — Senior Software Engineer",
        "○ Toprock India Pvt Ltd — Senior Software Engineer",
    ]



