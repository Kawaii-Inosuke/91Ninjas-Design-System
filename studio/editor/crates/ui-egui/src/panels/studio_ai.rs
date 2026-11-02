//! 91studio: the AI panel. The designer picks a client, gives content, and follows the 91 Studio agents
//! as they brief, lay out, render, critique and place the design, deciding at each approval. Once the
//! design is on the artboard, chat requests edit it (one undo step each). The panel only draws and asks:
//! all I/O goes through [`crate::studio::StudioAgents`] (`Services::studio_agents`). D-021.

use egui::{RichText, ScrollArea, TextEdit, Ui};
use serde_json::{Value, json};

use crate::VectorcraftApp;
use crate::studio::StudioReply;
use crate::theme::Tokens;
use crate::widgets;

pub const ID: &str = "studioAi";
/// Event lines kept in the panel (the run's full log stays with the agents).
const MAX_LINES: usize = 200;

#[derive(Clone, Default)]
struct Line {
    text: String,
    dim: bool,
    detail: Vec<String>,
}

#[derive(Clone, Default)]
struct State {
    started: bool,
    status: String,
    clients: Vec<(String, String)>,
    client: usize,
    content: String,
    instruction: String,
    run: Option<String>,
    running: bool,
    lines: Vec<Line>,
    gate: Option<Value>,
    notes: String,
    placed: bool,
    chat: String,
    busy: bool,
    error: Option<String>,
    preview: Option<egui::TextureHandle>,
}

fn s(v: &Value, key: &str) -> String {
    v.get(key).and_then(Value::as_str).unwrap_or_default().to_string()
}

/// The path of a run file as the agents serve it: `/api/runs/<id>/files/<path inside the run>`.
fn file_path(run: &str, abs: &str) -> Option<String> {
    let rel = abs.split(&format!("/runs/{run}/")).nth(1)?;
    Some(format!("/api/runs/{run}/files/{rel}"))
}

fn push(st: &mut State, text: impl Into<String>, dim: bool, detail: Vec<String>) {
    st.lines.push(Line { text: text.into(), dim, detail });
    if st.lines.len() > MAX_LINES {
        st.lines.remove(0);
    }
}

fn on_event(st: &mut State, e: &Value, fetch: &mut Vec<(String, String)>) {
    let d = e.get("data").cloned().unwrap_or(Value::Null);
    match e.get("type").and_then(Value::as_str).unwrap_or_default() {
        "stage" => push(st, format!("{} · {}", s(&d, "stage"), s(&d, "note")), true, vec![]),
        "brief" => {
            let points = d
                .get("key_points")
                .and_then(Value::as_array)
                .map(|a| a.iter().filter_map(Value::as_str).map(|p| format!("• {p}")).collect())
                .unwrap_or_default();
            push(st, format!("Brief: {} ({})", s(&d, "topic"), s(&d, "angle")), false, points);
        }
        "check" => {
            let n = d.get("matches").and_then(Value::as_array).map_or(0, Vec::len);
            push(st, if n == 0 { "No similar designs".to_string() } else { format!("{n} similar design(s) found") }, true, vec![]);
        }
        "gate" => {
            st.gate = Some(d);
            st.notes.clear();
        }
        "decision" => st.gate = None,
        "version" => {
            let fails: Vec<String> = d
                .get("fails")
                .and_then(Value::as_array)
                .map(|a| a.iter().map(|f| format!("{} · {} → {}", s(f, "rule"), s(f, "problem"), s(f, "fix"))).collect())
                .unwrap_or_default();
            let passes = d.get("passes").and_then(Value::as_bool).unwrap_or(false);
            let v = d.get("version").and_then(Value::as_u64).unwrap_or(0);
            push(
                st,
                if passes { format!("Version {v} passes the critic") } else { format!("Version {v}: {} finding(s)", fails.len()) },
                false,
                fails,
            );
            if let (Some(run), Some(p)) = (st.run.clone(), d.get("preview").and_then(Value::as_str))
                && let Some(path) = file_path(&run, p)
            {
                fetch.push(("preview".into(), path));
            }
        }
        "placed" => {
            st.placed = d.get("editor").and_then(Value::as_bool).unwrap_or(false);
            let text = if st.placed {
                format!(
                    "Placed on a new artboard ({} named layers). Edit it by hand or by chat below.",
                    d.get("layers").and_then(Value::as_u64).unwrap_or(0)
                )
            } else {
                s(&d, "note")
            };
            push(st, text, false, vec![]);
        }
        "message" => push(st, format!("{}: {}", if s(&d, "from") == "designer" { "You" } else { "Editor agent" }, s(&d, "text")), false, vec![]),
        "warning" => push(st, format!("Warning: {}", d), true, vec![]),
        "error" => push(st, format!("Error: {}", s(&d, "error")), false, vec![]),
        "done" => {
            st.running = false;
            st.gate = None;
            let files: Vec<String> = d
                .get("files")
                .and_then(Value::as_object)
                .map(|o| o.iter().map(|(k, v)| format!("{k}: {}", v.as_str().unwrap_or_default())).collect())
                .unwrap_or_default();
            let why = [s(&d, "reason"), s(&d, "error")].into_iter().filter(|x| !x.is_empty()).collect::<Vec<_>>().join(" ");
            push(st, format!("Finished: {}{}", s(&d, "status"), if why.is_empty() { String::new() } else { format!(" · {why}") }), false, files);
        }
        _ => {}
    }
}

fn on_answer(st: &mut State, tag: &str, result: Result<Value, String>, follow: &mut Option<String>) {
    st.busy = false;
    let v = match result {
        Ok(v) => v,
        Err(e) => {
            if tag == "health" {
                st.status = format!("Agents not reachable: {e}");
            } else {
                st.error = Some(e);
            }
            return;
        }
    };
    st.error = None;
    match tag {
        "health" => {
            let editor = v.get("editor").and_then(Value::as_bool).unwrap_or(false);
            st.status = if editor { "Agents ready · editor connected".into() } else { "Agents ready · editor bridge not connected".into() };
        }
        "clients" => {
            st.clients = v
                .as_array()
                .map(|a| a.iter().filter(|c| c.get("error").is_none()).map(|c| (s(c, "client"), s(c, "name"))).collect())
                .unwrap_or_default();
            st.client = st.client.min(st.clients.len().saturating_sub(1));
        }
        "start" => {
            let id = s(&v, "id");
            if !id.is_empty() {
                st.lines.clear();
                st.preview = None;
                st.placed = false;
                st.running = true;
                st.run = Some(id.clone());
                *follow = Some(id);
            }
        }
        _ => {}
    }
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
            StudioReply::File { result: Ok(bytes), .. } => {
                if let Ok(img) = image::load_from_memory(&bytes) {
                    let rgba = img.to_rgba8();
                    let size = [rgba.width() as usize, rgba.height() as usize];
                    let color = egui::ColorImage::from_rgba_unmultiplied(size, rgba.as_raw());
                    st.preview = Some(ctx.load_texture("studio-ai-preview", color, egui::TextureOptions::LINEAR));
                }
            }
            StudioReply::File { result: Err(e), .. } => st.error = Some(e),
        }
    }
    if let Some(run) = follow {
        agents.follow(&run);
    }
    for (key, path) in fetch {
        agents.fetch(&key, &path);
    }
}

fn gate_card(ui: &mut Ui, st: &mut State, t: &Tokens) -> Option<(String, String)> {
    let gate = st.gate.clone()?;
    let kind = s(&gate, "kind");
    let p = gate.get("payload").cloned().unwrap_or(Value::Null);
    let mut chosen = None;
    widgets::divider(ui);
    match kind.as_str() {
        "similar_exists" => {
            ui.label(RichText::new(tl!("A similar design already exists. Generate a new version anyway?")).strong());
            for m in p.get("matches").and_then(Value::as_array).into_iter().flatten() {
                ui.label(format!("“{}” · {} · {:.2}", s(m, "topic"), s(m, "status"), m.get("similarity").and_then(Value::as_f64).unwrap_or(0.0)));
                for (k, v) in m.get("files").and_then(Value::as_object).into_iter().flatten() {
                    ui.label(RichText::new(format!("{k}: {}", v.as_str().unwrap_or_default())).small().color(t.text_dim));
                }
            }
        }
        "outline" => {
            let spec = p.get("spec").cloned().unwrap_or(Value::Null);
            ui.label(RichText::new(format!("{}: {} ({})", tl!("Outline for approval"), s(&spec, "title"), s(&spec, "layout"))).strong());
            for it in spec.get("items").and_then(Value::as_array).into_iter().flatten() {
                ui.label(format!("• {} {}", s(it, "heading"), s(it, "body")));
            }
            for c in spec.get("columns").and_then(Value::as_array).into_iter().flatten() {
                let items: Vec<&str> =
                    c.get("items").and_then(Value::as_array).map(|a| a.iter().filter_map(Value::as_str).collect()).unwrap_or_default();
                ui.label(format!("▸ {} {} {}", s(c, "title"), s(c, "body"), items.join(" · ")));
            }
            if let Some(table) = spec.get("table") {
                for row in
                    std::iter::once(table.get("header")).chain(table.get("rows").and_then(Value::as_array).into_iter().flatten().map(Some)).flatten()
                {
                    let cells: Vec<&str> = row.as_array().map(|a| a.iter().filter_map(Value::as_str).collect()).unwrap_or_default();
                    ui.label(RichText::new(cells.join(" | ")).small());
                }
            }
        }
        "final" => {
            ui.label(RichText::new(tl!("Final approval: your edits on the artboard are included when you approve.")).strong());
            for f in p.get("unresolved").and_then(Value::as_array).into_iter().flatten() {
                ui.label(RichText::new(format!("{}: {}", tl!("Not resolved"), s(f, "problem"))).color(t.text_dim));
            }
        }
        other => {
            ui.label(other);
        }
    }
    let options: Vec<String> =
        gate.get("options").and_then(Value::as_array).map(|a| a.iter().filter_map(Value::as_str).map(str::to_string).collect()).unwrap_or_default();
    if options.iter().any(|o| o == "change") {
        ui.add(TextEdit::multiline(&mut st.notes).hint_text(tl!("What to change (for Change)")).desired_rows(2).desired_width(f32::INFINITY));
    }
    ui.horizontal_wrapped(|ui| {
        for o in &options {
            let label = match o.as_str() {
                "approve" => tl!("Approve"),
                "change" => tl!("Change"),
                "abandon" => tl!("Abandon"),
                "generate" => tl!("Generate again"),
                other => other,
            };
            let needs_notes = o == "change" && st.notes.trim().is_empty();
            if ui.add_enabled(!st.busy && !needs_notes, egui::Button::new(label)).clicked() {
                chosen = Some((kind.clone(), o.clone()));
            }
        }
    });
    chosen
}

pub fn show(app: &mut VectorcraftApp, ui: &mut Ui) {
    let t = Tokens::get(ui.ctx());
    if app.services.studio_agents.is_none() {
        ui.label(RichText::new(tl!("The 91 Studio agents aren't available in this build.")).color(t.text_dim));
        return;
    }
    let mut st: State = super::pstate(ui.ctx(), "studio-ai");
    if !st.started {
        st.started = true;
        if let Some(a) = app.services.studio_agents.as_mut() {
            a.request("health", "GET", "/api/health", None);
            a.request("clients", "GET", "/api/clients", None);
        }
    }
    absorb(app, ui.ctx(), &mut st);
    let mut send: Vec<(&str, &str, String, Option<Value>)> = vec![];

    ui.label(RichText::new(&st.status).small().color(t.text_dim));
    if !st.running {
        let current = st.clients.get(st.client).map(|c| c.1.clone()).unwrap_or_else(|| tl!("No clients installed").to_string());
        craft_ui::ComboBox::from_id_salt("studio-ai-client").selected_text(current).show_ui(ui, |ui| {
            for (i, (_, name)) in st.clients.iter().enumerate() {
                ui.selectable_value(&mut st.client, i, name);
            }
        });
        ui.add(
            TextEdit::multiline(&mut st.content)
                .hint_text(tl!("Paste the content to turn into an infographic"))
                .desired_rows(6)
                .desired_width(f32::INFINITY),
        );
        ui.add(TextEdit::singleline(&mut st.instruction).hint_text(tl!("Instruction (optional), e.g. use a timeline")).desired_width(f32::INFINITY));
        let ready = !st.busy && !st.content.trim().is_empty() && st.clients.get(st.client).is_some();
        if ui.add_enabled(ready, egui::Button::new(tl!("Make the infographic"))).clicked()
            && let Some((client, _)) = st.clients.get(st.client)
        {
            st.busy = true;
            send.push((
                "start",
                "POST",
                "/api/runs/json".into(),
                Some(json!({"client": client, "content": st.content, "instruction": st.instruction})),
            ));
        }
    } else if let Some(run) = &st.run {
        ui.label(RichText::new(format!("{} {run}", tl!("Run"))).small().color(t.text_dim));
    }

    if let Some((gate, decision)) = gate_card(ui, &mut st, &t)
        && let Some(run) = st.run.clone()
    {
        st.busy = true;
        send.push(("decide", "POST", format!("/api/runs/{run}/decide"), Some(json!({"gate": gate, "decision": decision, "notes": st.notes}))));
    }
    if let Some(tex) = &st.preview {
        let w = ui.available_width().min(tex.size_vec2().x);
        let h = w * tex.size_vec2().y / tex.size_vec2().x.max(1.0);
        ui.image((tex.id(), egui::vec2(w, h)));
    }
    if let Some(e) = &st.error {
        ui.label(RichText::new(e).color(t.text_dim));
    }
    widgets::divider(ui);
    ScrollArea::vertical().max_height(320.0).stick_to_bottom(true).show(ui, |ui| {
        for l in &st.lines {
            ui.label(if l.dim { RichText::new(&l.text).small().color(t.text_dim) } else { RichText::new(&l.text) });
            for d in &l.detail {
                ui.label(RichText::new(d).small().color(t.text_dim));
            }
        }
    });
    if st.placed
        && let Some(run) = st.run.clone()
    {
        widgets::divider(ui);
        ui.horizontal(|ui| {
            ui.add(
                TextEdit::singleline(&mut st.chat)
                    .hint_text(tl!("Ask for an edit, e.g. make the title bigger"))
                    .desired_width(ui.available_width() - 60.0),
            );
            if ui.add_enabled(!st.busy && !st.chat.trim().is_empty(), egui::Button::new(tl!("Send"))).clicked() {
                st.busy = true;
                send.push(("message", "POST", format!("/api/runs/{run}/messages"), Some(json!({"text": st.chat}))));
                st.chat.clear();
            }
        });
    }
    if st.running || !st.started || st.busy {
        ui.ctx().request_repaint_after(std::time::Duration::from_millis(250));
    }
    if let Some(a) = app.services.studio_agents.as_mut() {
        for (tag, method, path, body) in send {
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
    fn events_build_lines_and_gates() {
        let mut st = State { run: Some("r1".into()), running: true, ..Default::default() };
        let mut fetch = vec![];
        on_event(&mut st, &json!({"type": "brief", "data": {"topic": "Payments", "angle": "steps", "key_points": ["a", "b"]}}), &mut fetch);
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
        assert_eq!(st.lines[0].detail, vec!["• a", "• b"]);
        assert_eq!(st.lines[1].text, "Version 1: 1 finding(s)");
        assert_eq!(fetch, vec![("preview".to_string(), "/api/runs/r1/files/versions/v1_preview.png".to_string())]);
        assert!(st.gate.is_some());
        on_event(&mut st, &json!({"type": "done", "data": {"status": "done", "files": {"png": "/f.png"}}}), &mut fetch);
        assert!(!st.running && st.gate.is_none());
        assert_eq!(st.lines.last().map(|l| l.detail.clone()), Some(vec!["png: /f.png".to_string()]));
    }
}
