# 91Ninjas Studio icon and wordmark

- `icon.svg`, `icon-128.png`, `icon-256.png`, `icon-512.png`: the app icon. The 91Ninjas "91" mark in white on a
  rounded tile in the brand ink `#062324` (corner radius 112/512, as the upstream tile). The mark was redrawn as
  vector shapes measured from the 91Ninjas logo the owner supplied, so it is crisp at every size.
- `icon.ico` (Windows, 16–256 px) and `icon.icns` (macOS, up to 1024 px): made from `icon-512.png` for the installers (D-040).
- `91ninjas-wordmark.png`: the 91NINJAS wordmark from that logo, white on transparent; the welcome screen tints
  it with the theme's text colour.

The 91Ninjas name, mark and wordmark belong to 91Ninjas and are used here by the owner. They are not covered
by the code's open-source licence. To update: replace these files, keeping the names
(`crates/ui-egui/src/brand.rs`, `apps/vectorcraft/src/titlebar.rs` read them).
