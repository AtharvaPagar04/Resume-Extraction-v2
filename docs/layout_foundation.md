# Common layout foundation

`build_page_layout(page, page_number)` creates the internal, immutable geometry model used before canonical text is emitted. It is not serialized by `RawResume`.

```text
PyMuPDF dict blocks
  -> Span -> LayoutLine -> LayoutBlock
  -> PageLayout (rows, anchors, gutters, regions, drawings, table candidates)
  -> canonical text / compact RAW
```

## Coordinates and geometry

Boxes use PyMuPDF coordinates: `(x0, y0, x1, y1)`, in points, measured from the page's top-left. Calculations keep floats. `geometry.py` centralizes widths, areas, centers, gaps, containment, intersections, alignments, normalized coordinates, and overlap functions.

`horizontal_overlap_ratio` and `vertical_overlap_ratio` use overlap divided by the smaller participating width or height. `intersection_ratio` uses intersection area divided by the smaller rectangle area. These denominators deliberately answer containment-like questions, rather than IoU questions.

## Internal model

- `Span`: source and normalized text, bbox, font information, style evidence, and `(block, line, span)` source order.
- `LayoutLine`: reconstructed text, source spans, bbox, text-shape features, style ratios, and source order.
- `LayoutBlock`: source block with its lines; it is extraction evidence, not a semantic region.
- `PageLayout`: dimensions, all source objects, drawing and annotation geometry, reusable statistics, rows, anchors, gutters, regions, table candidates, and warnings.
- `LogicalRow`, `AnchorCluster`, `GutterCandidate`, `LayoutRegion`, and `TableCandidate`: conservative structural evidence for later modules.

`DrawingElement` identifies page-relative long/thin horizontal rules, vertical rules, rectangles, and non-structural drawings. `LayoutHyperlink` keeps annotation geometry only during processing; normal RAW retains only page and URI.

## Reconstruction and style

Span reconstruction retains explicit whitespace, whitespace-only spans, and only applies the handoff's geometric-space rule: gap over 1 point and relative gap over 0.18. It does not segment words, dehyphenate, alter case, or merge ordinary physical lines.

Style derives from PyMuPDF flags plus generalized font-name evidence. Font size, bold/italic ratios, uppercase ratio, token count, colon ending, and bullet form are presentation features only. They never create section or resume-entity labels.

## Rows, columns, and tables

Rows use the handoff's 0.45 smaller-height vertical-overlap ratio or 0.45 relevant-height center tolerance. X anchors are deterministically clustered within 2% of page width. Gutter candidates require a page-relative 8% anchor gap and at least three lines on each side. Regions are neutral left/right candidates only; no production column ordering is added here.

Table candidates combine PyMuPDF `find_tables()` and rule evidence. A rule candidate needs at least two horizontal and two vertical structural rules. No table text semantics or layout data are emitted by this foundation.

## Safety and debugging

`validate_span_accounting()` ensures every nonempty source span appears exactly once in reconstructed lines. `validate_line_accounting()` checks row/region grouping without loss or duplication. Current ordering still falls back to `(y0, x0, block_index, line_index)` when coverage fails. If foundation derivation fails while dict text remains readable, extraction uses basic source order for that page.

`format_page_layout()` and `dump_layout_debug()` are explicit developer tools. The latter writes a separate JSON artifact only when called; RAW JSON never contains geometry.
