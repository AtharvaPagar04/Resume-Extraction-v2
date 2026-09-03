from __future__ import annotations

from dataclasses import replace
from statistics import median

from .geometry import THRESHOLDS, same_row
from .layout_foundation import LayoutLine, is_bullet_only, starts_with_bullet


def _basic(lines: list[LayoutLine]) -> list[LayoutLine]:
    return sorted(lines, key=lambda line: (line.y0, line.x0, line.block_index, line.line_index))


def _same_row(a: LayoutLine, b: LayoutLine, median_height: float) -> bool:
    return same_row(a.bbox, b.bbox, median_height)


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


def _gutter(lines: list[LayoutLine], page_width: float) -> float | None:
    starts = sorted(line.x0 for line in lines)
    if len(starts) < 6:
        return None
    gap, left, right = max(((b - a, a, b) for a, b in zip(starts, starts[1:])), default=(0.0, 0.0, 0.0))
    if gap < THRESHOLDS.gutter_width_ratio * page_width:
        return None
    midpoint = (left + right) / 2
    if sum(line.x0 < midpoint for line in lines) < 3 or sum(line.x0 >= midpoint for line in lines) < 3:
        return None
    return midpoint


def _pair_bullets(lines: list[LayoutLine], page_width: float) -> list[LayoutLine]:
    ordered = list(lines)
    if not ordered:
        return []
    med = median(line.height for line in ordered) if ordered else 1.0
    consumed: set[int] = set()
    paired: list[LayoutLine] = []

    full_width = [line for line in ordered if line.width >= THRESHOLDS.full_width_ratio * page_width]
    gutter = _gutter([line for line in ordered if line not in full_width], page_width)

    for index, line in enumerate(ordered):
        if index in consumed:
            continue

        is_detached = is_bullet_only(line.text)
        is_inline = starts_with_bullet(line.text) and not is_detached

        if not is_detached and not is_inline:
            paired.append(line)
            continue

        marker_x0 = line.x0

        if is_detached:
            choices = [
                (candidate.x0 - line.x1, candidate_index, candidate)
                for candidate_index, candidate in enumerate(ordered[index + 1 :], index + 1)
                if candidate_index not in consumed
                and not is_bullet_only(candidate.text)
                and not starts_with_bullet(candidate.text)
                and (gutter is None or (line.x0 < gutter) == (candidate.x0 < gutter))
                and 0 <= candidate.x0 - line.x1 <= THRESHOLDS.bullet_right_gap_ratio * page_width
                and (_same_row(line, candidate, med) or 0 <= candidate.y0 - line.y0 <= THRESHOLDS.continuation_vertical_gap_ratio * med)
                and candidate.width > 0
            ]
            if not choices:
                paired.append(line)
                continue
            _, candidate_index, candidate = min(choices)
            continuation = [candidate]
            consumed.add(candidate_index)
            content_x0 = candidate.x0
            last = candidate
            start_search = candidate_index + 1
        else:
            continuation = []
            content_x0 = line.x0
            last = line
            start_search = index + 1

        for following_index in range(start_search, len(ordered)):
            if following_index in consumed or len(continuation) >= 10:
                continue
            following = ordered[following_index]

            # 1. Stop immediately at next bullet boundary
            if is_bullet_only(following.text) or starts_with_bullet(following.text):
                break

            # 2. Gutter / column boundary safety
            if gutter is not None and (line.x0 < gutter) != (following.x0 < gutter):
                break

            # 3. Vertical continuity check
            if following.y0 < last.y0:
                break
            v_gap = following.y0 - last.y1
            if v_gap > THRESHOLDS.continuation_vertical_gap_ratio * med:
                break

            # 4. Horizontal alignment / indent check
            indent_delta = following.x0 - content_x0
            backtrack_limit = THRESHOLDS.continuation_backtrack_ratio * page_width
            max_indent = THRESHOLDS.continuation_indent_ratio * page_width

            if indent_delta < -backtrack_limit or indent_delta > max_indent:
                break

            # 5. Heading / font size jump check
            if following.dominant_font_size > last.dominant_font_size + 1.5:
                break

            # 6. Standalone subheading guard: colon or non-colon with style transition and list-run restart
            # Colon labels: bold category prefix ending with colon (e.g. "Business Skills:")
            is_colon_label = (
                following.ends_with_colon
                and following.bold_ratio >= THRESHOLDS.colon_label_min_bold_ratio
                and following.bold_ratio > last.bold_ratio + THRESHOLDS.colon_label_min_bold_step
            )
            # Non-colon labels: bold standalone project/section title starting a block (e.g. "Snacc Item Rating")
            is_non_colon_label = (
                not following.ends_with_colon
                and following.bold_ratio >= THRESHOLDS.heading_label_min_bold_ratio
                and following.bold_ratio > last.bold_ratio + THRESHOLDS.heading_label_min_bold_step
                and (following.line_index == 0 or following.block_index != last.block_index)
            )
            if is_colon_label or is_non_colon_label:
                has_next_bullet = False
                for lookahead_idx in range(
                    following_index + 1,
                    min(following_index + THRESHOLDS.subheading_lookahead_span, len(ordered)),
                ):
                    lookahead_line = ordered[lookahead_idx]
                    if is_bullet_only(lookahead_line.text) or starts_with_bullet(lookahead_line.text):
                        if abs(lookahead_line.x0 - marker_x0) <= THRESHOLDS.anchor_tolerance_ratio * page_width:
                            has_next_bullet = True
                        break
                    if lookahead_line.y0 - following.y1 > THRESHOLDS.continuation_vertical_gap_ratio * med:
                        break
                if has_next_bullet:
                    break

            continuation.append(following)
            consumed.add(following_index)
            last = following

        if not continuation and is_inline:
            paired.append(line)
        elif is_detached:
            merged = replace(
                line,
                reconstructed_text=f"{line.text.strip()} " + " ".join(item.text for item in continuation),
                bbox=(line.x0, min(line.y0, continuation[0].y0), max(item.x1 for item in continuation), max(line.y1, last.y1)),
                source_ids=line.source_ids + tuple(source_id for item in continuation for source_id in item.source_ids),
            )
            paired.append(merged)
        else:
            merged = replace(
                line,
                reconstructed_text=f"{line.text.strip()} " + " ".join(item.text for item in continuation),
                bbox=(line.x0, min(line.y0, line.y0), max(item.x1 for item in [line] + continuation), max(line.y1, last.y1)),
                source_ids=line.source_ids + tuple(source_id for item in continuation for source_id in item.source_ids),
            )
            paired.append(merged)

    return paired


def _independent_regions(
    lines: list[LayoutLine],
    gutter: float,
    page_height: float,
    page_width: float,
) -> bool:
    left = [line for line in lines if line.x0 < gutter]
    right = [line for line in lines if line.x0 >= gutter]
    if len(left) < THRESHOLDS.gutter_side_support or len(right) < THRESHOLDS.gutter_side_support:
        return False
    coverage = max(line.y1 for line in right) - min(line.y0 for line in right)
    if coverage < THRESHOLDS.secondary_coverage_ratio * page_height:
        return False
    right_share = len(right) / max(1, len(lines))
    if right_share <= 0.25:
        return False
    row_groups = _rows(lines)
    topology_rows = [
        row
        for row in row_groups
        if not any(line.width >= THRESHOLDS.full_width_ratio * page_width for line in row)
    ]
    n_r = sum(
        1
        for row in topology_rows
        if any(line.x0 >= gutter for line in row) and not any(line.x0 < gutter for line in row)
    )
    n_l = sum(
        1
        for row in topology_rows
        if any(line.x0 < gutter for line in row) and not any(line.x0 >= gutter for line in row)
    )
    return n_r > 0 or n_l == 0


def _coverage_ok(original: list[LayoutLine], ordered: list[LayoutLine]) -> bool:
    source = [source_id for line in original for source_id in line.source_ids]
    emitted = [source_id for line in ordered for source_id in line.source_ids]
    return len(source) == len(emitted) and sorted(source) == sorted(emitted) and len(emitted) == len(set(emitted))


def _normalize_region_segment(lines: list[LayoutLine], gutter: float) -> list[LayoutLine]:
    left = [line for line in lines if line.x0 < gutter]
    right = [line for line in lines if line.x0 >= gutter]
    if not left or not right:
        return [line for row in _rows(lines) for line in row]
    left_width = max(line.x1 for line in left) - min(line.x0 for line in left)
    right_width = max(line.x1 for line in right) - min(line.x0 for line in right)
    left_ordered = [line for row in _rows(left) for line in row]
    right_ordered = [line for row in _rows(right) for line in row]
    return left_ordered + right_ordered if left_width >= right_width else right_ordered + left_ordered


def _pre_pair_normalize(lines: list[LayoutLine], page_width: float, page_height: float) -> list[LayoutLine]:
    if not lines:
        return []
    full_width = [line for line in lines if line.width >= THRESHOLDS.full_width_ratio * page_width]
    gutter_lines = [line for line in lines if line not in full_width]
    gutter = _gutter(gutter_lines, page_width)
    if gutter is None or not _independent_regions(lines, gutter, page_height, page_width):
        return [line for row in _rows(lines) for line in row]
    ordered: list[LayoutLine] = []
    segment: list[LayoutLine] = []
    for row in _rows(lines):
        band = [line for line in row if line.width >= THRESHOLDS.full_width_ratio * page_width]
        if band:
            ordered.extend(_normalize_region_segment(segment, gutter))
            segment = []
            ordered.extend(sorted(row, key=lambda line: (line.x0, line.block_index, line.line_index)))
        else:
            segment.extend(row)
    ordered.extend(_normalize_region_segment(segment, gutter))
    return ordered


def order_lines(lines: list[LayoutLine], page_width: float, page_height: float) -> tuple[list[LayoutLine], bool]:
    """Return normalized reading order and whether source-safe fallback was used."""
    basic = _basic(lines)
    if not lines:
        return [], False
    normalized = _pre_pair_normalize(lines, page_width, page_height)
    paired = _pair_bullets(normalized, page_width)
    full_width = [line for line in paired if line.width >= THRESHOLDS.full_width_ratio * page_width]
    gutter_lines = [line for line in paired if line not in full_width]
    gutter = _gutter(gutter_lines, page_width)
    if gutter is None or not _independent_regions(paired, gutter, page_height, page_width):
        ordered = [line for row in _rows(paired) for line in row]
    else:
        ordered = []
        segment: list[LayoutLine] = []
        for row in _rows(paired):
            band = [line for line in row if line.width >= THRESHOLDS.full_width_ratio * page_width]
            if band:
                ordered.extend(_region_order(segment, gutter))
                segment = []
                ordered.extend(_basic(band))
                remainder = [line for line in row if line not in band]
                if remainder:
                    ordered.extend(_basic(remainder))
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
