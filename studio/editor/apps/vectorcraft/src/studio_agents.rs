//! 91studio: starts the 91 Studio agents (the Python sidecar in `studio/agents`) with the app, stops them
//! when it closes, and implements the AI panel's [`StudioAgents`] on background threads (D-021).
//!
//! Found through `STUDIO_AGENTS_DIR`, else a `studio/agents` (or `agents`) folder next to the app's
//! install; run with `uv` (`STUDIO_UV`, else on PATH or in ~/.local/bin). `STUDIO_AGENTS=0` turns it off.
//! The sidecar listens on 127.0.0.1 with a per-launch random token (SECURITY T2).

use std::io::{BufRead, BufReader, Read, Write};
use std::net::{TcpListener, TcpStream};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Arc;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::mpsc::{Receiver, Sender, channel};
use std::time::Duration;

use serde_json::Value;
use vectorcraft_ui_egui::studio::{StudioAgents, StudioReply};

/// How long a request waits for its answer: a chat edit waits for the model.
const ANSWER_WAIT: Duration = Duration::from_secs(300);
/// Largest answer read (a preview PNG is far smaller).
const MAX_BODY: usize = 64 * 1024 * 1024;

/// Where the agents listen.
#[derive(Clone)]
pub struct SidecarInfo {
    pub port: u16,
    pub token: String,
}

/// The running sidecar; dropping it stops it.
pub struct Sidecar {
    pub info: SidecarInfo,
    child: Child,
}

impl Drop for Sidecar {
    fn drop(&mut self) {
        // Best effort: the process may already have ended.
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

fn agents_dir() -> Option<PathBuf> {
    if let Some(d) = std::env::var_os("STUDIO_AGENTS_DIR").filter(|d| !d.is_empty()) {
        return Some(PathBuf::from(d));
    }
    let exe = std::env::current_exe().ok()?;
    // Installed: only the agents shipped with this app, marked `.installed` by the packaging (D-049): agents\
    // next to the .exe on Windows, Contents/Resources/agents in the macOS bundle. Never a folder found further
    // up, which another account could have made.
    let here = exe.parent()?;
    let shipped = [here.join("agents"), here.parent().map_or_else(|| here.join("Resources"), |c| c.join("Resources")).join("agents")];
    if let Some(d) = shipped.into_iter().find(|d| d.join(INSTALLED).is_file()) {
        return Some(d);
    }
    // A checkout (studio/agents, found from target/debug or target/release) (D-040).
    exe.ancestors()
        .flat_map(|a| [a.join("studio").join("agents"), a.join("agents"), a.join("Resources").join("agents")])
        .find(|d| d.join("pyproject.toml").is_file() && d.join("studio_agents").is_dir())
}

/// Installed (not a checkout): the agents came with the app, so their Python environment lives in the user's
/// data folder (the install folder may not be writable) and is built from the lock file as shipped.
fn bundled(dir: &std::path::Path) -> bool {
    dir.join(INSTALLED).is_file() && std::env::var_os("STUDIO_AGENTS_DIR").is_none()
}

/// The marker the installers put in the agents folder they ship (D-049).
const INSTALLED: &str = ".installed";

/// The data folder the agents use (`~/.local/share/91studio`, as `studio_agents.config.data_dir`).
fn data_dir() -> Option<PathBuf> {
    vectorcraft_studio_bridge::home_dir().map(|h| h.join(".local").join("share").join("91studio"))
}

fn uv() -> Option<PathBuf> {
    if let Some(p) = std::env::var_os("STUDIO_UV").filter(|p| !p.is_empty()) {
        return Some(PathBuf::from(p));
    }
    // The `uv` shipped next to the app (D-040).
    let name = if cfg!(windows) { "uv.exe" } else { "uv" };
    if let Some(p) = std::env::current_exe().ok().and_then(|e| e.parent().map(|d| d.join(name))).filter(|p| p.is_file()) {
        return Some(p);
    }
    let on_path = std::env::var_os("PATH").into_iter().flat_map(|p| std::env::split_paths(&p).collect::<Vec<_>>());
    let home = std::env::var_os("HOME").map(PathBuf::from);
    let extra = home.into_iter().flat_map(|h| [h.join(".local").join("bin"), h.join(".cargo").join("bin")]);
    on_path.chain(extra).map(|d| d.join(if cfg!(windows) { "uv.exe" } else { "uv" })).find(|p| p.is_file())
}

fn free_port() -> Option<u16> {
    TcpListener::bind(("127.0.0.1", 0)).ok()?.local_addr().ok().map(|a| a.port())
}

/// Start the agents, or `None` (with the reason in the log) when they're off or can't be found.
pub fn launch() -> Option<Sidecar> {
    if std::env::var("STUDIO_AGENTS").is_ok_and(|v| v == "0") {
        return None;
    }
    let Some(dir) = agents_dir() else {
        log::info!("91Ninjas Studio agents not found (set STUDIO_AGENTS_DIR); the AI panel is off");
        return None;
    };
    let Some(uv) = uv() else {
        log::warn!("91Ninjas Studio agents: `uv` not found (set STUDIO_UV); the AI panel is off");
        return None;
    };
    let port = free_port()?;
    let token = match vectorcraft_studio_bridge::new_token() {
        Ok(t) => t,
        Err(e) => {
            log::warn!("91Ninjas Studio agents: {e}");
            return None;
        }
    };
    let log_path = vectorcraft_studio_bridge::bridge_dir().join("agents.log");
    let log_file = std::fs::create_dir_all(vectorcraft_studio_bridge::bridge_dir()).and_then(|()| std::fs::File::create(&log_path));
    let (out, err) = match log_file.and_then(|f| Ok((f.try_clone()?, f))) {
        Ok((a, b)) => (Stdio::from(a), Stdio::from(b)),
        Err(_) => (Stdio::null(), Stdio::null()),
    };
    let mut cmd = Command::new(&uv);
    cmd.args(["run", "--project"]).arg(&dir);
    if bundled(&dir)
        && let Some(data) = data_dir()
    {
        // First start: uv fetches Python and the packages once (a few minutes), into the user's data folder.
        cmd.args(["--frozen", "--no-dev"]).env("UV_PROJECT_ENVIRONMENT", data.join("agents-env")).env("STUDIO_BUNDLED", "1");
    }
    cmd.args(["python", "-m", "studio_agents.server"])
        .current_dir(&dir)
        // The connector Claude starts must run the same way (`/api/connect` reads these).
        .env("STUDIO_UV", &uv)
        .env("STUDIO_AGENTS_DIR_USED", &dir)
        .env("STUDIO_SIDECAR_TOKEN", &token)
        .env("STUDIO_SIDECAR_PORT", port.to_string())
        // The agents stop by themselves when the app is gone, even when it was killed.
        .env("STUDIO_PARENT_PID", std::process::id().to_string())
        // Updates without signing (D-043): what this build is and where its releases are.
        .env("STUDIO_APP_VERSION", vectorcraft_ui_egui::APP_VERSION)
        .env("STUDIO_RELEASES_REPO", vectorcraft_ui_egui::RELEASES_REPO)
        .env("STUDIO_APP_ARCH", std::env::consts::ARCH)
        .stdin(Stdio::null())
        .stdout(out)
        .stderr(err);
    // Windows: `uv` and Python are console programs; without this each start opened a black window (D-044).
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }
    let child = cmd.spawn();
    match child {
        Ok(child) => {
            log::info!("91Ninjas Studio agents starting on 127.0.0.1:{port} from {} (log {})", dir.display(), log_path.display());
            Some(Sidecar { info: SidecarInfo { port, token }, child })
        }
        Err(e) => {
            log::warn!("91Ninjas Studio agents failed to start with {}: {e}", uv.display());
            None
        }
    }
}

// ── the panel's client ──────────────────────────────────────────────────────────────────────────────

pub struct Client {
    info: SidecarInfo,
    ctx: egui::Context,
    tx: Sender<StudioReply>,
    rx: Receiver<StudioReply>,
    stream: Arc<AtomicU64>,
}

impl Client {
    pub fn new(info: SidecarInfo, ctx: egui::Context) -> Self {
        let (tx, rx) = channel();
        Self { info, ctx, tx, rx, stream: Arc::new(AtomicU64::new(0)) }
    }

    fn spawn(&self, name: &str, work: impl FnOnce(SidecarInfo, Sender<StudioReply>) + Send + 'static) {
        let (info, tx, ctx) = (self.info.clone(), self.tx.clone(), self.ctx.clone());
        let spawned = std::thread::Builder::new().name(format!("studio-agents-{name}")).spawn(move || {
            work(info, tx);
            ctx.request_repaint();
        });
        if let Err(e) = spawned {
            let _ = self.tx.send(StudioReply::Answer { tag: name.to_string(), result: Err(format!("could not start a thread: {e}")) });
        }
    }
}

impl StudioAgents for Client {
    fn request(&mut self, tag: &str, method: &str, path: &str, body: Option<Value>) {
        let (tag, method, path) = (tag.to_string(), method.to_string(), path.to_string());
        self.spawn("request", move |info, tx| {
            let result = call(&info, &method, &path, body.as_ref()).and_then(|bytes| {
                if bytes.is_empty() { Ok(Value::Null) } else { serde_json::from_slice(&bytes).map_err(|e| format!("bad answer: {e}")) }
            });
            let _ = tx.send(StudioReply::Answer { tag, result });
        });
    }

    fn fetch(&mut self, key: &str, path: &str) {
        let (key, path) = (key.to_string(), path.to_string());
        self.spawn("fetch", move |info, tx| {
            let _ = tx.send(StudioReply::File { key, result: call(&info, "GET", &path, None) });
        });
    }

    fn follow(&mut self, run_id: &str) {
        let generation = self.stream.fetch_add(1, Ordering::SeqCst) + 1;
        let (stream, ctx, path) = (Arc::clone(&self.stream), self.ctx.clone(), format!("/api/runs/{run_id}/events"));
        self.spawn("events", move |info, tx| {
            let current = || stream.load(Ordering::SeqCst) == generation;
            if let Err(e) = follow_events(&info, &path, &current, &mut |event| {
                let _ = tx.send(StudioReply::Event(event));
                ctx.request_repaint();
            }) {
                let _ = tx.send(StudioReply::Answer { tag: "events".into(), result: Err(e) });
            }
        });
    }

    fn poll(&mut self) -> Vec<StudioReply> {
        self.rx.try_iter().collect()
    }
}

// ── a minimal HTTP/1.1 client (the sidecar is on loopback; no TLS, no proxies) ───────────────────────

fn open(info: &SidecarInfo, method: &str, path: &str, body: Option<&Value>, read_timeout: Duration) -> Result<BufReader<TcpStream>, String> {
    let mut s = TcpStream::connect(("127.0.0.1", info.port)).map_err(|e| format!("the agents aren't running: {e}"))?;
    let _ = s.set_read_timeout(Some(read_timeout));
    let _ = s.set_write_timeout(Some(Duration::from_secs(30)));
    let body = body.map(Value::to_string).unwrap_or_default();
    let head = format!(
        "{method} {path} HTTP/1.1\r\nHost: 127.0.0.1:{}\r\nAuthorization: Bearer {}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
        info.port,
        info.token,
        body.len()
    );
    s.write_all(head.as_bytes()).and_then(|()| s.write_all(body.as_bytes())).map_err(|e| format!("send failed: {e}"))?;
    Ok(BufReader::new(s))
}

/// Status and headers (lower-case names).
fn read_head(r: &mut impl BufRead) -> Result<(u16, Vec<(String, String)>), String> {
    let mut line = String::new();
    r.read_line(&mut line).map_err(|e| format!("no answer: {e}"))?;
    let status = line.split_whitespace().nth(1).and_then(|s| s.parse().ok()).ok_or("not an HTTP answer")?;
    let mut headers = vec![];
    for _ in 0..100 {
        line.clear();
        r.read_line(&mut line).map_err(|e| format!("bad answer: {e}"))?;
        let l = line.trim_end();
        if l.is_empty() {
            return Ok((status, headers));
        }
        if let Some((k, v)) = l.split_once(':') {
            headers.push((k.trim().to_ascii_lowercase(), v.trim().to_string()));
        }
    }
    Err("too many headers".into())
}

/// Reads a chunked body (Transfer-Encoding: chunked) as a plain stream.
struct Chunked<R: BufRead> {
    inner: R,
    left: usize,
    done: bool,
}

impl<R: BufRead> Read for Chunked<R> {
    fn read(&mut self, buf: &mut [u8]) -> std::io::Result<usize> {
        if self.done {
            return Ok(0);
        }
        if self.left == 0 {
            let mut size = String::new();
            self.inner.read_line(&mut size)?;
            if size.trim().is_empty() {
                self.inner.read_line(&mut size)?; // the CRLF that ends the previous chunk
            }
            let n = usize::from_str_radix(size.trim().split(';').next().unwrap_or("0"), 16)
                .map_err(|_| std::io::Error::new(std::io::ErrorKind::InvalidData, "bad chunk size"))?;
            if n == 0 {
                self.done = true;
                return Ok(0);
            }
            self.left = n;
        }
        let want = buf.len().min(self.left);
        let n = self.inner.read(buf.get_mut(..want).unwrap_or_default())?;
        self.left -= n;
        Ok(n)
    }
}

fn body_reader<'a>(r: BufReader<TcpStream>, headers: &[(String, String)]) -> Box<dyn BufRead + 'a> {
    let chunked = headers.iter().any(|(k, v)| k == "transfer-encoding" && v.eq_ignore_ascii_case("chunked"));
    if chunked { Box::new(BufReader::new(Chunked { inner: r, left: 0, done: false })) } else { Box::new(r) }
}

fn call(info: &SidecarInfo, method: &str, path: &str, body: Option<&Value>) -> Result<Vec<u8>, String> {
    let mut r = open(info, method, path, body, ANSWER_WAIT)?;
    let (status, headers) = read_head(&mut r)?;
    let mut out = vec![];
    body_reader(r, &headers).take(MAX_BODY as u64).read_to_end(&mut out).map_err(|e| format!("answer cut short: {e}"))?;
    if (200..300).contains(&status) {
        return Ok(out);
    }
    let detail =
        serde_json::from_slice::<Value>(&out).ok().and_then(|v| v.get("detail").map(|d| d.as_str().map_or_else(|| d.to_string(), str::to_string)));
    Err(detail.unwrap_or_else(|| format!("the agents answered {status}")))
}

/// Read a run's server-sent events until the run ends, the stream closes, or a newer `follow` began.
fn follow_events(info: &SidecarInfo, path: &str, current: &dyn Fn() -> bool, on: &mut dyn FnMut(Value)) -> Result<(), String> {
    let mut r = open(info, "GET", path, None, Duration::from_secs(2))?;
    let (status, headers) = read_head(&mut r)?;
    if status != 200 {
        return Err(format!("the agents answered {status}"));
    }
    let mut body = body_reader(r, &headers);
    let mut line = String::new();
    while current() {
        // `line` is cleared only after a whole line: a timeout mid-line keeps what was read so far.
        match body.read_line(&mut line) {
            Ok(0) => return Ok(()),
            Ok(_) if !line.ends_with('\n') => {}
            Ok(_) => {
                let event = line.trim_end().strip_prefix("data: ").and_then(|data| serde_json::from_str::<Value>(data).ok());
                line.clear();
                if let Some(event) = event {
                    let done = event.get("type").and_then(Value::as_str) == Some("done");
                    on(event);
                    if done {
                        return Ok(());
                    }
                }
            }
            // Quiet while the agents think: keep waiting (and check whether a newer stream took over).
            Err(e) if matches!(e.kind(), std::io::ErrorKind::WouldBlock | std::io::ErrorKind::TimedOut) => {}
            Err(e) => return Err(format!("event stream broke: {e}")),
        }
    }
    Ok(())
}
