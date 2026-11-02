use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::sync::{Arc, Mutex};

use serde_json::{Value, json};

use crate::Forward;
use crate::api::command_allowed;
use crate::http::Server;

const TOKEN: &str = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";

/// A bridge on an ephemeral port whose "app" records every forwarded call and answers with
/// `answer(method, params)`.
struct Fixture {
    port: u16,
    seen: Arc<Mutex<Vec<(String, Value)>>>,
}

fn fixture(answer: impl Fn(&str, &Value) -> Value + Send + Sync + 'static) -> Fixture {
    let listener = TcpListener::bind(("127.0.0.1", 0)).unwrap();
    let port = listener.local_addr().unwrap().port();
    let seen = Arc::new(Mutex::new(Vec::new()));
    let log = Arc::clone(&seen);
    let forward: Arc<Forward> = Arc::new(move |m: &str, p: Value| {
        let r = answer(m, &p);
        log.lock().unwrap().push((m.to_string(), p));
        r
    });
    let server = Arc::new(Server { port, token: TOKEN.into(), forward });
    std::thread::spawn(move || {
        for s in listener.incoming().flatten() {
            let server = Arc::clone(&server);
            std::thread::spawn(move || server.serve(s));
        }
    });
    Fixture { port, seen }
}

fn ok_app() -> impl Fn(&str, &Value) -> Value + Send + Sync + 'static {
    |_, p| json!({"ok": true, "result": {"echo": p, "id": 7, "ids": [7]}})
}

/// Sends raw HTTP; returns (status, body).
fn send(port: u16, head_extra: &str, method: &str, path: &str, body: &str) -> (u16, Value) {
    let mut s = TcpStream::connect(("127.0.0.1", port)).unwrap();
    let req = format!("{method} {path} HTTP/1.1\r\nContent-Length: {}\r\n{head_extra}\r\n{body}", body.len());
    s.write_all(req.as_bytes()).unwrap();
    let mut out = String::new();
    s.read_to_string(&mut out).unwrap();
    let status = out.split_whitespace().nth(1).unwrap().parse().unwrap();
    let body = out.split("\r\n\r\n").nth(1).unwrap_or("null");
    (status, serde_json::from_str(body).unwrap())
}

fn authed(port: u16) -> String {
    format!("Host: 127.0.0.1:{port}\r\nAuthorization: Bearer {TOKEN}\r\n")
}

fn post(f: &Fixture, path: &str, body: Value) -> (u16, Value) {
    send(f.port, &authed(f.port), "POST", path, &body.to_string())
}

#[test]
fn health_needs_the_token() {
    let f = fixture(ok_app());
    let (status, body) = send(f.port, &authed(f.port), "GET", "/v1/health", "");
    assert_eq!(status, 200);
    assert_eq!(body["result"]["bridge"], "91studio");
    let (status, _) = send(f.port, &format!("Host: 127.0.0.1:{}\r\n", f.port), "GET", "/v1/health", "");
    assert_eq!(status, 401);
    let wrong = format!("Host: 127.0.0.1:{}\r\nAuthorization: Bearer {}\r\n", f.port, "f".repeat(64));
    assert_eq!(send(f.port, &wrong, "GET", "/v1/health", "").0, 401);
}

#[test]
fn browser_and_rebinding_requests_are_refused() {
    let f = fixture(ok_app());
    let from_page = format!("{}Origin: http://evil.example\r\n", authed(f.port));
    assert_eq!(send(f.port, &from_page, "GET", "/v1/health", "").0, 403);
    let rebound = format!("Host: evil.example:{}\r\nAuthorization: Bearer {TOKEN}\r\n", f.port);
    assert_eq!(send(f.port, &rebound, "GET", "/v1/health", "").0, 403);
    assert!(f.seen.lock().unwrap().is_empty());
}

#[test]
fn an_allowed_command_is_forwarded_as_engine_execute() {
    let f = fixture(ok_app());
    let (status, body) = post(&f, "/v1/call", json!({"command": "text.setText", "params": {"id": 3, "text": "Hi"}}));
    assert_eq!(status, 200, "{body}");
    let seen = f.seen.lock().unwrap();
    assert_eq!(seen[0].0, "engine.execute");
    assert_eq!(seen[0].1, json!({"command": "text.setText", "params": {"id": 3, "text": "Hi"}}));
}

#[test]
fn file_and_script_commands_are_refused() {
    let f = fixture(ok_app());
    for cmd in ["document.save", "file.open", "document.export", "scripts.run", "plugin.run", "object.exportSelection", "app.quit"] {
        let (status, body) = post(&f, "/v1/call", json!({"command": cmd}));
        assert_eq!(status, 403, "{cmd}: {body}");
    }
    let (status, _) = post(&f, "/v1/call", json!({"method": "ui.screenshot", "params": {}}));
    assert_eq!(status, 403);
    assert!(f.seen.lock().unwrap().is_empty());
}

#[test]
fn path_parameters_are_refused_anywhere() {
    let f = fixture(ok_app());
    let (status, _) = post(&f, "/v1/call", json!({"command": "file.place", "params": {"path": "/etc/passwd"}}));
    assert_eq!(status, 403);
    let (status, _) = post(&f, "/v1/call", json!({"method": "app.export", "params": {"format": "png", "path": "/tmp/x.png"}}));
    assert_eq!(status, 403);
    let nested = json!({"command": "object.move", "params": {"ids": [1], "dx": 0, "dy": 0, "extra": {"File": "x"}}});
    assert_eq!(post(&f, "/v1/call", nested).0, 403);
    assert!(f.seen.lock().unwrap().is_empty());
}

#[test]
fn place_needs_inline_data() {
    let f = fixture(ok_app());
    assert_eq!(post(&f, "/v1/call", json!({"command": "file.place", "params": {"name": "a"}})).0, 403);
    assert_eq!(post(&f, "/v1/call", json!({"command": "file.place", "params": {"name": "a", "dataBase64": "PHN2Zy8+"}})).0, 200);
}

#[test]
fn a_batch_is_one_undo_group_with_references() {
    let f = fixture(ok_app());
    let body = json!({"label": "Bigger title", "calls": [
        {"command": "document.find", "params": {"name": "title"}},
        {"command": "object.scale", "params": {"ids": ["$0.id"], "factor": 1.2}},
    ]});
    let (status, reply) = post(&f, "/v1/batch", body);
    assert_eq!(status, 200, "{reply}");
    let seen = f.seen.lock().unwrap();
    let cmds: Vec<&str> = seen.iter().map(|(_, p)| p["command"].as_str().unwrap()).collect();
    assert_eq!(cmds, ["edit.undoGroup.begin", "document.find", "object.scale", "edit.undoGroup.end"]);
    assert_eq!(seen[0].1["params"]["label"], "Bigger title");
    assert_eq!(seen[2].1["params"]["ids"], json!([7]), "the $0.id reference was resolved");
    assert_eq!(seen[3].1["params"], json!({}), "the group closes without cancel");
}

#[test]
fn a_failing_call_cancels_the_whole_batch() {
    let f = fixture(
        |_, p| {
            if p["command"] == "object.move" { json!({"ok": false, "error": "no such object"}) } else { json!({"ok": true, "result": {}}) }
        },
    );
    let body = json!({"calls": [{"command": "text.setText", "params": {"id": 1, "text": "x"}}, {"command": "object.move", "params": {"ids": [99], "dx": 1, "dy": 1}}, {"command": "text.setText", "params": {"id": 2, "text": "y"}}]});
    let (status, reply) = post(&f, "/v1/batch", body);
    assert_eq!(status, 422);
    assert_eq!(reply["failedCall"], 1);
    assert_eq!(reply["undone"], true);
    let seen = f.seen.lock().unwrap();
    let last = &seen.last().unwrap().1;
    assert_eq!(last["command"], "edit.undoGroup.end");
    assert_eq!(last["params"]["cancel"], true);
    assert_eq!(seen.len(), 4, "the call after the failure never ran");
}

#[test]
fn a_refused_call_stops_the_batch_before_anything_runs() {
    let f = fixture(ok_app());
    let body = json!({"calls": [{"command": "text.setText", "params": {"id": 1, "text": "x"}}, {"command": "document.save"}]});
    let (status, reply) = post(&f, "/v1/batch", body);
    assert_eq!(status, 403);
    assert_eq!(reply["failedCall"], 1);
    assert!(f.seen.lock().unwrap().is_empty());
}

#[test]
fn malformed_requests_are_rejected() {
    let f = fixture(ok_app());
    assert_eq!(send(f.port, &authed(f.port), "POST", "/v1/call", "not json").0, 400);
    assert_eq!(send(f.port, &authed(f.port), "POST", "/v1/call", "[1,2]").0, 400);
    assert_eq!(send(f.port, &authed(f.port), "GET", "/v1/call", "").0, 405);
    assert_eq!(send(f.port, &authed(f.port), "GET", "/nope", "").0, 404);
    let huge = format!("{}Content-Length: 999999999999\r\n", authed(f.port));
    let mut s = TcpStream::connect(("127.0.0.1", f.port)).unwrap();
    s.write_all(format!("POST /v1/call HTTP/1.1\r\n{huge}\r\n").as_bytes()).unwrap();
    let mut out = String::new();
    s.read_to_string(&mut out).unwrap();
    assert!(out.starts_with("HTTP/1.1 413") || out.starts_with("HTTP/1.1 400"), "{out}");
}

#[test]
fn the_allow_list() {
    for ok in ["text.setText", "object.move", "paint.setFill", "document.find", "file.new", "file.place", "edit.undo", "artboard.new"] {
        assert!(command_allowed(ok), "{ok}");
    }
    for no in ["document.save", "file.open", "document.export", "artboard.exportForScreens", "text.importText", "edit.preferences", "scripts.run"] {
        assert!(!command_allowed(no), "{no}");
    }
}
