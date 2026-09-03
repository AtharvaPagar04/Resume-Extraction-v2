# Table reconstruction

`reconstruct_tables(PageLayout)` is the only authoritative table-to-text path. It consumes the shared layout foundation; it does not parse PDF geometry a second time and it does not serialize table objects into RAW.

```text
PageLayout candidates
  -> reconcile overlapping native/rule/aligned candidates
  -> validate row and column structure
  -> assign each source span to one cell
  -> reconstruct wrapped cell text
  -> serialize row-wise text
  -> consume original table lines
  -> order non-table lines plus one table element
```

## Candidate sources and acceptance

PyMuPDF cell rectangles are preferred. Rule candidates use the foundation's horizontal and vertical guides. Borderless candidates require at least three repeated row bands and either three or more columns, or compact short label/value cells. Their columns are inferred from x-bands with support on distinct core rows, rather than every physical line start: inline fragments and one-off anchors do not become columns. A one-member row can extend an already-established candidate only when it aligns to an existing band, is nearby, and shares source-block continuity; it cannot create a candidate or another band. This intentionally avoids turning an ordinary main-flow/sidebar page into a table.

Candidates overlap-reconcile deterministically: native cells win over rules, rules over aligned evidence. Boundary-empty rows are trimmed first, then fully empty/interstitial raw columns are excluded from the logical table view. Active raw slots are clustered by strong horizontal interval overlap, so an inset header cell and its wider body cell become one visual column even if PyMuPDF assigned different raw indexes. Density and header alignment use those visual columns, while the raw grid remains the source-ownership authority.

The same logical-column mapping drives serialization. A reliable first meaningful header row becomes `Header: value` pairs; headerless tables use only nonempty logical values. Empty cells in an otherwise active column never remove that column globally, and a body value under an empty header is retained without an invented label.

Page-scale and sidebar-scale candidates are separately rejected only when repeated row pairing is weak and either logical-column recurrence or grid evidence is weak. This prevents a sparse page layout from being treated as a table without rejecting a genuine large, well-ruled grid. Rejected candidates remain in ordinary page flow and are retained in opt-in debug counts; they do not add a noisy RAW warning merely for being rejected.

## Cell and row reconstruction

Span ownership priority is containment, unique strongest intersection, then unique center membership. Ambiguous content rejects the candidate rather than risking duplicated or corrupted output. Cell text is rebuilt from the existing span/line reconstruction and joins physical wrapped lines with spaces.

Native table rows are evidence rather than final visual rows. Before density validation, text-bearing logical cells with overlapping vertical bands are reconciled into one visual row. Same-column continuations require a tight vertical gap, shared source block, and either a neighboring cell spanning both fragments or a matching continuation in another logical column; a structural horizontal rule blocks the merge. This permits wrapped labels and values to remain paired without collapsing independently flowing columns.

The first row is a header only when generic presentation evidence is stronger than body rows. Style evidence is normalized per logical cell before rows are compared, so a wrapped bold cell has one vote rather than one vote per physical span. A first-row bold/plain pattern repeated by body rows is treated as ordinary row role, not a header. With a header, output is `Header: value | Header: value`; without one, two columns become `label: value`, while wider rows retain positional ` | ` separators. Empty cells never shift other columns.

## Consumption and safety

An accepted table becomes one internal `LayoutLine` whose source IDs are all accepted source lines. Those original lines are removed before `order_lines()` runs. The current source-accounting check therefore verifies table source text exactly once along with non-table text. Hyperlink rectangles are associated with cells internally; RAW hyperlinks remain unchanged.

`table_debug_payload()` and `dump_table_debug()` are opt-in developer tools. They expose cell bounds, assigned spans, reconstructed text, serialization-coverage warnings, and rejected-candidate counts outside normal RAW output.
