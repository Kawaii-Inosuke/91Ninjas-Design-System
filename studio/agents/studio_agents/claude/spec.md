## Writing the spec
The renderer draws the spec with the client's templates and tokens: you choose the layout and write the content;
colours, fonts and spacing come from the brand.
- layout: `family/variant` from the client's layouts above, e.g. `vertical_list/icon`, `comparison_cards/tint`.
  Prefer one not used recently unless the designer asked for it or it clearly fits best. By content shape:
  steps/sequence → vertical_list or horizontal_timeline; 2–3 things compared → comparison_cards; attributes
  across items → cell_table; checklist/tips → card_list; types/parts or term+formula+example → column_grid;
  tiers, pyramids, hubs or anything else → custom.
- title: one line (about 50 characters at most), sentence case, no full stop. subtitle: optional, one line.
- Copy: tight and concrete. Headings 2–5 words; body at most ~20 words per item; one idea per item. No filler,
  no eyebrow labels, no emoji.
- Items 3–6 for lists and timelines; 2–3 columns for comparisons; a table has a header and 2–6 rows.
- icon (for /icon variants): call search_icons for each idea and use an exact name it returns.
- takeaway: optional, one line, only when the content states a clear conclusion.
- custom: `html` for the body only, with the house classes (.card .h .b .bullets .marker .takeaway), CSS
  variables such as var(--color-tint), and data-layer="..." on every meaningful element (these become the layer
  names). No scripts, no external URLs, no colours outside the variables.
- css: empty unless fixing a problem you saw in the preview; then the smallest rule, CSS variables only.

## Checking a render
Look at the preview against the rules below and the machine checks (each machine check is a failure unless
clearly harmless). Be strict about brand rules, legibility, overflow and invented facts; don't nitpick taste
when the rules are met. Fix by changing the spec (shorter copy, another variant or layout, fewer items, a small
css rule) and render again.
