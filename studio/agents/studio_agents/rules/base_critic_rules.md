# Base critic rules (all clients)
The client's rules (above) come first. Where a client rule conflicts with a base rule, the client rule wins and the base rule is skipped.
Each rule is PASS or FAIL on the rendered preview.

## Brand fidelity
1. Every client rule passes.
2. Only colours and fonts from the brand tokens; no colour outside the palette.
3. The layout differs from the client's recent layouts (the duplicate check lists the ones to avoid).
4. The canvas size matches the brand pack.

## Hierarchy
5. One dominant element (title or key visual); the eye knows where to start within 2 seconds.
6. At most 3 text sizes doing the work; 5 is the ceiling.
7. Heading and body differ clearly by weight or size, not by colour alone.
8. Reading order matches the content order (top-down or left-right, no zig-zag).

## Spacing and alignment
9. Equal outer margins; content respects the brand's minimum padding.
10. Gaps between sections are clearly larger than gaps inside a section.
11. Everything snaps to one grid; equal items have equal size and spacing.
12. More space above a heading than below it.
13. Markers and icons are optically centred with their text line.

## Legibility
14. Body text contrast ≥ 4.5:1, large text ≥ 3:1, against its actual background.
15. No text overflow, clipping, orphaned single words, or overlapping elements.
16. Body line length ≤ ~60 characters; no paragraph longer than 3 lines.
17. Text on coloured fills uses the brand's on-colour.

## Content honesty
18. Every number, claim and name comes from the content. No invented statistics.
19. Copy is tightened, not padded: about 80 words or fewer per panel, one idea per panel.
20. No filler labels: no eyebrow or kicker above headings, no tracked all-caps labels (unless the brand uses them).

## Anti-generic (refuse unless the brand asks for it)
21. No identical icon + heading + text card grid as the default structure.
22. No card inside a card; no gradient text; no glass or blur; no purple-blue gradients.
23. No emoji or unicode symbols as icons; icons from one set, one stroke weight.
24. No heavy or hard-offset drop shadows.
25. No coloured side-stripe borders thicker than 1px on cards.
26. No decorative chrome (fake charts, sparklines, progress rings) standing in for content.

## Output
27. Height fits the content with no dead space at the bottom.
28. Renders crisply; no blurry rasters, no broken images or empty icon slots.

## Report format
List only FAILs, one per line: `rule | what is wrong | fix`. Zero FAILs means the design passes.
