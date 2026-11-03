//! 91studio: the AI Design Assistant panel, the companion to Claude (D-033). The designing happens in a
//! conversation with Claude (Claude Code or Claude Desktop), through the 91Ninjas Studio connector; this panel
//! shows whether that's connected and how to connect it, what Claude is doing in the editor right now, the
//! clients (packs, brand folders, new ones from past work with Claude) and each client's design history.
//! The panel only draws and asks: all I/O goes through [`crate::studio::StudioAgents`] (D-021, D-033).

use egui::{Color32, CornerRadius, Frame, Margin, RichText, ScrollArea, Stroke, TextEdit, Ui, vec2};
use serde_json::{Value, json};

use crate::VectorcraftApp;
use crate::services::FilePick;
use crate::studio::StudioReply;
use crate::theme::Tokens;
use crate::{icons, widgets};

pub const ID: &str = "studioAi";
/// How often the panel asks again while the local service starts (every [`RETRY_EVERY_S`] seconds: five minutes,
/// for an installed app's first start, which fetches Python and its packages, D-040).
const START_RETRIES: u32 = 200;
const RETRY_EVERY_S: f64 = 1.5;
/// How often the activity feed is refreshed.
const ACTIVITY_EVERY_S: f64 = 2.0;
/// Activity lines kept.
const MAX_ACTIVITY: usize = 60;
const CLAUDE_CODE_URL: &str = "https://claude.com/claude-code";

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
    Claude,
    Clients,
    History,
}

/// The folder picked for a new client (the dialog answers outside the panel's frame).
static PAST_FOLDER: std::sync::Mutex<Option<String>> = std::sync::Mutex::new(None);

#[derive(Clone, Default)]
struct State {
    tab: Tab,
    started: bool,
    /// The first requests failed because the local service is still starting: asked again at `retry_at`.
    retry_pending: bool,
    retry_at: Option<f64>,
    retries: u32,
    agents_ok: bool,
    editor_ok: bool,
    status: String,
    clients: Vec<Client>,
    client: usize,
    /// How to connect Claude (`/api/connect`).
    connect: Option<Value>,
    /// When `/api/connect` was last asked (asked again while no Claude app is set up, to notice the set-up).
    connect_at: Option<f64>,
    show_connect: bool,
    /// What Claude did, oldest first (`/api/activity`), and when it was last asked.
    activity: Vec<Value>,
    activity_at: Option<f64>,
    /// A client pack being imported (its preview), waiting for Install.
    import: Option<Value>,
    /// New client from past work: its name and folder, for the request to Claude.
    new_name: String,
    new_folder: Option<String>,
    /// The team's shared clients (`/api/team`, D-037) and when they were last asked about.
    team: Option<Value>,
    team_at: Option<f64>,
    join_repo: String,
    join_key: String,
    join_name: String,
    join_password: String,
    /// What the team is busy with ("Sharing fcc with the team…"), shown with a spinner until the answer.
    team_working: String,
    /// The History tab: the client's designs, and which client they are for.
    history: Vec<Value>,
    history_for: String,
    note: String,
    busy: bool,
}

impl State {
    fn client(&self) -> Option<&Client> {
        self.clients.get(self.client)
    }
}

type Asks = Vec<(&'static str, &'static str, String, Option<Value>)>;

// ── helpers ─────────────────────────────────────────────────────────────────────────────────────────

fn s(v: &Value, key: &str) -> String {
    v.get(key).and_then(Value::as_str).unwrap_or_default().to_string()
}
fn hex(c: &str) -> Option<Color32> {
    let c = c.strip_prefix('#')?;
    let n = u32::from_str_radix(c.get(..6)?, 16).ok()?;
    Some(Color32::from_rgb((n >> 16) as u8, (n >> 8) as u8, n as u8))
}
fn short_path(p: &str) -> String {
    let parts: Vec<&str> = p.trim_end_matches('/').rsplit('/').take(2).collect();
    parts.into_iter().rev().collect::<Vec<_>>().join("/")
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
fn initials(name: &str) -> String {
    let i: String = name.split_whitespace().filter_map(|w| w.chars().next()).filter(|c| c.is_alphanumeric()).take(2).collect();
    if i.is_empty() { "?".into() } else { i.to_uppercase() }
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

// ── answers ─────────────────────────────────────────────────────────────────────────────────────────

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

fn on_answer(st: &mut State, tag: &str, result: Result<Value, String>) {
    st.busy = false;
    let v = match result {
        Ok(v) => v,
        // The app starts the local service with itself and it takes a few seconds: ask again quietly.
        Err(_) if matches!(tag, "health" | "clients" | "connect") && st.retries < START_RETRIES => {
            st.retry_pending = true;
            st.status = if st.retries > 8 { tl!("Setting up the AI tools (first start only, a few minutes)…") } else { tl!("Starting…") }.into();
            return;
        }
        Err(e) if tag == "health" => {
            st.agents_ok = false;
            st.status = format!("Not reachable: {e}");
            return;
        }
        Err(_) if tag == "activity" || tag == "team" => return,
        Err(e) => {
            st.team_working.clear();
            st.note = format!("{}: {e}", tl!("That didn't work"));
            return;
        }
    };
    match tag {
        "health" => {
            st.agents_ok = true;
            st.editor_ok = v.get("editor").and_then(Value::as_bool).unwrap_or(false);
            st.status = if !s(&v, "setup").is_empty() {
                s(&v, "setup")
            } else if st.editor_ok {
                tl!("Ready for Claude").into()
            } else {
                tl!("Ready · editor connection off").into()
            };
            // Asked again while a first-start setup runs, so the line changes when it's done.
            if !s(&v, "setup").is_empty() {
                st.retry_pending = true;
            }
        }
        "clients" => on_clients(st, &v),
        "connect" => st.connect = Some(v),
        "history" => st.history = v.as_array().cloned().unwrap_or_default(),
        "activity" => {
            st.activity.extend(v.as_array().cloned().unwrap_or_default());
            let over = st.activity.len().saturating_sub(MAX_ACTIVITY);
            st.activity.drain(..over);
        }
        "import" => {
            st.note = format!("“{}” v{} by {}: install it?", s(&v, "name"), s(&v, "version"), s(&v, "author"));
            st.import = Some(v);
        }
        "team" => st.team = Some(v),
        "team-change" => {
            st.team_working.clear();
            st.team = Some(v);
            st.started = false; // installed or reverted clients: refresh the list
        }
        "install" | "folder" => {
            st.note = format!("{} {}.", tl!("Added client"), s(&v, "client"));
            st.import = None;
            st.started = false; // refresh the list
        }
        _ => {}
    }
}

fn absorb(app: &mut VectorcraftApp, st: &mut State) {
    let Some(agents) = app.services.studio_agents.as_mut() else { return };
    for r in agents.poll() {
        if let StudioReply::Answer { tag, result } = r {
            on_answer(st, &tag, result);
        }
    }
}

// ── the Claude tab ──────────────────────────────────────────────────────────────────────────────────

/// Plain words for what a connector tool did.
fn tool_label(tool: &str) -> &str {
    match tool {
        "list_clients" => tl!("Looked at the clients"),
        "get_brand" => tl!("Read the brand"),
        "check_similar" => tl!("Checked for similar designs"),
        "start_design" => tl!("Started a design"),
        "render_design" => tl!("Drew a version"),
        "place_design" => tl!("Placed it on an artboard"),
        "remove_versions" => tl!("Removed versions"),
        "look_at_editor" => tl!("Looked at the artboard"),
        "current_file" => tl!("Checked the open file"),
        "edit_design" => tl!("Edited the design"),
        "approve_design" => tl!("Approved and exported"),
        "abandon_design" => tl!("Dropped a design"),
        "list_designs" => tl!("Looked at the designs"),
        "measure_past_work" => tl!("Measured past work"),
        "save_new_client" => tl!("Saved a new client"),
        other => other,
    }
}

fn copy_row(ui: &mut Ui, text: &str, t: &Tokens) {
    Frame::new().fill(t.input).stroke(Stroke::new(1.0, t.input_border)).corner_radius(CornerRadius::same(6)).inner_margin(Margin::same(8)).show(
        ui,
        |ui| {
            ui.set_width(ui.available_width());
            ui.add(egui::Label::new(RichText::new(text).monospace().size(11.0).color(t.text)).wrap());
            if widgets::secondary_button(ui, tl!("Copy")).clicked() {
                ui.ctx().copy_text(text.to_string());
            }
        },
    );
}

fn connect_card(app: &mut VectorcraftApp, ui: &mut Ui, st: &State, asks: &mut Asks, t: &Tokens) {
    let Some(c) = st.connect.as_ref() else {
        ui.label(RichText::new(tl!("Getting the connection details…")).small().color(t.text_dim));
        return;
    };
    ui.label(RichText::new(tl!("Connect Claude (once per computer)")).strong().color(t.text_strong));
    ui.add_space(4.0);
    // 91studio: one click does it (D-039); the commands below stay for doing it by hand.
    ui.horizontal_wrapped(|ui| {
        let code = c.get("claude_code_installed").and_then(Value::as_bool).unwrap_or(false);
        if code && widgets::primary_button(ui, tl!("Connect Claude Code")).clicked() {
            asks.push(("connect", "POST", "/api/connect/claude-code".into(), None));
        }
        if widgets::secondary_button(ui, tl!("Set up Claude Desktop"))
            .on_hover_text(tl!("Adds the connector to Claude Desktop's settings; restart Claude Desktop after"))
            .clicked()
        {
            asks.push(("connect", "POST", "/api/connect/claude-desktop".into(), None));
        }
    });
    ui.add_space(6.0);
    ui.label(RichText::new(tl!("Or by hand. Claude Code: run this in a terminal.")).small().color(t.text_dim));
    copy_row(ui, &s(c, "claude_code"), t);
    if !c.get("claude_code_installed").and_then(Value::as_bool).unwrap_or(false) {
        ui.horizontal_wrapped(|ui| {
            ui.label(RichText::new(tl!("Claude Code isn't installed yet.")).small().color(t.text_dim));
            if ui.link(tl!("Get Claude Code")).clicked() {
                app.open_url(CLAUDE_CODE_URL);
            }
        });
    }
    ui.add_space(6.0);
    ui.label(
        RichText::new(tl!("Claude Desktop (Mac, Windows): add this to its config (Settings › Developer › Edit Config).")).small().color(t.text_dim),
    );
    let desktop = c.get("claude_desktop").map(|d| serde_json::to_string_pretty(d).unwrap_or_default()).unwrap_or_default();
    copy_row(ui, &desktop, t);
    ui.add_space(6.0);
    ui.label(RichText::new(tl!("Then start Claude with this app open and ask, for example:")).small().color(t.text_dim));
    examples(ui, t);
}

fn examples(ui: &mut Ui, t: &Tokens) {
    for ex in [
        tl!("Make an FCC infographic from this text: …"),
        tl!("Give me three versions with different layouts"),
        tl!("Make the title bigger"),
        tl!("Add a new client called Acme from the folder ~/work/acme-past"),
    ] {
        ui.label(RichText::new(format!("“{ex}”")).small().italics().color(t.text));
    }
}

/// The Claude apps on this computer that have the connector set up ("Claude Code", "Claude Desktop").
fn connected(st: &State) -> Vec<String> {
    st.connect
        .as_ref()
        .and_then(|c| c.get("connected"))
        .and_then(Value::as_array)
        .map(|a| a.iter().filter_map(Value::as_str).map(str::to_string).collect())
        .unwrap_or_default()
}

fn claude_tab(app: &mut VectorcraftApp, ui: &mut Ui, st: &mut State, asks: &mut Asks, t: &Tokens) {
    ui.horizontal(|ui| {
        let dot = if st.agents_ok && st.editor_ok {
            Color32::from_rgb(52, 168, 83)
        } else if st.agents_ok {
            Color32::from_rgb(230, 162, 60)
        } else {
            t.text_disabled
        };
        let (r, _) = ui.allocate_exact_size(vec2(10.0, 10.0), egui::Sense::hover());
        ui.painter().circle_filled(r.center(), 4.0, dot);
        let apps = connected(st);
        let status =
            if apps.is_empty() || !st.agents_ok { st.status.clone() } else { format!("{} · {} {}", st.status, apps.join(" + "), tl!("connected")) };
        ui.label(RichText::new(status).small().color(t.text_dim));
    });
    ui.add_space(6.0);
    ScrollArea::vertical().auto_shrink([false, false]).stick_to_bottom(true).show(ui, |ui| {
        if st.activity.is_empty() {
            ui.vertical_centered(|ui| {
                ui.add_space(8.0);
                let (r, _) = ui.allocate_exact_size(vec2(64.0, 64.0), egui::Sense::hover());
                ui.painter().rect_filled(r, CornerRadius::same(18), t.accent.gamma_multiply(0.16));
                icons::paint(ui, "sparkles", r.shrink(17.0), t.accent);
                ui.add_space(10.0);
                ui.label(RichText::new(tl!("Design with Claude")).font(crate::theme::semibold(17.0)).color(t.text_strong));
                ui.add(
                    egui::Label::new(
                        RichText::new(tl!("Chat with Claude about the design; it builds it here on the artboard, in the client's brand, and you finish it by hand or by asking."))
                            .small()
                            .color(t.text_dim),
                    )
                    .wrap(),
                );
            });
            ui.add_space(12.0);
            let apps = connected(st);
            if apps.is_empty() || st.show_connect {
                connect_card(app, ui, st, asks, t);
            } else {
                // Set up already: say what to do next, keep the setup one click away.
                Frame::new().fill(t.panel_darker).stroke(Stroke::new(1.0, t.accent)).corner_radius(CornerRadius::same(10)).inner_margin(Margin::same(10)).show(
                    ui,
                    |ui| {
                        ui.set_width(ui.available_width());
                        ui.label(RichText::new(format!("✓ {} {}", apps.join(" + "), tl!("is connected"))).strong().color(t.text_strong));
                        ui.label(
                            RichText::new(tl!("Open Claude (run `claude` in a terminal) with this app open and ask for a design. Each step it takes shows up here."))
                                .small()
                                .color(t.text_dim),
                        );
                        ui.add_space(4.0);
                        examples(ui, t);
                    },
                );
            }
            if !apps.is_empty() {
                let label = if st.show_connect { tl!("Hide the connection details") } else { tl!("Connect another Claude app") };
                if ui.link(RichText::new(label).small()).clicked() {
                    st.show_connect = !st.show_connect;
                }
            }
            return;
        }
        let header = if st.show_connect { tl!("Hide the connection details") } else { tl!("How to connect Claude") };
        if ui.link(RichText::new(header).small()).clicked() {
            st.show_connect = !st.show_connect;
        }
        if st.show_connect {
            connect_card(app, ui, st, asks, t);
            widgets::divider(ui);
        }
        ui.label(RichText::new(tl!("What Claude is doing")).strong().color(t.text_strong));
        ui.add_space(4.0);
        for a in &st.activity {
            let ok = a.get("ok").and_then(Value::as_bool).unwrap_or(true);
            let at = s(a, "at");
            ui.horizontal_wrapped(|ui| {
                ui.label(RichText::new(at.get(11..16).unwrap_or_default()).small().monospace().color(t.text_disabled));
                ui.label(RichText::new(tool_label(&s(a, "tool"))).small().strong().color(if ok { t.text } else { t.text_dim }));
                let summary = s(a, "summary");
                let summary = if ok { summary } else { format!("{}: {summary}", tl!("failed")) };
                ui.add(egui::Label::new(RichText::new(summary).small().color(t.text_dim)).wrap());
            });
        }
    });
}

// ── the Clients tab ─────────────────────────────────────────────────────────────────────────────────

fn clients_tab(app: &mut VectorcraftApp, ui: &mut Ui, st: &mut State, asks: &mut Asks, t: &Tokens) {
    if let Some(folder) = PAST_FOLDER.lock().ok().and_then(|mut f| f.take()) {
        st.new_folder = Some(folder);
    }
    ScrollArea::vertical().auto_shrink([false, false]).show(ui, |ui| {
        if !st.note.is_empty() {
            ui.label(RichText::new(&st.note).small().color(t.text_dim));
            ui.add_space(4.0);
        }
        if import_card(ui, st, asks, t) {
            st.import = None;
        }
        team_card(ui, st, asks, t);
        if st.clients.is_empty() {
            ui.label(RichText::new(tl!("No clients yet.")).color(t.text_dim));
        }
        for i in 0..st.clients.len() {
            let Some(c) = st.clients.get(i).cloned() else { continue };
            let w = ui.available_width();
            let (r, resp) = ui.allocate_exact_size(vec2(w, 44.0), egui::Sense::click());
            let on = i == st.client;
            let fill = if on {
                t.row_selected
            } else if resp.hovered() {
                t.hover
            } else {
                t.panel_darker
            };
            ui.painter().rect_filled(r, CornerRadius::same(10), fill);
            let chip = egui::Rect::from_center_size(r.left_center() + vec2(24.0, 0.0), vec2(28.0, 28.0));
            ui.painter().rect_filled(chip, CornerRadius::same(7), c.color.unwrap_or(t.accent));
            ui.painter().text(chip.center(), egui::Align2::CENTER_CENTER, initials(&c.name), egui::FontId::proportional(11.5), Color32::WHITE);
            ui.painter().text(r.left_center() + vec2(48.0, 0.0), egui::Align2::LEFT_CENTER, &c.name, egui::FontId::proportional(13.0), t.text_strong);
            if resp.clicked() {
                st.client = i;
            }
            ui.add_space(6.0);
        }
        ui.add_space(10.0);
        new_client_card(app, ui, st, t);
        ui.add_space(8.0);
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

// ── the team's shared clients (D-037) ───────────────────────────────────────────────────────────────

fn team_card(ui: &mut Ui, st: &mut State, asks: &mut Asks, t: &Tokens) {
    let team = st.team.clone().unwrap_or(Value::Null);
    let joined = team.get("joined").and_then(Value::as_bool).unwrap_or(false);
    Frame::new().fill(t.panel_darker).stroke(Stroke::new(1.0, t.input_border)).corner_radius(CornerRadius::same(10)).inner_margin(Margin::same(10)).show(
        ui,
        |ui| {
            ui.set_width(ui.available_width());
            ui.label(RichText::new(tl!("Team clients")).strong().color(t.text_strong));
            if !st.team_working.is_empty() {
                ui.horizontal(|ui| {
                    ui.add(egui::Spinner::new().size(12.0));
                    ui.label(RichText::new(&st.team_working).small().color(t.text_dim));
                });
            }
            if !joined {
                ui.label(
                    RichText::new(tl!("Share clients with the team: everyone gets the same guides, rules and past work, and is asked before changes install. Designs stay on your laptop."))
                        .small()
                        .color(t.text_dim),
                );
                ui.add(TextEdit::singleline(&mut st.join_repo).hint_text(tl!("Team repo, e.g. 91ninjas/studio-clients")).desired_width(f32::INFINITY));
                ui.add(TextEdit::singleline(&mut st.join_key).password(true).hint_text(tl!("Team key (from your manager)")).desired_width(f32::INFINITY));
                ui.add(TextEdit::singleline(&mut st.join_name).hint_text(tl!("Your name")).desired_width(f32::INFINITY));
                ui.add(TextEdit::singleline(&mut st.join_password).password(true).hint_text(tl!("Manager password (managers only)")).desired_width(f32::INFINITY));
                ui.label(RichText::new(tl!("Designers: leave the manager password empty. The first manager to join sets it.")).small().color(t.text_dim));
                let ready = !st.join_repo.trim().is_empty() && !st.join_key.trim().is_empty() && !st.join_name.trim().is_empty() && !st.busy;
                if widgets::primary_button_enabled(ui, tl!("Join team"), ready).clicked() {
                    st.busy = true;
                    st.team_working = tl!("Joining the team…").into();
                    asks.push((
                        "team-change",
                        "POST",
                        "/api/team/join".into(),
                        Some(json!({"repo": st.join_repo.trim(), "key": st.join_key.trim(), "name": st.join_name.trim(), "password": st.join_password})),
                    ));
                    st.join_key.clear();
                    st.join_password.clear();
                }
                return;
            }
            let manager = team.get("manager").and_then(Value::as_bool).unwrap_or(false);
            let who = format!("{}{}", s(&team, "name"), if manager { format!(" ({})", tl!("manager")) } else { String::new() });
            ui.horizontal_wrapped(|ui| {
                ui.label(RichText::new(format!("{} · {who}", s(&team, "repo"))).small().color(t.text_dim));
                if ui.small_button(tl!("Check now")).clicked() {
                    asks.push(("team-change", "POST", "/api/team/check".into(), None));
                }
                // A new team key (an old one revoked) or another team: leave, then join again. Installed clients stay.
                if ui.small_button(tl!("Leave team")).on_hover_text(tl!("To join with a new key: your installed clients stay")).clicked() {
                    asks.push(("team-change", "DELETE", "/api/team".into(), None));
                }
            });
            if !s(&team, "error").is_empty() {
                ui.label(RichText::new(s(&team, "error")).small().color(t.text_dim));
            }
            for a in team.get("applied").and_then(Value::as_array).into_iter().flatten() {
                ui.label(RichText::new(format!("{} {} {}: {}", s(a, "by"), tl!("rejected a change to"), s(a, "client"), tl!("it's back to the earlier version."))).small());
            }
            let before = asks.len();
            for p in team.get("pending").and_then(Value::as_array).into_iter().flatten() {
                pending_change(ui, p, manager, asks, t);
            }
            // Installing or rejecting downloads or uploads the client's files: say so until it's done.
            if let Some(a) = asks.get(before..).and_then(|a| a.iter().find(|a| a.0 == "team-change")) {
                st.team_working = if a.2.ends_with("/reject") { tl!("Rejecting the change for everyone…") } else { tl!("Installing (downloading its files)…") }.into();
            }
            for c in team.get("unshared").and_then(Value::as_array).into_iter().flatten().filter_map(Value::as_str) {
                ui.horizontal_wrapped(|ui| {
                    ui.label(RichText::new(format!("{c}: {}", tl!("not shared with the team yet"))).small());
                    if st.team_working.is_empty() && ui.small_button(tl!("Share")).clicked() {
                        st.team_working = format!("{} {c} {}", tl!("Sharing"), tl!("with the team (uploading its files)…"));
                        asks.push(("team-change", "POST", "/api/team/share".into(), Some(json!({ "client": c }))));
                    }
                });
            }
        },
    );
    ui.add_space(8.0);
}

/// One change waiting for this designer: who, what, the lines added and removed, and the choices.
fn pending_change(ui: &mut Ui, p: &Value, manager: bool, asks: &mut Asks, t: &Tokens) {
    let client = s(p, "client");
    ui.add_space(6.0);
    Frame::new().fill(t.panel).stroke(Stroke::new(1.0, t.accent)).corner_radius(CornerRadius::same(8)).inner_margin(Margin::same(8)).show(ui, |ui| {
        ui.set_width(ui.available_width());
        let what = if s(p, "kind") == "new" { tl!("shared a new client") } else { tl!("updated") };
        ui.label(
            RichText::new(format!("{} {what} {} (v{})", s(p, "by"), s(p, "name"), p.get("version").and_then(Value::as_u64).unwrap_or(0))).strong(),
        );
        if !s(p, "message").is_empty() {
            ui.label(RichText::new(format!("“{}”", s(p, "message"))).small().italics());
        }
        let green = Color32::from_rgb(52, 168, 83);
        let red = Color32::from_rgb(220, 80, 70);
        for (file, lines) in p.get("changes").and_then(Value::as_object).into_iter().flatten() {
            let label = if file == "critic_rules.md" { tl!("Rules") } else { tl!("Design language") };
            egui::CollapsingHeader::new(RichText::new(format!("{label} ({})", lines.as_array().map_or(0, Vec::len))).small())
                .id_salt((client.as_str(), file.as_str()))
                .default_open(s(p, "kind") != "new")
                .show(ui, |ui| {
                    for l in lines.as_array().into_iter().flatten() {
                        let add = s(l, "op") == "+";
                        let text = format!("{} {}", if add { "+" } else { "−" }, s(l, "text"));
                        ui.add(egui::Label::new(RichText::new(text).small().monospace().color(if add { green } else { red })).wrap());
                    }
                });
        }
        for (key, sign) in [("files_added", "+"), ("files_removed", "−")] {
            let files: Vec<&str> = p.get(key).and_then(Value::as_array).map(|a| a.iter().filter_map(Value::as_str).collect()).unwrap_or_default();
            if !files.is_empty() {
                ui.label(RichText::new(format!("{sign} {}", files.join(", "))).small().color(t.text_dim));
            }
        }
        ui.horizontal_wrapped(|ui| {
            let install = if s(p, "kind") == "new" { tl!("Install") } else { tl!("Install update") };
            if widgets::primary_button(ui, install).clicked() {
                asks.push(("team-change", "POST", "/api/team/accept".into(), Some(json!({ "client": client }))));
            }
            if widgets::secondary_button(ui, tl!("Not now")).clicked() {
                asks.push(("team", "POST", "/api/team/decline".into(), Some(json!({ "client": client }))));
            }
            if manager && widgets::secondary_button(ui, tl!("Reject for everyone")).on_hover_text(tl!("Undo this change on every laptop")).clicked() {
                asks.push(("team-change", "POST", "/api/team/reject".into(), Some(json!({ "client": client }))));
            }
        });
    });
}

/// The request a designer gives Claude to add a client from past work.
fn new_client_prompt(name: &str, folder: &str) -> String {
    format!("Add a new client called {} from the past work in the folder {}", name.trim(), folder)
}

/// New client from past work (D-031, D-033): Claude measures the folder, drafts the design language and the
/// rules, shows them, and saves the client when the designer agrees. The panel prepares the request.
fn new_client_card(app: &mut VectorcraftApp, ui: &mut Ui, st: &mut State, t: &Tokens) {
    Frame::new().fill(t.panel_darker).stroke(Stroke::new(1.0, t.input_border)).corner_radius(CornerRadius::same(10)).inner_margin(Margin::same(10)).show(
        ui,
        |ui| {
            ui.set_width(ui.available_width());
            ui.horizontal(|ui| {
                icons::paint(ui, "sparkles", egui::Rect::from_min_size(ui.cursor().min, vec2(16.0, 16.0)), t.accent);
                ui.add_space(20.0);
                ui.label(RichText::new(tl!("New client from past work")).strong().color(t.text_strong));
            });
            ui.label(
                RichText::new(tl!("Claude measures 3 or more of the client's past designs, drafts their design language and rules, and saves the client after you check it."))
                    .small()
                    .color(t.text_dim),
            );
            ui.add(TextEdit::singleline(&mut st.new_name).hint_text(tl!("Client name, e.g. Acme Health")).desired_width(f32::INFINITY));
            ui.horizontal(|ui| {
                if can_pick_folder(app) && widgets::secondary_button(ui, tl!("Choose folder…")).clicked() {
                    pick_folder(app);
                }
                let shown = st.new_folder.as_deref().map_or_else(|| tl!("No folder yet").to_string(), short_path);
                ui.label(RichText::new(shown).small().color(t.text_dim));
            });
            if let Some(folder) = st.new_folder.as_deref().filter(|_| !st.new_name.trim().is_empty()) {
                ui.label(RichText::new(tl!("Ask Claude:")).small().color(t.text_dim));
                copy_row(ui, &new_client_prompt(&st.new_name, folder), t);
            }
        },
    );
}

fn pick_folder(app: &mut VectorcraftApp) {
    crate::picks::button(app, |app| {
        if let Some(path) = crate::picks::folder(app)
            && let Ok(mut slot) = PAST_FOLDER.lock()
        {
            *slot = Some(path);
        }
    });
}

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

// ── the History tab ─────────────────────────────────────────────────────────────────────────────────

fn history_tab(app: &mut VectorcraftApp, ui: &mut Ui, st: &State, t: &Tokens) {
    let who = st.client().map(|c| c.name.clone()).unwrap_or_default();
    ui.label(RichText::new(format!("{} {who}", tl!("Designs for"))).strong().color(t.text_strong));
    ui.add_space(6.0);
    ScrollArea::vertical().auto_shrink([false, false]).show(ui, |ui| {
        if st.history.is_empty() {
            ui.label(RichText::new(tl!("No designs yet. Ask Claude for one.")).color(t.text_dim));
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

fn tab_bar(ui: &mut Ui, st: &mut State, t: &Tokens) {
    ui.horizontal(|ui| {
        ui.spacing_mut().item_spacing.x = 18.0;
        for (tab, label) in [(Tab::Claude, tl!("Claude")), (Tab::Clients, tl!("Clients")), (Tab::History, tl!("History"))] {
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

pub fn show(app: &mut VectorcraftApp, ui: &mut Ui) {
    let t = Tokens::get(ui.ctx());
    if app.services.studio_agents.is_none() {
        ui.label(RichText::new(tl!("The AI Design Assistant isn't available in this build.")).color(t.text_dim));
        return;
    }
    let mut st: State = super::pstate(ui.ctx(), "studio-ai");
    let mut asks: Asks = vec![];
    let now = ui.input(|i| i.time);
    if !st.started {
        st.started = true;
        asks.push(("health", "GET", "/api/health".into(), None));
        asks.push(("clients", "GET", "/api/clients".into(), None));
        asks.push(("connect", "GET", "/api/connect".into(), None));
    }
    absorb(app, &mut st);
    if st.retry_pending {
        let at = *st.retry_at.get_or_insert(now + RETRY_EVERY_S);
        if now >= at {
            st.retry_pending = false;
            st.retry_at = None;
            st.retries += 1;
            st.started = false;
        }
    }
    // The team's shared clients: what's waiting, every 30 s (the local service asks GitHub every few minutes).
    if st.agents_ok && st.team_at.is_none_or(|at| now - at >= 30.0) {
        st.team_at = Some(now);
        asks.push(("team", "GET", "/api/team".into(), None));
    }
    // Until a Claude app is set up, look again now and then, so setting it up shows here without a restart.
    if st.agents_ok && connected(&st).is_empty() && st.connect_at.is_none_or(|at| now - at >= 5.0) {
        st.connect_at = Some(now);
        asks.push(("connect", "GET", "/api/connect".into(), None));
    }
    // The activity feed: what's new since the last line shown.
    if st.agents_ok && st.activity_at.is_none_or(|at| now - at >= ACTIVITY_EVERY_S) {
        st.activity_at = Some(now);
        let after = st.activity.last().map(|a| s(a, "at")).unwrap_or_default();
        asks.push(("activity", "GET", format!("/api/activity?after={}", after.replace('+', "%2B")), None));
    }

    tab_bar(ui, &mut st, &t);
    ui.add_space(6.0);
    match st.tab {
        Tab::Claude => claude_tab(app, ui, &mut st, &mut asks, &t),
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
    ui.ctx().request_repaint_after(std::time::Duration::from_millis(if st.retry_pending { 250 } else { 1000 }));
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
    fn answers_fill_the_panel() {
        let mut st = State::default();
        on_answer(&mut st, "health", Ok(json!({"ok": true, "editor": true, "ai": "claude"})));
        assert!(st.agents_ok && st.editor_ok && st.status == "Ready for Claude");
        on_answer(
            &mut st,
            "clients",
            Ok(json!([{"client": "fcc", "name": "FCC", "colors": {"primary": "#0164F0"}}, {"client": "bad", "error": "x"}])),
        );
        assert_eq!(st.clients.len(), 1);
        assert_eq!(st.clients[0].color, Some(Color32::from_rgb(1, 100, 240)));
        on_answer(&mut st, "activity", Ok(json!([{"at": "2026-11-03T10:00:00+00:00", "tool": "render_design", "summary": "x v1", "ok": true}])));
        on_answer(&mut st, "activity", Err("down".into()));
        assert_eq!(st.activity.len(), 1, "a failed poll changes nothing");
        assert_eq!(tool_label("place_design"), "Placed it on an artboard");
        on_answer(&mut st, "install", Ok(json!({"client": "acme"})));
        assert!(!st.started && st.note.contains("acme"), "the list is asked for again");
        assert!(connected(&st).is_empty());
        on_answer(&mut st, "connect", Ok(json!({"claude_code": "claude mcp add …", "connected": ["Claude Code"]})));
        assert_eq!(connected(&st), ["Claude Code"]);
    }

    #[test]
    fn the_service_starting_is_waited_for_quietly() {
        let mut st = State::default();
        on_answer(&mut st, "health", Err("Connection refused".into()));
        assert!(st.retry_pending && st.note.is_empty());
        st.retries = START_RETRIES;
        on_answer(&mut st, "health", Err("Connection refused".into()));
        assert!(st.status.starts_with("Not reachable"));
    }

    #[test]
    fn team_answers_reach_the_clients_tab() {
        let mut st = State { started: true, ..Default::default() };
        on_answer(&mut st, "team", Ok(json!({"joined": true, "pending": [{"client": "acme"}]})));
        assert!(st.started && st.team.as_ref().is_some_and(|t| t["pending"][0]["client"] == "acme"));
        on_answer(&mut st, "team-change", Ok(json!({"joined": true, "pending": []})));
        assert!(!st.started, "after an install the client list is asked for again");
        on_answer(&mut st, "team", Err("GitHub down".into()));
        assert!(st.note.is_empty(), "a failed background check stays quiet");
        on_answer(&mut st, "team-change", Err("only a manager can reject a change for everyone".into()));
        assert!(st.note.contains("only a manager"));
    }

    #[test]
    fn the_new_client_request_names_the_client_and_the_folder() {
        assert_eq!(
            new_client_prompt(" Acme Health ", "/home/u/acme"),
            "Add a new client called Acme Health from the past work in the folder /home/u/acme"
        );
        assert_eq!(short_path("/home/u/work/acme/past"), "acme/past");
        assert_eq!(initials("Flipkart Commerce Cloud"), "FC");
    }
}
