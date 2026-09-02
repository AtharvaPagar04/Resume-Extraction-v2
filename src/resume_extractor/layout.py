from __future__ import annotations

from dataclasses import dataclass, replace
from statistics import median


@dataclass(frozen=True)
class LayoutLine:
    text: str
    bbox: tuple[float, float, float, float]
    block_index: int
    line_index: int
    style: tuple[float, str, int] = (0.0, "", 0)
    source_ids: tuple[int, ...] = ()
    is_table: bool = False

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
        return max(0.0, self.x1 - self.x0)

    @property
    def height(self) -> float:
        return max(1.0, self.y1 - self.y0)


_BULLETS = {"•", "◦", "▪", "▫", "-", "–", "—", "*"}


def _basic(lines: list[LayoutLine]) -> list[LayoutLine]:
    return sorted(lines, key=lambda line: (line.y0, line.x0, line.block_index, line.line_index))


def _same_row(a: LayoutLine, b: LayoutLine, median_height: float) -> bool:
    overlap = max(0.0, min(a.y1, b.y1) - max(a.y0, b.y0)) / min(a.height, b.height)
    centers_close = abs((a.y0 + a.y1 - b.y0 - b.y1) / 2) <= 0.45 * max(median_height, a.height, b.height)
    return overlap >= 0.45 or centers_close


def _rows(lines: list[LayoutLine]) -> list[list[LayoutLine]]:
    if not lines:
        return []
    med = median(line.height for line in lines)
    groups: list[list[LayoutLine]] = []
    for line in _basic(lines):
        if groups and _same_row(groups[-1][0], line, med):
            groups[-1].append(line)
        else:
            groups.append([line])
    return [sorted(row, key=lambda line: (line.x0, line.block_index, line.line_index)) for row in groups]


def _pair_bullets(lines: list[LayoutLine], page_width: float) -> list[LayoutLine]:
    ordered = _basic(lines)
    med = median(line.height for line in ordered) if ordered else 1.0
    consumed: set[int] = set()
    paired: list[LayoutLine] = []
    for index, line in enumerate(ordered):
        if index in consumed:
            continue
        if line.text.strip() not in _BULLETS:
            paired.append(line)
            continue
        choices = [
            (candidate.x0 - line.x1, candidate_index, candidate)
            for candidate_index, candidate in enumerate(ordered[index + 1 :], index + 1)
            if candidate_index not in consumed
            and 0 <= candidate.x0 - line.x1 <= 0.12 * page_width
            and 0 <= candidate.y0 - line.y0 <= 1.8 * med
            and candidate.width > 0
        ]
        if not choices:
            paired.append(line)
            continue
        _, candidate_index, candidate = min(choices)
        continuation = [candidate]
        last = candidate
        for following_index, following in enumerate(ordered[candidate_index + 1 :], candidate_index + 1):
            if following_index in consumed or len(continuation) >= 5:
                continue
            if following.block_index != candidate.block_index or following.style != candidate.style:
                continue
            if following.width >= 0.68 * page_width or following.y0 < last.y0 or following.y0 - last.y1 > 1.8 * med:
                continue
            if abs(following.x0 - candidate.x0) > 0.08 * page_width or following.x0 < last.x0 - 0.01 * page_width:
                continue
            continuation.append(following)
            consumed.add(following_index)
            last = following
        merged = replace(
            line,
            text=f"{line.text.strip()} " + " ".join(item.text for item in continuation),
            bbox=(line.x0, min(line.y0, candidate.y0), max(item.x1 for item in continuation), max(line.y1, last.y1)),
            source_ids=line.source_ids + tuple(source_id for item in continuation for source_id in item.source_ids),
        )
        paired.append(merged)
        consumed.add(candidate_index)
    return paired


def _gutter(lines: list[LayoutLine], page_width: float) -> float | None:
    starts = sorted(line.x0 for line in lines)
    if len(starts) < 6:
        return None
    gap, left, right = max(((b - a, a, b) for a, b in zip(starts, starts[1:])), default=(0.0, 0.0, 0.0))
    if gap < 0.08 * page_width:
        return None
    midpoint = (left + right) / 2
    if sum(line.x0 < midpoint for line in lines) < 3 or sum(line.x0 >= midpoint for line in lines) < 3:
        return None
    return midpoint


def _independent_regions(lines: list[LayoutLine], gutter: float, page_height: float) -> bool:
    left = [line for line in lines if line.x0 < gutter]
    right = [line for line in lines if line.x0 >= gutter]
    if len(left) < 3 or len(right) < 3:
        return False
    coverage = max(line.y1 for line in right) - min(line.y0 for line in right)
    if coverage < 0.16 * page_height:
        return False
    row_groups = _rows(lines)
    paired = sum(any(line.x0 < gutter for line in row) and any(line.x0 >= gutter for line in row) for row in row_groups)
    return 1 - paired / max(1, len(row_groups)) >= 0.45 or len(right) / max(1, len(lines)) > 0.25


def _coverage_ok(original: list[LayoutLine], ordered: list[LayoutLine]) -> bool:
    source = [source_id for line in original for source_id in line.source_ids]
    emitted = [source_id for line in ordered for source_id in line.source_ids]
    return len(source) == len(emitted) and sorted(source) == sorted(emitted) and len(emitted) == len(set(emitted))


def order_lines(lines: list[LayoutLine], page_width: float, page_height: float) -> tuple[list[LayoutLine], bool]:
    """Return normalized reading order and whether source-safe fallback was used."""
    basic = _basic(lines)
    if not lines:
        return [], False
    paired = _pair_bullets(lines, page_width)
    full_width = [line for line in paired if line.width >= 0.68 * page_width]
    gutter = _gutter([line for line in paired if line not in full_width], page_width)
    if gutter is None or not _independent_regions(paired, gutter, page_height):
        ordered = [line for row in _rows(paired) for line in row]
    else:
        ordered = []
        segment: list[LayoutLine] = []
        for row in _rows(paired):
            band = [line for line in row if line.width >= 0.68 * page_width]
            if band:
                ordered.extend(_region_order(segment, gutter))
                segment = []
                ordered.extend(_basic(band))
            else:
                segment.extend(row)
        ordered.extend(_region_order(segment, gutter))
    if _coverage_ok(lines, ordered):
        return ordered, False
    return basic, True


def _region_order(lines: list[LayoutLine], gutter: float) -> list[LayoutLine]:
    left = [line for line in lines if line.x0 < gutter]
    right = [line for line in lines if line.x0 >= gutter]
    if not left or not right:
        return _basic(lines)
    left_width = max(line.x1 for line in left) - min(line.x0 for line in left)
    right_width = max(line.x1 for line in right) - min(line.x0 for line in right)
    # Main content first; equal-width columns use left-to-right order.
    return _basic(left) + _basic(right) if left_width >= right_width else _basic(right) + _basic(left)
