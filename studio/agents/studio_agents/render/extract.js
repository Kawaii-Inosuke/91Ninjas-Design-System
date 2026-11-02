// Runs inside the rendered design page (Playwright `page.evaluate`).
// Walks everything under `rootSelector` and returns a tree of groups and drawable leaves, in canvas
// coordinates and paint order. layered_svg.py serialises that tree to SVG.
//
// Leaf types: box (background/border as a rect), polygon (clip-path shape), sides (non-uniform borders),
// text (lines of styled runs), svg (inline SVG markup), image.
(rootSelector) => {
  const root = document.querySelector(rootSelector);
  if (!root) throw new Error(`root element ${rootSelector} not found`);
  const warnings = [];
  const warn = (msg) => { if (!warnings.includes(msg)) warnings.push(msg); };

  const INLINE = new Set(['inline', 'contents']);
  const isInline = (el) => INLINE.has(getComputedStyle(el).display);

  // ── ::before / ::after → real elements, so they have boxes we can measure ──────────────────────
  // Layout must not move while we do this; any element that shifts is reported.
  const all = [root, ...root.querySelectorAll('*')];
  const before = new Map(all.map((el) => [el, el.getBoundingClientRect()]));
  const SKIP_PROPS = new Set(['content']);
  const pending = [];
  for (const el of all) {
    if (el.closest('svg')) continue;
    for (const which of ['::before', '::after']) {
      const cs = getComputedStyle(el, which);
      if (cs.content === 'none' || cs.content === 'normal' || cs.display === 'none') continue;
      const span = document.createElement('span');
      for (const prop of cs) if (!SKIP_PROPS.has(prop)) span.style.setProperty(prop, cs.getPropertyValue(prop));
      const m = cs.content.match(/^"((?:[^"\\]|\\.)*)"$/);
      if (m) span.textContent = m[1].replace(/\\(.)/g, '$1');
      else if (cs.content !== '""') warn(`unsupported pseudo-element content: ${cs.content}`);
      span.dataset.pseudo = which.slice(2);
      pending.push([el, which, span]);
    }
  }
  const style = document.createElement('style');
  style.textContent = '[data-pseudo-host]::before, [data-pseudo-host]::after { content: none !important; }';
  document.head.appendChild(style);
  for (const [el, which, span] of pending) {
    el.dataset.pseudoHost = '';
    if (which === '::before') el.prepend(span); else el.append(span);
  }
  for (const el of all) {
    const a = before.get(el), b = el.getBoundingClientRect();
    if (Math.abs(a.left - b.left) > 0.5 || Math.abs(a.top - b.top) > 0.5 ||
        Math.abs(a.width - b.width) > 0.5 || Math.abs(a.height - b.height) > 0.5) {
      warn('layout shifted while converting ::before/::after; check positions');
      break;
    }
  }

  // ── helpers ────────────────────────────────────────────────────────────────────────────────────
  const R = root.getBoundingClientRect();
  const box = (r) => ({ x: r.left - R.left, y: r.top - R.top, w: r.width, h: r.height });
  const px = (v) => parseFloat(v) || 0;

  function color(v) {                       // computed colour → {hex, alpha} or null when invisible
    const m = v && v.match(/rgba?\(([^)]+)\)/);
    if (!m) return null;
    const p = m[1].split(/[ ,/]+/).filter(Boolean).map(Number);
    const a = p.length > 3 ? p[3] : 1;
    if (a === 0) return null;
    const hex = '#' + p.slice(0, 3).map((n) => Math.round(n).toString(16).padStart(2, '0')).join('');
    return { hex, alpha: a };
  }

  function nameOf(el) {
    if (el.dataset && el.dataset.layer) return el.dataset.layer;
    if (el.dataset && el.dataset.pseudo) {
      const host = el.parentElement;
      return `${nameOf(host)}-${el.dataset.pseudo}`;
    }
    if (el.id) return el.id;
    const cls = [...el.classList].find((c) => !c.startsWith('__'));
    if (cls) return cls;
    const tag = el.tagName.toLowerCase();
    if (tag === 'svg') {                                 // big inline SVGs are charts/illustrations, not icons
      const r = el.getBoundingClientRect();
      return Math.max(r.width, r.height) > 160 ? 'graphic' : 'icon';
    }
    return TAG_NAMES[tag] || (el.children.length ? 'content' : 'text');
  }
  const TAG_NAMES = { h1: 'heading', h2: 'heading', h3: 'heading', h4: 'heading', h5: 'heading', h6: 'heading',
                      p: 'paragraph', ul: 'list', ol: 'list', li: 'item', svg: 'icon', img: 'image', table: 'table' };

  function radii(cs, b) {                   // [[rx,ry] tl, tr, br, bl] in px
    const one = (v) => {
      const [h, w] = (v.split(' ').length > 1 ? v.split(' ') : [v, v]);
      const res = (s, ref) => (s.endsWith('%') ? (parseFloat(s) / 100) * ref : px(s));
      return [res(h, b.w), res(w, b.h)];
    };
    return [cs.borderTopLeftRadius, cs.borderTopRightRadius, cs.borderBottomRightRadius,
            cs.borderBottomLeftRadius].map(one);
  }

  function gradient(bg, b) {                // linear-gradient(...) → {x1,y1,x2,y2, stops:[{offset,hex,alpha}]}
    const m = bg.match(/^linear-gradient\((.*)\)$/);
    if (!m) return null;
    const parts = m[1].split(/,(?![^(]*\))/).map((s) => s.trim());
    let angle = 180;
    const dirs = { 'to top': 0, 'to right': 90, 'to bottom': 180, 'to left': 270,
                   'to top right': 45, 'to right top': 45, 'to bottom right': 135, 'to right bottom': 135,
                   'to bottom left': 225, 'to left bottom': 225, 'to top left': 315, 'to left top': 315 };
    if (/deg$/.test(parts[0])) angle = parseFloat(parts.shift());
    else if (parts[0] in dirs) angle = dirs[parts.shift()];
    const stops = parts.map((s, i) => {
      const c = color(s);
      const pos = s.match(/\)\s+([\d.]+)%/);
      return { offset: pos ? parseFloat(pos[1]) / 100 : i / Math.max(1, parts.length - 1),
               hex: c ? c.hex : '#000000', alpha: c ? c.alpha : 0 };
    });
    const a = (angle * Math.PI) / 180, dx = Math.sin(a), dy = -Math.cos(a);
    const half = (Math.abs(b.w * dx) + Math.abs(b.h * dy)) / 2;
    const cx = b.x + b.w / 2, cy = b.y + b.h / 2;
    return { x1: cx - dx * half, y1: cy - dy * half, x2: cx + dx * half, y2: cy + dy * half, stops };
  }

  function clipPolygon(cs, b) {
    if (!cs.clipPath || cs.clipPath === 'none') return null;
    const m = cs.clipPath.match(/^polygon\((.*)\)$/);
    if (!m) { warn(`clip-path not supported: ${cs.clipPath}`); return null; }
    return m[1].split(',').map((pair) => {
      const [sx, sy] = pair.trim().split(/\s+/);
      const v = (s, ref) => (s.endsWith('%') ? (parseFloat(s) / 100) * ref : px(s));
      return [b.x + v(sx, b.w), b.y + v(sy, b.h)];
    });
  }

  // ── own visuals of one element (background, border, clip shape) ────────────────────────────────
  function visuals(el, cs, b) {
    const out = [];
    if (cs.visibility !== 'visible' || b.w === 0 || b.h === 0) return out;
    if (cs.boxShadow !== 'none') warn('box-shadow is not converted (no shadows in the editor import)');
    if (cs.filter !== 'none') warn('CSS filter is not converted');
    if (cs.transform !== 'none') warn('CSS transform is not converted (bounds only)');
    const fill = color(cs.backgroundColor);
    const grad = cs.backgroundImage !== 'none' ? gradient(cs.backgroundImage, b) : null;
    if (cs.backgroundImage !== 'none' && !grad) warn(`background-image not supported: ${cs.backgroundImage.slice(0, 40)}`);

    const poly = clipPolygon(cs, b);
    if (poly) {
      if (fill || grad) out.push({ type: 'polygon', points: poly, fill, gradient: grad });
      return out;
    }
    const sides = ['Top', 'Right', 'Bottom', 'Left'].map((s) => ({
      width: px(cs[`border${s}Width`]), style: cs[`border${s}Style`], color: color(cs[`border${s}Color`]),
    })).map((s) => (s.style === 'none' || s.style === 'hidden' || !s.color || s.width === 0 ? null : s));
    const uniform = sides.every((s) => s && sides[0] && s.width === sides[0].width &&
      s.style === sides[0].style && s.color.hex === sides[0].color.hex && s.color.alpha === sides[0].color.alpha);
    const r = radii(cs, b);
    if (fill || grad || uniform) {
      out.push({ type: 'box', ...b, radii: r, fill, gradient: grad,
                 stroke: uniform ? { width: sides[0].width, style: sides[0].style, color: sides[0].color } : null });
    }
    if (!uniform && sides.some(Boolean)) {
      if (r.some(([a, c]) => a > 0 || c > 0)) warn('rounded corners with non-uniform borders are drawn square');
      out.push({ type: 'sides', ...b, sides });
    }
    return out;
  }

  // ── text: every text node whose nearest non-inline ancestor is `owner` ─────────────────────────
  const ctx2d = document.createElement('canvas').getContext('2d');
  const ascents = new Map();
  function ascent(cs) {
    const font = `${cs.fontStyle} ${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`;
    if (!ascents.has(font)) { ctx2d.font = font; ascents.set(font, ctx2d.measureText('Hg').fontBoundingBoxAscent); }
    return ascents.get(font);
  }
  function ownerOf(node) {
    let el = node.parentElement;
    while (el && el !== root && isInline(el)) el = el.parentElement;
    return el;
  }
  function runStyle(cs) {
    return {
      family: cs.fontFamily.split(',')[0].trim().replace(/^["']|["']$/g, ''),
      size: px(cs.fontSize), weight: cs.fontWeight, italic: cs.fontStyle !== 'normal',
      color: color(cs.color), letterSpacing: cs.letterSpacing === 'normal' ? 0 : px(cs.letterSpacing),
    };
  }
  function transformText(s, tt) {
    if (tt === 'uppercase') return s.toUpperCase();
    if (tt === 'lowercase') return s.toLowerCase();
    if (tt === 'capitalize') return s.replace(/(^|\s)(\S)/g, (_, a, c) => a + c.toUpperCase());
    return s;
  }
  function textLeaf(owner) {
    const walker = document.createTreeWalker(owner, NodeFilter.SHOW_TEXT);
    const range = document.createRange();
    const lines = [];
    for (let t = walker.nextNode(); t; t = walker.nextNode()) {
      if (!t.data.trim() && !t.data.includes(' ')) continue;
      if (ownerOf(t) !== owner || t.parentElement.closest('svg')) continue;
      const cs = getComputedStyle(t.parentElement);
      if (cs.visibility !== 'visible') continue;
      const st = runStyle(cs), asc = ascent(cs);
      for (let i = 0; i < t.data.length; i++) {
        range.setStart(t, i); range.setEnd(t, i + 1);
        const rects = range.getClientRects();
        if (!rects.length) continue;
        const rc = rects[0];
        let ch = t.data[i];
        if (/\s/.test(ch)) { if (rc.width === 0) continue; ch = ' '; }
        const baseline = rc.top - R.top + asc;
        let line = lines[lines.length - 1];
        if (!line || Math.abs(line.y - baseline) > Math.max(2, st.size * 0.3)) {
          line = { y: baseline, left: rc.left - R.left, right: rc.right - R.left, runs: [] };
          lines.push(line);
        }
        let run = line.runs[line.runs.length - 1];
        if (!run || run.node !== t) {
          run = { node: t, text: '', style: st, tt: cs.textTransform };
          line.runs.push(run);
        }
        if (ch === ' ' && run.text.endsWith(' ')) continue;
        run.text += ch;
        if (ch !== ' ') line.right = rc.right - R.left;
      }
    }
    for (const line of lines) {
      for (const run of line.runs) { run.text = transformText(run.text, run.tt); delete run.node; delete run.tt; }
      line.runs[0].text = line.runs[0].text.replace(/^ +/, '');
      const last = line.runs[line.runs.length - 1];
      last.text = last.text.replace(/ +$/, '');
      line.runs = line.runs.filter((r) => r.text.length);
    }
    const kept = lines.filter((l) => l.runs.length);
    if (!kept.length) return null;
    const ta = getComputedStyle(owner).textAlign;
    const anchor = ta === 'center' ? 'middle' : (ta === 'right' || ta === 'end') ? 'end' : 'start';
    for (const l of kept) l.x = anchor === 'middle' ? (l.left + l.right) / 2 : anchor === 'end' ? l.right : l.left;
    return { type: 'text', anchor, lines: kept.map(({ x, y, runs }) => ({ x, y, runs })) };
  }

  // ── inline SVG: copy markup with computed paint inlined ────────────────────────────────────────
  const PAINT = ['fill', 'fill-opacity', 'fill-rule', 'stroke', 'stroke-width', 'stroke-opacity',
                 'stroke-linecap', 'stroke-linejoin', 'stroke-dasharray', 'opacity'];
  const PAINT_DEFAULTS = { 'fill-opacity': '1', 'stroke-opacity': '1', opacity: '1', 'fill-rule': 'nonzero',
                           'stroke-linecap': 'butt', 'stroke-linejoin': 'miter', 'stroke-dasharray': 'none' };
  function svgLeaf(el, b) {
    const vb = el.viewBox && el.viewBox.baseVal && el.viewBox.baseVal.width
      ? [el.viewBox.baseVal.x, el.viewBox.baseVal.y, el.viewBox.baseVal.width, el.viewBox.baseVal.height] : null;
    const scale = vb ? Math.min(b.w / vb[2], b.h / vb[3]) : 1;
    const clone = el.cloneNode(true);
    const src = [...el.querySelectorAll('*')], dst = [...clone.querySelectorAll('*')];
    src.forEach((s, i) => {
      const d = dst[i], cs = getComputedStyle(s);
      for (const p of PAINT) {
        let v = cs.getPropertyValue(p);
        if (!v || PAINT_DEFAULTS[p] === v) continue;
        if (p === 'stroke-width') v = String(px(v));
        const c = color(v);
        if (c && (p === 'fill' || p === 'stroke')) {
          v = c.hex;
          if (c.alpha < 1) d.setAttribute(`${p}-opacity`, String(c.alpha));
        } else if (p === 'fill' || p === 'stroke') {
          if (v !== 'none') continue;
        }
        if (p === 'stroke-width' && cs.getPropertyValue('vector-effect') === 'non-scaling-stroke') v = String(px(v) / scale);
        d.setAttribute(p, v);
      }
      if (s instanceof SVGTextContentElement) {          // CSS-styled chart labels: font comes from CSS
        d.setAttribute('font-family', cs.fontFamily.split(',')[0].trim().replace(/^["']|["']$/g, ''));
        d.setAttribute('font-size', String(px(cs.fontSize)));
        d.setAttribute('font-weight', cs.fontWeight);
        if (cs.fontStyle !== 'normal') d.setAttribute('font-style', cs.fontStyle);
        if (cs.textAnchor !== 'start') d.setAttribute('text-anchor', cs.textAnchor);
        if (cs.dominantBaseline !== 'auto') d.setAttribute('dominant-baseline', cs.dominantBaseline);
        if (cs.letterSpacing !== 'normal') d.setAttribute('letter-spacing', String(px(cs.letterSpacing)));
      }
      d.removeAttribute('class'); d.removeAttribute('style'); d.removeAttribute('vector-effect');
      for (const a of [...d.attributes]) if (/^on/i.test(a.name)) d.removeAttribute(a.name);
    });
    clone.querySelectorAll('script, style, foreignObject').forEach((n) => n.remove());
    return { type: 'svg', ...b, viewBox: vb, markup: clone.innerHTML };
  }

  // ── tree walk ──────────────────────────────────────────────────────────────────────────────────
  function blockChildren(el) {              // non-inline descendants reached through inline wrappers
    const out = [];
    for (const c of el.children) {
      if (c.tagName === 'STYLE' || c.tagName === 'SCRIPT') continue;
      const cs = getComputedStyle(c);
      if (cs.display === 'none') continue;
      if (INLINE.has(cs.display) && c.tagName !== 'IMG' && c.tagName.toLowerCase() !== 'svg') {
        if (color(cs.backgroundColor) || cs.borderTopStyle !== 'none') warn('backgrounds/borders on inline text spans are not converted');
        out.push(...blockChildren(c));
      } else out.push(c);
    }
    return out;
  }
  function paintOrder(children) {           // simplified CSS stacking: z<0, in-flow, positioned, z>0
    const key = (c) => {
      const cs = getComputedStyle(c), z = cs.zIndex === 'auto' ? 0 : parseInt(cs.zIndex, 10);
      const positioned = cs.position !== 'static';
      return z < 0 ? [0, z] : !positioned ? [1, 0] : z === 0 ? [2, 0] : [3, z];
    };
    return children.map((c, i) => ({ c, k: key(c), i }))
      .sort((a, b) => a.k[0] - b.k[0] || a.k[1] - b.k[1] || a.i - b.i).map((o) => o.c);
  }

  function walk(el) {
    const cs = getComputedStyle(el);
    if (cs.display === 'none') return null;
    const b = box(el.getBoundingClientRect());
    const node = { type: 'group', name: nameOf(el), explicit: !!(el.dataset.layer || el.id),
                   opacity: px(cs.opacity) < 1 ? px(cs.opacity) : 1, children: [] };
    const tag = el.tagName.toLowerCase();
    if (tag === 'svg') { node.children.push(svgLeaf(el, b)); return node; }
    if (tag === 'img') {
      if (cs.visibility === 'visible') node.children.push({ type: 'image', ...b, src: el.currentSrc || el.src, fit: cs.objectFit });
      return node;
    }
    node.children.push(...visuals(el, cs, b));
    const text = textLeaf(el);
    if (text) node.children.push(text);
    for (const c of paintOrder(blockChildren(el))) {
      const child = walk(c);
      if (child) node.children.push(child);
    }
    return node;
  }

  const fontFaces = [];
  for (const sheet of document.styleSheets) {
    try { for (const rule of sheet.cssRules) if (rule instanceof CSSFontFaceRule) fontFaces.push(rule.cssText); }
    catch (e) { /* cross-origin sheet */ }
  }
  return { width: R.width, height: R.height, tree: walk(root), warnings, fontFaces };
}
