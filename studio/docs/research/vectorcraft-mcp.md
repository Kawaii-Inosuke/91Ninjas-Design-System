# VectorCraft MCP / control channel: research for `crates/studio-bridge`

Source: local clone at `studio/editor`, pinned commit `4ee0ef231aa31f50192f0072554fa390957532bf`.
All paths below are relative to `studio/editor/`. I read the code and did not build or run it.
**[read]** means I read it in the code. **[inferred]** means I worked it out from code I read but didn't run it.

---

## 1. Summary

1. MCP is a **thin translator**. Every MCP tool becomes one or more *control-channel methods* (`engine.execute`, `document.inspect`, `ui.render`, `app.export`, …) sent to a `Backend` (`crates/mcp/src/backend.rs:11-18`).
2. There are two backends. `Headless` (an in-process `vectorcraft_engine::Session` plus a CPU renderer, `crates/mcp/src/headless.rs:16-24`) and `Remote` (a JSON-lines TCP client to a running app on `127.0.0.1:7979`, `backend.rs:20-195`).
3. The live app's control server (`apps/vectorcraft/src/control_server.rs`) is the pattern to copy. Per-connection threads parse lines, wrap each one in a `ControlRequest {method, params, reply: Sender}`, push it on an mpsc channel, call `ctx.request_repaint()` and block up to 60 s for the reply (`control_server.rs:134-151`).
4. The egui UI thread drains that channel every frame, handling at most 64 requests per frame (`crates/ui-egui/src/app/control.rs:8-98`, called from `tick` at `crates/ui-egui/src/app.rs:356`). It answers through `control::handle`, whose core is `app.run(id, params)` → `Session::execute` (`crates/ui-egui/src/control.rs:253-262`, `crates/ui-egui/src/app/commands.rs:17-130`).
5. **Everything is a command.** `CommandSpec {id, label, menu, shortcut, params-doc, enabled, run, journal}` lives in a static registry (`crates/engine/src/cmd.rs:117-131, 234-341`). `Session::execute` is the single entry point (`crates/engine/src/session/execute.rs:36-130`).
6. Undo works by copy-on-write snapshots. `Session::edit(label, f)` clones the `Arc<Document>` and pushes one `HistoryEntry` (`session/edit.rs:9-137`, `history.rs`). A batch is one undo step either through `command.batch` (atomic, rolls back on error) or through `edit.undoGroup.begin/end` (what MCP `command_batch {atomic:true}` uses).
7. Node ids are `NodeId(u64)`, stable for the document's lifetime and never reused (`crates/doc/src/node/ids.rs:3-6`). Artboards are **not nodes**: they are rects in `Document.artboards`, and art lives in layers.
8. SVG import goes through usvg plus a custom XML text pass. Groups, names (`data-name` / `inkscape:label` / `serif:id` / `aria-label`, else `id`), live text with tspans, the first font family, fills and strokes, gradients and clip paths all survive. **Top-level `<g id>` become layers, and `file.place` flattens layers**, so wrap the SVG in one outer `<g id>` before placing it.
9. `file.place {name, dataBase64, at | rect, crop?}` is the right primitive for "put an SVG on the canvas". It makes one undo step, puts the art in one named group, selects it and never touches the clipboard (`crates/engine/src/cmd/place.rs:65-72`). `text.setText {id, text}` already exists.
10. Our bridge should be a token-authenticated HTTP front end at layer L6 that **depends only on `serde_json` + std (+ optionally `vectorcraft-engine`)**. The `vectorcraft` app binary (layer-exempt) glues it to the existing `ControlRequest` channel. Missing engine pieces are small: top-left/artboard-relative placement, an SVG-id → node-id map, and per-run text replace.

---

## 2. How MCP and the control channel reach the engine

### 2.1 Process models  [read]

| Mode | How started | Backend | Where the engine runs |
|---|---|---|---|
| Headless MCP | `vectorcraft-cli mcp --headless` (or no app listening) | `Headless::with_document()` (`apps/vectorcraft-cli/src/main.rs:215-271`) | In the MCP process, on the thread that called `Server::serve` |
| Live MCP | `vectorcraft --control 7979` then `vectorcraft-cli mcp --connect 127.0.0.1:7979` | `Remote::connect` (`backend.rs:35-39`) | In the GUI app, on the egui UI thread |
| Raw control | `vectorcraft --control <port>` (or `VECTORCRAFT_CONTROL_PORT`) | n/a: JSON lines straight to the app | egui UI thread |

`--automation-read-root` / `--automation-write-root` confine file I/O for control requests (`apps/vectorcraft/src/main.rs:233-237, 462-470`, `crates/engine/src/file_access.rs:41,141,161,205`).

### 2.2 Transports  [read]
- **MCP:** newline-delimited JSON-RPC 2.0 over stdio, hand-written with no async runtime (`crates/mcp/src/lib.rs:1-14`). A reader thread, an engine (work) thread and a writer thread (`server.rs:276-321`, `intake.rs:1-16`). Lines are capped at 16 MiB (`server.rs:41`). Requests that arrive while a call has run for more than 2 s are refused as busy (`server.rs:36`). Cancellation and progress notifications are supported.
- **Control channel:** JSON lines on `127.0.0.1:<port>`. A request is `{"id","method","params"}` and a reply is `{"id","ok":true,"result"}` or `{"id","ok":false,"error"}`. Limits: 4 MiB per line, 16 connections, 5 min idle, a 30 s line deadline, 60 s for the reply (`control_server.rs:24-58`).
- **No authentication on the control port.** `docs/control-protocol.md:26` says: "The port has no authentication, so only enable it while you use it". Its only defence against browsers is that a non-JSON first line (an HTTP request line) closes the connection (`control_server.rs:4-8, 183-206`).

### 2.3 Call path: one MCP tool call down to the engine  [read]

```
stdin line ──reader thread (intake::read_loop)──► queue ──engine thread: Server::work/run
  Server::handle → guard::catch_panic(request)                       server.rs:369-400
    "tools/call" → check_tool_arguments → tools::call_tool           server.rs:432-439
      dispatch(name) e.g. "command_run" → b.call("engine.execute",{command,params})   tools/dispatch.rs:97-106
        ├─ Headless::call → file_access::confine → answer()          headless.rs:323-368
        │     "engine.execute" → exec(): check_params → Session::execute(id, params) headless.rs:129-145
        └─ Remote::call → TCP line {"id","method","params"} → app   backend.rs:145-186
              control_server: serve_socket → ControlRequest::new → tx.send → ctx.request_repaint()
                                                                    control_server.rs:141-150
              (egui UI thread) VectorcraftApp::tick → drain_control  app.rs:244,356; app/control.rs:8-98
                 req.claim() → file_access::confine(catch_panic(control::handle(app, ctx, &req)))
                 control::handle "engine.execute": menus::check_params → app.run(id, params)
                                                                    control.rs:253-262
                 app.run → picks (file dialogs) → run_now: UI command? → session.execute(id,&params)
                                                                    app/commands.rs:17-130
                    Session::execute: find_command → enabled → run_guarded(spec.run) → journal
                                                                    session/execute.rs:36-130
```

### 2.4 Threading: handing work from a network thread to the egui thread  [read]
- `ControlRequest { method, params, reply: Sender<Value>, state: Arc<AtomicU8> }` (`crates/ui-egui/src/control.rs:98-138`). The state goes QUEUED → STARTED (`claim`) or QUEUED → GIVEN_UP (`Ticket::give_up` after a timeout). A request withdrawn before the app took it never runs (#1725). Copy this.
- The network thread does `tx.send(req)`, `ctx.request_repaint()`, then `rrx.recv_timeout(60 s)`. On timeout it calls `ticket.give_up()` and replies that the request was withdrawn, or that it may still complete (`control_server.rs:33-45,143-149`).
- The UI thread takes the `Receiver` out of `self` and handles at most `MAX_CONTROL_REQUESTS_PER_FRAME = 64` requests per frame (`control.rs:50`). Each request runs under `automation_roots` confinement and `catch_panic`, and the UI thread requests another frame if requests are left (`app/control.rs:8-98`).
- `ui.screenshot` replies are deferred: they need a presented frame (`control::Outcome::Screenshot`, `app/control.rs:36-90`). `ui.render` renders off-screen synchronously on the UI thread with `app.canvas.renderer` (`control.rs:380-411`).
- The app wires it up in `main.rs:462-464`: `control_server::start(port, cc.egui_ctx.clone())` returns an `mpsc::Receiver<ControlRequest>`, and `app.with_control(rx)` stores it (`crates/ui-egui/src/app.rs:176-177`).
- macOS: the control server holds an `NSProcessInfo` activity so App Nap doesn't throttle a hidden window (`control_server.rs:76-93`). **[inferred]** A minimized or occluded window still has to tick frames for requests to be served. `request_repaint` usually does that, but a locked screen breaks `ui.screenshot` (AGENTS.md "Running and looking at the app").

---

## 3. MCP tools → engine commands

Tool definitions are in `crates/mcp/src/tools/definitions.rs` (line numbers below are where each `tool(` call starts). Dispatch is `tools/dispatch.rs:74-273` and the helpers are `tools/handlers.rs`. Results are MCP content: `ToolResult::json` (pretty JSON text), or `image_result` (an image block plus a JSON text block, `handlers.rs:466-474`). Errors return `isError:true` text.

| MCP tool (def line) | Control method → engine command(s) | Key params | Returns |
|---|---|---|---|
| `command_list` (98) / `list_commands` (156) | `engine.commands` → `Session::commands()` + host/UI commands (`headless.rs:147-153`, `control.rs:163-169`); filtered in `dispatch.rs:34-72` | `filter`/`query`, `menu`, `enabled_only`/`enabledOnly`, `brief` | array of `CommandInfo {id,label,menu,shortcut,params,enabled,disabled_reason}` (`cmd.rs:133-161`); `list_commands` wraps it as `{count,commands}` |
| `command_run` (108) / `run_command` (166) / `invoke_menu` (325) | `engine.execute {command, params}` (`invoke_menu` with a UI → `ui.menu.invoke`) `dispatch.rs:97-106` | `id` or `command`, `params{}` | the command's own result JSON |
| `command_batch` (115) | `edit.undoGroup.begin {label}` → N × `engine.execute` (or `draw_path`/`draw_shape`) → `edit.undoGroup.end` (`dispatch.rs:335-424`); `"$N.path"` refs resolved by `vectorcraft_engine::steps::resolve` (`crates/engine/src/steps.rs:1-25`) | `steps:[{id,params}]` ≤256, `stop_on_error` (default true), `atomic`, `label` | `{completed, failed, results:[{ok,result|error}], created:[ids]}`; result bytes capped at 8 MiB |
| `doc_inspect` (125) / `inspect_document` (176) | `document.inspect` (`cmd/edit.rs:32`, `inspect.rs`) | `depth`, `childLimit`, `ids`, `kind`, `limit`, `offset` | `{layers|nodes, artboards:[{name,x,y,width,height}], selection, history, …}` (`inspect.rs:40-44,140,258-295`) |
| `render_preview` (132) | `document.inspect {depth:0}`, then `ui.render {scale, artboard}`, then thumbnail to `max_side` (`dispatch.rs:426-459`) | `artboard` (0-based), `max_side` (≤4096, default 1024), `traceViews` | PNG image block + `{width,height}` |
| `screenshot` (339) | `ui.render` (artboard) or `ui.screenshot` (window) (`handlers.rs:375-421`) | `path`, `scale`, `artboard`, `window`, `max_side` | PNG + info |
| `ui_inspect` (142) / `inspect_ui` (183), `ui_screenshot` (149), `type_text` (300), `answer_dialog` (307), `open_panel` (332) | `ui.inspect`, `ui.screenshot`, `ui.text`, `ui.dialog.set/confirm/cancel`, `ui.set {panel}`; need the desktop app | — | UI JSON / PNG |
| `select_tool` (190) | `ui.tool.select` → `tool.select` | `tool` | `{tool}` |
| `pointer_gesture` (197) | `ui.pointer {events[], mods}`, events forced to `space:"doc"` (`dispatch.rs:118-148`) | `events:[{kind,x,y}]`, `tool?` | `{selection,key,tool,requests}` |
| `press_key` (290) | `ui.key` (headless maps shortcut → command, `headless.rs:223-265`) | `key`, `mods` | `{handledBy,…}` |
| `draw_path` (227) | **`command.batch`** of `path.create {anchors|d, closed}` + `paint.setFill`/`paint.setStroke`/`stroke.set` (`item:null`) + `object.setProps {name,opacity}` + `effect.apply` (`handlers.rs:151-218`) | `points` or `d`, `fill`, `stroke`, `strokeWidth`, `dash`, `cap`, `join`, `opacity`, `name`, `effects` | `{id, paint, name?, opacity?, effects?}` |
| `draw_shape` (244) | `command.batch` of `shape.rectangle|ellipse|polygon|star|line` + the paint steps above (`handlers.rs:220-237`) | `shape`, geometry, paint | `{shape, id, paint…}` |
| `set_paint` (275) | `paint.setFill`, `paint.setStroke`, `stroke.set {weight,dash,cap,join}` with `ids` (`handlers.rs:85-96`) | `fill`, `stroke`, `strokeWidth`, `ids` | `{applied, objects, note?}` |
| `add_text` (404) | `text.create {x,y,text,size,font,color,justify,vertical, area:{width,height,fit}}` or `text.createInPath`, then `type.pathOptions`, then `document.node` for overflow (`handlers.rs:247-291`; spec `cmd/create.rs:88`) | `text`, `x`,`y` or `path`, `width`/`height`, style | `{id, overflow?}` |
| `transform` (457) | `select.set {ids}`, then `object.move {dx,dy,copy}`, `object.rotate {angle,origin}`, `object.scale {sx,sy}`, `object.reflect {axis}`, `object.shear {angle}` (`handlers.rs:293-332`) | `ids`, `dx`,`dy`, `rotate`, `scale`/`scaleX`/`scaleY`, `reflect`, `shear`, `origin`, `copy` | `{applied:[…]}`; **each op is its own undo step** |
| `pathfinder` (444) | `select.set`, then `object.pathfinder.<op>` | `operation`, `ids` | command result |
| `apply_effect` (430) | `effect.list` / `effect.apply {effect, params, ids}` | `effect`, `params`, `ids` | `{catalog}` or result |
| `create_graph` (479) | `graph.create` | csv/series… | result |
| `text_wrap` (499) | `select.set`, then `object.textWrap.make|release` | `offset`, `invert`, `release` | result |
| `open_file` (356) | `app.open {path}` → `document.open` (headless) / `file.open` (app) | `path` (made absolute for the app, `handlers.rs:14-20`) | `{index,title,format,warnings,…}` |
| `save_file` (366) | `app.save {path?, format?, options, wait:true}` → `document.save` / `file.save` | `path`, `format`, options | `{path, format, warnings…}` |
| `export` (385) | `document.export` or `document.exportSelection` (`dispatch.rs:211-231`) | `path?`, `format`, `artboard`, `range`, `scale` (raster), `outlineText`, `options{}` | `{path,format,bytes,warnings}` or `{dataBase64,…}` without a path |
| `undo` (514) / `redo` (515) | `edit.undo` / `edit.redo` (`cmd/edit.rs:101-102`) | — | `{}`-ish |

Resources: `vectorcraft://document` (`document.inspect`), `/document/json` (`document.json`, the full model), `vectorcraft://commands`, and templates `object/{id}`, `command/{id}`, `effect/{id}`, `swatch/{name}` (`crates/mcp/src/resources.rs:15-110`). There are 5 prompts (poster, icon-set, recolor, trace-and-style, export-set) in `prompts.rs`.

Other details:
- Unknown argument keys are rejected at the tool level (`dispatch.rs:325-333`) and at the command level (`check_params`, `headless.rs:130-135`, `control.rs:258`, AGENTS.md "#1442"). **The bridge must send exact keys.**
- `id` / `ids` in params are validated before anything runs. A layer id is refused for object commands (`session/execute.rs:17-55`).

---

## 4. Undo/history, batching, crash guard

### 4.1 How a command becomes an undo step  [read]
- `Session::edit(label, |doc, sel| …)` (`crates/engine/src/session/edit.rs:9-137`) takes `before = st.doc.clone()` (an `Arc<Document>`), mutates through `Arc::make_mut` (copy-on-write, so nodes are `Arc`-shared), and runs the post-passes: global edit, objects-on-path, dimensions, shaper, symbols, inline graphics, text wrap, threads, masks, asset/variable pruning. It then checks sanity (depth, coordinates in range) and, outside an interaction, `st.push_undo(label, before, selection)` (line 137).
- `History { undo: Vec<HistoryEntry>, redo, limit, byte_budget }`. Each entry keeps `Arc<Document>` + `Arc<Selection>` + a byte estimate. The budget is 1/8 of RAM, capped at 1 GiB, with at least 8 steps kept (`crates/engine/src/history.rs:3-147`).
- `edit.undo` / `edit.redo` swap the `Arc<Document>` (`cmd/edit.rs:101-102, 488-520`). Any in-progress interaction (typing, drag) is settled first.
- Interactions (tool drags): `begin_interaction` / preview / `commit_interaction` (`session/edit.rs:285`). Edits inside an interaction mutate without pushing; the commit pushes one step.

### 4.2 Batches  [read]
- **`command.batch {label?, commands:[{command, params}], artboard?}`** (`crates/engine/src/cmd/fileio/batch.rs:11-63`, spec `cmd/fileio/commands.rs:~105-114`):
  - It saves the whole `s.docs` plus session state (paint defaults, clipboard, untitled counter, explicit undo group).
  - Each step runs inside one interaction labelled `label` per document. Steps run through `execute_step`. Batches can't nest (line 82).
  - **On the first error everything is rolled back** (`roll_back`, lines 150-159), including documents the batch opened or closed. Files already written stay written.
  - On success each edited document keeps **one undo step**. The reply is `{results:[…]}`.
  - Steps **cannot** reference earlier results (`$N`): resolution happens in MCP / CLI, not in `command.batch`.
- **`edit.undoGroup.begin {label?}` / `edit.undoGroup.end {cancel?}`** (`cmd/edit.rs:110-127, 444-458`; `session/undo.rs:15,78`): edits between the two are one undo step in the document active at begin. `cancel:true` undoes them all. MCP `command_batch {atomic:true}` uses this (`dispatch.rs:348-420`), resolving `"$N.path"` references with `vectorcraft_engine::steps::resolve` (public API, `crates/engine/src/steps.rs:22`). The steps that ran **stay** if a later one fails, unless you end with `cancel:true`.
- The app's `run_now` injects `artboard` (the active artboard) into `command.batch` and into some commands (`app/commands.rs:42-60`).

### 4.3 Crash guard / rollback  [read]
- `guard::catch_panic(f)` = `catch_unwind(AssertUnwindSafe(f))` → `Err(msg)` (`crates/engine/src/guard.rs`). It is used at every entry point: MCP `Server::handle` (`server.rs:394`), `call_tool` (`dispatch.rs:310`), each batch step (`dispatch.rs:363`), `drain_control` (`app/control.rs:23`).
- `Session::run_guarded` (`session/execute.rs:205-237`) snapshots every open doc's `(doc, selection, interaction, history)` before a top-level command and restores them on panic, returning `EngineError::Internal`.
- wasm aborts on panic, so the guard only protects native builds. The no-panic lints (`unwrap`/`expect`/`panic!`… banned outside tests) are the real rule (AGENTS.md).

---

## 5. Document model essentials for agents

- **Ids** [read]: `NodeId(pub u64)`, serialized as a bare number. "Stable per-document object id. Never reused" (`crates/doc/src/node/ids.rs:3-6`). They are allocated by `Document::alloc_id` (`crates/doc/src/document.rs:259-265`). Ids are **per document**: placing or pasting art from another document gives it new ids (`Document::reid`, `crates/doc/src/document_guides.rs:277`). Undo restores old snapshots, so an id created after the undo point disappears on undo and is not reissued.
- **Addressing** [read]: commands take `id` / `ids` (u64), or act on the selection (`select.set {ids}`, `crates/engine/src/cmd/select.rs:42-47`). There is no path- or name-based addressing in commands. Use **`document.find {name?, kind?, text?, within?, …}` → `{matches:[{id,name,kind,path}]}`** (`cmd/edit.rs:34`) or `document.inspect {kind, ids}`, where each listed node has `path`, its ancestor ids with the layer first (`inspect.rs:435`). Names are not unique.
- **Node** [read] (`crates/doc/src/node.rs:52-74`): `id, name: Option<String>, visible, locked, opacity, blend, isolate, knockout, appearance (fills/strokes/effects), kind, bbox_angle, extras` (rare data such as `data-*` attributes and URLs).
- **NodeKind** [read] (`crates/doc/src/node/kind.rs:~30-110`): `Layer{color,template,printable,children,clip,…}`, `Group{children,clip}`, `Path{path,rule,live,clipping,guide}`, `Compound{children,rule}`, `Text(Box<TextObject>)`, `Image(Box<ImageObject>)`, `SymbolInstance{symbol,xf}`, `Blend`, `Envelope`, `Mesh`, `Repeat`, `PlacedDocument`, `Intertwine`. `document.inspect` reports panel labels (`kind_label()`: Layer, Group, Path, Type, Image…).
- **Text** [read] (`crates/doc/src/text.rs:35-80`, `text/runs.rs:15-102`): `TextObject {kind: Point | Area{frame} | OnPath{path,start,end}, xf: Affine, runs: Vec<TextRun{text, style: CharStyle, inline?}>, para, paras, area, …}`. `CharStyle` has `font_family`, `font_style`, `size`, `fill`, `stroke`, … (`text/char_style.rs`). `document.inspect` adds `text` (plain text) for type nodes (`inspect.rs:100`).
  - `text.setText {id|ids, text}` replaces the whole content, **keeps only the first run's style** and clears ruby (`crates/engine/src/cmd/typecmd.rs:77-84, 736-753`). Undo label "Typing".
  - `text.editRange {id, start, end, insert?, runs?}` (byte offsets) and `text.getRange` give run-level control (`cmd/textedit.rs:24,60`).
  - `text.setStyle {ids?, font?, style?, size?, fill?, …, start?, end?}` (`typecmd.rs:122`).
- **Artboards** [read] (`crates/doc/src/artboard.rs:4-40`): `Artboard {id: u32, name, rect: Rect (document points), locked, …, color}` in `Document.artboards: Vec<Artboard>`. They are **not in the layer tree**: art is not parented to an artboard, it just lies over the artboard's rect (Illustrator model). Address artboards by 0-based index. `artboard.new {x?,y?,width?,height?,name?,color?}` → `{index}` (`cmd/layer.rs:127-136, 878-906`). Coordinates are points, y down, with the origin at the first artboard's top-left. A new document is 612×792 (`server.rs:80-87`).
- `Document {artboards, layers: Vec<Arc<Node>>, swatches, symbols, text_threads, …}` (`crates/doc/src/lib.rs:149-200`). `document.json` dumps the whole model.

---

## 6. SVG import: what survives

Entry point: `vectorcraft_svg::import*` (`crates/svg/src/lib.rs:541-560`), implemented in `crates/svg/src/import.rs:104-295`. It parses with roxmltree, runs a prepass, converts `<text>` itself and lets usvg handle the geometry.

| Feature | Status | Evidence |
|---|---|---|
| Groups | `<g>` → `Group` (clip groups for clip-path) | `import/importer.rs:139-152` [read]. **[inferred]** usvg may drop id-less attribute-less groups, so give groups ids. |
| Top-level `<g id>` | Become **layers** when *all* top-level children are such groups (or text); otherwise everything goes into "Layer 1" | `import.rs:189-252` [read] |
| Layer/object names | `name_of(id)` = `data-name` → `inkscape:label` → `serif:id` → `aria-label` → the element `id`. Made-up usvg ids are ignored. | `import/prepass.rs:376-391`, `importer.rs:112-137` [read] |
| `data-*` attrs, events, foreign attrs | Kept on the node (keyed by element id, so the element needs an `id`) | `importer.rs:84-95`, `import.rs:244-246` [read] |
| `<text>` / `<tspan>` / `<textPath>` / `<a>` | **Live text**, read from the XML (not usvg): tspans become runs with their own CharStyle; per-char x/y/dx/dy/rotate become line breaks, kerning, baseline shift and rotation; `textPath` becomes type on a path; vertical writing-mode is supported; `text-anchor`, `letter-spacing` and `text-decoration` are read | `import/text.rs:1-21, 463-481, 428-438, 706` [read] |
| `font-family` | **Only the first family** of the stack is kept; weight/italic map to a style name ("SemiBold Italic"); `font-variation-settings` → axes. A missing font draws in the fallback (with a warning at create/setStyle time). | `text.rs:52-62, 392-404` [read] |
| Fills / strokes | Solid, linear/radial gradients (focal radius ignored, spreadMethod approximated), patterns, dash/cap/join, non-scaling strokes | `importer.rs:249, 487-510` [read] |
| Clip paths / masks | Clip paths → clip groups (nesting capped); masks → opacity masks (nested masks ignored) | `importer.rs:365-409` [read] |
| Filters | Unsupported filters are **dropped with a warning** (some of our own exports' shadows/glows round-trip) | `importer.rs:301` [read] |
| `<use>`/`<symbol>` | Become symbol instances when the instance draws identically, else plain art | `importer.rs:154-180` [read] |
| Inkscape pages | Become artboards | `import/layers.rs:123-150` [read] |
| Units | px/unitless = 1 pt; absolute units at 72 pt/in (CSS px 0.75 pt when the root has physical units) | `layers.rs:49-83` [read]; gap #864 "SVG units reverting to points" (`docs/gaps.md:309`) |
| Limits | 2,000,000 nodes, a depth cap, entity-expansion and filter-overflow guards | `import.rs:26-28, 56-103` [read] |

**Placing an SVG** (these are the commands that put SVG at a position or onto an artboard):

1. **`file.place {name: "x.svg", dataBase64 | path, at?: [cx,cy] | rect?: [x,y,w,h], crop?: "crop"|"bounding", template?, replace?, link?}`** (`crates/engine/src/cmd/place.rs:65-72`, implementation `place` at :548):
   - `at` is the **centre** (default: the first artboard's centre). `rect` means fit inside with the aspect kept.
   - By default the placed bounds are the art's bounds. `crop:"crop"` frames and clips the art by the SVG's viewBox, which adds a clip group (`place.rs:264-270`).
   - Result: **one group** containing the art, named after `name` (`build`, `place.rs:390-417`), with its ids reallocated (`reid`). It is one undo step, selects the group, never touches the clipboard, and returns `{ids, name, format, linked, width, height, warnings}`.
   - **Gotcha** [read]: `layer_art` (`place.rs:352-359`) takes the *children* of each imported layer. If the SVG's top level is all `<g id>` (layer mode), those named top-level groups are flattened away and their names lost. **Workaround: wrap the SVG body in a single `<g id="studio-root" data-name="…">`.** It becomes the only layer, and its children (your named groups) survive as groups. [inferred from code]
2. `clipboard.importSvg {svg | dataBase64, center?}` → `{count}`, then `edit.pasteInPlace` (`crates/engine/src/cmd/clipboard.rs:36-43`). This touches the clipboard and takes two commands, so prefer `file.place`.
3. `document.open {dataBase64, name}` opens an SVG as a new document, with layers kept and artboards from the viewBox or Inkscape pages (`cmd/fileio/commands.rs:7-15`).

---

## 7. Rendering previews and exporting

- **PNG preview (the canvas, no file):** `ui.render {artboard?, scale?, traceViews?, path?, data?}` → `{width,height,pngBase64}`.
  - In the app: `control.rs:380-411`, `app.canvas.renderer.render_region_with(&doc, artboard.rect, scale, &opts)` → `img.to_png()`. Options are those of a PNG export on white, `RasterExportOptions{background: Some([255;3])}.render_options(RasterFormat::Png)`.
  - Headless: `Headless::render` (`crates/mcp/src/headless.rs:267-296`) with `vectorcraft_render::Renderer::new()`.
  - Size guard: `vectorcraft_render::raster_size(rect, scale)`. The MCP `render_preview` computes scale = `max_side / max(w,h)` clamped to 0.01..1 (`dispatch.rs:426-459`).
- **Export (bytes or file):** `document.export {path?, format: svg|svgz|pdf|ai|png|jpg|webp|…, artboard?, artboards?, range?, scale?, ppi?, background?, svg:{…}, …}`. It returns `{path,format,bytes,warnings}`, or `{dataBase64,format,bytes,warnings,files?}` without a path (`cmd/fileio/commands.rs:~37-45`, `export::export`).
  - In the app, prefer `app.export {…, wait:true}`: the app writes the file through the host writer, keeps Background Export semantics, and `wait:true` makes the reply wait for the write (`control.rs:413-441`).
  - Use `document.serialize {format}` for text/bytes without writing, and `document.exportSelection` to export only the selection.
  - PDF: `format:"pdf"` (one page per artboard), with options from `document.exportPdf`.
  - SVG: one file per artboard when there are several, named `{stem}-{n}.svg`.

---

## 8. Blueprint: `crates/studio-bridge`

### 8.1 Where it sits (layering)  [read: `xtask/src/layers.rs:35-66,172-219`]
- Every workspace crate must be registered in `TABLE` (append-only) or `cargo xtask layers` fails ("unknown workspace crate", line ~153). Names are matched without the `vectorcraft-` prefix.
- A dependency must sit on a **lower** layer, or the same layer only with an `INTRA_LAYER_ORDER` chain. UI crates (egui/eframe/winit/craft-ui/rfd) are allowed only at L6 and above.
- **Recommendation:** name the package `vectorcraft-studio-bridge` (short name `studio-bridge`) and register `("studio-bridge", Class::Layer(6))`. Dependencies: `serde_json`, `log`, a sync HTTP lib (e.g. `tiny_http`, no async runtime, matching MCP's style), a CSPRNG (`getrandom`), and optionally `vectorcraft-engine` (L5) for the step resolver and a headless mode.
- **Do not** depend on `vectorcraft-ui-egui` or `vectorcraft-mcp` (both L6). If we want MCP's `Headless`/`Backend`, add `&["mcp", "studio-bridge"]` to `INTRA_LAYER_ORDER`.
- The **app binary `apps/vectorcraft` is `Exempt`**. It does the gluing: bridge → `ControlRequest` → `drain_control`.
- New crates need `[lints] workspace = true` and `#![forbid(unsafe_code)]` (CLAUDE.md "Non-negotiables"). It must not be a dependency of `apps/vectorcraft-web` (wasm): no sockets there.

### 8.2 Integration with the app event loop (mirror `control_server.rs`)
The cleanest design changes nothing in ui-egui:
```rust
// crates/studio-bridge/src/lib.rs (transport + API mapping only)
pub struct Config { pub port: u16, pub token: [u8; 32], pub max_body: usize /* e.g. 32 MiB */ }
pub type Forward = dyn Fn(&str /*method*/, serde_json::Value /*params*/) -> Option<serde_json::Value> + Send + Sync;
pub fn start(cfg: Config, forward: Arc<Forward>) -> std::io::Result<std::thread::JoinHandle<()>>;
```
Then in `apps/vectorcraft/src/main.rs`, refactor `control_server::start` so it can share one `Sender<ControlRequest>`, and pass a `forward` closure that copies `spawn_connection`:
`ControlRequest::new(method, params)` → `ticket` → `tx.send` → `ctx.request_repaint()` → `rrx.recv_timeout(60s)`, giving up with `ticket.give_up()` on timeout (`control_server.rs:141-149`).
`app.with_control(rx)` already exists (`crates/ui-egui/src/app.rs:176`), so both servers can feed one receiver.
On the UI thread this inherits `claim()`, `catch_panic`, automation-root confinement, the 64-per-frame cap and `check_params` validation for free.

The bridge's own threads: one accept thread plus a bounded worker pool (16, as `MAX_CONNECTIONS`). Requests to the app are serialized anyway, because the UI thread runs them in order.

**Headless option** (CI, server-side rendering): the same HTTP front end over an in-process `vectorcraft_engine::Session` (+ `vectorcraft_render::Renderer`) behind a `Mutex`, implementing the same method names as `crates/mcp/src/headless.rs:338-368`, or simply reusing `vectorcraft_mcp::Headless` (needs the intra-layer entry).

### 8.3 Endpoint list → concrete commands
All endpoints are `POST /v1/<name>` with a JSON body, `Authorization: Bearer <token>`, and replies of the form `{ok, result|error}`. Each endpoint becomes a sequence of control methods; the multi-step ones run inside an undo group (below).

| Endpoint | Control methods / engine commands | Notes |
|---|---|---|
| `inspect` `{depth?, ids?, kind?, limit?, offset?, find?: {name?,kind?,text?}}` | `document.inspect {…}`; `document.find {…}` when `find` is given; `document.node {id}` for one node | Return artboards with their indices. |
| `place_svg` `{svg, artboard?: n | new_artboard?: {width,height,name}, mode: "fit"|"exact"|"at", x?, y?, name?}` | (opt.) `artboard.new {…}` → `{index}`; `document.inspect {depth:0}` for the artboard rect; **wrap svg in `<g id="studio-root">`**; `file.place {name:"<name>.svg", dataBase64, rect:[ab.x,ab.y,ab.w,ab.h], crop:"crop"}` for an SVG authored at artboard size (viewBox ↦ artboard), or `file.place {…, at}` then `object.move {ids:[g], dx, dy}` to land the top-left on (x,y); then `document.inspect {ids:[g], depth: all}` to build the map | Returns `{group_id, nodes: {svg_id_or_name: node_id}, warnings}`. One undo step through the undo group. |
| `select` `{ids}` / `{find:{…}}` | `select.set {ids}` / `document.find {…, select:true}` | Selection-only. |
| `set_text` `{id, text}` or `{id, start, end, insert}` | `text.setText {id, text}`; `text.editRange {id,start,end,insert,runs?}` for styled runs | `setText` collapses runs to the first style. |
| `set_fill` `{ids, fill, stroke?, strokeWidth?}` | `paint.setFill {ids, color|none|swatch|gradient}`, `paint.setStroke`, `stroke.set {ids, weight}` | Same mapping as MCP `paint_params` (`handlers.rs:37-46`). For text use `text.setStyle {ids, fill}` when only the characters should change. |
| `transform` `{ids, dx?, dy?, rotate?, scale?, origin?, matrix?}` | `object.move`, `object.rotate`, `object.scale`, or one `object.transform {ids, matrix:[a,b,c,d,e,f]}` | Wrap in the undo group (MCP's `transform` makes one step per op). |
| `batch` `{label, steps:[{cmd, params}], atomic: true}` | `edit.undoGroup.begin {label}` → steps with `steps::resolve` for `"$N.id"` refs → `edit.undoGroup.end {cancel: failed}` | **One undo step per AI turn**, and a full rollback on failure (cancel). Alternative: `command.batch` (atomic but no refs). |
| `render_preview` `{artboard, max_side?}` | `document.inspect {depth:0}` → `ui.render {artboard, scale}` | Returns PNG (base64 or `image/png`). Clamp as `dispatch.rs:426-459` does. |
| `export` `{format: svg|png|pdf, artboard?, path?, scale?}` | `app.export {…, wait:true}` (live) or `document.export` (headless); no path → `dataBase64` | Respect automation roots for paths. |
| `undo` / `redo` | `edit.undo` / `edit.redo` | An undo group left open must be closed first. |
| `commands` | `engine.commands` | For agent self-discovery. |

### 8.4 Token auth design
- At startup, generate 32 random bytes and hex-encode them. Write `{port, token, pid}` to a file only the user can read (`0600`): `$XDG_RUNTIME_DIR/vectorcraft-studio/bridge.json`, or the app config dir (respect `VECTORCRAFT_CONFIG_DIR`). The Python agents read that file. Optionally also accept `STUDIO_BRIDGE_TOKEN` from the environment for CI.
- Bind `127.0.0.1` only. Require `Authorization: Bearer <token>` and compare in constant time. Reject requests carrying an `Origin` header, or a `Host` that isn't `127.0.0.1:<port>` / `localhost:<port>` (blocks browser and DNS-rebinding attacks, which the plain control port only blocks by accident).
- Cap header and body sizes (mirror `MAX_LINE` 4 MiB; SVG bodies may need ~32 MiB). Keep the deadlines (idle 300 s, request 30 s, reply 60 s), a connection cap of 16, and the timeout/give-up semantics.
- Every path parameter goes through the app's automation roots: start with `--automation-write-root` so `export {path}` can't write anywhere.
- Never pass raw `engine.execute` through without an allow-list. If we expose an escape hatch, restrict it to non-file commands (exclude `document.open`, `document.save`, `file.*`, `plugin.*`, `scripts.*`, `action.batch`).

### 8.5 Engine gaps / risks to plan for
1. **Place at (x, y) top-left on artboard N at 1:1.** `file.place` only takes a centre (`at`) or a fit rect. Workaround: place, then `object.move` with the returned `width`/`height` inside the undo group. Cleaner: add `origin: [x,y]` / `artboard: n` params to `file.place` (a new param in `place.rs` + tests), or add a `studio.placeSvg` engine command.
2. **SVG element id → NodeId map.** Nothing returns one. Ids are reallocated on place (`reid`), and only names (from id/labels) and `data-*` attrs survive. The bridge must walk the placed subtree. To make the map deterministic, put `data-name` (or `id`) on every element we care about. Better: an engine `document.find {data: {key, value}}` or a place reply that lists `{elementId → nodeId}`.
3. **Layer flattening on place** (see §6). The bridge must wrap the SVG, or the engine needs a `keepLayers` option for `file.place`.
4. **Text replace loses run styles.** `text.setText` keeps only the first run's style (`typecmd.rs:736-753`). Use `text.editRange` per run (byte offsets from `text.getRange`), or add a "replace text keeping per-paragraph/run styles" command.
5. **Fonts.** Only the first font family is honoured, and absent fonts fall back silently in rendering (a warning on create/setStyle). Fonts come from the system or `CRAFT_FONTS_DIR`; our brand fonts must be installed or provided at run time.
6. **Unsupported SVG filters** are dropped (with a warning). CSS support is partial (there is a CSS module, `import/css.rs`). Expect fidelity diffs for AI-generated SVG with `filter`, `mix-blend-mode` and the like. Surface `warnings` to agents.
7. **The UI thread must tick.** Requests time out after 60 s. A long export or render blocks the UI. Prefer headless for heavy batch work.
8. **Strict params.** An unknown key is an error (`check_params`), so keep the bridge's mappings in sync with the command docs. `engine.commands` gives the param prose. There is no JSON Schema: the "registry currently describes them in prose" (`docs/mcp.md` ~line 375).
9. **Undo-group semantics.** The group applies to the document active at begin. Switching documents mid-batch makes per-edit steps (`cmd/edit.rs:115`). Always `end` the group, even on error.

---

## 9. Adding an AI panel  [read]

1. **Panel module:** create `crates/ui-egui/src/panels/ai.rs` with `pub const ID: &str = "studioAi";`, `pub fn show(app: &mut VectorcraftApp, ui: &mut egui::Ui)` and an optional `pub fn menu(...) -> bool`. Rules: read state from `app.session`, act only through `app.run(id, params)`, and take colours from `theme::Tokens` (CLAUDE.md "The UI is thin"; `panels.rs:1-4`). Wrap user-visible strings in `tl!(…)`; existing panels use the i18n tables in `crates/ui-egui/src/i18n/*.tsv`.
2. **Declare it:** `pub mod ai;` in `crates/ui-egui/src/panels.rs:6-47`. Alternatively, like the built-in add-ons, put it under `crates/ui-egui/src/addons/` (`addons.rs:1-26`, e.g. `addons::cleanup::ID`), which also gives an Object › Plug-ins submenu.
3. **Register it:** add `(panels::ai::ID, "AI Assistant", "sparkles")` to `ICON_PANELS` (`crates/ui-egui/src/state.rs:165-210`; icons are Lucide names or `dc-*` assets). Add its id to a row of `ICON_PANEL_GROUPS` (`state.rs:218-228`) if it should show in the collapsed icon column. `all_panels()` / `rail_panel()` then feed docking automatically (`panel_docking.rs:788, 1294, 1312`).
4. **Dispatch:** add `panels::ai::ID => ai::show(app, ui),` in `show_icon_panel` (`panels.rs:~141-188`), and a `menu` arm in `panel_menu_items` (`panels.rs:~192-230`).
5. **Window menu:** add `panel("AI Assistant", crate::panels::ai::ID)` in `crates/ui-egui/src/menus/tree.rs:~640-660` (alphabetical list).
6. **Talking to Python agents from the panel:** don't make HTTP calls from the panel. Put the request in app state, let a background thread or channel deliver results (the pattern `app.placing` / `app.opening` / `background` use), and apply the AI's edits through the bridge or `app.run`, so every edit stays a command and an undo step.
7. **Tests:** headless egui frame tests, as `panels/transparency.rs` and `panels/tests_*.rs` do (AGENTS.md). If an icon asset is added, it needs an `ASSETS.md` row.

---

## 10. Contribution, licence and trademark constraints

From `AGENTS.md`/`CLAUDE.md` (they are nearly identical; CLAUDE.md adds the lints/forbid-unsafe rule), `NOTICE`, `README.md:375-394` and `docs/brand/LICENSE-brand.txt` [read]:
- **Licence:** code is MIT OR Apache-2.0. We may fork, modify and ship, keeping `LICENSE-MIT`, `LICENSE-APACHE` and `NOTICE` (third-party notices: Lucide ISC, OFL fonts, the Adobe CMap BSD-3 notice from hayro-cmap, the IJG notice, …).
- **Trademark / rebrand (mandatory for a modified distribution):** "If you distribute or publish a modified version of VectorCraft … you **must remove the ArtCraft Marks** … (or replace them with your own) and must not call it ArtCraft." We may say "based on VectorCraft by the ArtCraft team" in plain text. Places that use the marks or the name:
  - `docs/brand/*` (logos);
  - the README header and footer;
  - `crates/ui-egui/src/community.rs` (Discord/getartcraft links, ArtCraft strings);
  - `crates/ui-egui/src/dialogs/about.rs:67` ("Part of ArtCraft…");
  - `crates/ui-egui/src/menus/tree.rs:696-697` (Help › ArtCraft Website);
  - i18n `.tsv` entries;
  - packaging under `packaging/`.

  **[inferred]** The licence restricts the *ArtCraft* marks. "VectorCraft" itself isn't listed as a restricted mark in `LICENSE-brand.txt`, but renaming our product (e.g. "Studio") avoids confusion. Keep the `DrawCraft` legacy paths working if we keep the file formats.
- **`cargo xtask brands`** (`xtask/src/brands.rs`) fails CI on vendor names (adobe, illustrator, photoshop, pantone, …) in Rust string literals, Cargo descriptions, cursor manifests and packaging text. Write "the reference app" instead. This applies to our bridge's tool descriptions and panel strings too.
- **Clean-room:** never use Adobe software, its files, assets or wording; never copy GPL/AGPL code (Inkscape, lib2geom). Behaviour may come only from public docs. Assets must be original, OSI, CC0 or redistributable CC, each with an `ASSETS.md` row (`cargo xtask assets`). Fonts live in `storytold/craft-fonts`, never in the repo.
- **Code rules:**
  - No panics in shipped code (`unwrap`/`expect`/`panic!`/indexing on untrusted data banned by clippy).
  - Network input is untrusted: fuzz/proptest new protocols (see `crates/mcp/tests/protocol_props.rs`).
  - New crates need `[lints] workspace = true` + `#![forbid(unsafe_code)]`.
  - Use `chrono` for time. Rust only (no handwritten JS/TS). Never break wasm.
  - Pass `cargo xtask ci` (fmt, clippy -D warnings, tests, layers, wasm).
  - One task id per commit.
  - "Everything is a command": new behaviour goes in `crates/engine/src/cmd/*` with tests, and the UI calls `app.run`.
- **Process rules for upstream contributions:** update the parity docs (`docs/target-app-parity.md`, `docs/gaps.md`, …) with each landed task; don't add or change CI in feature PRs; follow the contributor-credit rules in `contributors/`. None of these bind a private fork, but follow them if we upstream.
