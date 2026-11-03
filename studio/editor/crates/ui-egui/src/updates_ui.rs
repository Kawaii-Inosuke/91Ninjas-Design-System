//! Software updates, background check at startup, and startup prompt modal.

use serde_json::Value;

use crate::VectorcraftApp;

#[cfg(not(target_arch = "wasm32"))]
use craft_update::updater::{Action, Updater};
#[cfg(not(target_arch = "wasm32"))]
use craft_update::{CheckResultDialog, CheckResultResponse};

pub(crate) struct Runtime {
    #[cfg(not(target_arch = "wasm32"))]
    updater: Updater,
    /// The desktop app turns the check at start on; tests and the web build leave it off.
    #[cfg(not(target_arch = "wasm32"))]
    startup_check: bool,
    #[cfg(not(target_arch = "wasm32"))]
    check_requested: bool,
    /// The context of the last frame, to wake the UI when a check is queued from a menu callback.
    #[cfg(not(target_arch = "wasm32"))]
    repaint: Option<egui::Context>,
    /// Whether the result of Help ▸ Check for Updates is in view.
    pub(crate) manual_window_open: bool,
    /// The web build has no updater; its window only says so.
    #[cfg(target_arch = "wasm32")]
    pub(crate) status_message: Option<String>,
    /// 91studio: updates through the local service, without signing (D-043).
    #[cfg(not(target_arch = "wasm32"))]
    studio: StudioUpdate,
}

impl Default for Runtime {
    fn default() -> Self {
        Self {
            #[cfg(not(target_arch = "wasm32"))]
            updater: Updater::new(&crate::UPDATE_APP, env!("CARGO_PKG_VERSION")),
            #[cfg(not(target_arch = "wasm32"))]
            startup_check: false,
            #[cfg(not(target_arch = "wasm32"))]
            check_requested: false,
            #[cfg(not(target_arch = "wasm32"))]
            repaint: None,
            manual_window_open: false,
            #[cfg(target_arch = "wasm32")]
            status_message: None,
            #[cfg(not(target_arch = "wasm32"))]
            studio: StudioUpdate::default(),
        }
    }
}

impl VectorcraftApp {
    /// Whether the app asks for a newer release when it starts (off until the desktop app turns it on).
    #[cfg(not(target_arch = "wasm32"))]
    pub fn set_startup_update_check(&mut self, on: bool) {
        self.updates_rt.startup_check = on;
    }
}

pub(crate) fn check(app: &mut VectorcraftApp) -> Result<Value, String> {
    #[cfg(not(target_arch = "wasm32"))]
    {
        if studio_updates(app) {
            app.updates_rt.studio.manual = true;
            app.updates_rt.studio.manual_pending = true;
            if let Some(ctx) = &app.updates_rt.repaint {
                ctx.request_repaint();
            }
            return Ok(Value::Null);
        }
        app.updates_rt.check_requested = true;
        app.updates_rt.manual_window_open = true;
        if let Some(ctx) = &app.updates_rt.repaint {
            ctx.request_repaint();
        }
        Ok(Value::Null)
    }
    #[cfg(target_arch = "wasm32")]
    {
        app.updates_rt.manual_window_open = true;
        app.updates_rt.status_message = Some("Update check is not supported in the web browser.".into());
        Ok(Value::Null)
    }
}

pub(crate) fn tick(app: &mut VectorcraftApp, ctx: &egui::Context) {
    #[cfg(not(target_arch = "wasm32"))]
    {
        let studio = studio_updates(app);
        let rt = &mut app.updates_rt;
        rt.repaint = Some(ctx.clone());
        if std::mem::take(&mut rt.check_requested) {
            rt.updater.check_now(ctx);
        } else if rt.startup_check && !studio {
            rt.updater.check_at_start(ctx);
        }
        rt.updater.tick(ctx);
    }
    #[cfg(target_arch = "wasm32")]
    let _ = (app, ctx);
}

pub(crate) fn show(app: &mut VectorcraftApp, ctx: &egui::Context) {
    #[cfg(not(target_arch = "wasm32"))]
    if studio_updates(app) {
        studio_frame(app, ctx);
    }
    #[cfg(not(target_arch = "wasm32"))]
    {
        // The restart helper is scheduled before the app closes, so no update starts over unsaved work.
        let clean = !crate::unsaved::any_dirty(app);
        match app.updates_rt.updater.show_if(ctx, clean) {
            Action::OpenUrl(url) => app.open_url(&url),
            Action::Close => ctx.send_viewport_cmd(egui::ViewportCommand::Close),
            Action::None => {}
        }
        // An available update goes to the prompt above, which replaces the result dialog.
        let rt = &mut app.updates_rt;
        if rt.manual_window_open && rt.updater.prompt_open() {
            rt.manual_window_open = false;
        }
        if rt.manual_window_open {
            match CheckResultDialog::new(&crate::UPDATE_APP, env!("CARGO_PKG_VERSION")).show(ctx, rt.updater.status()) {
                CheckResultResponse::None => {}
                CheckResultResponse::Close => rt.manual_window_open = false,
                CheckResultResponse::CheckAgain => rt.updater.check_now(ctx),
            }
        }
        // Closing the dialog dismisses the outcome it showed.
        if !rt.manual_window_open {
            rt.updater.dismiss_status();
        }
    }

    #[cfg(target_arch = "wasm32")]
    if app.updates_rt.manual_window_open {
        let mut open = true;
        egui::Window::new(tl!("91Ninjas Studio Updates"))
            .open(&mut open)
            .collapsible(false)
            .resizable(false)
            .pivot(egui::Align2::CENTER_CENTER)
            .default_pos(ctx.content_rect().center())
            .show(ctx, |ui| {
                if let Some(msg) = &app.updates_rt.status_message {
                    ui.label(msg);
                }
                ui.add_space(8.0);
                if ui.button(tl!("OK")).clicked() {
                    app.updates_rt.manual_window_open = false;
                }
            });
        if !open {
            app.updates_rt.manual_window_open = false;
        }
    }
}

// ── 91studio: updates without signing certificates (D-043) ─────────────────────────────────────────
//
// craft-update installs only over a signed copy, and ours are unsigned (D-035). In release builds that
// start the local service, the service finds the new version, downloads it from the team's releases repo,
// checks it against the release's SHA256SUMS.txt and writes a small installer script; the app runs that
// script and closes, and the script installs once the app has gone (and opens it again when asked).

#[cfg(not(target_arch = "wasm32"))]
static STUDIO_ANSWERS: std::sync::Mutex<Vec<(String, Result<Value, String>)>> = std::sync::Mutex::new(Vec::new());

/// An answer of the local service for the update prompt (routed by [`crate::panels::studio_ai::absorb_replies`]).
pub(crate) fn studio_answer(tag: &str, result: Result<Value, String>) {
    #[cfg(not(target_arch = "wasm32"))]
    if let Ok(mut a) = STUDIO_ANSWERS.lock() {
        a.push((tag.to_string(), result));
    }
    #[cfg(target_arch = "wasm32")]
    let _ = (tag, result);
}

#[cfg(not(target_arch = "wasm32"))]
const STUDIO_RECHECK_S: f64 = 3.0 * 3600.0;

#[cfg(not(target_arch = "wasm32"))]
#[derive(Default)]
struct StudioUpdate {
    asked_at: Option<f64>,
    waiting: bool,
    /// What `/api/update` said last.
    info: Option<Value>,
    open: bool,
    /// The version the designer said "Later" to (asked again on the next start).
    later: String,
    /// Help ▸ Check for Updates: show the answer even when there is nothing new.
    manual: bool,
    manual_pending: bool,
    /// The designer chose to install: Some(restart).
    install: Option<bool>,
    prepare_sent: bool,
    note: String,
}

/// Release builds with the local service update through it; the others use craft-update (which only offers the
/// download page for unsigned copies) or nothing.
#[cfg(not(target_arch = "wasm32"))]
fn studio_updates(app: &VectorcraftApp) -> bool {
    !crate::RELEASES_REPO.is_empty() && app.services.studio_agents.is_some()
}

#[cfg(not(target_arch = "wasm32"))]
fn sv<'a>(v: &'a Option<Value>, key: &str) -> &'a str {
    v.as_ref().and_then(|v| v.get(key)).and_then(Value::as_str).unwrap_or("")
}

/// Run the installer script so it outlives the app (its own process group, no console window).
#[cfg(not(target_arch = "wasm32"))]
fn run_detached(program: &str, args: &[String]) -> std::io::Result<()> {
    let mut cmd = std::process::Command::new(program);
    cmd.args(args).stdin(std::process::Stdio::null()).stdout(std::process::Stdio::null()).stderr(std::process::Stdio::null());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NEW_PROCESS_GROUP: u32 = 0x0000_0200;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        cmd.creation_flags(CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW);
    }
    cmd.spawn().map(drop)
}

#[cfg(not(target_arch = "wasm32"))]
fn studio_frame(app: &mut VectorcraftApp, ctx: &egui::Context) {
    crate::panels::studio_ai::absorb_replies(app, ctx);
    let now = ctx.input(|i| i.time);
    let mut asks: Vec<(&'static str, &'static str, String, Option<Value>)> = vec![];
    let mut close = false;
    {
        let u = &mut app.updates_rt.studio;
        let answers = STUDIO_ANSWERS.lock().map(|mut a| std::mem::take(&mut *a)).unwrap_or_default();
        for (tag, result) in answers {
            u.waiting = false;
            match (tag.as_str(), result) {
                ("update" | "update-download", Ok(v)) => {
                    let available = v.get("available").and_then(Value::as_bool).unwrap_or(false);
                    let latest = v.get("latest").and_then(Value::as_str).unwrap_or("").to_string();
                    if available && latest != u.later && u.install.is_none() {
                        u.open = true;
                    }
                    if !available && u.manual {
                        u.open = true;
                    }
                    if let Some(err) = v.get("check_error").and_then(Value::as_str).filter(|_| u.manual) {
                        u.note = err.to_string();
                    }
                    u.info = Some(v);
                }
                ("update-prepare", Ok(v)) => {
                    let program = v.get("program").and_then(Value::as_str).unwrap_or("");
                    let args: Vec<String> = v.get("args").and_then(Value::as_array).map(|a| a.iter().filter_map(|x| x.as_str().map(String::from)).collect()).unwrap_or_default();
                    match run_detached(program, &args) {
                        Ok(()) if u.install == Some(true) => close = true,
                        Ok(()) => {
                            u.open = false;
                            u.note = tl!("The update installs when you close 91Ninjas Studio.").into();
                        }
                        Err(e) => {
                            u.install = None;
                            u.prepare_sent = false;
                            u.note = format!("{}: {e}", tl!("Couldn't start the installer"));
                        }
                    }
                }
                ("update", Err(_)) => {} // the local service is still starting: asked again below
                (_, Err(e)) => {
                    u.install = None;
                    u.prepare_sent = false;
                    u.note = e;
                }
                _ => {}
            }
        }
        // When to ask: at start (again every 15 s until the service answers), every few hours after, and when
        // Help ▸ Check for Updates asks; while downloading, twice a second.
        let state = sv(&u.info, "state").to_string();
        let due = match u.asked_at {
            None => true,
            Some(at) if u.info.is_none() => now - at >= 15.0,
            Some(at) if state == "downloading" => now - at >= 0.5,
            Some(at) => now - at >= STUDIO_RECHECK_S,
        };
        if std::mem::take(&mut u.manual_pending) {
            u.note.clear();
            u.asked_at = Some(now);
            u.waiting = true;
            asks.push(("update", "GET", "/api/update?force=true".into(), None));
        } else if !u.waiting && due {
            u.asked_at = Some(now);
            u.waiting = true;
            asks.push(("update", "GET", "/api/update".into(), None));
        }
        if let Some(restart) = u.install
            && state == "ready"
            && !u.prepare_sent
        {
            u.prepare_sent = true;
            let exe = std::env::current_exe().map(|p| p.display().to_string()).unwrap_or_default();
            asks.push(("update-prepare", "POST", "/api/update/prepare".into(), Some(serde_json::json!({"exe": exe, "pid": std::process::id(), "restart": restart}))));
        }
        if state == "downloading" || u.install.is_some() {
            ctx.request_repaint_after(std::time::Duration::from_millis(500));
        }
    }
    if app.updates_rt.studio.open {
        let page = sv(&app.updates_rt.studio.info, "page").to_string();
        match studio_prompt(ctx, &mut app.updates_rt.studio) {
            Some(PromptAction::Install(restart)) => {
                let u = &mut app.updates_rt.studio;
                u.install = Some(restart);
                u.prepare_sent = false;
                u.note.clear();
                asks.push(("update-download", "POST", "/api/update/download".into(), None));
            }
            Some(PromptAction::Page) => app.open_url(if page.is_empty() { crate::RELEASES_REPO } else { &page }),
            None => {}
        }
    }
    if let Some(agents) = app.services.studio_agents.as_mut() {
        for (tag, method, path, body) in asks {
            agents.request(tag, method, &path, body);
        }
    }
    if close {
        ctx.send_viewport_cmd(egui::ViewportCommand::Close);
    }
}

#[cfg(not(target_arch = "wasm32"))]
enum PromptAction {
    Install(bool),
    Page,
}

#[cfg(not(target_arch = "wasm32"))]
fn studio_prompt(ctx: &egui::Context, u: &mut StudioUpdate) -> Option<PromptAction> {
    let t = crate::theme::Tokens::get(ctx);
    let mut action = None;
    let available = u.info.as_ref().and_then(|v| v.get("available")).and_then(Value::as_bool).unwrap_or(false);
    let state = sv(&u.info, "state").to_string();
    let (latest, current) = (sv(&u.info, "latest").to_string(), sv(&u.info, "current").to_string());
    let can_install = u.info.as_ref().and_then(|v| v.get("can_install")).and_then(Value::as_bool).unwrap_or(false);
    egui::Modal::new(egui::Id::unique("studio-update")).show(ctx, |ui| {
        ui.set_width(420.0);
        if !available {
            ui.label(egui::RichText::new(tl!("91Ninjas Studio is up to date")).strong().size(16.0));
            ui.label(egui::RichText::new(format!("{} {current}", tl!("Version"))).color(t.text_dim));
            if !u.note.is_empty() {
                ui.label(egui::RichText::new(&u.note).color(t.text_dim));
            }
            ui.add_space(8.0);
            if crate::widgets::primary_button(ui, tl!("OK")).clicked() {
                u.open = false;
                u.manual = false;
            }
            return;
        }
        ui.label(egui::RichText::new(format!("91Ninjas Studio {latest} {}", tl!("is available"))).strong().size(16.0));
        ui.label(egui::RichText::new(format!("{} {current}. {}", tl!("You have"), tl!("Your clients, designs and settings are kept."))).color(t.text_dim));
        let notes = sv(&u.info, "notes").trim().to_string();
        if !notes.is_empty() {
            ui.add_space(6.0);
            egui::ScrollArea::vertical().max_height(180.0).show(ui, |ui| {
                ui.label(notes);
            });
        }
        ui.add_space(8.0);
        let error = sv(&u.info, "error").to_string();
        if state == "downloading" || (u.install.is_some() && state == "ready") {
            let (got, size) = (
                u.info.as_ref().and_then(|v| v.get("received")).and_then(Value::as_f64).unwrap_or(0.0),
                u.info.as_ref().and_then(|v| v.get("size")).and_then(Value::as_f64).unwrap_or(0.0),
            );
            let progress = u.info.as_ref().and_then(|v| v.get("progress")).and_then(Value::as_f64).unwrap_or(0.0) as f32;
            let text = if state == "ready" {
                tl!("Downloaded and checked. Installing…").to_string()
            } else {
                format!("{} {:.0} / {:.0} MB", tl!("Downloading"), got / 1e6, size / 1e6)
            };
            ui.add(egui::ProgressBar::new(progress).text(text));
            return;
        }
        if !u.note.is_empty() || (state == "error" && !error.is_empty()) {
            let msg = if u.note.is_empty() { error } else { u.note.clone() };
            ui.label(egui::RichText::new(msg).color(t.text_dim));
            ui.add_space(4.0);
        }
        ui.horizontal_wrapped(|ui| {
            if can_install {
                if crate::widgets::primary_button(ui, tl!("Update and restart")).on_hover_text(tl!("Downloads, closes the app (asking about unsaved work), installs and opens it again")).clicked() {
                    action = Some(PromptAction::Install(true));
                }
                if crate::widgets::secondary_button(ui, tl!("When I close the app")).on_hover_text(tl!("Downloads now and installs the next time you close 91Ninjas Studio")).clicked() {
                    action = Some(PromptAction::Install(false));
                }
            } else if crate::widgets::primary_button(ui, tl!("Download page")).clicked() {
                action = Some(PromptAction::Page);
            }
            if crate::widgets::secondary_button(ui, tl!("Later")).clicked() {
                u.later = latest.clone();
                u.open = false;
                u.manual = false;
            }
        });
    });
    action
}

#[cfg(test)]
mod tests {
    use super::*;
    #[cfg(not(target_arch = "wasm32"))]
    use craft_update::updater::Status;
    use vectorcraft_engine::Session;

    #[test]
    #[cfg(not(target_arch = "wasm32"))]
    fn studio_update_answers_open_the_prompt_until_later() {
        let mut u = StudioUpdate::default();
        let ctx = egui::Context::default();
        let info = serde_json::json!({"available": true, "latest": "1.0.2", "current": "1.0.1", "notes": "Manager password.",
            "can_install": true, "state": "idle", "progress": 0.0});
        u.info = Some(info.clone());
        u.open = true;
        let mut output = ctx.run_ui(egui::RawInput::default(), |ui| {
            assert!(studio_prompt(ui.ctx(), &mut u).is_none());
        });
        output.textures_delta.clear();
        assert!(u.open);
        // Downloading shows the progress, without buttons.
        u.info = Some(serde_json::json!({"available": true, "latest": "1.0.2", "state": "downloading", "progress": 0.4, "received": 2e7, "size": 5e7}));
        let mut output = ctx.run_ui(egui::RawInput::default(), |ui| {
            assert!(studio_prompt(ui.ctx(), &mut u).is_none());
        });
        output.textures_delta.clear();
        studio_answer("update", Ok(info));
        assert_eq!(STUDIO_ANSWERS.lock().unwrap().len(), 1);
    }

    #[test]
    fn default_runtime_is_idle() {
        let rt = Runtime::default();
        assert!(!rt.manual_window_open);
        #[cfg(not(target_arch = "wasm32"))]
        {
            assert!(!rt.startup_check);
            assert!(!rt.updater.prompt_open());
            assert_eq!(rt.updater.status(), &Status::Idle);
        }
    }

    #[test]
    #[cfg(not(target_arch = "wasm32"))]
    fn startup_prompt_shows_and_closes() {
        let mut app = VectorcraftApp::new(Session::new(), Default::default());
        let ctx = egui::Context::default();
        let release = craft_update::Release {
            tag: "v99.0.0".into(),
            version: craft_update::parse_version("99.0.0"),
            name: "VectorCraft 99.0.0".into(),
            html_url: "https://github.com/storytold/vectorcraft/releases/tag/v99.0.0".into(),
            ..Default::default()
        };
        app.updates_rt.updater.offer(release);

        let mut output = ctx.run_ui(egui::RawInput::default(), |ui| {
            show(&mut app, ui.ctx());
        });
        output.textures_delta.clear();
        assert!(app.updates_rt.updater.prompt_open());
    }
}
