from resume_extractor.geometry import (
    aligned_center_x,
    aligned_left,
    aligned_right,
    bbox_area,
    bbox_height,
    bbox_width,
    center_x,
    center_y,
    contains,
    gap_x,
    gap_y,
    horizontal_overlap,
    horizontal_overlap_ratio,
    intersection_area,
    intersection_ratio,
    intersects,
    nearby_horizontal,
    nearby_vertical,
    normalized_x,
    normalized_y,
    same_column,
    same_row,
    vertical_overlap,
    vertical_overlap_ratio,
)


A = (0.0, 0.0, 10.0, 10.0)
B = (5.0, 5.0, 15.0, 15.0)


def test_bbox_dimensions_area_and_centers():
    assert (bbox_width(A), bbox_height(A), bbox_area(A), center_x(A), center_y(A)) == (10.0, 10.0, 100.0, 5.0, 5.0)


def test_intersection_and_overlap_ratios_use_smaller_dimension():
    assert intersection_area(A, B) == 25.0
    assert horizontal_overlap(A, B) == vertical_overlap(A, B) == 5.0
    assert horizontal_overlap_ratio(A, B) == vertical_overlap_ratio(A, B) == 0.5
    assert intersection_ratio(A, B) == 0.25


def test_gaps_and_relationships():
    right = (15.0, 0.0, 25.0, 10.0)
    below = (0.0, 15.0, 10.0, 25.0)
    assert gap_x(A, right) == gap_y(A, below) == 5.0
    assert not intersects(A, right) and contains((0.0, 0.0, 20.0, 20.0), A)
    assert nearby_horizontal(A, right, 5.0) and nearby_vertical(A, below, 5.0)


def test_alignment_and_row_column_helpers():
    shifted = (1.0, 2.0, 11.0, 12.0)
    same_visual_row = (30.0, 1.0, 40.0, 11.0)
    assert aligned_left(A, shifted, 1.0)
    assert aligned_right(A, shifted, 1.0)
    assert aligned_center_x(A, shifted, 1.0)
    assert same_column(A, shifted, 1.0)
    assert same_row(A, same_visual_row, 10.0)


def test_normalized_coordinates_are_page_relative():
    assert normalized_x(100.0, 200.0) == normalized_y(100.0, 200.0) == 0.5
