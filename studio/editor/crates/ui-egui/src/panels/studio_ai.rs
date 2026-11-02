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
/// How often the panel asks again while the agents start (every [`RETRY_EVERY_S`] seconds: about a minute).
const START_RETRIES: u32 = 40;
const RETRY_EVERY_S: f64 = 1.5;
/// The message box's height.
const COMPOSER_H: f32 = 96.0;

// ── app bar button ──────────────────────────────────────────────────────────────────────────────────

/// The AI button's label, height and width (Control bar; the app bar while the Control bar is hidden).
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
    // 91studio: outlined in the accent, as in the 91Ninjas mockup (D-028).
    if resp.hovered() {
        ui.painter().rect_filled(r, CornerRadius::same(6), t.hover);
    }
    ui.painter().rect_stroke(r, CornerRadius::same(6), egui::Stroke::new(1.0, t.accent), egui::StrokeKind::Inside);
    icons::paint(ui, "sparkles", ir, t.accent);
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

/// The panel's tabs.
#[derive(Clone, Copy, Default, PartialEq)]
enum Tab {
    #[default]
    Chat,
    Clients,
    History,
}

/// An image the designer picked to attach (the dialog answers outside the panel's frame).
static ATTACHED: std::sync::Mutex<Option<String>> = std::sync::Mutex::new(None);
/// The folder of past work picked for a new client (5d); taken into [`Onboarding::folder`] on the next frame.
static PAST_FOLDER: std::sync::Mutex<Option<String>> = std::sync::Mutex::new(None);
/// How often the panel asks how a client draft is going.
const ONBOARD_POLL_S: f64 = 1.5;

/// 5d: a new client drafted from past work (Clients tab): the form, then the draft job's state.
#[derive(Clone, Default)]
struct Onboarding {
    open: bool,
    name: String,
    notes: String,
    folder: Option<String>,
    /// The draft job as the agents report it (`/api/onboard/<id>`).
    job: Option<Value>,
    poll_at: Option<f64>,
    error: String,
}

#[derive(Clone, Default)]
struct State {
    tab: Tab,
    started: bool,
    /// The first health/clients requests failed because the agents are still starting: they are
    /// asked again at `retry_at` (seconds, egui time), up to [`START_RETRIES`] times.
    retry_pending: bool,
    retry_at: Option<f64>,
    retries: u32,
    agents_ok: bool,
    editor_ok: bool,
    status: String,
    clients: Vec<Client>,
    client: usize,
    draft: String,
    /// A layout hint from a suggestion chip ("use a timeline"), sent with the next design.
    instruction: String,
    /// How many versions the next design makes (5c): 1, or 2–3 with different layouts side by side (0 counts as 1).
    versions: u8,
    run: Option<String>,
    running: bool,
    /// What the agents are doing now ("drawing version 2").
    thinking: String,
    messages: Vec<Msg>,
    gate: Option<Value>,
    import: Option<Value>,
    placed: bool,
    busy: bool,
    /// A picture of the content, sent with the next design.
    attachment: Option<String>,
    onboarding: Onboarding,
    /// The History tab: the client's designs, and which client they are for.
    history: Vec<Value>,
    history_for: String,
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
        // 5c: the versions side by side; nothing is editable by chat until one is picked.
        "placed" if d.get("variants").is_some() => {
            st.placed = false;
            let n = d.get("variants").and_then(Value::as_array).map_or(0, Vec::len);
            let text = if d.get("editor").and_then(Value::as_bool).unwrap_or(false) {
                format!("Here are {n} versions side by side, each on its own artboard. Pick the one to keep.")
            } else {
                format!("Here are {n} versions (the editor isn't connected, so they're files for now). Pick the one to keep.")
            };
            st.say(Who::Assistant, text, vec![]);
        }
        "picked" => {
            st.placed = d.get("placed").is_some_and(|p| !p.is_null());
            let text = format!(
                "Kept version {}. The others left the document in one step (Ctrl+Z brings them back); their files stay saved. Edit it by hand, or tell me what to change.",
                s(&d, "letter")
            );
            st.say(Who::Assistant, text, vec![]);
        }
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
        // A key or model failed and another took over (D-027).
        "rotation" => st.say(Who::Note, format!("{} failed; switched to {}", s(&d, "failed"), s(&d, "next")), vec![]),
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
        // The app starts the agents with itself and they take a few seconds: ask again quietly.
        Err(_) if matches!(tag, "health" | "clients") && st.retries < START_RETRIES => {
            st.retry_pending = true;
            st.status = tl!("Starting the AI agents…").into();
            return;
        }
        Err(e) if tag == "health" => {
            st.agents_ok = false;
            st.status = format!("Agents not reachable: {e}");
            return;
        }
        // The Clients tab shows onboarding problems in its own card.
        Err(e) if tag.starts_with("onboard") => {
            st.onboarding.error = e;
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
        "history" => st.history = v.as_array().cloned().unwrap_or_default(),
        "start" => {
            let id = s(&v, "id");
            if !id.is_empty() {
                st.placed = false;
                st.running = true;
                st.thinking = tl!("reading the content").into();
                st.run = Some(id.clone());
                st.attachment = None;
                *follow = Some(id);
            }
        }
        "import" => {
            st.say(Who::Assistant, format!("“{}” v{} by {}: install it?", s(&v, "name"), s(&v, "version"), s(&v, "author")), vec![]);
            st.import = Some(v);
            st.tab = Tab::Chat;
        }
        "onboard" => {
            st.onboarding.error.clear();
            st.onboarding.job = Some(v);
            st.onboarding.poll_at = None;
        }
        "onboard-save" => {
            st.onboarding = Onboarding::default();
            st.say(Who::Assistant, format!("Added client {}. It's selected; paste your content to start.", s(&v, "name")), vec![]);
            st.clients.push(Client { id: s(&v, "client"), name: s(&v, "name"), color: None });
            st.client = st.clients.len().saturating_sub(1);
            st.started = false;
            st.tab = Tab::Chat;
        }
        "install" | "folder" => {
            st.say(Who::Assistant, format!("Added client {}. It's selected; paste your content to start.", s(&v, "client")), vec![]);
            st.import = None;
            st.clients.push(Client { id: s(&v, "client"), name: s(&v, "client"), color: None });
            st.client = st.clients.len().saturating_sub(1);
            st.started = false; // refresh the list (names, colours)
            st.tab = Tab::Chat;
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

/// 5d: "New client from past work": name, folder, notes → the agents measure and draft → the designer reviews
/// (and may edit the draft files) → Save. Polls the job while it runs.
fn onboarding(app: &mut VectorcraftApp, ui: &mut Ui, st: &mut State, asks: &mut Asks, t: &Tokens) {
    if let Some(folder) = PAST_FOLDER.lock().ok().and_then(|mut f| f.take()) {
        st.onboarding.folder = Some(folder);
    }
    let ob = &mut st.onboarding;
    if !ob.open {
        if action_row(ui, "sparkles", tl!("New client from past work…"), t) {
            ob.open = true;
        }
        return;
    }
    let job_status = ob.job.as_ref().map(|j| s(j, "status")).unwrap_or_default();
    let id = ob.job.as_ref().map(|j| s(j, "id")).unwrap_or_default();
    // While the agents measure and draft, ask how it's going every ONBOARD_POLL_S seconds.
    if matches!(job_status.as_str(), "measuring" | "drafting") {
        let now = ui.input(|i| i.time);
        let at = *ob.poll_at.get_or_insert(now + ONBOARD_POLL_S);
        if now >= at {
            ob.poll_at = None;
            asks.push(("onboard", "GET", format!("/api/onboard/{id}"), None));
        }
        ui.ctx().request_repaint_after(std::time::Duration::from_millis(400));
    }
    Frame::new().fill(t.panel_darker).stroke(Stroke::new(1.0, t.accent)).corner_radius(CornerRadius::same(10)).inner_margin(Margin::same(10)).show(
        ui,
        |ui| {
            ui.set_width(ui.available_width());
            ui.label(RichText::new(tl!("New client from past work")).strong());
            match job_status.as_str() {
                "" | "failed" => {
                    ui.label(
                        RichText::new(tl!(
                            "Pick a folder with 3 or more of the client's past designs (SVG, PNG, JPEG). Font files and a guidelines .md or .txt in it help too."
                        ))
                        .small()
                        .color(t.text_dim),
                    );
                    ui.add_space(4.0);
                    ui.add(TextEdit::singleline(&mut ob.name).hint_text(tl!("Client name, e.g. Acme Health")).desired_width(f32::INFINITY));
                    ui.horizontal(|ui| {
                        if can_pick_folder(app) && widgets::secondary_button(ui, tl!("Choose folder…")).clicked() {
                            pick_past_folder(app);
                        }
                        let shown = ob.folder.as_deref().map_or_else(|| tl!("No folder yet").to_string(), short_path);
                        ui.label(RichText::new(shown).small().color(t.text_dim));
                    });
                    ui.add(
                        TextEdit::multiline(&mut ob.notes)
                            .hint_text(tl!("Anything the designs don't show (optional), e.g. never use red"))
                            .desired_rows(2)
                            .desired_width(f32::INFINITY),
                    );
                    if job_status == "failed" {
                        let err = ob.job.as_ref().map(|j| s(j, "error")).unwrap_or_default();
                        ui.label(RichText::new(format!("{}: {err}", tl!("The draft failed"))).small().color(t.text_dim));
                    }
                    let ready = !ob.name.trim().is_empty() && ob.folder.is_some() && !st.busy;
                    ui.horizontal(|ui| {
                        if widgets::primary_button_enabled(ui, tl!("Draft the client"), ready).clicked()
                            && let Some(folder) = ob.folder.clone()
                        {
                            ob.error.clear();
                            asks.push(("onboard", "POST", "/api/onboard".into(), Some(json!({"name": ob.name.trim(), "folder": folder, "notes": ob.notes}))));
                        }
                        if widgets::secondary_button(ui, tl!("Cancel")).clicked() {
                            *ob = Onboarding::default();
                        }
                    });
                }
                "measuring" | "drafting" => {
                    ui.horizontal(|ui| {
                        ui.add(egui::Spinner::new().size(12.0));
                        let what = if job_status == "measuring" {
                            tl!("Measuring the past designs: colours, sizes, corners…")
                        } else {
                            tl!("Drafting the design language and the rulebook…")
                        };
                        ui.label(RichText::new(what).small().color(t.text_dim));
                    });
                }
                _ => {
                    let job = ob.job.clone().unwrap_or(Value::Null);
                    draft_review(ui, &job, t);
                    if job_status == "ready" {
                        let problems = job.pointer("/summary/problems").and_then(Value::as_array).map_or(0, Vec::len);
                        ui.horizontal_wrapped(|ui| {
                            if widgets::primary_button_enabled(ui, tl!("Save client"), problems == 0 && !st.busy).clicked() {
                                asks.push(("onboard-save", "POST", format!("/api/onboard/{id}/save"), Some(json!({}))));
                            }
                            if widgets::secondary_button(ui, tl!("Edit the draft files")).clicked() {
                                let _ = crate::io::open_in_app(app, &s(&job, "dir"));
                            }
                            if widgets::secondary_button(ui, tl!("Reload")).on_hover_text(tl!("Read the draft files again after editing them")).clicked() {
                                asks.push(("onboard", "GET", format!("/api/onboard/{id}"), None));
                            }
                            if widgets::secondary_button(ui, tl!("Discard")).clicked() {
                                asks.push(("onboard-discard", "DELETE", format!("/api/onboard/{id}"), None));
                                *ob = Onboarding::default();
                            }
                        });
                    }
                }
            }
            if !ob.error.is_empty() {
                ui.label(RichText::new(&ob.error).small().color(t.text_dim));
            }
        },
    );
}

/// The draft as the designer checks it: name, description, colours by role, fonts, layouts, rules, problems.
fn draft_review(ui: &mut Ui, job: &Value, t: &Tokens) {
    let sum = job.get("summary").cloned().unwrap_or(Value::Null);
    ui.label(RichText::new(s(&sum, "name")).font(crate::theme::semibold(15.0)).color(t.text_strong));
    if !s(&sum, "description").is_empty() {
        ui.label(RichText::new(s(&sum, "description")).small().color(t.text_dim));
    }
    ui.add_space(4.0);
    ui.horizontal_wrapped(|ui| {
        for (name, v) in sum.get("colors").and_then(Value::as_object).into_iter().flatten() {
            let Some(c) = v.as_str().and_then(hex) else { continue };
            let (r, resp) = ui.allocate_exact_size(vec2(18.0, 18.0), egui::Sense::hover());
            ui.painter().rect_filled(r, CornerRadius::same(4), c);
            ui.painter().rect_stroke(r, CornerRadius::same(4), Stroke::new(1.0, t.border), egui::StrokeKind::Inside);
            resp.on_hover_text(format!("{name} {}", v.as_str().unwrap_or_default()));
        }
    });
    let list = |key: &str| -> String {
        sum.get(key).and_then(Value::as_array).map(|a| a.iter().filter_map(Value::as_str).collect::<Vec<_>>().join(", ")).unwrap_or_default()
    };
    ui.label(RichText::new(format!("{}: {}", tl!("Fonts"), list("fonts"))).small());
    ui.label(RichText::new(format!("{}: {}", tl!("Layouts"), list("layouts"))).small());
    let rules = sum.get("rules").and_then(Value::as_u64).unwrap_or(0);
    ui.label(RichText::new(format!("{rules} {}", tl!("rules for the brand check"))).small());
    for p in sum.get("problems").and_then(Value::as_array).into_iter().flatten().filter_map(Value::as_str) {
        ui.label(RichText::new(format!("{}: {p}", tl!("Fix before saving"))).small().color(t.text_dim));
    }
    ui.label(
        RichText::new(tl!(
            "Check it against the client's work. Edit DESIGN.md and critic_rules.md in the draft folder if anything is off, then Reload."
        ))
        .small()
        .color(t.text_dim),
    );
}

fn short_path(p: &str) -> String {
    let parts: Vec<&str> = p.trim_end_matches('/').rsplit('/').take(2).collect();
    parts.into_iter().rev().collect::<Vec<_>>().join("/")
}

fn pick_past_folder(app: &mut VectorcraftApp) {
    crate::picks::button(app, |app| {
        if let Some(path) = crate::picks::folder(app)
            && let Ok(mut slot) = PAST_FOLDER.lock()
        {
            *slot = Some(path);
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

/// The empty thread: what the assistant does, and the things it can start with.
fn empty_state(app: &mut VectorcraftApp, ui: &mut Ui, st: &mut State, t: &Tokens) {
    ui.vertical_centered(|ui| {
        ui.add_space(18.0);
        let (r, _) = ui.allocate_exact_size(vec2(72.0, 72.0), egui::Sense::hover());
        ui.painter().rect_filled(r, CornerRadius::same(20), t.accent.gamma_multiply(0.16));
        icons::paint(ui, "sparkles", r.shrink(19.0), t.accent);
        ui.add_space(14.0);
        ui.label(RichText::new(tl!("Design with AI")).font(crate::theme::semibold(18.0)).color(t.text_strong));
        ui.add_space(4.0);
        let line = match st.client() {
            None => tl!("Add a client first: a .91pack from a colleague, or a brand folder.").to_string(),
            Some(c) => format!("Turn your content into on-brand infographics for {}, then finish them on the artboard.", c.name),
        };
        ui.add(egui::Label::new(RichText::new(line).color(t.text_dim)).wrap());
    });
    ui.add_space(16.0);
    let rows: [(&str, &str, Option<&str>, u8); 4] = [
        ("wand-sparkles", tl!("Infographic from my text"), None, 1),
        ("copy", tl!("Three versions to choose from"), None, 3),
        ("history", tl!("As a timeline"), Some("use a timeline"), 1),
        ("layout-grid", tl!("As a comparison"), Some("compare the options side by side"), 1),
    ];
    for (icon, label, instruction, versions) in rows {
        if action_row(ui, icon, label, t) {
            st.instruction = instruction.map(|i| tl!(i).to_string()).unwrap_or_default();
            st.versions = versions;
            ui.memory_mut(|m| m.request_focus(ui.make_persistent_id("studio-ai-draft")));
        }
    }
    if action_row(ui, "file-plus", tl!("Add a client"), t) {
        st.tab = Tab::Clients;
    }
    let _ = app;
}

/// A full-width row: icon, label, chevron. Returns whether it was clicked.
fn action_row(ui: &mut Ui, icon: &str, label: &str, t: &Tokens) -> bool {
    let w = ui.available_width();
    let (r, resp) = ui.allocate_exact_size(vec2(w, 42.0), egui::Sense::click());
    ui.painter().rect_filled(r, CornerRadius::same(10), if resp.hovered() { t.hover } else { t.panel_darker });
    ui.painter().rect_stroke(r, CornerRadius::same(10), Stroke::new(1.0, t.input_border), egui::StrokeKind::Inside);
    icons::paint(ui, icon, egui::Rect::from_center_size(r.left_center() + vec2(22.0, 0.0), vec2(17.0, 17.0)), t.accent);
    ui.painter().text(r.left_center() + vec2(42.0, 0.0), egui::Align2::LEFT_CENTER, label, egui::FontId::proportional(13.0), t.text);
    icons::paint(ui, "chevron-right", egui::Rect::from_center_size(r.right_center() - vec2(18.0, 0.0), vec2(14.0, 14.0)), t.text_dim);
    if resp.hovered() {
        ui.ctx().set_cursor_icon(egui::CursorIcon::PointingHand);
    }
    ui.add_space(8.0);
    resp.clicked()
}

/// Chat · Clients · History, underlined in the accent.
fn tab_bar(ui: &mut Ui, st: &mut State, t: &Tokens) {
    ui.horizontal(|ui| {
        ui.spacing_mut().item_spacing.x = 18.0;
        for (tab, label) in [(Tab::Chat, tl!("Chat")), (Tab::Clients, tl!("Clients")), (Tab::History, tl!("History"))] {
            let on = st.tab == tab;
            let galley = ui.painter().layout_no_wrap(label.to_string(), crate::theme::semibold(13.0), if on { t.accent } else { t.text_dim });
            let (r, resp) = ui.allocate_exact_size(vec2(galley.size().x, 30.0), egui::Sense::click());
            ui.painter().galley(egui::pos2(r.left(), r.center().y - galley.size().y / 2.0 - 2.0), galley, if on { t.accent } else { t.text_dim });
            if on {
                ui.painter().line_segment([r.left_bottom(), r.right_bottom()], Stroke::new(2.0, t.accent));
            }
            if resp.clicked() {
                st.tab = tab;
            }
        }
    });
    widgets::divider(ui);
}

/// The Clients tab: the installed clients (a click picks one) and the ways to add one.
fn clients_tab(app: &mut VectorcraftApp, ui: &mut Ui, st: &mut State, asks: &mut Asks, t: &Tokens) {
    ScrollArea::vertical().auto_shrink([false, false]).show(ui, |ui| {
        onboarding(app, ui, st, asks, t);
        ui.add_space(10.0);
        if st.clients.is_empty() {
            ui.label(RichText::new(tl!("No clients yet.")).color(t.text_dim));
        }
        for i in 0..st.clients.len() {
            let Some(c) = st.clients.get(i).cloned() else { continue };
            let w = ui.available_width();
            let (r, resp) = ui.allocate_exact_size(vec2(w, 44.0), egui::Sense::click());
            let on = i == st.client;
            ui.painter().rect_filled(
                r,
                CornerRadius::same(10),
                if on {
                    t.row_selected
                } else if resp.hovered() {
                    t.hover
                } else {
                    t.panel_darker
                },
            );
            let chip = egui::Rect::from_center_size(r.left_center() + vec2(24.0, 0.0), vec2(28.0, 28.0));
            ui.painter().rect_filled(chip, CornerRadius::same(7), c.color.unwrap_or(t.accent));
            ui.painter().text(chip.center(), egui::Align2::CENTER_CENTER, initials(&c.name), egui::FontId::proportional(11.5), Color32::WHITE);
            ui.painter().text(r.left_center() + vec2(48.0, 0.0), egui::Align2::LEFT_CENTER, &c.name, egui::FontId::proportional(13.0), t.text_strong);
            if resp.clicked() {
                st.client = i;
                st.tab = Tab::Chat;
            }
            ui.add_space(6.0);
        }
        ui.add_space(10.0);
        if can_open(app) && action_row(ui, "file-plus", tl!("Import a client pack (.91pack)…"), t) {
            import_pack(app);
        }
        if can_pick_folder(app) && action_row(ui, "folder-open", tl!("Add a client from a brand folder…"), t) {
            add_folder(app);
        }
        ui.label(
            RichText::new(tl!("A brand folder holds DESIGN.md, critic_rules.md, past/ (3 or more old infographics) and assets/fonts."))
                .small()
                .color(t.text_dim),
        );
    });
}

/// The History tab: the client's designs, newest first, with buttons that open their files.
fn history_tab(app: &mut VectorcraftApp, ui: &mut Ui, st: &State, t: &Tokens) {
    let who = st.client().map(|c| c.name.clone()).unwrap_or_default();
    ui.label(RichText::new(format!("{} {who}", tl!("Designs for"))).strong().color(t.text_strong));
    ui.add_space(6.0);
    ScrollArea::vertical().auto_shrink([false, false]).show(ui, |ui| {
        if st.history.is_empty() {
            ui.label(RichText::new(tl!("No designs yet. Start one in Chat.")).color(t.text_dim));
        }
        for d in &st.history {
            Frame::new()
                .fill(t.panel_darker)
                .stroke(Stroke::new(1.0, t.input_border))
                .corner_radius(CornerRadius::same(10))
                .inner_margin(Margin::same(10))
                .show(ui, |ui| {
                    ui.set_width(ui.available_width());
                    ui.add(egui::Label::new(RichText::new(s(d, "topic")).strong().color(t.text_strong)).wrap());
                    let date = s(d, "approved_at");
                    let date = if date.is_empty() { s(d, "created_at") } else { date };
                    ui.label(
                        RichText::new(format!("{} · {} · {}", s(d, "status"), s(d, "layout"), date.get(..10).unwrap_or_default()))
                            .small()
                            .color(t.text_dim),
                    );
                    ui.horizontal_wrapped(|ui| {
                        for (k, v) in d.get("files").and_then(Value::as_object).into_iter().flatten() {
                            let Some(path) = v.as_str() else { continue };
                            if ui.small_button(format!("{} {}", tl!("Open"), k.to_uppercase())).on_hover_text(path).clicked() {
                                app.run("file.open", json!({ "path": path })).ok();
                            }
                        }
                    });
                });
            ui.add_space(8.0);
        }
    });
}

fn initials(name: &str) -> String {
    let i: String = name.split_whitespace().filter_map(|w| w.chars().next()).filter(|c| c.is_alphanumeric()).take(2).collect();
    if i.is_empty() { "?".into() } else { i.to_uppercase() }
}

/// The approval card at the end of the thread; the designer's choice comes back.
/// The gate's card; a click returns (gate, decision, choice): `choice` is the version picked at the `pick` gate.
fn gate_card(ui: &mut Ui, st: &State, t: &Tokens) -> Option<(String, String, Option<usize>)> {
    let gate = st.gate.as_ref()?;
    let kind = s(gate, "kind");
    let p = gate.get("payload").cloned().unwrap_or(Value::Null);
    let mut chosen = None;
    Frame::new().fill(t.panel_darker).stroke(Stroke::new(1.0, t.accent)).corner_radius(CornerRadius::same(10)).inner_margin(Margin::same(10)).show(
        ui,
        |ui| {
            ui.set_width(ui.available_width());
            gate_body(ui, &kind, &p, t);
            if kind == "pick"
                && let Some(k) = pick_versions(ui, st, &p, t)
            {
                chosen = Some((kind.clone(), "pick".to_string(), Some(k)));
            }
            let options: Vec<String> = gate
                .get("options")
                .and_then(Value::as_array)
                .map(|a| a.iter().filter_map(Value::as_str).map(str::to_string).collect())
                .unwrap_or_default();
            ui.add_space(6.0);
            ui.horizontal_wrapped(|ui| {
                for o in options.iter().filter(|o| o.as_str() != "change" && o.as_str() != "pick") {
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
                        chosen = Some((kind.clone(), o.clone(), None));
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
        "outline" => match p.get("variants").and_then(Value::as_array) {
            // 5c: one outline per version.
            Some(vs) => {
                ui.label(RichText::new(format!("{} versions with different layouts:", vs.len())).strong());
                for v in vs {
                    ui.add_space(4.0);
                    ui.label(RichText::new(format!("{} {}", tl!("Version"), s(v, "letter"))).color(t.accent));
                    outline(ui, v.get("spec").unwrap_or(&Value::Null), t);
                }
            }
            None => outline(ui, p.get("spec").unwrap_or(&Value::Null), t),
        },
        "pick" => {
            ui.label(RichText::new(tl!("Which version do you want to keep?")).strong());
        }
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

/// 5c: one row per version (preview, letter, layout, title) with its Keep button. Returns the version clicked.
fn pick_versions(ui: &mut Ui, st: &State, p: &Value, t: &Tokens) -> Option<usize> {
    let mut picked = None;
    for (i, v) in p.get("variants").and_then(Value::as_array).into_iter().flatten().enumerate() {
        ui.add_space(4.0);
        ui.horizontal(|ui| {
            let key = format!("v{}", v.get("version").and_then(Value::as_u64).unwrap_or(0));
            if let Some(tex) = st.messages.iter().find(|m| m.image_key.as_deref() == Some(key.as_str())).and_then(|m| m.image.as_ref()) {
                let size = tex.size_vec2();
                let h = 72.0;
                let w = if size.y > 0.0 { (size.x / size.y * h).min(110.0) } else { h };
                ui.add(egui::Image::new(tex).fit_to_exact_size(vec2(w, h)).corner_radius(CornerRadius::same(4)));
            }
            ui.vertical(|ui| {
                ui.label(RichText::new(format!("{} {} · {}", tl!("Version"), s(v, "letter"), s(v, "layout"))).strong());
                ui.label(RichText::new(s(v, "title")).small().color(t.text_dim));
                let n = v.get("unresolved").and_then(Value::as_array).map_or(0, Vec::len);
                if n > 0 {
                    ui.label(RichText::new(format!("{n} {}", tl!("brand check issue(s) left"))).small().color(t.text_dim));
                }
                if widgets::primary_button(ui, &format!("{} {}", tl!("Keep"), s(v, "letter"))).clicked() && !st.busy {
                    picked = Some(i);
                }
            });
        });
    }
    picked
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

/// The message box: the text, an image to attach, and send. Returns the text when the designer sends it.
fn composer(app: &mut VectorcraftApp, ui: &mut Ui, st: &mut State, t: &Tokens) -> Option<String> {
    if let Some(path) = ATTACHED.lock().ok().and_then(|mut a| a.take()) {
        st.attachment = Some(path);
    }
    let hint = if st.gate.as_ref().is_some_and(|g| s(g, "kind") == "outline") {
        tl!("What should change in the outline?")
    } else if st.gate.as_ref().is_some_and(|g| s(g, "kind") == "pick") {
        tl!("Type A, B or C, or press Keep above")
    } else if st.placed {
        tl!("Ask for an edit, e.g. make the title bigger")
    } else if st.running {
        tl!("Working on it…")
    } else {
        tl!("Describe what you want, or paste your content…")
    };
    let has_input = !st.draft.trim().is_empty() || (st.attachment.is_some() && !st.running);
    let picking = st.gate.as_ref().is_some_and(|g| s(g, "kind") == "pick");
    let can_send = !st.busy && has_input && (!st.running || st.placed || accepts_notes(st) || picking) && st.client().is_some();
    let mut send = false;
    Frame::new().fill(t.input).stroke(Stroke::new(1.0, t.input_border)).corner_radius(CornerRadius::same(12)).inner_margin(Margin::same(10)).show(
        ui,
        |ui| {
            ui.set_width(ui.available_width());
            if !st.instruction.is_empty() && ui.small_button(format!("{}: {}  ×", tl!("Instruction"), st.instruction)).clicked() {
                st.instruction.clear();
            }
            // Enter sends, Shift+Enter breaks the line: Enter is taken before the text box sees it, while it has the keyboard.
            let id = ui.make_persistent_id("studio-ai-draft");
            let enter = ui.memory(|m| m.has_focus(id)) && ui.input_mut(|i| i.consume_key(egui::Modifiers::NONE, egui::Key::Enter));
            ui.add(TextEdit::multiline(&mut st.draft).id(id).hint_text(hint).frame(Frame::NONE).desired_rows(2).desired_width(f32::INFINITY));
            ui.horizontal(|ui| {
                let can_attach = can_open(app) && !st.running;
                let attach =
                    ui.add_enabled_ui(can_attach, |ui| widgets::icon_button(ui, "image", tl!("Attach a picture of the content"), false, 26.0)).inner;
                if attach.clicked() {
                    attach_picture(app);
                }
                // 5c: how many versions the next design makes.
                if !st.running {
                    versions_picker(ui, st, t);
                }
                if let Some(a) = st.attachment.clone() {
                    let name = std::path::Path::new(&a).file_name().map(|n| n.to_string_lossy().to_string()).unwrap_or(a);
                    if ui.small_button(format!("{name}  ×")).clicked() {
                        st.attachment = None;
                    }
                }
                ui.with_layout(Layout::right_to_left(Align::Center), |ui| {
                    let (r, resp) = ui.allocate_exact_size(vec2(34.0, 34.0), egui::Sense::click());
                    ui.painter().rect_filled(r, CornerRadius::same(9), if can_send { t.accent_strong } else { t.button });
                    icons::paint(ui, "dc-arrow-up", r.shrink(9.0), if can_send { t.on_accent } else { t.text_disabled });
                    send = can_send && (resp.on_hover_text(tl!("Send (Enter)")).clicked() || enter);
                });
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

/// "Versions 1 2 3": how many versions the next design makes (5c).
fn versions_picker(ui: &mut Ui, st: &mut State, t: &Tokens) {
    ui.label(RichText::new(tl!("Versions")).small().color(t.text_dim)).on_hover_text(tl!("Make 2 or 3 versions with different layouts and pick one"));
    for n in 1..=3u8 {
        let on = st.versions.max(1) == n;
        let (r, resp) = ui.allocate_exact_size(vec2(20.0, 20.0), egui::Sense::click());
        if on {
            ui.painter().rect_filled(r, CornerRadius::same(5), t.accent_strong);
        } else if resp.hovered() {
            ui.painter().rect_filled(r, CornerRadius::same(5), t.hover);
        }
        let color = if on { t.on_accent } else { t.text };
        ui.painter().text(r.center(), egui::Align2::CENTER_CENTER, n.to_string(), crate::theme::semibold(11.5), color);
        if resp.clicked() {
            st.versions = n;
        }
    }
}

/// Pick a picture of the content (PNG, JPEG, WebP) through [`crate::picks`]; it reaches the panel by [`ATTACHED`].
fn attach_picture(app: &mut VectorcraftApp) {
    crate::picks::button(app, |app| {
        let pick = FilePick { filters: vec![FilePick::filter("Image", &["png", "jpg", "jpeg", "webp"])], ..Default::default() };
        if let Some(path) = crate::picks::open(app, &pick)
            && let Ok(mut slot) = ATTACHED.lock()
        {
            *slot = Some(path);
        }
    });
}

fn accepts_notes(st: &State) -> bool {
    st.gate.as_ref().is_some_and(|g| g.get("options").and_then(Value::as_array).is_some_and(|o| o.iter().any(|x| x == "change")))
}

/// The version a typed reply picks at the `pick` gate: its letter alone or after "version"/"keep" (`n` versions).
fn picked_letter(text: &str, n: usize) -> Option<usize> {
    let t = text.trim().trim_end_matches(['.', '!']).to_ascii_lowercase();
    let t = t.strip_prefix("keep ").unwrap_or(&t);
    let t = t.strip_prefix("version ").unwrap_or(t).trim();
    let mut chars = t.chars();
    let (Some(c), None) = (chars.next(), chars.next()) else { return None };
    let k = (c as usize).checked_sub('a' as usize)?;
    (k < n).then_some(k)
}

/// Where a sent message goes: a new design, notes for the outline, or an edit on the artboard.
fn route(st: &mut State, text: String, asks: &mut Asks) {
    let shown = match (&st.attachment, text.is_empty(), st.running) {
        (Some(_), true, false) => tl!("(a picture of the content)").to_string(),
        _ => text.clone(),
    };
    st.say(Who::Designer, shown, vec![]);
    st.busy = true;
    let run = st.run.clone().unwrap_or_default();
    let outline_gate = st.gate.as_ref().is_some_and(|g| s(g, "kind") == "outline");
    let pick_gate = st.gate.as_ref().is_some_and(|g| s(g, "kind") == "pick");
    if st.running && pick_gate {
        // "B", "b", "version b", "keep B": the letter picks; anything else asks for a pick.
        let n = st.gate.as_ref().and_then(|g| g.pointer("/payload/variants")).and_then(Value::as_array).map_or(0, Vec::len);
        match picked_letter(&text, n) {
            Some(k) => {
                asks.push(("decide", "POST", format!("/api/runs/{run}/decide"), Some(json!({"gate": "pick", "decision": "pick", "choice": k}))))
            }
            None => {
                st.busy = false;
                st.say(Who::Assistant, tl!("Pick a version first: press Keep under the one you want, or type its letter."), vec![]);
            }
        }
    } else if st.running && outline_gate {
        asks.push(("decide", "POST", format!("/api/runs/{run}/decide"), Some(json!({"gate": "outline", "decision": "change", "notes": text}))));
    } else if st.running && st.placed {
        st.thinking = tl!("editing the artboard").into();
        asks.push(("message", "POST", format!("/api/runs/{run}/messages"), Some(json!({ "text": text }))));
    } else if st.running && accepts_notes(st) {
        asks.push(("decide", "POST", format!("/api/runs/{run}/decide"), Some(json!({"gate": "final", "decision": "change", "notes": text}))));
    } else if let Some(c) = st.client() {
        let body =
            json!({"client": c.id, "content": text, "instruction": st.instruction, "image_path": st.attachment, "variants": st.versions.clamp(1, 3)});
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
    if st.retry_pending {
        let now = ui.input(|i| i.time);
        let at = *st.retry_at.get_or_insert(now + RETRY_EVERY_S);
        if now >= at {
            st.retry_pending = false;
            st.retry_at = None;
            st.retries += 1;
            asks.push(("health", "GET", "/api/health".into(), None));
            asks.push(("clients", "GET", "/api/clients".into(), None));
        }
    }

    tab_bar(ui, &mut st, &t);
    ui.add_space(6.0);
    match st.tab {
        Tab::Chat => chat_tab(app, ui, &mut st, &mut asks, &t),
        Tab::Clients => clients_tab(app, ui, &mut st, &mut asks, &t),
        Tab::History => {
            let id = st.client().map(|c| c.id.clone()).unwrap_or_default();
            if !id.is_empty() && st.history_for != id {
                st.history_for = id.clone();
                st.history.clear();
                asks.push(("history", "GET", format!("/api/history?client={id}"), None));
            }
            history_tab(app, ui, &st, &t);
        }
    }
    if st.running || st.busy || !st.started || st.retry_pending {
        ui.ctx().request_repaint_after(std::time::Duration::from_millis(250));
    }
    if let Some(a) = app.services.studio_agents.as_mut() {
        for (tag, method, path, body) in asks {
            a.request(tag, method, &path, body);
        }
    }
    super::set_pstate(ui.ctx(), "studio-ai", st);
}

/// The Chat tab: the client, the conversation (or what to start with), and the message box.
fn chat_tab(app: &mut VectorcraftApp, ui: &mut Ui, st: &mut State, asks: &mut Asks, t: &Tokens) {
    header(app, ui, st, t);
    ui.add_space(4.0);
    let thread_h = (ui.available_height() - COMPOSER_H - 12.0).max(120.0);
    let mut decision = None;
    // Follow new messages; an empty thread starts at its top (the welcome).
    let follow = !st.messages.is_empty() || st.running;
    ScrollArea::vertical().max_height(thread_h).auto_shrink([false, false]).stick_to_bottom(follow).show(ui, |ui| {
        if st.messages.is_empty() && !st.running {
            empty_state(app, ui, st, t);
        }
        for m in &st.messages {
            bubble(ui, m, t);
        }
        if import_card(ui, st, asks, t) {
            st.import = None;
        }
        decision = gate_card(ui, st, t);
        if st.running && st.gate.is_none() && !st.thinking.is_empty() {
            ui.horizontal(|ui| {
                ui.add(egui::Spinner::new().size(12.0));
                ui.label(RichText::new(format!("{}…", st.thinking)).small().color(t.text_dim));
            });
        }
    });
    if let Some((gate, decision, choice)) = decision
        && let Some(run) = st.run.clone()
    {
        st.busy = true;
        asks.push(("decide", "POST", format!("/api/runs/{run}/decide"), Some(json!({"gate": gate, "decision": decision, "choice": choice}))));
    }
    ui.add_space(6.0);
    if let Some(text) = composer(app, ui, st, t) {
        route(st, text, asks);
    }
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

    /// 5c: asking for versions, the pick by letter, and what the thread says about them.
    #[test]
    fn versions_are_asked_for_picked_by_letter_and_told() {
        let client = Client { id: "fcc".into(), name: "FCC".into(), color: None };
        let mut st = State { clients: vec![client], versions: 3, ..Default::default() };
        let mut asks = vec![];
        route(&mut st, "Five steps to pay safely".into(), &mut asks);
        assert_eq!(asks[0].3.as_ref().map(|b| b["variants"].clone()), Some(json!(3)));
        // No choice made: one version.
        let mut st = State { clients: st.clients.clone(), ..Default::default() };
        let mut asks = vec![];
        route(&mut st, "Five steps".into(), &mut asks);
        assert_eq!(asks[0].3.as_ref().map(|b| b["variants"].clone()), Some(json!(1)));

        assert_eq!(picked_letter("B", 3), Some(1));
        assert_eq!(picked_letter(" keep version c. ", 3), Some(2));
        assert_eq!(picked_letter("c", 2), None);
        assert_eq!(picked_letter("add it back", 3), None);
        let gate = json!({"kind": "pick", "options": ["pick", "abandon"], "payload": {"variants": [{"letter": "A"}, {"letter": "B"}]}});
        let mut st = State { run: Some("r1".into()), running: true, gate: Some(gate), ..Default::default() };
        let mut asks = vec![];
        route(&mut st, "b".into(), &mut asks);
        assert_eq!(asks[0].3.as_ref().map(|b| (b["gate"].clone(), b["choice"].clone())), Some((json!("pick"), json!(1))));
        let mut asks = vec![];
        route(&mut st, "the second one please".into(), &mut asks);
        assert!(asks.is_empty() && !st.busy, "anything else asks for a pick");

        let mut st = State::default();
        let mut fetch = vec![];
        on_event(&mut st, &json!({"type": "placed", "data": {"editor": true, "variants": [{}, {}, {}]}}), &mut fetch);
        assert!(!st.placed && st.messages.last().is_some_and(|m| m.text.contains("3 versions side by side")));
        on_event(&mut st, &json!({"type": "picked", "data": {"letter": "B", "placed": {"group_id": 7}}}), &mut fetch);
        assert!(st.placed && st.messages.last().is_some_and(|m| m.text.starts_with("Kept version B")));
    }

    /// 5d: the draft job reaches the Clients tab; saving adds and selects the client; failures stay in the card.
    #[test]
    fn a_client_draft_is_followed_and_saved() {
        let mut st = State::default();
        let mut follow = None;
        on_answer(&mut st, "onboard", Ok(json!({"id": "j1", "status": "drafting"})), &mut follow);
        assert_eq!(st.onboarding.job.as_ref().map(|j| s(j, "status")).as_deref(), Some("drafting"));
        on_answer(&mut st, "onboard", Err("the folder needs at least 3 past designs".into()), &mut follow);
        assert!(st.onboarding.error.contains("at least 3") && st.messages.is_empty(), "shown in the card, not the chat");
        st.onboarding.open = true;
        on_answer(&mut st, "onboard-save", Ok(json!({"client": "acme-health", "name": "Acme Health"})), &mut follow);
        assert!(!st.onboarding.open && st.onboarding.job.is_none());
        assert_eq!(st.clients.last().map(|c| c.id.as_str()), Some("acme-health"));
        assert_eq!(st.client, 0);
        assert!(matches!(st.tab, Tab::Chat) && !st.started);
        assert_eq!(short_path("/home/u/work/acme/past"), "acme/past");
    }

    #[test]
    fn colours_and_initials() {
        assert_eq!(hex("#0164F0"), Some(Color32::from_rgb(1, 100, 240)));
        assert_eq!(hex("#0164F01A"), Some(Color32::from_rgb(1, 100, 240)));
        assert_eq!(hex("blue"), None);
    }
}
