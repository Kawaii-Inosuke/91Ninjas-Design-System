//! 91studio: starts the 91 Studio bridge (`vectorcraft-studio-bridge`) and hands its calls to the
//! app as control requests, on the same queue and with the same give-up rule as the control server
//! (`control_server.rs`). Enabled with `STUDIO_BRIDGE=1`. See `studio/docs/DECISIONS.md` D-008.

use std::sync::Arc;
use std::sync::mpsc::Sender;
use std::time::Duration;

use serde_json::{Value, json};
use vectorcraft_engine::file_access::AutomationRoots;
use vectorcraft_ui_egui::ControlRequest;

/// How long a call waits for the app's reply (as the control server).
const REPLY_WAIT: Duration = Duration::from_secs(60);

pub fn enabled() -> bool {
    std::env::var("STUDIO_BRIDGE").is_ok_and(|v| v == "1")
}

/// Starts the bridge → the file confinement for its requests: they may touch only the bridge's own
/// folder (they never name files anyway; this is the second wall). The confinement is made first:
/// without it the bridge doesn't start. `None` when it couldn't start.
pub fn start(tx: Sender<ControlRequest>, ctx: egui::Context) -> Option<Arc<AutomationRoots>> {
    let dir = vectorcraft_studio_bridge::bridge_dir().join("files");
    let roots = std::fs::create_dir_all(&dir)
        .map_err(|e| e.to_string())
        .and_then(|()| AutomationRoots::new(&[dir.as_path()], &[dir.as_path()]))
        .ok()
        .flatten();
    let Some(roots) = roots else {
        log::error!("91 Studio bridge not started: could not confine its requests to {}", dir.display());
        return None;
    };
    let forward = Arc::new(move |method: &str, params: Value| -> Value {
        let (req, rrx) = ControlRequest::new(method, params);
        let ticket = req.ticket();
        if tx.send(req).is_err() {
            return json!({"ok": false, "error": "the app is shutting down"});
        }
        ctx.request_repaint();
        rrx.recv_timeout(REPLY_WAIT).unwrap_or_else(|_| {
            let error = if ticket.give_up() {
                "timeout: the app did not start the request in time; it was withdrawn and will not run"
            } else {
                "timeout: the app is still running the request; it may yet complete"
            };
            json!({"ok": false, "error": error})
        })
    });
    match vectorcraft_studio_bridge::start(forward) {
        Ok(_) => Some(roots),
        Err(e) => {
            log::error!("91 Studio bridge failed to start: {e}");
            None
        }
    }
}
