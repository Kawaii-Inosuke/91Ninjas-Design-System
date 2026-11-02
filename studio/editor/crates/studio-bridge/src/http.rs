//! A minimal HTTP/1.1 server: one request per connection, JSON in and out, bounded sizes and
//! deadlines. No keep-alive, no chunked bodies: the only client is our own agent sidecar.

use std::io::{BufRead, BufReader, Read, Write};
use std::net::TcpStream;
use std::sync::Arc;
use std::time::Duration;

use serde_json::{Value, json};

use crate::Forward;

/// Largest request head (request line + headers).
const MAX_HEAD: usize = 16 * 1024;
/// Largest request body: a placed SVG with embedded images fits.
pub(crate) const MAX_BODY: usize = 32 * 1024 * 1024;
const READ_TIMEOUT: Duration = Duration::from_secs(30);
const WRITE_TIMEOUT: Duration = Duration::from_secs(60);

pub(crate) struct Server {
    pub port: u16,
    pub token: String,
    pub forward: Arc<Forward>,
}

pub(crate) struct Request {
    pub method: String,
    pub path: String,
    pub headers: Vec<(String, String)>,
    pub body: Vec<u8>,
}

impl Request {
    fn header(&self, name: &str) -> Option<&str> {
        self.headers.iter().find(|(k, _)| k == name).map(|(_, v)| v.as_str())
    }
}

/// An HTTP status and a JSON body.
pub(crate) struct Response(pub u16, pub Value);

impl Response {
    pub fn error(status: u16, msg: impl Into<String>) -> Self {
        Self(status, json!({"ok": false, "error": msg.into()}))
    }
}

impl Server {
    pub fn serve(&self, stream: TcpStream) {
        // Best effort: without the deadlines a stalled peer only holds its own thread.
        let _ = stream.set_read_timeout(Some(READ_TIMEOUT));
        let _ = stream.set_write_timeout(Some(WRITE_TIMEOUT));
        let response = match read_request(&stream) {
            Ok(req) => self.check(&req).unwrap_or_else(|| crate::api::route(&req, self.forward.as_ref())),
            Err(r) => r,
        };
        write_response(stream, &response);
    }

    /// Browser and DNS-rebinding guards, then the token. `None`: the request may go on.
    fn check(&self, req: &Request) -> Option<Response> {
        if req.header("origin").is_some() {
            return Some(Response::error(403, "requests from web pages are not accepted"));
        }
        let host_ok = req.header("host").is_some_and(|h| h == format!("127.0.0.1:{}", self.port) || h == format!("localhost:{}", self.port));
        if !host_ok {
            return Some(Response::error(403, "wrong Host header"));
        }
        let given = req.header("authorization").and_then(|a| a.strip_prefix("Bearer ")).unwrap_or("");
        if !constant_time_eq(given.as_bytes(), self.token.as_bytes()) {
            return Some(Response::error(401, "missing or wrong bridge token"));
        }
        None
    }
}

fn constant_time_eq(a: &[u8], b: &[u8]) -> bool {
    a.len() == b.len() && a.iter().zip(b).fold(0u8, |acc, (x, y)| acc | (x ^ y)) == 0
}

fn read_request(stream: &TcpStream) -> Result<Request, Response> {
    let mut reader = BufReader::new(stream.take((MAX_HEAD + MAX_BODY) as u64));
    let mut head_len = 0usize;
    let mut line = String::new();
    let mut read_line = |line: &mut String| -> Result<(), Response> {
        line.clear();
        let n = reader.read_line(line).map_err(|e| Response::error(400, format!("read failed: {e}")))?;
        head_len += n;
        if n == 0 || head_len > MAX_HEAD {
            return Err(Response::error(400, "malformed or oversized request head"));
        }
        Ok(())
    };
    read_line(&mut line)?;
    let mut parts = line.split_whitespace();
    let (method, path) = match (parts.next(), parts.next(), parts.next()) {
        (Some(m), Some(p), Some(v)) if v.starts_with("HTTP/1.") => (m.to_string(), p.to_string()),
        _ => return Err(Response::error(400, "not an HTTP/1.x request")),
    };
    let mut headers = Vec::new();
    loop {
        read_line(&mut line)?;
        let l = line.trim_end_matches(['\r', '\n']);
        if l.is_empty() {
            break;
        }
        let Some((k, v)) = l.split_once(':') else { return Err(Response::error(400, "malformed header")) };
        headers.push((k.trim().to_ascii_lowercase(), v.trim().to_string()));
    }
    let mut req = Request { method, path, headers, body: Vec::new() };
    if req.header("transfer-encoding").is_some() {
        return Err(Response::error(411, "send a Content-Length body, not chunked"));
    }
    let len: usize = match req.header("content-length") {
        None => 0,
        Some(v) => v.parse().map_err(|_| Response::error(400, "bad Content-Length"))?,
    };
    if len > MAX_BODY {
        return Err(Response::error(413, format!("body over {MAX_BODY} bytes")));
    }
    req.body = vec![0; len];
    reader.read_exact(&mut req.body).map_err(|e| Response::error(400, format!("short body: {e}")))?;
    Ok(req)
}

fn reason(status: u16) -> &'static str {
    match status {
        200 => "OK",
        400 => "Bad Request",
        401 => "Unauthorized",
        403 => "Forbidden",
        404 => "Not Found",
        405 => "Method Not Allowed",
        411 => "Length Required",
        413 => "Payload Too Large",
        422 => "Unprocessable Entity",
        503 => "Service Unavailable",
        _ => "Error",
    }
}

fn write_response(mut stream: TcpStream, Response(status, body): &Response) {
    let body = body.to_string();
    let head = format!(
        "HTTP/1.1 {status} {}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
        reason(*status),
        body.len()
    );
    // The peer may be gone; nothing else to tell it.
    let _ = stream.write_all(head.as_bytes()).and_then(|_| stream.write_all(body.as_bytes())).and_then(|_| stream.flush());
}

pub(crate) fn refuse_busy(stream: TcpStream) {
    let _ = stream.set_write_timeout(Some(Duration::from_secs(5)));
    write_response(stream, &Response::error(503, "too many bridge connections"));
}
