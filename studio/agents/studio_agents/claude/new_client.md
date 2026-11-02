## Writing the new client
Measurements beat impressions: pick colours from the measured list (the one covering most of the page is
usually `surface`, the dark one for most text `ink`); don't invent hex values. Guideline documents beat
measurements when they say something explicitly, unless the past work clearly contradicts them (past work wins;
say so). If text is outlined and nothing names the font, name the closest common font you see and say it was
judged from the images. Sizes are px at the canvas width the designs use.

DESIGN.md: YAML front matter, then a short markdown style guide (look and feel, composition, components such as
cards, pills, markers, connectors and icons, do's and don'ts). Every colour and type style below is required:
the templates use them.

```
---
version: alpha
name: Acme Health
brand_version: 0.1.0
description: One line on the look.
colors:
  primary: "#0A7A5A"      # titles, filled pills, connectors
  accent: "#F5A623"       # markers, outlines, icons
  tint: "#E3F4EE"         # light card / panel fill
  ink: "#222222"          # body text
  surface: "#FFFFFF"      # page background
  cell: "#F2F2F2"         # neutral table / card cells
  line: "#999999"         # neutral outlines
  on-primary: "#FFFFFF"   # text on primary fills
  header-bar: "#0A7A5A1A" # optional: title band (may have alpha)
typography:
  title:   {fontFamily: Poppins, fontSize: 44px, fontWeight: 600, lineHeight: 1.2}
  heading: {fontFamily: Poppins, fontSize: 30px, fontWeight: 600, lineHeight: 1.3}
  body:    {fontFamily: Poppins, fontSize: 26px, fontWeight: 400, lineHeight: 1.45}
  label:   {fontFamily: Poppins, fontSize: 26px, fontWeight: 600, lineHeight: 1.3}
  marker:  {fontFamily: Poppins, fontSize: 40px, fontWeight: 700, lineHeight: 1}
rounded: {sm: 12px, md: 18px, lg: 28px, full: 999px}
spacing: {page: 72px, section: 60px, item: 24px}
infographic:
  canvas: {width: 1080, height: auto}
  layouts:              # family/variant the past designs use, most typical first
    - vertical_list/icon
    - card_list/check
---

# Acme Health: design language
...
```

Rules (critic_rules.md): 10–25 checkable one-sentence rules with exact values, in sections (## Locked,
## Typography, ## Components, ## Layout), numbered R1, R2… Each says what a failure looks like when that helps.

Layout families the templates make: vertical_list (number|icon), horizontal_timeline (number|icon),
comparison_cards (outline|tint), card_list (check|number), cell_table, column_grid, custom (anything else; other
shapes the client uses may be listed by their own name).
