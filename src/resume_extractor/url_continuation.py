"""Source-aware URL continuation candidate builder and lexical helpers.

Contact Phase 1A: Reconstructs wrapped URL candidates using either
shared hyperlink annotation evidence or demonstrably incomplete lexical
constructs (incomplete percent-escapes and recognized HTML entities) with
minimal-prefix consumption. Authoritative text and layout lines are never mutated.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Sequence

from .layout import LayoutLine
from .layout_foundation import CharacterEvidence, LayoutHyperlink, Span, span_text_from_source_chars
from .primitives import _EMAIL, _URL, normalize_url


SUPPORTED_HTML_ENTITIES: frozenset[str] = frozenset({"amp", "lt", "gt", "quot", "apos"})
_BULLET_RE = re.compile(r"^[•◦∗\*\u2022\u25e6\u2217\u25aa\u25ab\u2043\u25cf\u25cb\u25a0\u25a1●|~]")
_NEW_URL_RE = re.compile(r"^(?:https?://|www\.|mailto:|linkedin\.com/|github\.com/)", re.IGNORECASE)


@dataclass(frozen=True)
class DerivedUrlCandidate:
    """Internal provenance container for a derived URL continuation candidate."""

    value: str
    page_number: int
    evidence_kind: str
    prefix_source_ids: tuple[int, ...]
    continuation_source_ids: tuple[int, ...]
    prefix_line_index: int
    continuation_line_index: int
    prefix_local_start: int
    prefix_local_end: int
    continuation_local_start: int
    continuation_local_end: int
    prefix_document_start: int
    prefix_document_end: int
    consumed_prefix_length: int = 0
    prefix_token: str = ""
    suffix_token: str = ""


def percent_completion_prefix(fragment: str, next_fragment: str) -> tuple[str, str] | None:
    """Return the minimal hex completion prefix and remainder if fragment ends in an incomplete percent-escape."""
    frag = fragment.rstrip()
    nxt = next_fragment.lstrip()
    if frag.endswith("%"):
        match = re.match(r"^([0-9a-fA-F]{2})", nxt)
        if match:
            return match.group(1), nxt[2:]
        return None
    pct_match = re.search(r"%([0-9a-fA-F])$", frag)
    if pct_match:
        match = re.match(r"^([0-9a-fA-F])", nxt)
        if match:
            return match.group(1), nxt[1:]
        return None
    return None


def entity_completion_prefix(fragment: str, next_fragment: str) -> tuple[str, str] | None:
    """Return the minimal entity completion prefix and remainder if fragment ends in an incomplete HTML entity."""
    frag = fragment.rstrip()
    nxt = next_fragment.lstrip()
    entity_match = re.search(r"&([a-zA-Z0-9]{1,5})$", frag)
    if not entity_match:
        return None
    prefix = entity_match.group(1).lower()
    comp_match = re.match(r"^([a-zA-Z0-9]{0,5});", nxt)
    if not comp_match:
        return None
    candidate_entity = prefix + comp_match.group(1).lower()
    if candidate_entity not in SUPPORTED_HTML_ENTITIES:
        return None
    completion_prefix = comp_match.group(0)
    return completion_prefix, nxt[len(completion_prefix):]


def source_chars_for_line_fragment(
    line: LayoutLine,
    fragment_start: int,
    fragment_end: int,
) -> tuple[CharacterEvidence, ...] | None:
    """Map a candidate fragment [fragment_start, fragment_end) in line.text to source CharacterEvidence.

    Returns a tuple of CharacterEvidence objects representing the exact participating characters.
    If the fragment cannot be mapped, contains synthetic characters, or fails projection verification,
    returns None (fail closed).
    """
    if fragment_start >= fragment_end:
        return None
    if fragment_start < 0 or fragment_end > len(line.text):
        return None
    if not line.char_map or len(line.char_map) != len(line.text):
        return None

    frag_chars = line.char_map[fragment_start:fragment_end]
    if any(c is None for c in frag_chars):
        return None

    frag_chars_tuple = tuple(frag_chars)  # type: ignore[arg-type]
    if span_text_from_source_chars(frag_chars_tuple) != line.text[fragment_start:fragment_end]:
        return None

    return frag_chars_tuple


def character_hyperlink_uri(
    char: CharacterEvidence,
    hyperlinks: Sequence[LayoutHyperlink],
) -> str | None:
    """Determine the unique normalized hyperlink URI owning a single source character.

    Ownership predicate: character center inside hyperlink bbox (zero tolerance).
        cx = (char.bbox[0] + char.bbox[2]) / 2.0
        cy = (char.bbox[1] + char.bbox[3]) / 2.0
        h.bbox[0] <= cx <= h.bbox[2] and h.bbox[1] <= cy <= h.bbox[3]

    Rules:
    - 0 owning hyperlinks -> returns None (unowned).
    - Multiple hyperlinks all normalizing to the same URI -> returns that URI.
    - Multiple hyperlinks normalizing to different URIs -> returns None (ambiguous / conflict).
    """
    cx = (char.bbox[0] + char.bbox[2]) / 2.0
    cy = (char.bbox[1] + char.bbox[3]) / 2.0
    matched_uris: set[str] = set()

    for h in hyperlinks:
        if h.bbox[0] <= cx <= h.bbox[2] and h.bbox[1] <= cy <= h.bbox[3]:
            norm = normalize_url(h.uri)
            if norm:
                matched_uris.add(norm)

    if len(matched_uris) == 1:
        return next(iter(matched_uris))
    return None


def fragment_hyperlink_uri(
    chars: Sequence[CharacterEvidence],
    hyperlinks: Sequence[LayoutHyperlink],
) -> str | None:
    """Determine the single unique normalized hyperlink URI owning all characters in a fragment.

    Rules:
    - Fragment must contain at least 1 character.
    - Every participating character must be owned.
    - Every participating character must resolve uniquely to the same normalized URI.
    - Otherwise returns None (fail closed).
    """
    if not chars:
        return None

    owner_uri: str | None = None
    for c in chars:
        u = character_hyperlink_uri(c, hyperlinks)
        if u is None:
            return None
        if owner_uri is None:
            owner_uri = u
        elif owner_uri != u:
            return None

    return owner_uri


def build_derived_url_candidates(
    lines: Sequence[LayoutLine],
    hyperlinks: Sequence[LayoutHyperlink],
    page_number: int = 1,
    page_document_start: int = 0,
) -> tuple[list[DerivedUrlCandidate], set[str]]:
    """Derive continuation URL candidates page-locally from layout lines and source evidence.

    Returns:
        (derived_candidates, consumed_prefixes)
        where derived_candidates carry exact source-scoped prefix/suffix provenance.
    """
    derived: list[DerivedUrlCandidate] = []
    consumed_prefixes: set[str] = set()

    if len(lines) < 2:
        return derived, consumed_prefixes

    line_doc_starts: list[int] = []
    cur_offset = page_document_start
    for line in lines:
        line_doc_starts.append(cur_offset)
        cur_offset += len(line.text) + 1

    for i in range(len(lines) - 1):
        line1 = lines[i]
        line2 = lines[i + 1]
        line1_doc_start = line_doc_starts[i]

        matches = list(_URL.finditer(line1.text))
        if not matches:
            continue

        last_match = matches[-1]
        if line1.text[last_match.end():].strip():
            continue

        prefix_local_start = last_match.start()
        prefix_local_end = last_match.end()
        prefix_document_start = line1_doc_start + prefix_local_start
        prefix_document_end = line1_doc_start + prefix_local_end
        url_token = last_match.group(0)

        line2_text = line2.text.lstrip()
        if not line2_text:
            continue
        token2 = line2_text.split()[0]
        token2_start = line2.text.find(token2)
        token2_end = token2_start + len(token2)

        # Negative guards on Line 2
        if _BULLET_RE.match(token2) or _NEW_URL_RE.match(token2) or _EMAIL.fullmatch(token2):
            continue

        chars1 = source_chars_for_line_fragment(line1, prefix_local_start, prefix_local_end)
        uri1 = fragment_hyperlink_uri(chars1, hyperlinks) if chars1 else None

        chars2 = source_chars_for_line_fragment(line2, token2_start, token2_end)
        uri2 = fragment_hyperlink_uri(chars2, hyperlinks) if chars2 else None

        # Channel 1: Shared Hyperlink Evidence
        if uri1 and uri2 and uri1 == uri2:
            joined = url_token + token2
            derived.append(
                DerivedUrlCandidate(
                    value=joined,
                    page_number=page_number,
                    evidence_kind="shared_annotation_continuation",
                    prefix_source_ids=line1.source_ids,
                    continuation_source_ids=line2.source_ids,
                    prefix_line_index=line1.line_index,
                    continuation_line_index=line2.line_index,
                    prefix_local_start=prefix_local_start,
                    prefix_local_end=prefix_local_end,
                    continuation_local_start=token2_start,
                    continuation_local_end=token2_end,
                    prefix_document_start=prefix_document_start,
                    prefix_document_end=prefix_document_end,
                    consumed_prefix_length=len(url_token),
                    prefix_token=url_token,
                    suffix_token=token2,
                )
            )
            consumed_prefixes.add(url_token)
            continue

        # Channel 2: Incomplete Percent Escape
        pct_res = percent_completion_prefix(url_token, token2)
        if pct_res:
            hex_prefix, _ = pct_res
            joined = url_token + hex_prefix
            derived.append(
                DerivedUrlCandidate(
                    value=joined,
                    page_number=page_number,
                    evidence_kind="percent_completion",
                    prefix_source_ids=line1.source_ids,
                    continuation_source_ids=line2.source_ids,
                    prefix_line_index=line1.line_index,
                    continuation_line_index=line2.line_index,
                    prefix_local_start=prefix_local_start,
                    prefix_local_end=prefix_local_end,
                    continuation_local_start=token2_start,
                    continuation_local_end=token2_start + len(hex_prefix),
                    prefix_document_start=prefix_document_start,
                    prefix_document_end=prefix_document_end,
                    consumed_prefix_length=len(hex_prefix),
                    prefix_token=url_token,
                    suffix_token=hex_prefix,
                )
            )
            consumed_prefixes.add(url_token)
            continue

        # Channel 3: Incomplete Recognized HTML Entity
        ent_res = entity_completion_prefix(url_token, token2)
        if ent_res:
            comp_prefix, _ = ent_res
            if uri1 and uri2 and uri1 == uri2:
                joined = url_token + token2
                suffix_tok = token2
                ev_kind = "shared_annotation_continuation"
            else:
                joined = url_token + comp_prefix
                suffix_tok = comp_prefix
                ev_kind = "entity_completion"
            derived.append(
                DerivedUrlCandidate(
                    value=joined,
                    page_number=page_number,
                    evidence_kind=ev_kind,
                    prefix_source_ids=line1.source_ids,
                    continuation_source_ids=line2.source_ids,
                    prefix_line_index=line1.line_index,
                    continuation_line_index=line2.line_index,
                    prefix_local_start=prefix_local_start,
                    prefix_local_end=prefix_local_end,
                    continuation_local_start=token2_start,
                    continuation_local_end=token2_start + len(suffix_tok),
                    prefix_document_start=prefix_document_start,
                    prefix_document_end=prefix_document_end,
                    consumed_prefix_length=len(suffix_tok),
                    prefix_token=url_token,
                    suffix_token=suffix_tok,
                )
            )
            consumed_prefixes.add(url_token)
            continue

    return derived, consumed_prefixes
