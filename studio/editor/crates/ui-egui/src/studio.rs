//! 91studio: how the AI panel ([`crate::panels::studio_ai`]) reaches the 91 Studio agents. The panel never
//! does I/O: the host app (desktop) implements [`StudioAgents`] on background threads and the panel polls it
//! once a frame. Without one (the web, tests without a fake) the panel says the agents aren't available.
//! See `studio/docs/DECISIONS.md` D-021.

use serde_json::Value;

/// What came back since the last [`StudioAgents::poll`].
#[derive(Clone, Debug)]
pub enum StudioReply {
    /// The answer to [`StudioAgents::request`] with this tag: the JSON body, or an error message.
    Answer { tag: String, result: Result<Value, String> },
    /// One event of the followed run (`{seq, type, at, data}`).
    Event(Value),
    /// A run file fetched with [`StudioAgents::fetch`] (a preview PNG).
    File { key: String, result: Result<Vec<u8>, String> },
}

/// The agents' API, as the host provides it. Every call returns at once; results arrive through `poll`.
pub trait StudioAgents {
    /// `method` `path` (e.g. "POST", "/api/runs/json") with a JSON body; the answer comes back tagged `tag`.
    fn request(&mut self, tag: &str, method: &str, path: &str, body: Option<Value>);
    /// Stream a run's events (replacing any earlier stream); each arrives as [`StudioReply::Event`].
    fn follow(&mut self, run_id: &str);
    /// Fetch a file (bytes) from `path`; it arrives as [`StudioReply::File`] with `key`.
    fn fetch(&mut self, key: &str, path: &str);
    /// Everything that arrived since the last call, in order.
    fn poll(&mut self) -> Vec<StudioReply>;
}
