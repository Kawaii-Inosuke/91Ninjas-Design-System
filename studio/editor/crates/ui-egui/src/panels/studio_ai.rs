//! 91studio: the AI Design Assistant, a chat with the 91Ninjas Studio agents. The designer picks a client in
//! the header, writes or pastes content, and the agents answer in the thread as they brief, lay out, render,
//! critique and place the design; approvals are cards in the thread, and once the design is on the artboard
//! a message edits it (one undo step each). Clients are added from the header menu (a `.91pack`, or a brand
//! folder). The panel only draws and asks: all I/O goes through [`crate::studio::StudioAgents`] (D-021, D-024).

use egui::{Align, Color32, CornerRadius, Frame, Layout, Margin, RichText, ScrollArea, Stroke, TextEdit, Ui, vec2};
use serde_json::{Value, json};

use crate::VectorcraftApp;
use crate::services::FilePick;
use crate::studio::StudioReply;
use crate::theme::Tokens;
use crate::{icons, widgets};

pub const ID: &str = "studioAi";
/// Messages kept in the thread (the run's full log stays with the agents).
const MAX_MESSAGES: usize = 150;
/// The message box's height.
const COMPOSER_H: f32 = 96.0;

// ── app bar button ──────────────────────────────────────────────────────────────────────────────────

/// The app bar button's label, height and width.
fn bar_layout(ui: &Ui) -> (std::sync::Arc<egui::Galley>, f32, f32) {
    let h = 24.0;
    let galley = ui.painter().layout_no_wrap(tl!("AI").to_string(), egui::FontId::proportional(12.0), Color32::PLACEHOLDER);
    let w = galley.size().x + h * 0.55 + h * 0.9;
    (galley, h, w)
}

/// The app bar button's width (the bar drops it when space runs out).
pub fn bar_button_width(ui: &Ui) -> f32 {
    bar_layout(ui).2
}

/// The app bar's AI button: a quiet soft button with the wand in the accent colour; opens this panel.
pub fn bar_button(app: &mut VectorcraftApp, ui: &mut Ui) -> egui::Response {
    let t = Tokens::get(ui.ctx());
    let (galley, h, w) = bar_layout(ui);
    let icon = h * 0.55;
    let (r, resp) = ui.allocate_exact_size(vec2(w, h), egui::Sense::click());
    let ir = egui::Rect::from_center_size(r.left_center() + vec2(h * 0.35 + icon / 2.0, 0.0), vec2(icon, icon));
    ui.painter().rect_filled(r, CornerRadius::same(6), if resp.hovered() { t.hover } else { t.button });
    icons::paint(ui, "wand-sparkles", ir, t.accent);
    let y = r.center().y - galley.size().y / 2.0;
    ui.painter().galley(egui::pos2(ir.right() + h * 0.2, y), galley, if resp.hovered() { t.text_strong } else { t.text });
    let resp = resp.on_hover_text(tl!("Open the AI Design Assistant"));
    if resp.clicked() {
        app.run("window.panel", json!({ "panel": ID })).ok();
    }
    resp
}

// ── state ───────────────────────────────────────────────────────────────────────────────────────────

#[derive(Clone, Copy, Default, PartialEq)]
enum Who {
    #[default]
    Assistant,
    Designer,
    /// A quiet progress line, not a bubble.
    Note,
}

#[derive(Clone, Default)]
struct Msg {
    who: Who,
    text: String,
    detail: Vec<String>,
    /// Which fetched image belongs here (`v1`, `v2`…).
    image_key: Option<String>,
    image: Option<egui::TextureHandle>,
}

#[derive(Clone, Default)]
struct Client {
    id: String,
    name: String,
    color: Option<Color32>,
}

#[derive(Clone, Default)]
struct State {
    started: bool,
    agents_ok: bool,
    editor_ok: bool,
    status: String,
    clients: Vec<Client>,
    client: usize,
    draft: String,
    /// A layout hint from a suggestion chip ("use a timeline"), sent with the next design.
    instruction: String,
    run: Option<String>,
    running: bool,
    /// What the agents are doing now ("drawing version 2").
    thinking: String,
    messages: Vec<Msg>,
    gate: Option<Value>,
    import: Option<Value>,
    placed: bool,
    busy: bool,
}

fn s(v: &Value, key: &str) -> String {
    v.get(key).and_then(Value::as_str).unwrap_or_default().to_string()
}

fn hex(c: &str) -> Option<Color32> {
    let c = c.strip_prefix('#')?;
    let n = u32::from_str_radix(c.get(..6)?, 16).ok()?;
    Some(Color32::from_rgb((n >> 16) as u8, (n >> 8) as u8, n as u8))
}

/// The path of a run file as the agents serve it: `/api/runs/<id>/files/<path inside the run>`.
fn file_path(run: &str, abs: &str) -> Option<String> {
    let rel = abs.split(&format!("/runs/{run}/")).nth(1)?;
    Some(format!("/api/runs/{run}/files/{rel}"))
}

impl State {
    fn say(&mut self, who: Who, text: impl Into<String>, detail: Vec<String>) {
        self.messages.push(Msg { who, text: text.into(), detail, ..Default::default() });
        if self.messages.len() > MAX_MESSAGES {
            self.messages.remove(0);
        }
    }

    fn client(&self) -> Option<&Client> {
        self.clients.get(self.client)
    }
}

// ── what the agents send ────────────────────────────────────────────────────────────────────────────

fn on_event(st: &mut State, e: &Value, fetch: &mut Vec<(String, String)>) {
    let d = e.get("data").cloned().unwrap_or(Value::Null);
    match e.get("type").and_then(Value::as_str).unwrap_or_default() {
        "stage" => st.thinking = s(&d, "note"),
        "brief" => {
            let mut points: Vec<String> = d
                .get("key_points")
                .and_then(Value::as_array)
                .map(|a| a.iter().filter_map(Value::as_str).map(|p| format!("• {p}")).collect())
                .unwrap_or_default();
            points.push(format!("Layout idea: {}", s(&d, "suggested_layout")));
            st.say(Who::Assistant, format!("Here's the brief: {} ({}).", s(&d, "topic"), s(&d, "angle")), points);
        }
        "check" if d.get("verdict").and_then(Value::as_str) == Some("ok") => st.say(Who::Note, tl!("No similar designs for this client"), vec![]),
        "gate" => {
            st.gate = Some(d);
            st.thinking.clear();
        }
        "decision" => st.gate = None,
        "version" => on_version(st, &d, fetch),
        "placed" => {
            st.placed = d.get("editor").and_then(Value::as_bool).unwrap_or(false);
            let text = if st.placed {
                format!(
                    "It's on a new artboard with {} named layers. Edit it by hand, or tell me what to change.",
                    d.get("layers").and_then(Value::as_u64).unwrap_or(0)
                )
            } else {
                s(&d, "note")
            };
            st.say(Who::Assistant, text, vec![]);
        }
        "message" if s(&d, "from") != "designer" => st.say(Who::Assistant, s(&d, "text"), vec![]),
        "error" => st.say(Who::Assistant, format!("Something went wrong: {}", s(&d, "error")), vec![]),
        "done" => on_done(st, &d),
        _ => {}
    }
}

fn on_version(st: &mut State, d: &Value, fetch: &mut Vec<(String, String)>) {
    let fails: Vec<String> = d
        .get("fails")
        .and_then(Value::as_array)
        .map(|a| a.iter().map(|f| format!("{} · {} → {}", s(f, "rule"), s(f, "problem"), s(f, "fix"))).collect())
        .unwrap_or_default();
    let v = d.get("version").and_then(Value::as_u64).unwrap_or(0);
    let text = if d.get("passes").and_then(Value::as_bool).unwrap_or(false) {
        format!("Version {v} passes the brand check.")
    } else {
        format!("Version {v} has {} issue(s); fixing them.", fails.len())
    };
    st.say(Who::Assistant, text, fails);
    if let (Some(run), Some(p)) = (st.run.clone(), d.get("preview").and_then(Value::as_str))
        && let Some(path) = file_path(&run, p)
    {
        let key = format!("v{v}");
        if let Some(m) = st.messages.last_mut() {
            m.image_key = Some(key.clone());
        }
        fetch.push((key, path));
    }
}

fn on_done(st: &mut State, d: &Value) {
    st.running = false;
    st.gate = None;
    st.thinking.clear();
    // A failed run already said what went wrong (its `error` event).
    if s(d, "status") == "failed" && st.messages.last().is_some_and(|m| m.text.starts_with("Something went wrong")) {
        return;
    }
    let files: Vec<String> = d
        .get("files")
        .and_then(Value::as_object)
        .map(|o| o.iter().map(|(k, v)| format!("{k}: {}", v.as_str().unwrap_or_default())).collect())
        .unwrap_or_default();
    let text = match s(d, "status").as_str() {
        "done" => tl!("Approved and saved. The files:").to_string(),
        "abandoned" => tl!("Stopped. Nothing was saved.").to_string(),
        "not_usable" => format!("I can't make an infographic from that: {}", s(d, "reason")),
        other => format!("Finished ({other}). {}", s(d, "error")),
    };
    st.say(Who::Assistant, text, files);
}

fn on_answer(st: &mut State, tag: &str, result: Result<Value, String>, follow: &mut Option<String>) {
    st.busy = false;
    let v = match result {
        Ok(v) => v,
        Err(e) if tag == "health" => {
            st.agents_ok = false;
            st.status = format!("Agents not reachable: {e}");
            return;
        }
        Err(e) => return st.say(Who::Assistant, format!("That didn't work: {e}"), vec![]),
    };
    match tag {
        "health" => {
            st.agents_ok = true;
            st.editor_ok = v.get("editor").and_then(Value::as_bool).unwrap_or(false);
            st.status =
                if st.editor_ok { tl!("Agents ready · editor connected").into() } else { tl!("Agents ready · editor not connected").into() };
        }
        "clients" => on_clients(st, &v),
        "start" => {
            let id = s(&v, "id");
            if !id.is_empty() {
                st.placed = false;
                st.running = true;
                st.thinking = tl!("reading the content").into();
                st.run = Some(id.clone());
                *follow = Some(id);
            }
        }
        "import" => {
            st.say(Who::Assistant, format!("“{}” v{} by {}: install it?", s(&v, "name"), s(&v, "version"), s(&v, "author")), vec![]);
            st.import = Some(v);
        }
        "install" | "folder" => {
            st.say(Who::Assistant, format!("Added client {}. It's selected; paste your content to start.", s(&v, "client")), vec![]);
            st.import = None;
            st.clients.push(Client { id: s(&v, "client"), name: s(&v, "client"), color: None });
            st.client = st.clients.len().saturating_sub(1);
            st.started = false; // refresh the list (names, colours)
        }
        _ => {}
    }
}

fn on_clients(st: &mut State, v: &Value) {
    let selected = st.client().map(|c| c.id.clone());
    st.clients = v
        .as_array()
        .map(|a| {
            a.iter()
                .filter(|c| c.get("error").is_none())
                .map(|c| Client {
                    id: s(c, "client"),
                    name: s(c, "name"),
                    color: c.get("colors").and_then(|k| k.get("primary")).and_then(Value::as_str).and_then(hex),
                })
                .collect()
        })
        .unwrap_or_default();
    st.client = selected.and_then(|id| st.clients.iter().position(|c| c.id == id)).unwrap_or(0);
}

/// Apply whatever the agents sent since the last frame.
fn absorb(app: &mut VectorcraftApp, ctx: &egui::Context, st: &mut State) {
    let Some(agents) = app.services.studio_agents.as_mut() else { return };
    let mut follow = None;
    let mut fetch = vec![];
    for r in agents.poll() {
        match r {
            StudioReply::Answer { tag, result } => on_answer(st, &tag, result, &mut follow),
            StudioReply::Event(e) => on_event(st, &e, &mut fetch),
            StudioReply::File { key, result: Ok(bytes) } => attach_image(ctx, st, &key, &bytes),
            StudioReply::File { result: Err(_), .. } => {}
        }
    }
    if let Some(run) = follow {
        agents.follow(&run);
    }
    for (key, path) in fetch {
        agents.fetch(&key, &path);
    }
}

fn attach_image(ctx: &egui::Context, st: &mut State, key: &str, bytes: &[u8]) {
    let Ok(img) = image::load_from_memory(bytes) else { return };
    let rgba = img.to_rgba8();
    let color = egui::ColorImage::from_rgba_unmultiplied([rgba.width() as usize, rgba.height() as usize], rgba.as_raw());
    let tex = ctx.load_texture(format!("studio-ai-{key}"), color, egui::TextureOptions::LINEAR);
    if let Some(m) = st.messages.iter_mut().rev().find(|m| m.image_key.as_deref() == Some(key)) {
        m.image = Some(tex);
    }
}

// ── drawing ─────────────────────────────────────────────────────────────────────────────────────────

/// What the panel asks the agents for this frame.
type Asks = Vec<(&'static str, &'static str, String, Option<Value>)>;

/// The client chip and menu, and the connection dot.
fn header(app: &mut VectorcraftApp, ui: &mut Ui, st: &mut State, t: &Tokens) {
    ui.horizontal(|ui| {
        let (name, color) = st.client().map_or((tl!("No client yet").to_string(), t.text_dim), |c| (c.name.clone(), c.color.unwrap_or(t.accent)));
        let initials: String =
            name.split_whitespace().filter_map(|w| w.chars().next()).filter(|c| c.is_alphanumeric()).take(2).collect::<String>().to_uppercase();
        let (r, _) = ui.allocate_exact_size(vec2(28.0, 28.0), egui::Sense::hover());
        ui.painter().rect_filled(r, CornerRadius::same(7), color);
        ui.painter().text(
            r.center(),
            egui::Align2::CENTER_CENTER,
            if initials.is_empty() { "?" } else { &initials },
            egui::FontId::proportional(11.5),
            Color32::WHITE,
        );
        ui.menu_button(RichText::new(format!("{name} ▾")).strong().color(t.text_strong), |ui| client_menu(app, ui, st));
        ui.with_layout(Layout::right_to_left(Align::Center), |ui| {
            let dot = if st.agents_ok && st.editor_ok {
                Color32::from_rgb(52, 168, 83)
            } else if st.agents_ok {
                Color32::from_rgb(230, 162, 60)
            } else {
                t.text_disabled
            };
            let (r, resp) = ui.allocate_exact_size(vec2(10.0, 10.0), egui::Sense::hover());
            ui.painter().circle_filled(r.center(), 4.0, dot);
            resp.on_hover_text(&st.status);
        });
    });
}

fn client_menu(app: &mut VectorcraftApp, ui: &mut Ui, st: &mut State) {
    for i in 0..st.clients.len() {
        let label = st.clients.get(i).map(|c| c.name.clone()).unwrap_or_default();
        if widgets::menu_item(ui, &label, true, i == st.client) {
            st.client = i;
            ui.close();
        }
    }
    if !st.clients.is_empty() {
        ui.separator();
    }
    if widgets::menu_item(ui, tl!("Import client pack…"), can_open(app), false) {
        ui.close();
        import_pack(app);
    }
    if widgets::menu_item(ui, tl!("Add client from a brand folder…"), can_pick_folder(app), false) {
        ui.close();
        add_folder(app);
    }
}

/// File › Open-style dialogs go through [`crate::picks`] (off the UI thread on Linux, sheets on macOS): when
/// the dialog answers, this runs again with the path and hands it to the agents (their answer is tagged).
fn import_pack(app: &mut VectorcraftApp) {
    crate::picks::button(app, |app| {
        let pick = FilePick { filters: vec![FilePick::filter("91Ninjas client pack", &["91pack"])], ..Default::default() };
        if let Some(path) = crate::picks::open(app, &pick)
            && let Some(a) = app.services.studio_agents.as_mut()
        {
            a.request("import", "POST", "/api/clients/import-path", Some(json!({ "path": path })));
        }
    });
}

fn add_folder(app: &mut VectorcraftApp) {
    crate::picks::button(app, |app| {
        if let Some(path) = crate::picks::folder(app)
            && let Some(a) = app.services.studio_agents.as_mut()
        {
            a.request("folder", "POST", "/api/clients/from-folder", Some(json!({ "path": path })));
        }
    });
}

fn can_open(app: &VectorcraftApp) -> bool {
    crate::picks::can(app, &crate::picks::PickRequest::Open(FilePick::default()))
}

fn can_pick_folder(app: &VectorcraftApp) -> bool {
    crate::picks::can(app, &crate::picks::PickRequest::Folder)
}

fn bubble(ui: &mut Ui, m: &Msg, t: &Tokens) {
    if m.who == Who::Note {
        ui.label(RichText::new(format!("·  {}", m.text)).small().color(t.text_dim));
        return;
    }
    let mine = m.who == Who::Designer;
    let width = (ui.available_width() * 0.86).max(120.0);
    let layout = if mine { Layout::right_to_left(Align::Min) } else { Layout::left_to_right(Align::Min) };
    ui.with_layout(layout, |ui| {
        let fill = if mine { t.accent } else { t.button };
        let text = if mine { t.on_accent } else { t.text };
        Frame::new().fill(fill).corner_radius(CornerRadius::same(10)).inner_margin(Margin::symmetric(10, 8)).show(ui, |ui| {
            // A column of wrapping lines, however the row around the bubble lays out.
            ui.vertical(|ui| {
                ui.set_max_width(width);
                ui.add(egui::Label::new(RichText::new(&m.text).color(text)).wrap());
                for d in &m.detail {
                    ui.add(egui::Label::new(RichText::new(d).small().color(if mine { t.on_accent } else { t.text_dim })).wrap());
                }
                if let Some(tex) = &m.image {
                    let w = width.min(tex.size_vec2().x);
                    ui.add_space(4.0);
                    ui.image((tex.id(), vec2(w, w * tex.size_vec2().y / tex.size_vec2().x.max(1.0))));
                }
            });
        });
    });
    ui.add_space(6.0);
}

/// Suggestions shown before the first message.
fn welcome(app: &mut VectorcraftApp, ui: &mut Ui, st: &mut State, t: &Tokens) {
    let who = st.client().map_or_else(|| tl!("your clients").to_string(), |c| c.name.clone());
    let hello = if st.clients.is_empty() {
        tl!("Hi! Add a client first (the menu above: a .91pack from a colleague, or a brand folder). Then paste your content and I'll design it.")
            .to_string()
    } else {
        format!(
            "Hi! I design on-brand infographics for {who}. Paste an article section, notes or copy below: I'll write a brief, lay it out, check it against the brand rules and put it on an artboard for you to finish."
        )
    };
    bubble(ui, &Msg { text: hello, ..Default::default() }, t);
    ui.add_space(4.0);
    ui.horizontal_wrapped(|ui| {
        ui.spacing_mut().item_spacing = vec2(6.0, 6.0);
        if chip(ui, "wand-sparkles", tl!("Infographic from my text"), t) {
            st.instruction.clear();
        }
        if chip(ui, "history", tl!("As a timeline"), t) {
            st.instruction = tl!("use a timeline").into();
        }
        if chip(ui, "layout-grid", tl!("As a comparison"), t) {
            st.instruction = tl!("compare the options side by side").into();
        }
        if can_open(app) && chip(ui, "file-plus", tl!("Import client pack"), t) {
            import_pack(app);
        }
        if can_pick_folder(app) && chip(ui, "folder-open", tl!("Add client from folder"), t) {
            add_folder(app);
        }
    });
}

fn chip(ui: &mut Ui, icon: &str, label: &str, t: &Tokens) -> bool {
    let resp = Frame::new()
        .fill(t.panel_darker)
        .stroke(Stroke::new(1.0, t.border))
        .corner_radius(CornerRadius::same(14))
        .inner_margin(Margin::symmetric(10, 5))
        .show(ui, |ui| {
            ui.horizontal(|ui| {
                ui.spacing_mut().item_spacing.x = 5.0;
                icons::icon(ui, icon, 14.0, t.accent);
                ui.label(RichText::new(label).small().color(t.text));
            });
        })
        .response
        .interact(egui::Sense::click());
    if resp.hovered() {
        ui.ctx().set_cursor_icon(egui::CursorIcon::PointingHand);
    }
    resp.clicked()
}

/// The approval card at the end of the thread; the designer's choice comes back.
fn gate_card(ui: &mut Ui, st: &State, t: &Tokens) -> Option<(String, String)> {
    let gate = st.gate.as_ref()?;
    let kind = s(gate, "kind");
    let p = gate.get("payload").cloned().unwrap_or(Value::Null);
    let mut chosen = None;
    Frame::new().fill(t.panel_darker).stroke(Stroke::new(1.0, t.accent)).corner_radius(CornerRadius::same(10)).inner_margin(Margin::same(10)).show(
        ui,
        |ui| {
            ui.set_width(ui.available_width());
            gate_body(ui, &kind, &p, t);
            let options: Vec<String> = gate
                .get("options")
                .and_then(Value::as_array)
                .map(|a| a.iter().filter_map(Value::as_str).map(str::to_string).collect())
                .unwrap_or_default();
            ui.add_space(6.0);
            ui.horizontal_wrapped(|ui| {
                for o in options.iter().filter(|o| o.as_str() != "change") {
                    let label = match o.as_str() {
                        "approve" => tl!("Approve"),
                        "abandon" => tl!("Stop"),
                        "generate" => tl!("Make a new version"),
                        other => other,
                    };
                    let clicked = if o == "approve" || o == "generate" {
                        widgets::primary_button(ui, label).clicked()
                    } else {
                        widgets::secondary_button(ui, label).clicked()
                    };
                    if clicked && !st.busy {
                        chosen = Some((kind.clone(), o.clone()));
                    }
                }
            });
            if options.iter().any(|o| o == "change") {
                ui.label(RichText::new(tl!("Or type what to change below and send it.")).small().color(t.text_dim));
            }
        },
    );
    chosen
}

fn gate_body(ui: &mut Ui, kind: &str, p: &Value, t: &Tokens) {
    match kind {
        "similar_exists" => {
            ui.label(RichText::new(tl!("A similar design already exists for this client.")).strong());
            for m in p.get("matches").and_then(Value::as_array).into_iter().flatten() {
                ui.label(format!(
                    "“{}” · {} · similarity {:.2}",
                    s(m, "topic"),
                    s(m, "status"),
                    m.get("similarity").and_then(Value::as_f64).unwrap_or(0.0)
                ));
                for (k, v) in m.get("files").and_then(Value::as_object).into_iter().flatten() {
                    ui.label(RichText::new(format!("{k}: {}", v.as_str().unwrap_or_default())).small().color(t.text_dim));
                }
            }
        }
        "outline" => outline(ui, p.get("spec").unwrap_or(&Value::Null), t),
        "final" => {
            ui.label(RichText::new(tl!("Happy with it? Approve to export and save it; your edits on the artboard are included.")).strong());
            for f in p.get("unresolved").and_then(Value::as_array).into_iter().flatten() {
                ui.label(RichText::new(format!("{}: {}", tl!("Not resolved"), s(f, "problem"))).small().color(t.text_dim));
            }
        }
        other => {
            ui.label(other);
        }
    }
}

fn outline(ui: &mut Ui, spec: &Value, t: &Tokens) {
    ui.label(RichText::new(format!("{} · {}", s(spec, "title"), s(spec, "layout"))).strong());
    for it in spec.get("items").and_then(Value::as_array).into_iter().flatten() {
        ui.label(format!("• {}  {}", s(it, "heading"), s(it, "body")));
    }
    for c in spec.get("columns").and_then(Value::as_array).into_iter().flatten() {
        let items: Vec<&str> = c.get("items").and_then(Value::as_array).map(|a| a.iter().filter_map(Value::as_str).collect()).unwrap_or_default();
        ui.label(format!("▸ {}  {} {}", s(c, "title"), s(c, "body"), items.join(" · ")));
    }
    if let Some(table) = spec.get("table") {
        let rows = std::iter::once(table.get("header")).chain(table.get("rows").and_then(Value::as_array).into_iter().flatten().map(Some)).flatten();
        for row in rows {
            let cells: Vec<&str> = row.as_array().map(|a| a.iter().filter_map(Value::as_str).collect()).unwrap_or_default();
            ui.label(RichText::new(cells.join(" | ")).small());
        }
    }
    if !s(spec, "takeaway").is_empty() {
        ui.label(RichText::new(s(spec, "takeaway")).italics().color(t.text_dim));
    }
}

fn import_card(ui: &mut Ui, st: &State, asks: &mut Asks, t: &Tokens) -> bool {
    let Some(pv) = &st.import else { return false };
    let mut done = false;
    Frame::new().fill(t.panel_darker).stroke(Stroke::new(1.0, t.accent)).corner_radius(CornerRadius::same(10)).inner_margin(Margin::same(10)).show(
        ui,
        |ui| {
            ui.set_width(ui.available_width());
            ui.horizontal_wrapped(|ui| {
                for c in pv.get("colors").and_then(Value::as_object).into_iter().flatten().filter_map(|(_, v)| v.as_str().and_then(hex)) {
                    let (r, _) = ui.allocate_exact_size(vec2(14.0, 14.0), egui::Sense::hover());
                    ui.painter().rect_filled(r, CornerRadius::same(3), c);
                }
            });
            let fonts: Vec<&str> =
                pv.get("fonts").and_then(Value::as_array).map(|a| a.iter().filter_map(Value::as_str).collect()).unwrap_or_default();
            let samples = pv.get("samples").and_then(Value::as_array).map_or(0, Vec::len);
            ui.label(RichText::new(format!("{} · {samples} past designs", fonts.join(", "))).small().color(t.text_dim));
            let id = s(pv, "id");
            let exists = pv.get("exists").and_then(Value::as_bool).unwrap_or(false);
            let modes: &[(&str, &str)] =
                if exists { &[("replace", "Replace the installed one"), ("keep_both", "Keep both")] } else { &[("replace", "Install")] };
            ui.horizontal(|ui| {
                for (mode, label) in modes {
                    if widgets::primary_button(ui, tl!(label)).clicked() {
                        asks.push(("install", "POST", format!("/api/clients/import/{id}/install"), Some(json!({ "mode": mode }))));
                        done = true;
                    }
                }
            });
        },
    );
    done
}

/// The message box; returns the text when the designer sends it.
fn composer(ui: &mut Ui, st: &mut State, t: &Tokens) -> Option<String> {
    let hint = if st.gate.as_ref().is_some_and(|g| s(g, "kind") == "outline") {
        tl!("What should change in the outline?")
    } else if st.placed {
        tl!("Ask for an edit, e.g. make the title bigger")
    } else if st.running {
        tl!("Working on it…")
    } else {
        tl!("Paste your content, or describe the infographic…")
    };
    let can_send = !st.busy && !st.draft.trim().is_empty() && (!st.running || st.placed || accepts_notes(st)) && st.client().is_some();
    let mut send = false;
    Frame::new().fill(t.input).stroke(Stroke::new(1.0, t.input_border)).corner_radius(CornerRadius::same(12)).inner_margin(Margin::same(8)).show(
        ui,
        |ui| {
            if !st.instruction.is_empty() {
                ui.horizontal(|ui| {
                    ui.label(RichText::new(format!("{}: {}", tl!("Instruction"), st.instruction)).small().color(t.accent));
                    if ui.small_button("×").clicked() {
                        st.instruction.clear();
                    }
                });
            }
            ui.horizontal(|ui| {
                let w = (ui.available_width() - 40.0).max(60.0);
                // Enter sends, Shift+Enter breaks the line: Enter is taken before the text box sees it, while it has the keyboard.
                let id = ui.make_persistent_id("studio-ai-draft");
                let enter = ui.memory(|m| m.has_focus(id)) && ui.input_mut(|i| i.consume_key(egui::Modifiers::NONE, egui::Key::Enter));
                ui.add(TextEdit::multiline(&mut st.draft).id(id).hint_text(hint).frame(Frame::NONE).desired_rows(3).desired_width(w));
                let (r, resp) = ui.allocate_exact_size(vec2(30.0, 30.0), egui::Sense::click());
                ui.painter().circle_filled(r.center(), 15.0, if can_send { t.accent } else { t.button });
                icons::paint(ui, "dc-arrow-up", r.shrink(8.0), if can_send { t.on_accent } else { t.text_disabled });
                send = can_send && (resp.on_hover_text(tl!("Send (Enter)")).clicked() || enter);
            });
        },
    );
    if send {
        let text = st.draft.trim().to_string();
        st.draft.clear();
        return Some(text);
    }
    None
}

fn accepts_notes(st: &State) -> bool {
    st.gate.as_ref().is_some_and(|g| g.get("options").and_then(Value::as_array).is_some_and(|o| o.iter().any(|x| x == "change")))
}

/// Where a sent message goes: a new design, notes for the outline, or an edit on the artboard.
fn route(st: &mut State, text: String, asks: &mut Asks) {
    st.say(Who::Designer, text.clone(), vec![]);
    st.busy = true;
    let run = st.run.clone().unwrap_or_default();
    let outline_gate = st.gate.as_ref().is_some_and(|g| s(g, "kind") == "outline");
    if st.running && outline_gate {
        asks.push(("decide", "POST", format!("/api/runs/{run}/decide"), Some(json!({"gate": "outline", "decision": "change", "notes": text}))));
    } else if st.running && st.placed {
        st.thinking = tl!("editing the artboard").into();
        asks.push(("message", "POST", format!("/api/runs/{run}/messages"), Some(json!({ "text": text }))));
    } else if st.running && accepts_notes(st) {
        asks.push(("decide", "POST", format!("/api/runs/{run}/decide"), Some(json!({"gate": "final", "decision": "change", "notes": text}))));
    } else if let Some(c) = st.client() {
        let body = json!({"client": c.id, "content": text, "instruction": st.instruction});
        asks.push(("start", "POST", "/api/runs/json".into(), Some(body)));
    }
}

pub fn show(app: &mut VectorcraftApp, ui: &mut Ui) {
    let t = Tokens::get(ui.ctx());
    if app.services.studio_agents.is_none() {
        ui.label(RichText::new(tl!("The AI Design Assistant isn't available in this build.")).color(t.text_dim));
        return;
    }
    let mut st: State = super::pstate(ui.ctx(), "studio-ai");
    let mut asks: Asks = vec![];
    if !st.started {
        st.started = true;
        asks.push(("health", "GET", "/api/health".into(), None));
        asks.push(("clients", "GET", "/api/clients".into(), None));
    }
    absorb(app, ui.ctx(), &mut st);

    header(app, ui, &mut st, &t);
    widgets::divider(ui);
    let thread_h = (ui.available_height() - COMPOSER_H - 12.0).max(120.0);
    let mut decision = None;
    ScrollArea::vertical().max_height(thread_h).auto_shrink([false, false]).stick_to_bottom(true).show(ui, |ui| {
        if st.messages.is_empty() && !st.running {
            welcome(app, ui, &mut st, &t);
        }
        for m in &st.messages {
            bubble(ui, m, &t);
        }
        if import_card(ui, &st, &mut asks, &t) {
            st.import = None;
        }
        decision = gate_card(ui, &st, &t);
        if st.running && st.gate.is_none() && !st.thinking.is_empty() {
            ui.horizontal(|ui| {
                ui.add(egui::Spinner::new().size(12.0));
                ui.label(RichText::new(format!("{}…", st.thinking)).small().color(t.text_dim));
            });
        }
    });
    if let Some((gate, choice)) = decision
        && let Some(run) = st.run.clone()
    {
        st.busy = true;
        asks.push(("decide", "POST", format!("/api/runs/{run}/decide"), Some(json!({"gate": gate, "decision": choice}))));
    }
    ui.add_space(6.0);
    if let Some(text) = composer(ui, &mut st, &t) {
        route(&mut st, text, &mut asks);
    }
    if st.running || st.busy || !st.started {
        ui.ctx().request_repaint_after(std::time::Duration::from_millis(250));
    }
    if let Some(a) = app.services.studio_agents.as_mut() {
        for (tag, method, path, body) in asks {
            a.request(tag, method, &path, body);
        }
    }
    super::set_pstate(ui.ctx(), "studio-ai", st);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn run_file_paths_stay_inside_the_run() {
        assert_eq!(
            file_path("r1", "/home/u/.local/share/91studio/runs/r1/versions/v1_preview.png").as_deref(),
            Some("/api/runs/r1/files/versions/v1_preview.png")
        );
        assert_eq!(file_path("r1", "/elsewhere/v1.png"), None);
    }

    #[test]
    fn events_become_a_conversation() {
        let mut st = State { run: Some("r1".into()), running: true, ..Default::default() };
        let mut fetch = vec![];
        on_event(
            &mut st,
            &json!({"type": "brief", "data": {"topic": "Payments", "angle": "steps", "key_points": ["a", "b"], "suggested_layout": "vertical_list"}}),
            &mut fetch,
        );
        on_event(
            &mut st,
            &json!({"type": "version", "data": {"version": 1, "passes": false, "fails": [{"rule": "15", "problem": "long", "fix": "shorten"}], "preview": "/x/runs/r1/versions/v1_preview.png"}}),
            &mut fetch,
        );
        on_event(
            &mut st,
            &json!({"type": "gate", "data": {"kind": "final", "options": ["approve", "change", "abandon"], "payload": {}}}),
            &mut fetch,
        );
        assert_eq!(st.messages[0].text, "Here's the brief: Payments (steps).");
        assert_eq!(st.messages[1].text, "Version 1 has 1 issue(s); fixing them.");
        assert_eq!(st.messages[1].image_key.as_deref(), Some("v1"));
        assert_eq!(fetch, vec![("v1".to_string(), "/api/runs/r1/files/versions/v1_preview.png".to_string())]);
        assert!(st.gate.is_some() && accepts_notes(&st));
        on_event(&mut st, &json!({"type": "done", "data": {"status": "done", "files": {"png": "/f.png"}}}), &mut fetch);
        assert!(!st.running && st.gate.is_none());
        assert_eq!(st.messages.last().map(|m| m.detail.clone()), Some(vec!["png: /f.png".to_string()]));
        let n = st.messages.len();
        on_event(&mut st, &json!({"type": "error", "data": {"error": "quota"}}), &mut fetch);
        on_event(&mut st, &json!({"type": "done", "data": {"status": "failed", "error": "quota"}}), &mut fetch);
        assert_eq!(st.messages.len(), n + 1, "a failure is said once");
    }

    #[test]
    fn a_message_goes_where_the_conversation_is() {
        let client = Client { id: "fcc".into(), name: "FCC".into(), color: None };
        let mut st = State { clients: vec![client], instruction: "use a timeline".into(), ..Default::default() };
        let mut asks = vec![];
        route(&mut st, "Five steps to pay safely".into(), &mut asks);
        assert_eq!(asks[0].2, "/api/runs/json");
        assert_eq!(asks[0].3.as_ref().map(|b| b["instruction"].clone()), Some(json!("use a timeline")));
        let mut st = State {
            run: Some("r1".into()),
            running: true,
            gate: Some(json!({"kind": "outline", "options": ["approve", "change", "abandon"]})),
            ..Default::default()
        };
        let mut asks = vec![];
        route(&mut st, "fewer items".into(), &mut asks);
        assert_eq!(asks[0].3.as_ref().map(|b| (b["gate"].clone(), b["decision"].clone())), Some((json!("outline"), json!("change"))));
        let mut st = State {
            run: Some("r1".into()),
            running: true,
            placed: true,
            gate: Some(json!({"kind": "final", "options": ["approve", "change", "abandon"]})),
            ..Default::default()
        };
        let mut asks = vec![];
        route(&mut st, "make the title bigger".into(), &mut asks);
        assert_eq!(asks[0].2, "/api/runs/r1/messages");
        assert_eq!(st.messages[0].who as u8, Who::Designer as u8);
    }

    #[test]
    fn colours_and_initials() {
        assert_eq!(hex("#0164F0"), Some(Color32::from_rgb(1, 100, 240)));
        assert_eq!(hex("#0164F01A"), Some(Color32::from_rgb(1, 100, 240)));
        assert_eq!(hex("blue"), None);
    }
}
