//! The bridge's endpoints and the policy on what agents may run.
//!
//! - `GET  /v1/health`: `{bridge: "91studio", api: 1}`
//! - `POST /v1/call {command, params?}`: one engine command (`engine.execute`)
//! - `POST /v1/call {method, params?}`: one of [`CONTROL_METHODS`] (rendering, export, discovery)
//! - `document` (optional, on both): the uid of the document the call belongs to (91studio, D-032): it
//!   runs there whichever tab is in front (`studio.inDocument`); `studio.newDocument` returns a new one's uid
//! - `POST /v1/batch {label, calls: [{command|method, params?}…]}`: the calls as **one undo step**
//!   (`edit.undoGroup.begin/end`). A call may use the results of earlier ones (`"$0.ids.0"`,
//!   `"$last.id"`: `vectorcraft_engine::steps`). The first failure cancels the group, undoing
//!   everything the batch did, and the reply names the failed call.
//!
//! Every call is checked against the allow-list: only commands that edit, inspect or select art,
//! open a new document, place data handed over in the request, or undo; never one that reads or
//! writes files, runs scripts or plug-ins (SECURITY T4).

use serde_json::{Value, json};

use crate::Forward;
use crate::http::{Request, Response};

/// Most calls in one batch.
const MAX_BATCH: usize = 500;

/// Engine command families agents may run.
const ALLOWED_PREFIXES: &[&str] = &[
    "object.",
    "text.",
    "type.",
    "paint.",
    "stroke.",
    "fill.",
    "select.",
    "artboard.",
    "layer.",
    "align.",
    "arrange.",
    "group.",
    "shape.",
    "path.",
    "pathfinder.",
    "swatch.",
    "gradient.",
    "appearance.",
    "transform.",
];
/// Single engine commands agents may run.
const ALLOWED_COMMANDS: &[&str] = &[
    "edit.undo",
    "edit.redo",
    "edit.undoGroup.begin",
    "edit.undoGroup.end",
    "edit.copy",
    "edit.paste",
    "edit.pasteInFront",
    "edit.pasteInBack",
    "edit.pasteInPlace",
    "edit.clear",
    "document.inspect",
    "document.find",
    "document.node",
    "document.activate",
    "file.new",
    "file.place",
    "file.documentColorMode",
];
/// Words that mark a command touching files, scripts, plug-ins or the outside world, even inside an
/// allowed family.
const DENIED_WORDS: &[&str] = &["save", "open", "export", "import", "script", "plugin", "library", "link", "package", "print", "action", "browse"];
/// Parameter keys that name files; a call carrying one is refused.
const PATH_KEYS: &[&str] = &["path", "paths", "file", "files", "dir", "directory", "folder", "url", "uri"];
/// Control methods (beyond engine commands) agents may call.
pub(crate) const CONTROL_METHODS: &[&str] = &["ui.render", "app.export", "engine.commands", "studio.documents", "studio.newDocument"];

pub(crate) fn route(req: &Request, forward: &Forward) -> Response {
    match (req.method.as_str(), req.path.as_str()) {
        ("GET", "/v1/health") => Response(200, json!({"ok": true, "result": {"bridge": "91studio", "api": 1}})),
        ("POST", "/v1/call") => with_body(req, |body| call(&body, forward)),
        ("POST", "/v1/batch") => with_body(req, |body| batch(&body, forward)),
        (_, "/v1/health" | "/v1/call" | "/v1/batch") => Response::error(405, "method not allowed"),
        _ => Response::error(404, "no such endpoint"),
    }
}

fn with_body(req: &Request, f: impl FnOnce(Value) -> Response) -> Response {
    match serde_json::from_slice::<Value>(&req.body) {
        Ok(v @ Value::Object(_)) => f(v),
        Ok(_) => Response::error(400, "the body must be a JSON object"),
        Err(e) => Response::error(400, format!("invalid JSON: {e}")),
    }
}

/// One allowed call → the control method and params to forward.
fn resolve_call(call: &Value) -> Result<(String, Value), String> {
    let params = match call.get("params") {
        None | Some(Value::Null) => json!({}),
        Some(p @ Value::Object(_)) => p.clone(),
        Some(_) => return Err("`params` must be an object".into()),
    };
    if let Some(key) = path_key(&params, 0) {
        return Err(format!("parameter `{key}` names a file; the bridge never reads or writes files"));
    }
    match (call.get("command").and_then(Value::as_str), call.get("method").and_then(Value::as_str)) {
        (Some(cmd), None) => {
            if !command_allowed(cmd) {
                return Err(format!("command `{cmd}` is not allowed through the bridge"));
            }
            if cmd == "file.place" && params.get("dataBase64").is_none() {
                return Err("file.place needs `dataBase64` (the bridge never reads files)".into());
            }
            Ok(("engine.execute".into(), json!({"command": cmd, "params": params})))
        }
        (None, Some(m)) if CONTROL_METHODS.contains(&m) => Ok((m.to_string(), params)),
        (None, Some(m)) => Err(format!("method `{m}` is not allowed through the bridge")),
        _ => Err("give exactly one of `command` or `method`".into()),
    }
}

pub(crate) fn command_allowed(cmd: &str) -> bool {
    let lower = cmd.to_ascii_lowercase();
    if DENIED_WORDS.iter().any(|w| lower.contains(w)) && cmd != "file.place" {
        return false;
    }
    ALLOWED_COMMANDS.contains(&cmd) || ALLOWED_PREFIXES.iter().any(|p| cmd.starts_with(p))
}

/// The first key anywhere in `v` that names a file, if any.
fn path_key(v: &Value, depth: usize) -> Option<String> {
    if depth > 32 {
        return None;
    }
    match v {
        Value::Object(o) => {
            o.iter().find_map(|(k, x)| if PATH_KEYS.contains(&k.to_ascii_lowercase().as_str()) { Some(k.clone()) } else { path_key(x, depth + 1) })
        }
        Value::Array(a) => a.iter().find_map(|x| path_key(x, depth + 1)),
        _ => None,
    }
}

fn call(body: &Value, forward: &Forward) -> Response {
    let document = match document_of(body) {
        Ok(d) => d,
        Err(e) => return Response::error(400, e),
    };
    match resolve_call(body) {
        Ok((method, params)) => reply(send(forward, document, &method, params)),
        Err(e) => Response::error(403, e),
    }
}

/// 91studio: the document a call or batch names (`document`: its uid), if any (D-032).
fn document_of(body: &Value) -> Result<Option<u64>, String> {
    match body.get("document") {
        None | Some(Value::Null) => Ok(None),
        Some(v) => v.as_u64().map(Some).ok_or_else(|| "`document` must be a document uid (a number)".to_string()),
    }
}

/// Forward a resolved call, inside `document` when one is named: the app runs it there and brings the
/// designer's tab back in the same request, so switching tabs meanwhile can't redirect it.
fn send(forward: &Forward, document: Option<u64>, method: &str, params: Value) -> Value {
    match document {
        Some(uid) if method != "studio.documents" && method != "studio.newDocument" && method != "engine.commands" => {
            forward("studio.inDocument", json!({"uid": uid, "method": method, "params": params}))
        }
        _ => forward(method, params),
    }
}

/// A control reply as an HTTP response: 200 when it succeeded, 422 when the app refused it.
fn reply(v: Value) -> Response {
    let ok = v.get("ok").and_then(Value::as_bool).unwrap_or(false);
    Response(if ok { 200 } else { 422 }, v)
}

fn batch(body: &Value, forward: &Forward) -> Response {
    let Some(calls) = body.get("calls").and_then(Value::as_array) else {
        return Response::error(400, "missing `calls` (a list of {command|method, params?})");
    };
    if calls.is_empty() || calls.len() > MAX_BATCH {
        return Response::error(400, format!("`calls` must hold 1 to {MAX_BATCH} calls"));
    }
    // Check every call that has no references before anything runs: a refused call never leaves
    // half a batch behind. Calls with references are checked again once resolved.
    for (i, c) in calls.iter().enumerate() {
        if let Err(e) = resolve_call(c) {
            return Response(403, json!({"ok": false, "error": e, "failedCall": i}));
        }
    }
    let document = match document_of(body) {
        Ok(d) => d,
        Err(e) => return Response::error(400, e),
    };
    let label = body.get("label").and_then(Value::as_str).unwrap_or("AI edit");
    let begin = send(forward, document, "engine.execute", json!({"command": "edit.undoGroup.begin", "params": {"label": label}}));
    if begin.get("ok").and_then(Value::as_bool) != Some(true) {
        return reply(begin);
    }
    let mut results: Vec<Value> = Vec::with_capacity(calls.len());
    for (i, c) in calls.iter().enumerate() {
        match run_step(c, &results, forward, document) {
            Ok(result) => results.push(result),
            Err(error) => {
                let end = send(forward, document, "engine.execute", json!({"command": "edit.undoGroup.end", "params": {"cancel": true}}));
                let undone = end.get("ok").and_then(Value::as_bool) == Some(true);
                return Response(422, json!({"ok": false, "error": error, "failedCall": i, "undone": undone}));
            }
        }
    }
    let end = send(forward, document, "engine.execute", json!({"command": "edit.undoGroup.end", "params": {}}));
    if end.get("ok").and_then(Value::as_bool) != Some(true) {
        return reply(end);
    }
    Response(200, json!({"ok": true, "result": {"results": results}}))
}

fn run_step(call: &Value, results: &[Value], forward: &Forward, document: Option<u64>) -> Result<Value, String> {
    let resolved = vectorcraft_engine::steps::resolve(call, results)?;
    let (method, params) = resolve_call(&resolved)?;
    let r = send(forward, document, &method, params);
    if r.get("ok").and_then(Value::as_bool) == Some(true) {
        Ok(r.get("result").cloned().unwrap_or(Value::Null))
    } else {
        Err(r.get("error").and_then(Value::as_str).unwrap_or("the app refused the call").to_string())
    }
}
