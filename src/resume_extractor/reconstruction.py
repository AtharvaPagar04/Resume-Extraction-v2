from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


_CID_ARTIFACT = re.compile(r"\(cid:\d+\)")


@dataclass(frozen=True)
class SpanFragment:
    text: str
    x0: float
    x1: float
    size: float = 0.0


def normalize_text(text: str) -> str:
    """Normalize renderer-only whitespace without changing words or punctuation."""
    text = text.replace("\u00a0", " ")
    text = _CID_ARTIFACT.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def reconstruct_line_from_spans(spans: list[dict[str, Any]]) -> str:
    """Rebuild one physical line using source whitespace before geometric gaps."""
    fragments = sorted(
        (
            SpanFragment(
                text=str(span.get("text", "")),
                x0=float(span.get("bbox", (0, 0, 0, 0))[0]),
                x1=float(span.get("bbox", (0, 0, 0, 0))[2]),
                size=float(span.get("size", 0.0) or 0.0),
            )
            for span in spans
        ),
        key=lambda fragment: fragment.x0,
    )
    parts: list[str] = []
    previous: SpanFragment | None = None
    pending_whitespace = False
    for fragment in fragments:
        source = _CID_ARTIFACT.sub("", fragment.text).replace("\u00a0", " ")
        if not source:
            continue
        if source.isspace():
            pending_whitespace = True
            continue
        cleaned = source.strip()
        if not cleaned:
            pending_whitespace = pending_whitespace or bool(source)
            continue
        if previous is not None:
            gap = fragment.x0 - previous.x1
            geometric_space = gap > 1.0 and gap / max(previous.size, fragment.size, 1.0) > 0.18
            if pending_whitespace or source[:1].isspace() or previous.text[-1:].isspace() or geometric_space:
                parts.append(" ")
        parts.append(cleaned)
        previous = fragment
        pending_whitespace = False
    return normalize_text("".join(parts))
