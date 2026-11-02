//! 91studio: the app bar's Share and Export buttons (D-028), as in the 91Ninjas mockup. They open
//! menus of existing File commands; nothing here exports by itself.

use egui::{CornerRadius, Sense, Stroke, Ui, vec2};
use serde_json::json;

use crate::theme::{self, Tokens};
use crate::{VectorcraftApp, icons, menus};

/// Height of both buttons.
pub(super) const HEIGHT: f32 = 24.0;

/// Export's menu: (label, command).
const EXPORT_ITEMS: &[(&str, &str)] = &[
    ("Export As PNG…", "file.export.png"),
    ("Export As SVG…", "file.export.svg"),
    ("Export As…", "file.exportAs"),
    ("Export for Screens…", "file.exportForScreens"),
];

/// Share's menu: hand a design to another designer.
const SHARE_ITEMS: &[(&str, &str)] = &[("Package…", "ui.packageDialog"), ("Save a Copy…", "file.saveCopy")];

fn label_width(ui: &Ui, text: &str) -> f32 {
    ui.painter().layout_no_wrap(text.to_string(), theme::semibold(12.5), egui::Color32::WHITE).size().x
}

/// The width both buttons take, gap included.
pub(super) fn width(ui: &Ui) -> f32 {
    export_width(ui) + share_width(ui) + 8.0
}

/// The width Export alone takes.
pub(super) fn export_width(ui: &Ui) -> f32 {
    label_width(ui, tl!("Export")) + 40.0
}

fn share_width(ui: &Ui) -> f32 {
    label_width(ui, tl!("Share")) + 26.0
}

fn items_menu(app: &mut VectorcraftApp, ui: &mut Ui, items: &[(&str, &str)]) {
    for (label, id) in items {
        let on = menus::enabled(app, id);
        if ui.add_enabled(on, egui::Button::new(tl!(label))).clicked() {
            menus::invoke(app, id, json!({}));
            ui.close();
        }
    }
}

/// Draws Export (accent fill, menu) then, with `share`, Share (outlined, menu), right to left: call in a
/// right-to-left layout. A narrow window drops Share first.
pub(super) fn show(app: &mut VectorcraftApp, ui: &mut Ui, share: bool) {
    let t = Tokens::get(ui.ctx());
    let has_doc = app.session.active().is_some();
    // Export: the accent call to action, with a chevron.
    let (r, resp) = ui.allocate_exact_size(vec2(export_width(ui), HEIGHT), Sense::click());
    let fill = match (has_doc, resp.hovered()) {
        (false, _) => t.input,
        (true, true) => t.accent,
        (true, false) => t.accent_strong,
    };
    let fg = if has_doc { t.on_accent } else { t.text_disabled };
    ui.painter().rect_filled(r, CornerRadius::same(6), fill);
    let g = ui.painter().layout_no_wrap(tl!("Export").to_string(), theme::semibold(12.5), fg);
    ui.painter().galley(egui::pos2(r.left() + 12.0, r.center().y - g.size().y / 2.0), g, fg);
    icons::paint(ui, "chevron-down", egui::Rect::from_center_size(r.right_center() - vec2(14.0, 0.0), vec2(12.0, 12.0)), fg);
    let resp = resp.on_hover_text(tl!("Export the document"));
    egui::Popup::menu(&resp).show(|ui| items_menu(app, ui, EXPORT_ITEMS));
    if !share {
        return;
    }
    ui.add_space(8.0);
    // Share: outlined.
    let (r, resp) = ui.allocate_exact_size(vec2(share_width(ui), HEIGHT), Sense::click());
    let fg = if has_doc { t.text_strong } else { t.text_disabled };
    if resp.hovered() {
        ui.painter().rect_filled(r, CornerRadius::same(6), t.hover);
    }
    ui.painter().rect_stroke(r, CornerRadius::same(6), Stroke::new(1.0, t.button_border), egui::StrokeKind::Inside);
    let g = ui.painter().layout_no_wrap(tl!("Share").to_string(), theme::semibold(12.5), fg);
    ui.painter().galley(r.center() - g.size() / 2.0, g, fg);
    let resp = resp.on_hover_text(tl!("Package the document for another designer"));
    egui::Popup::menu(&resp).show(|ui| items_menu(app, ui, SHARE_ITEMS));
}
