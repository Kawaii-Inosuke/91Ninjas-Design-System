//! Help → About 91Ninjas Studio (91studio: was About VectorCraft).

use crate::VectorcraftApp;
use crate::theme;

/// The About window, while `UiState::about` is set.
pub(super) fn show(app: &mut VectorcraftApp, ctx: &egui::Context) {
    if !app.ui.about {
        return;
    }
    let mut open = true;
    egui::Window::new(tl!("About 91Ninjas Studio")) // 91studio
        .collapsible(false)
        .resizable(false)
        .open(&mut open)
        .pivot(egui::Align2::CENTER_CENTER)
        .default_pos(ctx.content_rect().center())
        .frame(egui::Frame::window(&ctx.global_style()).inner_margin(egui::Margin::same(18)))
        .show(ctx, |ui| {
            // 91studio (D-046): About · Privacy · Open-source licences. The licences of the code and assets
            // the app is built on must ship with it; they are kept here, out of the way.
            let tab_id = egui::Id::unique("about_tab");
            let mut tab = ui.data_mut(|d| d.get_temp::<u8>(tab_id)).unwrap_or(0);
            ui.horizontal(|ui| {
                for (i, label) in [tl!("About"), tl!("Privacy"), tl!("Open-source licences")].into_iter().enumerate() {
                    let i = i as u8;
                    if ui.selectable_label(tab == i, label).clicked() {
                        tab = i;
                    }
                }
            });
            ui.data_mut(|d| d.insert_temp(tab_id, tab));
            ui.separator();
            match tab {
                1 => privacy_tab(ui),
                2 => licences_tab(app, ui),
                _ => about_tab(app, ui),
            }
        });
    app.ui.about = open;
}

/// The app's copyright notice (also in the Windows program file and the Mac app, D-047).
pub const COPYRIGHT: &str = "© 2026 91Ninjas. All rights reserved. Internal use only.";

/// Who to ask about the app.
pub const CONTACT: &str = "Yashodhan";

/// The About tab: logo, name, version, company, what it does, and who to contact.
fn about_tab(app: &mut VectorcraftApp, ui: &mut egui::Ui) {
    let t = crate::theme::Tokens::get(ui.ctx());
    ui.set_width(440.0);
    ui.horizontal(|ui| {
        let (r, _) = ui.allocate_exact_size(egui::vec2(52.0, 52.0), egui::Sense::hover());
        crate::brand::paint_mark(ui, r);
        ui.vertical(|ui| {
            ui.label(egui::RichText::new(crate::native_menu::APP_NAME).font(theme::semibold(22.0)));
            ui.label(egui::RichText::new(crate::i18n::fmt(tl!("Version {version}"), &[("version", env!("CARGO_PKG_VERSION"))])).color(t.text_dim));
        });
    });
    ui.add_space(12.0);
    ui.label(tl!(
        "On-brand infographics for 91Ninjas' clients, made with Claude and finished in a full vector editor. Claude builds the design in the client's brand; you refine it by hand or by asking."
    ));
    ui.add_space(12.0);
    egui::Grid::new("about-facts").num_columns(2).spacing([14.0, 6.0]).show(ui, |ui| {
        ui.label(egui::RichText::new(tl!("Company")).color(t.text_dim));
        ui.label(tl!("91Ninjas · an internal tool for the 91Ninjas design team"));
        ui.end_row();
        ui.label(egui::RichText::new(tl!("Contact")).color(t.text_dim));
        ui.label(crate::i18n::fmt(tl!("Any issue or question: {name}"), &[("name", CONTACT)]));
        ui.end_row();
        ui.label(egui::RichText::new(tl!("Legal")).color(t.text_dim));
        ui.label(tl!("See Privacy and Open-source licences above."));
        ui.end_row();
    });
    ui.add_space(10.0);
    ui.label(egui::RichText::new(COPYRIGHT).size(11.0).color(t.text_dim));
    // The GPU the window renders with, for bug reports (Preferences › Performance picks it).
    if let Some(adapter) = &app.graphics_adapter {
        ui.add_space(4.0);
        ui.label(egui::RichText::new(crate::i18n::fmt(tl!("Graphics: {adapter}"), &[("adapter", adapter.as_str())])).size(11.0).color(t.text_dim));
    }
}

/// The privacy policy: what the app keeps, what leaves the computer and where it goes.
pub const PRIVACY: &[(&str, &str)] = &[
    (
        "Your work stays on your computer",
        "Designs, documents, clients, history and settings are stored only on this computer, in your user folder. 91Ninjas Studio has no account of its own, and no analytics, tracking or advertising.",
    ),
    (
        "Claude",
        "The app has no AI of its own. When you ask Claude for a design, your Claude app (Claude Desktop or Claude Code) sends what the work needs (your request, the content you give it, the client's brand guide and design previews) to Anthropic under your own Claude account, under Anthropic's terms and privacy policy. Don't give Claude anything a client hasn't agreed to share.",
    ),
    (
        "Team clients",
        "If you join the team, the clients you choose to share (design guide, rules, past work, fonts, icons) and your name go to the team's private GitHub repository, which only people with the team key can read. Designs are never shared. Leave team removes your name.",
    ),
    (
        "Updates and first start",
        "The app asks the public 91Ninjas Studio releases page on GitHub for new versions, and on first start downloads its AI tools (Python packages and a drawing engine) from their official sources. These are ordinary web requests: they carry no personal information, though the servers see your computer's internet address, as with any website.",
    ),
    ("Logs", "Logs stay on this computer for troubleshooting. They leave it only if you send them to someone."),
    ("Questions", "Ask Yashodhan, who looks after 91Ninjas Studio."),
];

fn privacy_tab(ui: &mut egui::Ui) {
    let t = crate::theme::Tokens::get(ui.ctx());
    ui.set_width(520.0);
    ui.label(egui::RichText::new(tl!("Privacy policy")).font(theme::semibold(16.0)));
    ui.add_space(6.0);
    egui::ScrollArea::vertical().max_height(380.0).show(ui, |ui| {
        for (title, body) in PRIVACY {
            ui.label(egui::RichText::new(tl!(title)).strong());
            ui.label(egui::RichText::new(tl!(body)).color(t.text));
            ui.add_space(8.0);
        }
    });
}

/// The notices the licences of the code, fonts and icons the app is built on require it to carry.
pub const NOTICE: &str = include_str!("../../../../NOTICE");
const LICENSE_MIT: &str = include_str!("../../../../LICENSE-MIT");

fn licences_tab(app: &mut VectorcraftApp, ui: &mut egui::Ui) {
    let t = crate::theme::Tokens::get(ui.ctx());
    ui.set_width(660.0);
    ui.label(egui::RichText::new(tl!("91Ninjas Studio includes open-source software. Its licences ask for these notices to be kept with the app.")).color(t.text_dim));
    ui.add_space(6.0);
    egui::ScrollArea::vertical().max_height(420.0).show(ui, |ui| {
        ui.label(egui::RichText::new(NOTICE).monospace().size(11.0));
        ui.add_space(8.0);
        ui.label(egui::RichText::new(LICENSE_MIT).monospace().size(11.0));
        ui.add_space(8.0);
        egui::CollapsingHeader::new(tl!("Contributors")).id_salt("about-contributors").show(ui, |ui| crate::credits::contributors_ui(app, ui));
        egui::CollapsingHeader::new(tl!("Models")).id_salt("about-models").show(ui, |ui| crate::credits::models_ui(ui));
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn about_names_the_company_contact_and_keeps_the_licence_notices() {
        assert_eq!(CONTACT, "Yashodhan");
        assert!(COPYRIGHT.starts_with("© 2026 91Ninjas") && !COPYRIGHT.contains("VectorCraft"));
        assert!(PRIVACY.iter().any(|(t, _)| *t == "Claude") && PRIVACY.iter().any(|(t, _)| *t == "Team clients"));
        // The licence requires the copyright and permission notice to ship with the app.
        assert!(NOTICE.contains("Licensed under the MIT License or the Apache License"));
        assert!(LICENSE_MIT.contains("Permission is hereby granted"));
    }
}
