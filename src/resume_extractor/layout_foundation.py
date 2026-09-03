"""Internal common geometry model shared by ordering, tables, and link mapping.

Nothing in this module is part of the RAW JSON contract.  It retains one page's
source geometry until canonical text has been emitted, then callers discard it.
"""

from __future__ import annotations

import contextlib
import io
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median
from typing import Any, Iterable

import pymupdf as fitz

from .geometry import BBox, THRESHOLDS, bbox_height, bbox_width, center_x, center_y, intersection_ratio, normalized_x, normalized_y, same_row, union_bbox
from .reconstruction import normalize_text, reconstruct_line_from_spans


_BULLET_MARKERS = frozenset({"•", "●", "▪", "▫", "◦", "‣", "∙", "-", "–", "—", "*", "∗"})
_BOLD_NAMES = ("bold", "semibold", "semi-bold", "demi", "black", "heavy")
_ITALIC_NAMES = ("italic", "oblique", "slanted")


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def uppercase_ratio(text: str) -> float:
    letters = [character for character in text if character.isalpha()]
    return _ratio(sum(character.isupper() for character in letters), len(letters))


def is_bullet_only(text: str) -> bool:
    return text.strip() in _BULLET_MARKERS


def starts_with_bullet(text: str) -> bool:
    stripped = text.lstrip()
    return bool(stripped) and stripped[0] in _BULLET_MARKERS and (len(stripped) == 1 or stripped[1].isspace())


def detect_style(font_name: str, flags: int) -> tuple[bool, bool]:
    """Use documented PyMuPDF flags first, then generalized font-name evidence."""
    font = font_name.casefold()
    return bool(flags & 16) or any(marker in font for marker in _BOLD_NAMES), bool(flags & 2) or any(marker in font for marker in _ITALIC_NAMES)


@dataclass(frozen=True)
class Span:
    raw_text: str
    normalized_text: str
    bbox: BBox
    font_name: str
    font_size: float
    flags: int
    bold: bool
    italic: bool
    uppercase_ratio: float
    block_index: int
    line_index: int
    span_index: int
    source_order: tuple[int, int, int]

    @property
    def x0(self) -> float:
        return self.bbox[0]

    @property
    def y0(self) -> float:
        return self.bbox[1]

    @property
    def x1(self) -> float:
        return self.bbox[2]

    @property
    def y1(self) -> float:
        return self.bbox[3]


@dataclass(frozen=True)
class LayoutLine:
    raw_text: str
    reconstructed_text: str
    bbox: BBox
    block_index: int
    line_index: int
    source_order: tuple[int, int]
    source_ids: tuple[int, ...]
    spans: tuple[Span, ...] = ()
    dominant_font_size: float = 0.0
    median_font_size: float = 0.0
    dominant_font_name: str = ""
    dominant_flags: int = 0
    bold_ratio: float = 0.0
    italic_ratio: float = 0.0
    uppercase_ratio: float = 0.0
    alpha_character_count: int = 0
    token_count: int = 0
    text_length: int = 0
    ends_with_colon: bool = False
    is_bullet_only: bool = False
    starts_with_bullet: bool = False
    is_table: bool = False

    @property
    def text(self) -> str:
        return self.reconstructed_text

    @property
    def x0(self) -> float:
        return self.bbox[0]

    @property
    def y0(self) -> float:
        return self.bbox[1]

    @property
    def x1(self) -> float:
        return self.bbox[2]

    @property
    def y1(self) -> float:
        return self.bbox[3]

    @property
    def width(self) -> float:
        return bbox_width(self.bbox)

    @property
    def height(self) -> float:
        return max(1.0, bbox_height(self.bbox))

    @property
    def left_edge(self) -> float:
        return self.x0

    @property
    def right_edge(self) -> float:
        return self.x1

    @property
    def center_x(self) -> float:
        return center_x(self.bbox)

    @property
    def center_y(self) -> float:
        return center_y(self.bbox)

    @property
    def style(self) -> tuple[float, str, int]:
        return self.dominant_font_size, self.dominant_font_name, self.dominant_flags

    def is_full_width_candidate(self, page_width: float) -> bool:
        return self.width >= THRESHOLDS.full_width_ratio * page_width

    @property
    def is_short_label_candidate(self) -> bool:
        return self.token_count <= 4 and self.text_length <= 40 and not self.starts_with_bullet


@dataclass(frozen=True)
class LayoutBlock:
    block_index: int
    bbox: BBox
    lines: tuple[LayoutLine, ...]
    source_order: int

    @property
    def text(self) -> str:
        return "\n".join(line.text for line in self.lines)

    @property
    def width(self) -> float:
        return bbox_width(self.bbox)

    @property
    def height(self) -> float:
        return bbox_height(self.bbox)


@dataclass(frozen=True)
class DrawingElement:
    kind: str
    bbox: BBox
    orientation: str | None
    length: float
    thickness: float
    source_order: int

    @property
    def x0(self) -> float:
        return self.bbox[0]

    @property
    def y0(self) -> float:
        return self.bbox[1]

    @property
    def x1(self) -> float:
        return self.bbox[2]

    @property
    def y1(self) -> float:
        return self.bbox[3]


@dataclass(frozen=True)
class LayoutHyperlink:
    uri: str
    page_number: int
    bbox: BBox
    source_order: int


@dataclass(frozen=True)
class LogicalRow:
    members: tuple[LayoutLine, ...]
    bbox: BBox
    y_center: float

    @property
    def x_ordered_members(self) -> tuple[LayoutLine, ...]:
        return self.members


@dataclass(frozen=True)
class AnchorCluster:
    x: float
    normalized_x: float
    support: int
    members: tuple[LayoutLine, ...]


@dataclass(frozen=True)
class GutterCandidate:
    x0: float
    x1: float
    center: float
    left_support: int
    right_support: int
    vertical_coverage: float


@dataclass(frozen=True)
class LayoutRegion:
    region_id: int
    bbox: BBox
    member_lines: tuple[LayoutLine, ...]
    width_ratio: float
    vertical_coverage: float
    source_order_min: tuple[int, int]
    source_order_max: tuple[int, int]


@dataclass(frozen=True)
class TableCandidate:
    bbox: BBox
    source: str
    evidence_count: int
    row_guides: tuple[float, ...] = ()
    column_guides: tuple[float, ...] = ()
    native_table: Any = field(default=None, repr=False, compare=False)


@dataclass(frozen=True)
class SourceAccounting:
    missing: tuple[tuple[int, int, int], ...]
    duplicated: tuple[tuple[int, int, int], ...]

    @property
    def valid(self) -> bool:
        return not self.missing and not self.duplicated


@dataclass(frozen=True)
class PageLayout:
    page_number: int
    width: float
    height: float
    blocks: tuple[LayoutBlock, ...]
    lines: tuple[LayoutLine, ...]
    spans: tuple[Span, ...]
    drawings: tuple[DrawingElement, ...]
    hyperlinks: tuple[LayoutHyperlink, ...]
    rows: tuple[LogicalRow, ...]
    anchor_clusters: tuple[AnchorCluster, ...]
    gutters: tuple[GutterCandidate, ...]
    regions: tuple[LayoutRegion, ...]
    table_candidates: tuple[TableCandidate, ...]
    median_line_height: float
    median_font_size: float
    median_line_width: float
    median_x_gap: float
    text_bbox: BBox | None
    left_margin: float | None
    right_margin: float | None
    warnings: tuple[str, ...] = ()

    def find_overlapping_spans(self, bbox: BBox, minimum_ratio: float = 0.01) -> tuple[Span, ...]:
        return tuple(span for span in self.spans if intersection_ratio(span.bbox, bbox) >= minimum_ratio)

    def find_overlapping_lines(self, bbox: BBox, minimum_ratio: float = 0.01) -> tuple[LayoutLine, ...]:
        return tuple(line for line in self.lines if intersection_ratio(line.bbox, bbox) >= minimum_ratio)

    def nearest_line(self, bbox: BBox) -> LayoutLine | None:
        return min(self.lines, key=lambda line: (abs(line.center_y - center_y(bbox)), abs(line.center_x - center_x(bbox)), line.source_order), default=None)


def _median(values: Iterable[float]) -> float:
    values = list(values)
    return median(values) if values else 0.0


def _line_from_dict(block_index: int, line_index: int, raw_line: dict[str, Any], source_id: int) -> tuple[LayoutLine, tuple[Span, ...]]:
    spans = tuple(
        Span(
            raw_text=str(raw_span.get("text", "")),
            normalized_text=normalize_text(str(raw_span.get("text", ""))),
            bbox=tuple(float(value) for value in raw_span.get("bbox", (0, 0, 0, 0))),
            font_name=str(raw_span.get("font", "")),
            font_size=float(raw_span.get("size", 0.0) or 0.0),
            flags=int(raw_span.get("flags", 0) or 0),
            bold=detect_style(str(raw_span.get("font", "")), int(raw_span.get("flags", 0) or 0))[0],
            italic=detect_style(str(raw_span.get("font", "")), int(raw_span.get("flags", 0) or 0))[1],
            uppercase_ratio=uppercase_ratio(str(raw_span.get("text", ""))),
            block_index=block_index,
            line_index=line_index,
            span_index=span_index,
            source_order=(block_index, line_index, span_index),
        )
        for span_index, raw_span in enumerate(raw_line.get("spans", ()))
    )
    reconstructed = reconstruct_line_from_spans(list(raw_line.get("spans", ())))
    raw_text = "".join(span.raw_text for span in spans)
    sizes = [span.font_size for span in spans if span.font_size]
    weights = [max(1, len(span.normalized_text)) for span in spans]
    dominant = max(range(len(spans)), key=lambda index: weights[index], default=None)
    alpha = sum(character.isalpha() for character in reconstructed)
    return LayoutLine(
        raw_text=raw_text,
        reconstructed_text=reconstructed,
        bbox=tuple(float(value) for value in raw_line.get("bbox", (0, 0, 0, 0))),
        block_index=block_index,
        line_index=line_index,
        source_order=(block_index, line_index),
        source_ids=(source_id,),
        spans=spans,
        dominant_font_size=spans[dominant].font_size if dominant is not None else 0.0,
        median_font_size=_median(sizes),
        dominant_font_name=spans[dominant].font_name if dominant is not None else "",
        dominant_flags=spans[dominant].flags if dominant is not None else 0,
        bold_ratio=_ratio(sum(weight for span, weight in zip(spans, weights) if span.bold), sum(weights)),
        italic_ratio=_ratio(sum(weight for span, weight in zip(spans, weights) if span.italic), sum(weights)),
        uppercase_ratio=uppercase_ratio(reconstructed),
        alpha_character_count=alpha,
        token_count=len(re.findall(r"\S+", reconstructed)),
        text_length=len(reconstructed),
        ends_with_colon=reconstructed.endswith(":"),
        is_bullet_only=is_bullet_only(reconstructed),
        starts_with_bullet=starts_with_bullet(reconstructed),
    ), spans


def group_rows(lines: Iterable[LayoutLine], median_line_height: float | None = None) -> tuple[LogicalRow, ...]:
    ordered = sorted(lines, key=lambda line: (line.y0, line.x0, line.source_order))
    if not ordered:
        return ()
    med = median_line_height or _median(line.height for line in ordered)
    groups: list[list[LayoutLine]] = []
    for line in ordered:
        if groups and same_row(groups[-1][0].bbox, line.bbox, med):
            groups[-1].append(line)
        else:
            groups.append([line])
    return tuple(
        LogicalRow(tuple(sorted(group, key=lambda line: (line.x0, line.source_order))), union_bbox([line.bbox for line in group]) or (0, 0, 0, 0), center_y(union_bbox([line.bbox for line in group]) or (0, 0, 0, 0)))
        for group in groups
    )


def cluster_x_anchors(lines: Iterable[LayoutLine], page_width: float, tolerance_ratio: float = THRESHOLDS.anchor_tolerance_ratio) -> tuple[AnchorCluster, ...]:
    ordered = sorted(lines, key=lambda line: (line.x0, line.source_order))
    groups: list[list[LayoutLine]] = []
    tolerance = tolerance_ratio * page_width
    for line in ordered:
        if groups and abs(line.x0 - sum(item.x0 for item in groups[-1]) / len(groups[-1])) <= tolerance:
            groups[-1].append(line)
        else:
            groups.append([line])
    return tuple(AnchorCluster(sum(line.x0 for line in group) / len(group), normalized_x(sum(line.x0 for line in group) / len(group), page_width), len(group), tuple(group)) for group in groups)


def find_gutter_candidates(lines: Iterable[LayoutLine], anchors: Iterable[AnchorCluster], page_width: float, page_height: float) -> tuple[GutterCandidate, ...]:
    all_lines = tuple(lines)
    ordered = sorted(anchors, key=lambda anchor: anchor.x)
    candidates: list[GutterCandidate] = []
    for left, right in zip(ordered, ordered[1:]):
        if right.x - left.x < THRESHOLDS.gutter_width_ratio * page_width:
            continue
        midpoint = (left.x + right.x) / 2
        left_lines = [line for line in all_lines if line.x0 < midpoint]
        right_lines = [line for line in all_lines if line.x0 >= midpoint]
        if len(left_lines) < THRESHOLDS.gutter_side_support or len(right_lines) < THRESHOLDS.gutter_side_support:
            continue
        coverage = min(max(line.y1 for line in left_lines) - min(line.y0 for line in left_lines), max(line.y1 for line in right_lines) - min(line.y0 for line in right_lines)) / max(1.0, page_height)
        candidates.append(GutterCandidate(left.x, right.x, midpoint, len(left_lines), len(right_lines), coverage))
    return tuple(candidates)


def derive_regions(lines: Iterable[LayoutLine], gutter: GutterCandidate, page_width: float, page_height: float) -> tuple[LayoutRegion, ...]:
    sides = ([line for line in lines if line.x0 < gutter.center], [line for line in lines if line.x0 >= gutter.center])
    regions: list[LayoutRegion] = []
    for region_id, members in enumerate(sides):
        if not members:
            continue
        bbox = union_bbox([line.bbox for line in members]) or (0, 0, 0, 0)
        regions.append(LayoutRegion(region_id, bbox, tuple(members), bbox_width(bbox) / max(1.0, page_width), bbox_height(bbox) / max(1.0, page_height), min(line.source_order for line in members), max(line.source_order for line in members)))
    return tuple(regions)


def validate_span_accounting(spans: Iterable[Span], lines: Iterable[LayoutLine]) -> SourceAccounting:
    expected = Counter(span.source_order for span in spans if span.normalized_text)
    emitted = Counter(span.source_order for line in lines for span in line.spans if span.normalized_text)
    missing = tuple(sorted(order for order, count in (expected - emitted).items() for _ in range(count)))
    duplicated = tuple(sorted(order for order, count in (emitted - expected).items() for _ in range(count)))
    return SourceAccounting(missing, duplicated)


def validate_line_accounting(lines: Iterable[LayoutLine], groups: Iterable[LogicalRow | LayoutRegion]) -> tuple[tuple[int, int], ...]:
    groups = tuple(groups)
    expected = Counter(line.source_order for line in lines)
    emitted = Counter(line.source_order for group in groups for line in (group.members if isinstance(group, LogicalRow) else group.member_lines))
    return tuple(sorted(order for order, count in (expected - emitted).items() for _ in range(count))) + tuple(sorted(order for order, count in (emitted - expected).items() for _ in range(count)))


def _drawings(page: fitz.Page, page_width: float, page_height: float) -> tuple[DrawingElement, ...]:
    try:
        raw_drawings = page.get_drawings()
    except Exception:
        return ()
    output: list[DrawingElement] = []
    for source_order, drawing in enumerate(raw_drawings):
        raw_bbox = drawing.get("rect")
        if raw_bbox is None:
            continue
        bbox = tuple(float(value) for value in raw_bbox)
        width, height = bbox_width(bbox), bbox_height(bbox)
        thickness = float(drawing.get("width", 0.0) or 0.0)
        items = drawing.get("items", ())
        rectangle = any(item and item[0] == "re" for item in items)
        horizontal = width >= THRESHOLDS.min_horizontal_rule_ratio * page_width and height <= max(thickness * 2, THRESHOLDS.thin_rule_ratio * page_height)
        vertical = height >= THRESHOLDS.min_vertical_rule_ratio * page_height and width <= max(thickness * 2, THRESHOLDS.thin_rule_ratio * page_width)
        kind = "rectangle" if rectangle else "horizontal_rule" if horizontal else "vertical_rule" if vertical else "drawing"
        orientation = "horizontal" if horizontal else "vertical" if vertical else None
        output.append(DrawingElement(kind, bbox, orientation, width if horizontal else height if vertical else max(width, height), thickness, source_order))
    return tuple(output)


def _table_candidates(page: fitz.Page, drawings: tuple[DrawingElement, ...]) -> tuple[tuple[TableCandidate, ...], tuple[str, ...]]:
    warnings: list[str] = []
    candidates: list[TableCandidate] = []
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            found = page.find_tables()
        for table in getattr(found, "tables", found):
            candidates.append(TableCandidate(tuple(float(value) for value in table.bbox), "pymupdf", 1, native_table=table))
    except Exception:
        warnings.append("TABLE_EXTRACTION_FAILED")
    horizontal = [drawing for drawing in drawings if drawing.kind == "horizontal_rule"]
    vertical = [drawing for drawing in drawings if drawing.kind == "vertical_rule"]
    if len(horizontal) >= 2 and len(vertical) >= 2:
        bbox = union_bbox([drawing.bbox for drawing in [*horizontal, *vertical]])
        if bbox:
            candidates.append(TableCandidate(bbox, "rules", len(horizontal) + len(vertical), tuple(sorted(drawing.y0 for drawing in horizontal)), tuple(sorted(drawing.x0 for drawing in vertical))))
    return tuple(candidates), tuple(warnings)


def _hyperlinks(page: fitz.Page, page_number: int) -> tuple[LayoutHyperlink, ...]:
    try:
        links = page.get_links()
    except Exception:
        return ()
    output: list[LayoutHyperlink] = []
    for source_order, link in enumerate(links):
        uri, bbox = link.get("uri"), link.get("from")
        if uri and bbox is not None:
            output.append(LayoutHyperlink(str(uri), page_number, tuple(float(value) for value in bbox), source_order))
    return tuple(output)


def build_page_layout(page: fitz.Page, page_number: int) -> PageLayout:
    """Extract one immutable, source-accounted page geometry model."""
    width, height = float(page.rect.width), float(page.rect.height)
    data = page.get_text("dict", flags=fitz.TEXT_PRESERVE_LIGATURES | fitz.TEXT_PRESERVE_WHITESPACE)
    blocks: list[LayoutBlock] = []
    lines: list[LayoutLine] = []
    spans: list[Span] = []
    source_id = 0
    for block_index, raw_block in enumerate(data.get("blocks", ())):
        if raw_block.get("type") != 0:
            continue
        block_lines: list[LayoutLine] = []
        for line_index, raw_line in enumerate(raw_block.get("lines", ())):
            line, line_spans = _line_from_dict(block_index, line_index, raw_line, source_id)
            spans.extend(line_spans)
            if not line.text:
                continue
            source_id += 1
            block_lines.append(line)
            lines.append(line)
        if block_lines:
            blocks.append(LayoutBlock(block_index, tuple(float(value) for value in raw_block.get("bbox", (0, 0, 0, 0))), tuple(block_lines), block_index))
    median_line_height = _median(line.height for line in lines)
    rows = group_rows(lines, median_line_height)
    anchors = cluster_x_anchors(lines, width)
    gutters = find_gutter_candidates(lines, anchors, width, height)
    regions = derive_regions(lines, gutters[0], width, height) if gutters else ()
    drawings = _drawings(page, width, height)
    tables, warnings = _table_candidates(page, drawings)
    x_gaps = [right.x0 - left.x1 for row in rows for left, right in zip(row.members, row.members[1:]) if right.x0 > left.x1]
    text_bbox = union_bbox([line.bbox for line in lines])
    return PageLayout(
        page_number, width, height, tuple(blocks), tuple(lines), tuple(spans), drawings, _hyperlinks(page, page_number), rows, anchors, gutters, regions, tables,
        median_line_height, _median(span.font_size for span in spans if span.font_size), _median(line.width for line in lines), _median(x_gaps), text_bbox,
        min((line.x0 for line in lines), default=None), width - max(line.x1 for line in lines) if lines else None, warnings,
    )


def format_page_layout(layout: PageLayout) -> str:
    lines = [f"page={layout.page_number} size={layout.width:.1f}x{layout.height:.1f} lines={len(layout.lines)} spans={len(layout.spans)}"]
    lines.extend(f"{line.source_order} {line.bbox!r} size={line.dominant_font_size:.1f} bold={line.bold_ratio:.2f} {line.text[:80]!r}" for line in layout.lines)
    return "\n".join(lines)


def dump_layout_debug(layout: PageLayout, path: str | Path) -> Path:
    """Explicit-only developer artifact; it is never included in RAW JSON."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "page": layout.page_number,
        "size": [layout.width, layout.height],
        "warnings": list(layout.warnings),
        "lines": [{"source_order": line.source_order, "bbox": line.bbox, "text": line.text, "font_size": line.dominant_font_size, "bold_ratio": line.bold_ratio} for line in layout.lines],
        "rows": [{"bbox": row.bbox, "members": [line.source_order for line in row.members]} for row in layout.rows],
        "drawings": [{"kind": drawing.kind, "bbox": drawing.bbox} for drawing in layout.drawings],
        "tables": [{"bbox": table.bbox, "source": table.source, "evidence_count": table.evidence_count} for table in layout.table_candidates],
        "gutters": [{"x0": gutter.x0, "x1": gutter.x1, "coverage": gutter.vertical_coverage} for gutter in layout.gutters],
        "regions": [{"bbox": region.bbox, "members": [line.source_order for line in region.member_lines]} for region in layout.regions],
    }
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return target
