"""Source-accounted table reconstruction over the common layout foundation."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import json
from pathlib import Path
from statistics import median
from typing import Iterable

from .geometry import BBox, THRESHOLDS, bbox_area, bbox_height, bbox_width, center_x, center_y, contains, gap_y, horizontal_overlap_ratio, intersection_area, intersects, union_bbox, vertical_overlap
from .layout_foundation import LayoutHyperlink, LayoutLine, LogicalRow, PageLayout, Span, TableCandidate
from .reconstruction import normalize_text, reconstruct_line_from_spans


@dataclass(frozen=True)
class TableCell:
    row_index: int
    column_index: int
    bbox: BBox
    source_spans: tuple[Span, ...] = ()
    source_lines: tuple[LayoutLine, ...] = ()
    text: str = ""
    hyperlinks: tuple[LayoutHyperlink, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not self.text


@dataclass(frozen=True)
class TableRow:
    row_index: int
    cells: tuple[TableCell, ...]
    bbox: BBox
    is_header: bool = False


@dataclass(frozen=True)
class LogicalColumn:
    """A text-bearing raw-column view used for validation and serialization."""

    raw_column_indices: tuple[int, ...]
    bbox: BBox
    populated_row_count: int
    nonempty_cell_count: int
    source_span_count: int


@dataclass(frozen=True)
class _VisualBand:
    x: float
    members: tuple[LayoutLine, ...]
    supporting_rows: frozenset[int]


@dataclass(frozen=True)
class ReconstructedTable:
    bbox: BBox
    rows: tuple[TableRow, ...]
    source_lines: tuple[LayoutLine, ...]
    source_spans: tuple[Span, ...]
    column_count: int
    reconstruction_method: str
    text: str
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class TableReconstructionResult:
    tables: tuple[ReconstructedTable, ...]
    consumed_line_orders: frozenset[tuple[int, int]]
    warnings: tuple[str, ...]
    rejected_candidates: int


def _unique_guides(values: Iterable[float], tolerance: float = 1.5) -> tuple[float, ...]:
    groups: list[list[float]] = []
    for value in sorted(values):
        if groups and abs(value - sum(groups[-1]) / len(groups[-1])) <= tolerance:
            groups[-1].append(value)
        else:
            groups.append([value])
    return tuple(sum(group) / len(group) for group in groups)


def _bbox_overlap(a: BBox, b: BBox) -> float:
    return intersection_area(a, b) / max(1e-9, min(bbox_width(a) * bbox_height(a), bbox_width(b) * bbox_height(b)))


def _candidate_quality(candidate: TableCandidate) -> tuple[int, int, int]:
    # Explainable deterministic preference: native cells, then guides, then evidence.
    return (2 if candidate.native_table is not None else 1 if candidate.source == "rules" else 0, len(candidate.row_guides) + len(candidate.column_guides), candidate.evidence_count)


def _core_rows(layout: PageLayout) -> list[tuple[int, LogicalRow]]:
    return [
        (index, row)
        for index, row in enumerate(layout.rows)
        if len(row.members) >= 2 and not any(line.is_full_width_candidate(layout.width) for line in row.members)
    ]


def _compatible_core_rows(previous: LogicalRow, current: LogicalRow, tolerance: float) -> bool:
    previous_edges = min(line.x0 for line in previous.members), max(line.x0 for line in previous.members)
    current_edges = min(line.x0 for line in current.members), max(line.x0 for line in current.members)
    return abs(previous_edges[0] - current_edges[0]) <= tolerance and abs(previous_edges[1] - current_edges[1]) <= tolerance


def _core_runs(layout: PageLayout, core_rows: list[tuple[int, LogicalRow]]) -> list[list[tuple[int, LogicalRow]]]:
    if not core_rows:
        return []
    max_gap = max(1.0, layout.median_line_height * 4)
    tolerance = layout.width * THRESHOLDS.anchor_tolerance_ratio
    runs: list[list[tuple[int, LogicalRow]]] = []
    for item in core_rows:
        if runs and item[1].bbox[1] - runs[-1][-1][1].bbox[3] <= max_gap and _compatible_core_rows(runs[-1][-1][1], item[1], tolerance):
            runs[-1].append(item)
        else:
            runs.append([item])
    return runs


def _major_bands(run: list[tuple[int, LogicalRow]], tolerance: float) -> tuple[_VisualBand, ...]:
    lines = [(row_index, line) for row_index, row in run for line in row.members]
    guides = _unique_guides((line.x0 for _, line in lines), tolerance)
    bands: list[_VisualBand] = []
    for guide in guides:
        members = tuple(line for _, line in lines if abs(line.x0 - guide) <= tolerance)
        supporting_rows = frozenset(row_index for row_index, line in lines if abs(line.x0 - guide) <= tolerance)
        if len(supporting_rows) >= THRESHOLDS.aligned_band_min_row_support:
            bands.append(_VisualBand(guide, members, supporting_rows))
    return tuple(bands)


def _row_occupancy(row: LogicalRow, bands: tuple[_VisualBand, ...], tolerance: float) -> dict[int, tuple[LayoutLine, ...]]:
    occupied: dict[int, list[LayoutLine]] = defaultdict(list)
    for line in row.members:
        matches = sorted((abs(line.x0 - band.x), index) for index, band in enumerate(bands) if abs(line.x0 - band.x) <= tolerance)
        if len(matches) == 1 or len(matches) > 1 and matches[0][0] < matches[1][0]:
            occupied[matches[0][1]].append(line)
    return {index: tuple(lines) for index, lines in occupied.items()}


def _is_cross_region_bridge(layout: PageLayout, run: list[tuple[int, LogicalRow]]) -> bool:
    """Reject only a core run that bridges two persistent foundation gutter sides."""
    bbox = union_bbox([line.bbox for _, row in run for line in row.members])
    if bbox is None:
        return False
    for gutter in layout.gutters:
        if not (bbox[0] < gutter.center < bbox[2]) or gutter.vertical_coverage < THRESHOLDS.secondary_coverage_ratio:
            continue
        crosses_every_row = all(
            any(line.x0 < gutter.center for line in row.members)
            and any(line.x0 >= gutter.center for line in row.members)
            for _, row in run
        )
        if not crosses_every_row:
            continue
        sides = (
            [line for line in layout.lines if line.x0 < gutter.center],
            [line for line in layout.lines if line.x0 >= gutter.center],
        )
        if all(any(line.y1 < bbox[1] for line in side) and any(line.y0 > bbox[3] for line in side) for side in sides):
            return True
    return False


def _attach_continuations(layout: PageLayout, run: list[tuple[int, LogicalRow]], bands: tuple[_VisualBand, ...], tolerance: float) -> list[tuple[int, LogicalRow]]:
    max_gap = max(1.0, layout.median_line_height * 4)
    core_indexes = {index for index, _ in run}
    core_rows = tuple(row for _, row in run)
    attached = list(run)
    for row_index, row in enumerate(layout.rows):
        if row_index in core_indexes or len(row.members) != 1 or row.members[0].is_full_width_candidate(layout.width):
            continue
        occupancy = _row_occupancy(row, bands, tolerance)
        if len(occupancy) != 1 or min(gap_y(row.bbox, core.bbox) for core in core_rows) > max_gap:
            continue
        band_index, = occupancy
        line = row.members[0]
        same_band_lines = [member for _, core in run for member in _row_occupancy(core, bands, tolerance).get(band_index, ())]
        if any(line.block_index == member.block_index for member in same_band_lines):
            attached.append((row_index, row))
    return sorted(attached, key=lambda item: (item[1].bbox[1], item[1].bbox[0], item[0]))


def _aligned_candidates(layout: PageLayout) -> tuple[TableCandidate, ...]:
    """Find high-precision borderless grids from recurring visual bands."""
    candidates: list[TableCandidate] = []
    tolerance = layout.width * THRESHOLDS.anchor_tolerance_ratio
    for run in _core_runs(layout, _core_rows(layout)):
        if len(run) < 3:
            continue
        bands = _major_bands(run, tolerance)
        if len(bands) < 2:
            continue
        occupancy = [_row_occupancy(row, bands, tolerance) for _, row in run]
        if len(bands) == 2:
            compact_label_value = bands[1].x - bands[0].x >= 0.20 * layout.width and all(
                0 in occupied and 1 in occupied
                and min(occupied[0], key=lambda line: line.x0).width <= 0.20 * layout.width
                and min(occupied[0], key=lambda line: line.x0).is_short_label_candidate
                for occupied in occupancy
            )
            if not compact_label_value:
                continue
        elif any(len(occupied) < len(bands) for occupied in occupancy):
            continue
        if _is_cross_region_bridge(layout, run):
            continue
        rows = _attach_continuations(layout, run, bands, tolerance)
        members = [line for _, row in rows for line in row.members]
        bbox = union_bbox([line.bbox for line in members])
        if bbox:
            candidates.append(TableCandidate(bbox, "aligned", len(rows) + len(bands), tuple(row.y_center for _, row in rows), tuple(band.x for band in bands)))
    return tuple(candidates)


def _reconcile(candidates: Iterable[TableCandidate]) -> tuple[TableCandidate, ...]:
    selected: list[TableCandidate] = []
    for candidate in sorted(candidates, key=lambda item: (_candidate_quality(item), -item.bbox[1]), reverse=True):
        overlapping = [existing for existing in selected if _bbox_overlap(candidate.bbox, existing.bbox) >= 0.5]
        if not overlapping:
            selected.append(candidate)
    return tuple(sorted(selected, key=lambda item: (item.bbox[1], item.bbox[0], item.source)))


def _native_cells(candidate: TableCandidate) -> tuple[tuple[BBox | None, ...], ...]:
    table = candidate.native_table
    if table is None or getattr(table, "row_count", 0) < 2 or getattr(table, "col_count", 0) < 2:
        return ()
    return tuple(tuple(tuple(float(value) for value in cell) if cell is not None else None for cell in row.cells) for row in table.rows)


def _guided_cells(candidate: TableCandidate) -> tuple[tuple[BBox | None, ...], ...]:
    if candidate.source == "aligned":
        x_starts = _unique_guides(candidate.column_guides)
        y_centers = _unique_guides(candidate.row_guides)
        if len(x_starts) < 2 or len(y_centers) < 2:
            return ()
        x = (*x_starts, candidate.bbox[2])
        y = (candidate.bbox[1], *(left + (right - left) / 2 for left, right in zip(y_centers, y_centers[1:])), candidate.bbox[3])
        return tuple(tuple((x[column], y[row], x[column + 1], y[row + 1]) for column in range(len(x) - 1)) for row in range(len(y) - 1))
    x = _unique_guides((*candidate.column_guides, candidate.bbox[0], candidate.bbox[2]))
    y = _unique_guides((*candidate.row_guides, candidate.bbox[1], candidate.bbox[3]))
    if len(x) < 3 or len(y) < 3:
        return ()
    return tuple(tuple((x[column], y[row], x[column + 1], y[row + 1]) for column in range(len(x) - 1)) for row in range(len(y) - 1))


def _line_spans_in_bbox(layout: PageLayout, bbox: BBox) -> tuple[LayoutLine, ...]:
    selected: list[LayoutLine] = []
    for line in layout.lines:
        nonempty = [span for span in line.spans if span.normalized_text]
        if nonempty and all(_center_inside(span.bbox, bbox) for span in nonempty):
            selected.append(line)
    return tuple(selected)


def _center_inside(inner: BBox, outer: BBox) -> bool:
    return outer[0] <= center_x(inner) <= outer[2] and outer[1] <= center_y(inner) <= outer[3]


def _assign_span(span: Span, cells: list[tuple[int, int, BBox]]) -> tuple[int, int] | None:
    full = [(row, column, bbox) for row, column, bbox in cells if contains(bbox, span.bbox)]
    if len(full) == 1:
        return full[0][:2]
    intersections = sorted(((intersection_area(span.bbox, bbox), row, column, bbox) for row, column, bbox in cells), reverse=True)
    if intersections and intersections[0][0] > 0 and (len(intersections) == 1 or intersections[0][0] > intersections[1][0]):
        return intersections[0][1:3]
    centered = [(row, column) for row, column, bbox in cells if _center_inside(span.bbox, bbox)]
    return centered[0] if len(centered) == 1 else None


def _cell_text(spans: Iterable[Span], lines: Iterable[LayoutLine]) -> str:
    wanted = {span.source_order for span in spans}
    reconstructed: list[str] = []
    for line in sorted(lines, key=lambda item: item.source_order):
        fragments = [span for span in line.spans if span.source_order in wanted]
        if not fragments:
            continue
        reconstructed.append(reconstruct_line_from_spans([{"text": span.raw_text, "bbox": span.bbox, "size": span.font_size} for span in fragments]))
    return normalize_text(" ".join(part for part in reconstructed if part))


def _header_row(cells: tuple[TableCell, ...], body_rows: tuple[tuple[TableCell, ...], ...]) -> bool:
    if not cells or not any(cell.text for cell in cells):
        return False
    first_style = _style_weight(cells)
    body_style = median([_style_weight(row) for row in body_rows if any(cell.text for cell in row)] or [first_style])
    return first_style > body_style + 0.1


def _style_weight(cells: Iterable[TableCell]) -> float:
    spans = [span for cell in cells for span in cell.source_spans]
    if not spans:
        return 0.0
    return sum((1.0 if span.bold else 0.0) + span.font_size / 1000 for span in spans) / len(spans)


def _trim_boundary_empty_rows(rows: list[TableRow]) -> list[TableRow]:
    start, end = 0, len(rows)
    while start < end and not any(cell.text for cell in rows[start].cells):
        start += 1
    while end > start and not any(cell.text for cell in rows[end - 1].cells):
        end -= 1
    return rows[start:end]


def _logical_columns(rows: list[TableRow]) -> tuple[LogicalColumn, ...]:
    """Cluster active raw slots by recurring visual x-range, never raw index alone."""
    evidence: list[tuple[int, BBox, list[TableCell]]] = []
    for index in range(max((len(row.cells) for row in rows), default=0)):
        cells = [row.cells[index] for row in rows if index < len(row.cells)]
        nonempty = [cell for cell in cells if cell.text]
        if nonempty:
            bbox = union_bbox([cell.bbox for cell in cells if cell.bbox != (0, 0, 0, 0)])
            if bbox:
                evidence.append((index, bbox, cells))

    clusters: list[list[tuple[int, BBox, list[TableCell]]]] = []
    for item in sorted(evidence, key=lambda value: (center_x(value[1]), value[1][0], value[0])):
        matches = [
            (horizontal_overlap_ratio(item[1], union_bbox([member[1] for member in cluster]) or item[1]), cluster_index)
            for cluster_index, cluster in enumerate(clusters)
        ]
        matches = sorted((match for match in matches if match[0] >= THRESHOLDS.table_column_overlap_ratio), reverse=True)
        # An equally strong match would make ownership of the raw slot unclear.
        if not matches or (len(matches) > 1 and matches[0][0] - matches[1][0] < 0.02):
            clusters.append([item])
        else:
            clusters[matches[0][1]].append(item)

    columns: list[LogicalColumn] = []
    for cluster in sorted(clusters, key=lambda value: (min(member[1][0] for member in value), min(member[0] for member in value))):
        raw_indices = tuple(sorted(member[0] for member in cluster))
        cells = [cell for _, _, members in cluster for cell in members]
        nonempty = [cell for cell in cells if cell.text]
        columns.append(LogicalColumn(
            raw_indices,
            union_bbox([cell.bbox for cell in cells if cell.bbox != (0, 0, 0, 0)]) or (0, 0, 0, 0),
            len({cell.row_index for cell in nonempty}),
            len(nonempty),
            sum(len(cell.source_spans) for cell in nonempty),
        ))
    return tuple(columns)


def _combine_cells(row_index: int, column_index: int, cells: Iterable[TableCell], fallback_bbox: BBox) -> TableCell:
    """Keep raw ownership while presenting one derived visual cell."""
    members = tuple(cells)
    spans = tuple(sorted({span.source_order: span for cell in members for span in cell.source_spans}.values(), key=lambda span: span.source_order))
    lines = tuple(sorted({line.source_order: line for cell in members for line in cell.source_lines}.values(), key=lambda line: line.source_order))
    links = tuple({(link.uri, link.bbox): link for cell in members for link in cell.hyperlinks}.values())
    bbox = union_bbox([cell.bbox for cell in members if cell.bbox != (0, 0, 0, 0)]) or fallback_bbox
    text = _cell_text(spans, lines) if spans else normalize_text(" ".join(cell.text for cell in members if cell.text))
    return TableCell(row_index, column_index, bbox, spans, lines, text, links)


def _logical_rows(rows: list[TableRow], columns: tuple[LogicalColumn, ...]) -> tuple[TableRow, ...]:
    logical: list[TableRow] = []
    for row in rows:
        cells = tuple(
            _combine_cells(row.row_index, logical_index, (row.cells[index] for index in column.raw_column_indices if index < len(row.cells)), column.bbox)
            for logical_index, column in enumerate(columns)
        )
        logical.append(TableRow(row.row_index, cells, row.bbox, row.is_header))
    return tuple(logical)


def _rule_separates(layout: PageLayout, candidate: TableCandidate, first: TableCell, second: TableCell) -> bool:
    top, bottom = sorted((center_y(first.bbox), center_y(second.bbox)))
    if top == bottom:
        return False
    return any(
        drawing.kind == "horizontal_rule"
        and top < center_y(drawing.bbox) < bottom
        and bbox_width(drawing.bbox) >= 0.4 * bbox_width(candidate.bbox)
        and horizontal_overlap_ratio(drawing.bbox, candidate.bbox) >= 0.4
        for drawing in layout.drawings
    )


def _continuation_basics(first: TableCell, second: TableCell, layout: PageLayout, candidate: TableCandidate, median_height: float) -> bool:
    if first.column_index != second.column_index or _rule_separates(layout, candidate, first, second):
        return False
    shared_blocks = {line.block_index for line in first.source_lines} & {line.block_index for line in second.source_lines}
    return bool(shared_blocks) and gap_y(first.bbox, second.bbox) <= THRESHOLDS.table_row_continuation_gap_ratio * max(1.0, median_height)


def _same_column_continuation(first: TableCell, second: TableCell, cells: tuple[TableCell, ...], layout: PageLayout, candidate: TableCandidate, median_height: float) -> bool:
    if not _continuation_basics(first, second, layout, candidate, median_height):
        return False
    if any(
        cell.column_index != first.column_index
        and vertical_overlap(cell.bbox, first.bbox) > 0
        and vertical_overlap(cell.bbox, second.bbox) > 0
        for cell in cells
    ):
        return True
    # Both sides may wrap in synchronised native rows. Require a matching
    # continuation in another logical column before reconnecting the bands.
    companions = [cell for cell in cells if cell.column_index != first.column_index]
    return any(
        _continuation_basics(left, right, layout, candidate, median_height)
        for index, left in enumerate(companions)
        for right in companions[:index]
    )


def _reconstruct_visual_rows(layout: PageLayout, candidate: TableCandidate, rows: tuple[TableRow, ...]) -> tuple[TableRow, ...]:
    """Reconcile native row slots only when visual cell geometry proves a shared band."""
    nodes = [(row_index, cell_index, cell) for row_index, row in enumerate(rows) for cell_index, cell in enumerate(row.cells) if cell.text]
    if not nodes:
        return rows
    parents = list(range(len(nodes)))

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def join(first: int, second: int) -> None:
        first, second = find(first), find(second)
        if first != second:
            parents[second] = first

    median_height = median([bbox_height(cell.bbox) for _, _, cell in nodes])
    cells = tuple(cell for _, _, cell in nodes)
    for first_index, (_, _, first) in enumerate(nodes):
        for second_index, (_, _, second) in enumerate(nodes[:first_index]):
            if _rule_separates(layout, candidate, first, second):
                continue
            if first.column_index != second.column_index and vertical_overlap(first.bbox, second.bbox) > 0:
                join(first_index, second_index)
            elif _same_column_continuation(first, second, cells, layout, candidate, median_height):
                join(first_index, second_index)

    components: dict[int, list[tuple[int, int, TableCell]]] = defaultdict(list)
    for index, node in enumerate(nodes):
        components[find(index)].append(node)
    output: list[TableRow] = []
    for members in components.values():
        row_index = min(rows[index].row_index for index, _, _ in members)
        cells_by_column = defaultdict(list)
        for _, _, cell in members:
            cells_by_column[cell.column_index].append(cell)
        row_bbox = union_bbox([cell.bbox for _, _, cell in members]) or candidate.bbox
        cells = tuple(
            _combine_cells(row_index, column_index, cells_by_column[column_index], (rows[0].cells[column_index].bbox[0], row_bbox[1], rows[0].cells[column_index].bbox[2], row_bbox[3]))
            if column_index in cells_by_column
            else TableCell(row_index, column_index, (rows[0].cells[column_index].bbox[0], row_bbox[1], rows[0].cells[column_index].bbox[2], row_bbox[3]))
            for column_index in range(len(rows[0].cells))
        )
        output.append(TableRow(row_index, cells, row_bbox))
    # Preserve intentionally empty internal rows; boundary blanks were already trimmed.
    output.extend(row for row in rows if not any(cell.text for cell in row.cells))
    return tuple(sorted(output, key=lambda row: (row.bbox[1], row.bbox[0], row.row_index)))


def _has_grid_evidence(layout: PageLayout, candidate: TableCandidate) -> bool:
    if candidate.source == "rules":
        return True
    horizontal = sum(drawing.kind == "horizontal_rule" and intersects(drawing.bbox, candidate.bbox) and drawing.length >= 0.4 * bbox_width(candidate.bbox) for drawing in layout.drawings)
    vertical = sum(drawing.kind == "vertical_rule" and intersects(drawing.bbox, candidate.bbox) and drawing.length >= 0.4 * bbox_height(candidate.bbox) for drawing in layout.drawings)
    return horizontal >= 2 and vertical >= 2


def _is_layout_artifact(layout: PageLayout, candidate: TableCandidate, rows: tuple[TableRow, ...], columns: tuple[LogicalColumn, ...]) -> bool:
    """Reject only page-scale/side-scale candidates without repeated row pairing."""
    if len(rows) < 2 or len(columns) < 2:
        return False
    paired_rows = sum(sum(bool(cell.text) for cell in row.cells) >= 2 for row in rows)
    correspondence = paired_rows / len(rows)
    repeated_columns = sum(column.populated_row_count >= 2 for column in columns)
    weak_column_recurrence = repeated_columns < 2 or repeated_columns / len(columns) < 0.5
    width_ratio = bbox_width(candidate.bbox) / max(1.0, layout.width)
    height_ratio = bbox_height(candidate.bbox) / max(1.0, layout.height)
    area_ratio = bbox_area(candidate.bbox) / max(1.0, layout.width * layout.height)
    page_scale = height_ratio >= THRESHOLDS.table_pseudo_tall_ratio and (width_ratio >= THRESHOLDS.table_pseudo_wide_ratio or area_ratio >= THRESHOLDS.table_pseudo_area_ratio)
    sidebar_scale = height_ratio >= THRESHOLDS.table_pseudo_tall_ratio and width_ratio <= 1 - THRESHOLDS.table_pseudo_wide_ratio
    return (page_scale or sidebar_scale) and correspondence < THRESHOLDS.table_row_correspondence_ratio and (weak_column_recurrence or not _has_grid_evidence(layout, candidate))


def _serialize(rows: tuple[TableRow, ...]) -> str:
    header_index = 0 if rows and rows[0].is_header else None
    headers = [cell.text for cell in rows[0].cells] if header_index is not None else []
    output: list[str] = []
    for row in rows[1 if header_index is not None else 0 :]:
        values = [cell.text for cell in row.cells]
        if not any(values):
            continue
        if header_index is not None:
            parts = [f"{header}: {value}" if header else value for header, value in zip(headers, values) if value]
            output.append(" | ".join(parts))
        elif len(values) == 2 and values[0] and values[1]:
            output.append(f"{values[0]}: {values[1]}")
        else:
            output.append(" | ".join(value for value in values if value))
    return "\n".join(output)


def _serialization_covers_source(rows: tuple[TableRow, ...], text: str) -> bool:
    """Developer check: ownership can be exact while a serializer omits a cell."""
    return all(cell.text in text for row in rows for cell in row.cells if cell.text)


def _reconstruct_candidate(layout: PageLayout, candidate: TableCandidate) -> ReconstructedTable | None:
    cell_grid = _native_cells(candidate) or _guided_cells(candidate)
    if len(cell_grid) < 2 or len(cell_grid[0]) < 2:
        return None
    source_lines = _line_spans_in_bbox(layout, candidate.bbox)
    source_spans = tuple(span for line in source_lines for span in line.spans if span.normalized_text)
    if len(source_spans) < 4:
        return None
    positions = [(row, column, bbox) for row, row_cells in enumerate(cell_grid) for column, bbox in enumerate(row_cells) if bbox is not None]
    owned: dict[tuple[int, int], list[Span]] = defaultdict(list)
    for span in source_spans:
        owner = _assign_span(span, positions)
        if owner is None:
            return None
        owned[owner].append(span)
    if sum(len(value) for value in owned.values()) != len(source_spans):
        return None
    line_by_span = {span.source_order: line for line in source_lines for span in line.spans}
    rows: list[TableRow] = []
    for row_index, row_cells in enumerate(cell_grid):
        cells: list[TableCell] = []
        for column_index, bbox in enumerate(row_cells):
            if bbox is None:
                cells.append(TableCell(row_index, column_index, (0, 0, 0, 0)))
                continue
            spans = tuple(sorted(owned[(row_index, column_index)], key=lambda span: (span.y0, span.x0, span.source_order)))
            lines = tuple(sorted({line_by_span[span.source_order] for span in spans}, key=lambda line: line.source_order))
            links = tuple(link for link in layout.hyperlinks if _center_inside(link.bbox, bbox))
            cells.append(TableCell(row_index, column_index, bbox, spans, lines, _cell_text(spans, lines), links))
        row_bbox = union_bbox([cell.bbox for cell in cells if cell.bbox != (0, 0, 0, 0)]) or candidate.bbox
        rows.append(TableRow(row_index, tuple(cells), row_bbox))
    rows = _trim_boundary_empty_rows(rows)
    columns = _logical_columns(rows)
    logical_rows = _logical_rows(rows, columns)
    logical_rows = _reconstruct_visual_rows(layout, candidate, logical_rows)
    if len(logical_rows) < 2 or len(columns) < 2 or _is_layout_artifact(layout, candidate, logical_rows, columns):
        return None
    # Density is based on text-bearing logical columns, never empty PyMuPDF padding.
    minimum_populated_cells = max(2, (len(columns) + 1) // 2)
    if sum(sum(bool(cell.text) for cell in row.cells) >= minimum_populated_cells for row in logical_rows) < 2:
        return None
    header = _header_row(logical_rows[0].cells, tuple(row.cells for row in logical_rows[1:]))
    if header:
        logical_rows = (TableRow(logical_rows[0].row_index, logical_rows[0].cells, logical_rows[0].bbox, True), *logical_rows[1:])
    text = _serialize(logical_rows)
    if not text:
        return None
    method = "pymupdf" if candidate.native_table is not None else candidate.source
    warnings = () if _serialization_covers_source(logical_rows, text) else ("SERIALIZATION_COVERAGE_GAP",)
    return ReconstructedTable(candidate.bbox, logical_rows, source_lines, source_spans, len(columns), method, text, warnings)


def reconstruct_tables(layout: PageLayout) -> TableReconstructionResult:
    """Accept only fully source-accounted tables; rejected candidates stay in normal flow."""
    candidates = _reconcile([*layout.table_candidates, *_aligned_candidates(layout)])
    tables: list[ReconstructedTable] = []
    consumed: set[tuple[int, int]] = set()
    rejected = 0
    for candidate in candidates:
        if any(_bbox_overlap(candidate.bbox, table.bbox) >= 0.5 for table in tables):
            continue
        table = _reconstruct_candidate(layout, candidate)
        if table is None or any(line.source_order in consumed for line in table.source_lines):
            rejected += 1
            continue
        expected = Counter(span.source_order for span in table.source_spans)
        emitted = Counter(span.source_order for row in table.rows for cell in row.cells for span in cell.source_spans)
        if expected != emitted:
            rejected += 1
            continue
        tables.append(table)
        consumed.update(line.source_order for line in table.source_lines)
    return TableReconstructionResult(tuple(tables), frozenset(consumed), (), rejected)


def table_debug_payload(result: TableReconstructionResult) -> dict:
    return {
        "tables": [
            {
                "bbox": table.bbox,
                "method": table.reconstruction_method,
                "warnings": list(table.warnings),
                "rows": [[{"bbox": cell.bbox, "text": cell.text, "spans": [span.source_order for span in cell.source_spans]} for cell in row.cells] for row in table.rows],
            }
            for table in result.tables
        ],
        "rejected_candidates": result.rejected_candidates,
        "warnings": list(result.warnings),
    }


def dump_table_debug(result: TableReconstructionResult, path: str | Path) -> Path:
    """Explicit developer artifact for cells, guides, and ownership; never RAW."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(table_debug_payload(result), ensure_ascii=False, indent=2), encoding="utf-8")
    return target
