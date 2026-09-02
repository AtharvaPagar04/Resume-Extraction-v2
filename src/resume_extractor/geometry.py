"""Shared PDF geometry primitives.

All rectangles use PyMuPDF's `(x0, y0, x1, y1)` page coordinate convention.
Overlap ratios divide by the smaller participating dimension, not union area:
they answer whether the smaller item overlaps the other along one axis.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, TypeAlias


BBox: TypeAlias = tuple[float, float, float, float]


class HasBBox(Protocol):
    bbox: BBox


RectLike: TypeAlias = BBox | HasBBox


@dataclass(frozen=True)
class LayoutThresholds:
    """Page-relative layout constants proven by the extraction handoff."""

    full_width_ratio: float = 0.68  # line width / page width
    same_row_overlap_ratio: float = 0.45  # overlap height / smaller line height
    same_row_center_height_ratio: float = 0.45  # center distance / relevant line height
    gutter_width_ratio: float = 0.08  # anchor gap / page width
    gutter_side_support: int = 3  # lines on each gutter side
    bullet_right_gap_ratio: float = 0.12  # bullet-to-content horizontal gap / page width
    continuation_indent_ratio: float = 0.08  # continuation x delta / page width
    continuation_backtrack_ratio: float = 0.01  # allowed x backtrack / page width
    continuation_vertical_gap_ratio: float = 1.8  # vertical gap / median line height
    secondary_coverage_ratio: float = 0.16  # region vertical coverage / page height
    unpaired_row_ratio: float = 0.45  # rows not spanning both candidate regions
    anchor_tolerance_ratio: float = 0.02  # x positions / page width
    min_horizontal_rule_ratio: float = 0.15  # rule length / page width
    min_vertical_rule_ratio: float = 0.15  # rule length / page height
    thin_rule_ratio: float = 0.01  # rule thickness / perpendicular page dimension


THRESHOLDS = LayoutThresholds()


def rect(value: RectLike) -> BBox:
    if isinstance(value, tuple):
        return value
    if hasattr(value, "bbox"):
        return value.bbox
    return float(value.x0), float(value.y0), float(value.x1), float(value.y1)


def bbox_width(value: RectLike) -> float:
    box = rect(value)
    return max(0.0, box[2] - box[0])


def bbox_height(value: RectLike) -> float:
    box = rect(value)
    return max(0.0, box[3] - box[1])


def bbox_area(value: RectLike) -> float:
    return bbox_width(value) * bbox_height(value)


def center_x(value: RectLike) -> float:
    box = rect(value)
    return (box[0] + box[2]) / 2


def center_y(value: RectLike) -> float:
    box = rect(value)
    return (box[1] + box[3]) / 2


def intersection_bbox(a: RectLike, b: RectLike) -> BBox:
    left, right = rect(a), rect(b)
    return max(left[0], right[0]), max(left[1], right[1]), min(left[2], right[2]), min(left[3], right[3])


def intersection_area(a: RectLike, b: RectLike) -> float:
    return bbox_area(intersection_bbox(a, b))


def horizontal_overlap(a: RectLike, b: RectLike) -> float:
    return max(0.0, min(rect(a)[2], rect(b)[2]) - max(rect(a)[0], rect(b)[0]))


def vertical_overlap(a: RectLike, b: RectLike) -> float:
    return max(0.0, min(rect(a)[3], rect(b)[3]) - max(rect(a)[1], rect(b)[1]))


def horizontal_overlap_ratio(a: RectLike, b: RectLike) -> float:
    return horizontal_overlap(a, b) / max(1e-9, min(bbox_width(a), bbox_width(b)))


def vertical_overlap_ratio(a: RectLike, b: RectLike) -> float:
    return vertical_overlap(a, b) / max(1e-9, min(bbox_height(a), bbox_height(b)))


def intersection_ratio(a: RectLike, b: RectLike) -> float:
    """Intersection area divided by the smaller rectangle's area."""
    return intersection_area(a, b) / max(1e-9, min(bbox_area(a), bbox_area(b)))


def gap_x(a: RectLike, b: RectLike) -> float:
    left, right = sorted((rect(a), rect(b)), key=lambda box: box[0])
    return max(0.0, right[0] - left[2])


def gap_y(a: RectLike, b: RectLike) -> float:
    top, bottom = sorted((rect(a), rect(b)), key=lambda box: box[1])
    return max(0.0, bottom[1] - top[3])


def contains(outer: RectLike, inner: RectLike) -> bool:
    a, b = rect(outer), rect(inner)
    return a[0] <= b[0] and a[1] <= b[1] and a[2] >= b[2] and a[3] >= b[3]


def intersects(a: RectLike, b: RectLike) -> bool:
    return intersection_area(a, b) > 0


def aligned_left(a: RectLike, b: RectLike, tolerance: float) -> bool:
    return abs(rect(a)[0] - rect(b)[0]) <= tolerance


def aligned_right(a: RectLike, b: RectLike, tolerance: float) -> bool:
    return abs(rect(a)[2] - rect(b)[2]) <= tolerance


def aligned_center_x(a: RectLike, b: RectLike, tolerance: float) -> bool:
    return abs(center_x(a) - center_x(b)) <= tolerance


def same_row(a: RectLike, b: RectLike, median_height: float, thresholds: LayoutThresholds = THRESHOLDS) -> bool:
    return vertical_overlap_ratio(a, b) >= thresholds.same_row_overlap_ratio or abs(center_y(a) - center_y(b)) <= thresholds.same_row_center_height_ratio * max(median_height, bbox_height(a), bbox_height(b))


def same_column(a: RectLike, b: RectLike, tolerance: float) -> bool:
    return aligned_left(a, b, tolerance) or aligned_center_x(a, b, tolerance)


def nearby_vertical(a: RectLike, b: RectLike, tolerance: float) -> bool:
    return gap_y(a, b) <= tolerance


def nearby_horizontal(a: RectLike, b: RectLike, tolerance: float) -> bool:
    return gap_x(a, b) <= tolerance


def normalized_x(value: float, page_width: float) -> float:
    return value / max(1e-9, page_width)


def normalized_y(value: float, page_height: float) -> float:
    return value / max(1e-9, page_height)


def union_bbox(values: list[RectLike]) -> BBox | None:
    if not values:
        return None
    boxes = [rect(value) for value in values]
    return min(box[0] for box in boxes), min(box[1] for box in boxes), max(box[2] for box in boxes), max(box[3] for box in boxes)
