# Phase 1: layered SVG

*Date: 2026-11-02 · Status: **done**. 12/12 tests pass, and 13 real designs convert at ≤ 0.03% pixel difference.*

## Goal
Turn a rendered HTML design into an SVG that the editor imports as **named, selectable layers with live, editable text**. This is the format the AI agents will place on the artboard from Phase 2 onwards (D-005).

## What was built
All in `studio/agents/`. It is our own Python project (uv, Python 3.12), separate from `Design skill/` (D-010).

| File | What it does |
|---|---|
| `studio_agents/render/extract.js` | Runs inside the rendered page in headless Chromium. Turns `::before`/`::after` into real elements so they can be measured, then walks the design in paint order. It records boxes (backgrounds, borders, radii, gradients), clip-path polygons, per-side borders, text (lines × styled runs, measured per character), inline SVG (with computed colours and fonts inlined) and images. |
| `studio_agents/render/layered_svg.py` | `layered_svg(html) → LayeredSVG(svg, layers, warnings…)`. It simplifies the tree (drops empty groups, unwraps anonymous single-child groups), gives readable, unique layer names, and writes the SVG. It also has `screenshot_html`/`screenshot_svg` previews and a CLI. |
| `tests/test_layered_svg.py` | Structure rules (one outer group, unique readable ids, live text with lines and runs, one font per element, no filters, CSS features covered) and a pixel match against the browser render. |
| `tests/test_editor_import.py` | Imports into the **real editor** headlessly and checks: RGB artboard at the design's size, named groups, 16 editable Type objects, exact brand colours, and a pixel match of the editor's own render. |
| `tests/editor_helpers.py` | A test-only version of the future bridge `place_svg`: `file.new` (RGB, design size) → `file.place` → `object.move` to correct the position. |
| `tests/fixtures/sample_infographic.html` | Test design that uses every supported feature, built in DejaVu Sans so the browser and the editor have the same font. |
| `studio/samples/sample-layered.svg` | Output for a hand check in the desktop app. |

**Usage:**
```bash
cd studio/agents
uv run python -m studio_agents.render.layered_svg design.html -o design.svg --name design-<slug>
uv run pytest -q
```

## Results
- **Test design:** 12/12 tests pass.
  - The SVG matches the browser render (< 0.3% of pixels differ, after a light blur that absorbs anti-aliasing).
  - The editor's own export matches too (< 1%; the remaining difference is the editor's text anti-aliasing).
- **13 real designs from the skill's `output/fcc/`** (vertical lists, comparison tables, a chart, formula cards) all convert with no warnings, at **0.00–0.03%** pixel difference. They were used read-only as inputs and are *not* copied into the repo, because they are client material.
- **In the editor:** a comparison table becomes `ctable › cell-1…20`, each with its own editable text box and shape. Chart labels inside an inline SVG become editable Type objects.

## Decisions taken
Recorded in `DECISIONS.md`.
- **D-013:** DOM-walk conversion. Pseudo-elements are made into real elements, and layer names come from `data-layer` → id → class → tag. Centred text keeps `text-anchor="middle"`, so it stays centred when designers edit it.
- **D-014:** Placement procedure: an RGB document at the design's size, then place, then a corrective move using `background` as the reference point.

## Problems found and fixed during the phase
- **Placed design shifted 175 px left.** Cause: the editor **over-measures multi-line centred text** (single-line, left and right-aligned text are fine), and `file.place` centres the wrong bounds. *Workaround:* move the design using `background` as the reference. *Real fix:* in the editor fork, Phase 2 (first Rust task).
- **Real designs looked 2–5% off.** Cause: my SVG preview loaded from `about:blank`, which blocks `file://` fonts. This was a bug in the preview tool, not the converter. It now loads from a file.
- **Chart labels inside inline SVG came out tiny.** Their font was set by CSS, and only paint properties were copied. Font properties are now copied too.
- **Meaningless layer names** (`div-1`, `ul`, `h2`) are now readable: `content`, `list`, `heading`, `icon`/`graphic`, `item`.
- **Desktop app documents default to CMYK.** The placement creates an `rgb` document, so brand hex colours stay exact.

## Known limitations (not needed for current designs)
Each one produces a warning in `LayeredSVG.warnings`; none of the 13 real designs triggered any.
- `box-shadow`, CSS `filter` and CSS `transform` are not converted (the editor import has no shadows or filters).
- `background-image: url(...)` and `overflow:hidden` clipping are not converted.
- Backgrounds on inline text spans (highlighter style) are not converted.
- Dashed borders use a 3:2 dash rhythm, which is close to Chromium's but not identical.
- Mixed-style text is one Type object per text block with styled runs. Editing in the editor keeps the runs (verified for single-style text; mixed runs to be checked in Phase 2 with `text.editRange`).

## Open issues
| # | Issue | Plan |
|---|---|---|
| 1 | Editor over-measures multi-line centred text | Fix in the fork (Phase 2) with a `// 91studio:` comment. Until then, the placement uses the reference move. |
| 2 | **Brand fonts must be installed for the editor to draw them.** The browser uses the pack's `@font-face`, but the editor uses system fonts (Inter isn't installed here). | Phase 3: client pack install copies fonts to `~/.local/share/fonts/91studio/<client>/`, then runs `fc-cache`. |
| 3 | The font weight name differs ("DejaVu Sans Regular drawn in Book"). It's harmless for DejaVu. | Check with real brand fonts once installed |

## Hand check for you (optional)
Open the editor, use **File › Place** (press Ctrl+L and paste the path) on
`/home/yashodhan/91Ninjas/Design system/studio/samples/sample-layered.svg`. In the Layers panel, check that title, subtitle, steps › step-1…3, tips-card, icon-shield and footer are there and that the text is editable.

*In a new desktop document it will be centred and slightly shifted (issue 1), and the document is CMYK by default. The bridge fixes both in Phase 2.*

## Next: Phase 2 (Studio Bridge, Rust)
1. `crates/studio-bridge`: an authenticated localhost API wired into the app's `ControlRequest` channel (D-008).
2. Calls: `new_document` (RGB), `place_svg` (with the correct position), `inspect`, `find`, `set_text`, `set_fill`, `transform`, `turn_begin/end` (one undo step per AI turn), `render_preview`, `export`.
3. Fix the editor's bounds for multi-line centred text.
4. Python bridge client plus a smoke test against the running desktop app.
