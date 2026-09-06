from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable


_CID_ARTIFACT = re.compile(r"\(cid:\d+\)")


@dataclass(frozen=True, slots=True)
class ReconstructedLineProjection:
    """Canonical reconstruction projection containing visible text and aligned character provenance."""

    text: str
    char_map: tuple[Any, ...]


@dataclass(frozen=True)
class SpanFragment:
    text: str
    x0: float
    x1: float
    size: float = 0.0
    chars: tuple[Any, ...] = ()


def normalize_text(text: str) -> str:
    """Normalize renderer-only whitespace and legacy Symbol-font bullets without changing words or punctuation."""
    text = text.replace("\u00a0", " ").replace("\uf0b7", "•").replace("\u200b", "")
    text = _CID_ARTIFACT.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def reconstruct_line_with_provenance(spans: Iterable[Any]) -> ReconstructedLineProjection:
    """Rebuild one physical line and compute 1-to-1 character provenance in a single canonical pass."""
    fragments: list[SpanFragment] = []
    for s in spans:
        if isinstance(s, dict):
            text = str(s.get("text", ""))
            bbox = tuple(float(v) for v in s.get("bbox", (0, 0, 0, 0)))
            size = float(s.get("size", 0.0) or 0.0)
            chars = tuple(s.get("chars", ()))
        else:
            text = getattr(s, "raw_text", getattr(s, "text", ""))
            bbox = getattr(s, "bbox", (0, 0, 0, 0))
            size = getattr(s, "font_size", getattr(s, "size", 0.0))
            chars = tuple(getattr(s, "chars", ()))
        fragments.append(
            SpanFragment(
                text=str(text),
                x0=float(bbox[0]),
                x1=float(bbox[2]),
                size=float(size or 0.0),
                chars=chars,
            )
        )

    fragments.sort(key=lambda fragment: fragment.x0)

    parts_items: list[tuple[str, Any]] = []
    previous: SpanFragment | None = None
    pending_whitespace = False

    for fragment in fragments:
        raw = fragment.text
        if not raw:
            continue
        chars = fragment.chars
        has_chars = bool(chars and len(chars) == len(raw))

        span_items: list[tuple[str, Any]] = []
        i = 0
        raw_len = len(raw)
        while i < raw_len:
            m = _CID_ARTIFACT.match(raw, i)
            if m:
                i = m.end()
                continue
            ch = raw[i]
            c_ev = chars[i] if has_chars else None
            i += 1
            if ch == "\u200b":
                continue
            elif ch == "\u00a0":
                span_items.append((" ", c_ev))
            elif ch == "\uf0b7":
                span_items.append(("•", c_ev))
            else:
                span_items.append((ch, c_ev))

        source = "".join(ch for ch, _ in span_items)
        if not source:
            continue
        if source.isspace():
            pending_whitespace = True
            continue

        lead = 0
        while lead < len(span_items) and span_items[lead][0].isspace():
            lead += 1
        trail = len(span_items)
        while trail > lead and span_items[trail - 1][0].isspace():
            trail -= 1

        cleaned_items = span_items[lead:trail]
        if not cleaned_items:
            pending_whitespace = pending_whitespace or bool(source)
            continue

        if previous is not None:
            gap = fragment.x0 - previous.x1
            geometric_space = gap > 1.0 and gap / max(previous.size, fragment.size, 1.0) > 0.18
            if pending_whitespace or source[:1].isspace() or previous.text[-1:].isspace() or geometric_space:
                parts_items.append((" ", None))

        parts_items.extend(cleaned_items)
        previous = fragment
        pending_whitespace = False

    # Normalize whitespace: strip leading/trailing and collapse consecutive spaces
    start_idx = 0
    while start_idx < len(parts_items) and parts_items[start_idx][0].isspace():
        start_idx += 1
    end_idx = len(parts_items)
    while end_idx > start_idx and parts_items[end_idx - 1][0].isspace():
        end_idx -= 1

    trimmed = parts_items[start_idx:end_idx]
    final_chars: list[str] = []
    final_map: list[Any] = []
    in_space = False

    for ch, c_ev in trimmed:
        if ch.isspace():
            if not in_space:
                final_chars.append(" ")
                final_map.append(None)
                in_space = True
        else:
            in_space = False
            final_chars.append(ch)
            final_map.append(c_ev)

    text_out = "".join(final_chars)
    map_out = tuple(final_map)
    assert len(text_out) == len(map_out)
    return ReconstructedLineProjection(text=text_out, char_map=map_out)


def reconstruct_line_from_spans(spans: list[dict[str, Any]]) -> str:
    """Rebuild one physical line using source whitespace before geometric gaps."""
    return reconstruct_line_with_provenance(spans).text
