# Card-edge accent acceptance

## Intel stories

- GIVEN action-needed, active, monitoring, read, archived, and resolved stories, WHEN the Intel feed renders, THEN no story has a full-height colored edge and status remains readable from text/dots/pills and lifecycle opacity.
- GIVEN a story is hovered, focused with the keyboard, or selected/active, WHEN the state changes, THEN hover uses the surface/border, focus has a visible offset ring, and selection uses a non-semantic border/surface treatment.
- GIVEN a resolved or archived story, WHEN it renders, THEN its explicit resolved/archive controls and text remain available and it is not mistaken for unread content.

## Holdings

- GIVEN grouped holdings, WHEN card mode renders at desktop or narrow width, THEN each row has a 6px decorative marker beside the identity using the same group color as its section.
- GIVEN the marker migration, WHEN before/after geometry is measured, THEN row height and ticker starting position are unchanged.

## Callouts and structure

- GIVEN warning/error/success callouts, WHEN they render in light or dark theme, THEN icon/text and a full tinted outline/surface carry meaning without an edge rail.
- GIVEN structural dividers, charts, blockquotes, drawers, and nested guides, WHEN audited, THEN legitimate neutral/data boundaries remain intact.
