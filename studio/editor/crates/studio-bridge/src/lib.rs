//! 91 Studio bridge: the localhost HTTP API through which the 91 Studio AI agents drive the editor.
//!
//! The bridge is transport and policy only. Every call it accepts becomes a control request
//! (`engine.execute`, `ui.render`, …) handed to the app through [`Forward`]; the app runs it on the
//! UI thread exactly as it runs the control channel's requests, with the same crash guard, undo
//! history and file confinement. See `studio/docs/TRD.md` › "Bridge API" and `studio/docs/SECURITY.md`.
//!
//! Security model (SECURITY T1, T3, T4):
//! - binds `127.0.0.1` only, on an ephemeral port unless `STUDIO_BRIDGE_PORT` names one;
//! - every request needs `Authorization: Bearer <token>`, a random 256-bit token made per launch and
//!   written with the port to `bridge.json`, readable by the user only;
//! - requests with an `Origin` header (browsers) or a `Host` other than this loopback port (DNS
//!   rebinding) are refused before the token is even looked at;
//! - only allow-listed commands run ([`api`]), and none may name a file path.
#![forbid(unsafe_code)]

mod api;
mod http;

use std::io;
use std::net::TcpListener;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::sync::atomic::{AtomicUsize, Ordering};

use serde_json::{Value, json};

/// Runs one control request in the app and returns its reply (`{ok: true, result}` or
/// `{ok: false, error}`), as the control channel's methods answer.
pub type Forward = dyn Fn(&str, Value) -> Value + Send + Sync;

/// Connections served at once; more are refused with 503.
const MAX_CONNECTIONS: usize = 16;

/// A running bridge: where it listens and where its connection file is.
pub struct Bridge {
    pub port: u16,
    pub info_file: PathBuf,
    pub files_dir: PathBuf,
}

/// The folder for the bridge's connection file and the only folder its requests may touch:
/// `STUDIO_BRIDGE_DIR`, else `$XDG_RUNTIME_DIR/91studio`, else `~/.local/share/91studio/bridge` (Mac, Windows).
pub fn bridge_dir() -> PathBuf {
    if let Some(d) = std::env::var_os("STUDIO_BRIDGE_DIR").filter(|d| !d.is_empty()) {
        return PathBuf::from(d);
    }
    if let Some(d) = std::env::var_os("XDG_RUNTIME_DIR").filter(|d| !d.is_empty()) {
        return PathBuf::from(d).join("91studio");
    }
    // 91studio: Mac and Windows have no runtime folder: the app's data folder, which Claude's connector (started
    // with a minimal environment) finds the same way (D-040). The temp folder only without a home.
    if let Some(home) = std::env::var_os("HOME").or_else(|| std::env::var_os("USERPROFILE")).filter(|h| !h.is_empty()) {
        return PathBuf::from(home).join(".local").join("share").join("91studio").join("bridge");
    }
    let user = std::env::var("USER").or_else(|_| std::env::var("USERNAME")).unwrap_or_else(|_| "user".into());
    std::env::temp_dir().join(format!("91studio-{user}"))
}

/// Starts the bridge: binds, writes `bridge.json` (port, token, pid) for the agents, and serves on
/// a background thread for the life of the process.
pub fn start(forward: Arc<Forward>) -> io::Result<Bridge> {
    let dir = bridge_dir();
    let files_dir = dir.join("files");
    create_private_dir(&dir)?;
    create_private_dir(&files_dir)?;
    let token = match std::env::var("STUDIO_BRIDGE_TOKEN") {
        Ok(t) if t.len() >= 32 => t,
        _ => new_token()?,
    };
    let port: u16 = std::env::var("STUDIO_BRIDGE_PORT").ok().and_then(|p| p.parse().ok()).unwrap_or(0);
    let listener = TcpListener::bind(("127.0.0.1", port))?;
    let port = listener.local_addr()?.port();
    let info_file = dir.join("bridge.json");
    let info = json!({"port": port, "token": token, "pid": std::process::id(), "api": 1, "filesDir": files_dir});
    write_private_file(&info_file, info.to_string().as_bytes())?;
    let server = http::Server { port, token, forward };
    std::thread::Builder::new().name("studio-bridge".into()).spawn(move || accept_loop(listener, server))?;
    log::info!("91 Studio bridge listening on 127.0.0.1:{port} (connection file {})", info_file.display());
    Ok(Bridge { port, info_file, files_dir })
}

fn accept_loop(listener: TcpListener, server: http::Server) {
    let server = Arc::new(server);
    let open = Arc::new(AtomicUsize::new(0));
    for stream in listener.incoming().flatten() {
        if open.fetch_add(1, Ordering::SeqCst) >= MAX_CONNECTIONS {
            open.fetch_sub(1, Ordering::SeqCst);
            http::refuse_busy(stream);
            continue;
        }
        let (server, open) = (Arc::clone(&server), Arc::clone(&open));
        let spawned = std::thread::Builder::new().name("studio-bridge-conn".into()).spawn(move || {
            server.serve(stream);
            open.fetch_sub(1, Ordering::SeqCst);
        });
        if let Err(e) = spawned {
            log::warn!("studio bridge: could not start a connection thread: {e}");
        }
    }
}

/// 32 random bytes as 64 hex digits (the bridge's token; the app uses it for the agents' token too).
pub fn new_token() -> io::Result<String> {
    let mut bytes = [0u8; 32];
    getrandom::fill(&mut bytes).map_err(|e| io::Error::other(format!("no random source: {e}")))?;
    Ok(bytes.iter().map(|b| format!("{b:02x}")).collect())
}

fn create_private_dir(dir: &Path) -> io::Result<()> {
    std::fs::create_dir_all(dir)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(dir, std::fs::Permissions::from_mode(0o700))?;
    }
    Ok(())
}

fn write_private_file(path: &Path, bytes: &[u8]) -> io::Result<()> {
    use std::io::Write;
    let mut opts = std::fs::OpenOptions::new();
    opts.write(true).create(true).truncate(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        opts.mode(0o600);
    }
    let mut f = opts.open(path)?;
    f.write_all(bytes)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o600))?;
    }
    Ok(())
}

#[cfg(test)]
mod tests;
