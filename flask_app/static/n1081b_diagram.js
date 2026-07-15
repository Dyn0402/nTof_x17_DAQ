/* ============================================================================
 * n1081b_diagram.js — self-contained renderer for the N1081B trigger diagram.
 *
 * window.N1081B.render(state, rootEl) draws one scrolling "page" per module
 * (styled like the real CAEN-red N1081B front panel), an overview page, and an
 * active-scan banner. `state` is the payload from /n1081b/state (design model
 * merged with the newest live board read-back) — see n1081b_module_map.py.
 *
 * Used two ways, one source of truth:
 *   - DAQ GUI "Trigger" tab: loads this file, fetches /n1081b/state, calls render.
 *   - Standalone HTML export: this file is inlined with a `const STATE = {...}`.
 * The renderer injects its own <style> once, so it needs no external CSS.
 * ==========================================================================*/
(function () {
  "use strict";

  // ------------------------------------------------------------------ styles
  const CSS = `
  .n81-root { --n81-red:#c02434; --n81-red2:#9c1b29; --n81-panel:#151a21;
      --n81-panel2:#1b222b; --n81-border:#2a3542; --n81-text:#e2e8f0;
      --n81-muted:#8b96a5; --n81-bg:#0e1116; --n81-on:#3ddc84; --n81-off:#5a6472;
      --n81-warn:#f0c040; --n81-bad:#ef5b5b; --n81-scan:#f2913d;
      color:var(--n81-text); font-size:0.9rem; }
  .n81-root * { box-sizing:border-box; }

  /* quick-jump nav */
  .n81-jump { position:sticky; top:0; z-index:20; display:flex; gap:.4rem;
      flex-wrap:wrap; align-items:center; padding:.5rem .2rem; margin-bottom:.4rem;
      background:linear-gradient(var(--n81-bg),var(--n81-bg) 78%,transparent); }
  .n81-jump a { text-decoration:none; color:var(--n81-muted); border:1px solid var(--n81-border);
      border-radius:999px; padding:.2rem .7rem; font-size:.78rem; white-space:nowrap;
      transition:.15s; }
  .n81-jump a:hover { color:var(--n81-text); border-color:var(--n81-red); }
  .n81-jump a .dot { display:inline-block; width:8px; height:8px; border-radius:50%;
      margin-right:.35rem; vertical-align:middle; }
  .n81-jump .n81-src { margin-left:auto; color:var(--n81-muted); font-size:.75rem;
      font-family:var(--n81-mono,ui-monospace,monospace); }

  /* active-scan banner */
  .n81-banner { border:1px solid var(--n81-border); border-left:5px solid var(--n81-scan);
      background:rgba(242,145,61,.09); border-radius:10px; padding:.6rem 1rem;
      margin-bottom:.8rem; display:flex; gap:1rem; align-items:baseline; flex-wrap:wrap; }
  .n81-banner.idle { border-left-color:var(--n81-off); background:var(--n81-panel); }
  .n81-banner .tag { font-weight:700; font-size:1.15rem; color:var(--n81-scan); }
  .n81-banner.idle .tag { color:var(--n81-muted); font-size:1rem; }
  .n81-banner .note { color:var(--n81-muted); font-size:.85rem; }
  .n81-banner .age { margin-left:auto; color:var(--n81-muted); font-size:.75rem; }

  /* module page */
  .n81-mod { scroll-margin-top:52px; margin-bottom:1.6rem; border-radius:12px;
      overflow:hidden; border:1px solid var(--n81-border); background:var(--n81-panel); }
  .n81-mod-head { display:flex; align-items:center; gap:.9rem; flex-wrap:wrap;
      padding:.7rem 1rem; background:linear-gradient(180deg,var(--n81-red),var(--n81-red2));
      color:#fff; border-bottom:3px solid #000; }
  .n81-mod-num { font-weight:800; font-size:1.5rem; letter-spacing:.03em;
      background:rgba(0,0,0,.28); border-radius:8px; padding:.1rem .7rem; }
  .n81-mod-titles { display:flex; flex-direction:column; }
  .n81-mod-role { font-weight:700; font-size:1.05rem; line-height:1.15; }
  .n81-mod-role small { font-weight:400; opacity:.85; }
  .n81-mod-ip { font-family:var(--n81-mono,ui-monospace,monospace); font-size:.78rem;
      opacity:.9; }
  .n81-mod-badges { margin-left:auto; display:flex; gap:.4rem; align-items:center;
      flex-wrap:wrap; }
  .n81-badge { font-size:.72rem; font-weight:600; padding:.18rem .55rem; border-radius:999px;
      background:rgba(0,0,0,.25); color:#fff; white-space:nowrap; letter-spacing:.02em; }
  .n81-badge.on  { background:rgba(61,220,132,.9); color:#08210f; }
  .n81-badge.offl{ background:rgba(0,0,0,.4); color:#ffd9d9; }
  .n81-badge.des { background:rgba(255,255,255,.18); }

  .n81-mod-note { padding:.5rem 1rem; color:var(--n81-muted); font-size:.8rem;
      border-bottom:1px solid var(--n81-border); background:var(--n81-panel2); }
  .n81-mod-note b { color:var(--n81-warn); }

  /* section row */
  .n81-sec { display:grid; grid-template-columns:150px 1fr; gap:0;
      border-bottom:1px solid var(--n81-border); }
  .n81-sec:last-child { border-bottom:none; }
  .n81-sec-tab { background:var(--n81-red2); color:#fff; padding:.7rem .8rem;
      display:flex; flex-direction:column; gap:.25rem; border-right:3px solid #000; }
  .n81-sec-letter { font-size:1.6rem; font-weight:800; line-height:1; }
  .n81-sec-role { font-size:.75rem; opacity:.95; line-height:1.2; }
  .n81-sec-fn { align-self:flex-start; margin-top:.2rem; font-size:.7rem; font-weight:700;
      background:#000; color:#fff; padding:.12rem .45rem; border-radius:5px; letter-spacing:.03em; }
  .n81-sec-fn.mismatch { background:var(--n81-bad); }

  .n81-sec-body { padding:.7rem .8rem; }
  .n81-flow { display:flex; align-items:stretch; gap:.55rem; flex-wrap:wrap; }
  .n81-flow-col { display:flex; flex-direction:column; gap:.35rem; min-width:0; }
  .n81-flow-col.grow { flex:1 1 200px; }
  .n81-col-label { font-size:.63rem; text-transform:uppercase; letter-spacing:.09em;
      color:var(--n81-muted); font-weight:700; }
  .n81-arrow { align-self:center; color:var(--n81-off); font-size:1.1rem; padding:0 .1rem; }

  /* connectors (LEMO) */
  .n81-lemos { display:flex; gap:.4rem; flex-wrap:wrap; padding:.35rem .45rem;
      background:#0a0d12; border:1px solid #000; border-radius:8px; }
  .n81-lemo { position:relative; width:34px; text-align:center; cursor:default; }
  .n81-lemo .ring { width:26px; height:26px; margin:0 auto; border-radius:50%;
      background:radial-gradient(circle at 40% 35%, #3a4350, #0c0f14 70%);
      border:2px solid var(--n81-off); display:flex; align-items:center; justify-content:center; }
  .n81-lemo .ring::after { content:""; width:9px; height:9px; border-radius:50%;
      background:#222b36; }
  .n81-lemo .num { font-size:.62rem; color:var(--n81-muted); margin-top:1px;
      font-family:var(--n81-mono,ui-monospace,monospace); }
  .n81-lemo.on    .ring { border-color:var(--role); box-shadow:0 0 7px -1px var(--role); }
  .n81-lemo.on    .ring::after { background:var(--role); box-shadow:0 0 5px var(--role); }
  .n81-lemo.on    .num { color:var(--n81-text); }
  .n81-lemo.design .ring { border-color:var(--role); opacity:.75; border-style:dashed; }
  .n81-lemo.offmm .ring { border-color:var(--n81-bad); }
  .n81-lemo.offmm .ring::after { background:var(--n81-bad); }
  .n81-lemo.veto  .ring { border-color:var(--n81-bad); }
  .n81-lemo.scan  .ring { border-color:var(--n81-scan) !important;
      animation:n81pulse 1.3s ease-in-out infinite; }
  .n81-lemo.scan  .num { color:var(--n81-scan); }
  .n81-lemo .inv { position:absolute; top:-3px; right:2px; font-size:.7rem;
      color:var(--n81-warn); font-weight:800; }
  @keyframes n81pulse { 0%,100%{ box-shadow:0 0 0 0 var(--n81-scan);} 50%{ box-shadow:0 0 0 5px transparent;} }

  /* core (function + params) */
  .n81-core { flex:1 1 220px; background:var(--n81-panel2); border:1px solid var(--n81-border);
      border-radius:8px; padding:.5rem .65rem; display:flex; flex-direction:column; gap:.3rem; }
  .n81-core .summary { font-weight:600; }
  .n81-core .physics { color:var(--n81-muted); font-size:.8rem; }
  .n81-chips { display:flex; gap:.35rem; flex-wrap:wrap; }
  .n81-chip { font-size:.7rem; padding:.13rem .45rem; border-radius:5px;
      background:#0c1016; border:1px solid var(--n81-border); color:var(--n81-muted);
      font-family:var(--n81-mono,ui-monospace,monospace); }
  .n81-chip b { color:var(--n81-text); font-weight:600; }
  .n81-core .cnote { color:var(--n81-warn); font-size:.72rem; }

  /* from/to connector chips */
  .n81-conns { display:flex; flex-direction:column; gap:.3rem; justify-content:center;
      min-width:110px; }
  .n81-conn { font-size:.72rem; color:var(--n81-muted); text-decoration:none;
      border:1px solid var(--n81-border); border-radius:6px; padding:.2rem .45rem;
      display:block; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  a.n81-conn:hover { color:var(--n81-text); border-color:var(--n81-red); }
  .n81-conn .mm { color:var(--role); font-weight:700; }

  /* per-channel table */
  .n81-tbl { width:100%; border-collapse:collapse; margin-top:.55rem; font-size:.76rem;
      font-family:var(--n81-mono,ui-monospace,monospace); }
  .n81-tbl th { text-align:left; color:var(--n81-muted); font-weight:600;
      border-bottom:1px solid var(--n81-border); padding:.2rem .4rem; font-size:.68rem;
      text-transform:uppercase; letter-spacing:.05em; }
  .n81-tbl td { padding:.2rem .4rem; border-bottom:1px solid #1a212a; vertical-align:top; }
  .n81-tbl tr:last-child td { border-bottom:none; }
  .n81-tbl .st-on  { color:var(--n81-on); }
  .n81-tbl .st-off { color:var(--n81-off); }
  .n81-tbl .st-mm  { color:var(--n81-bad); font-weight:700; }
  .n81-tbl .st-scan{ color:var(--n81-scan); font-weight:700; }
  .n81-tbl .io-io  { color:var(--role); font-weight:700; }
  .n81-tbl .where  { color:var(--n81-text); }
  .n81-tbl .params { color:var(--n81-muted); }
  .n81-sec-toggle { margin-top:.5rem; font-size:.72rem; color:var(--n81-muted);
      background:none; border:1px dashed var(--n81-border); border-radius:6px;
      padding:.15rem .5rem; cursor:pointer; }
  .n81-sec-toggle:hover { color:var(--n81-text); border-color:var(--n81-red); }

  /* overview */
  .n81-ov { border:1px solid var(--n81-border); border-radius:12px; background:var(--n81-panel);
      padding:1rem 1.1rem; margin-bottom:1.2rem; }
  .n81-ov h3 { margin:0 0 .3rem; font-size:1.1rem; }
  .n81-ov .sub { color:var(--n81-muted); font-size:.82rem; margin-bottom:.8rem; }
  .n81-chain { display:flex; gap:.4rem; align-items:stretch; flex-wrap:wrap; margin:.4rem 0 1rem; }
  .n81-chain .step { background:var(--n81-panel2); border:1px solid var(--n81-border);
      border-radius:8px; padding:.45rem .6rem; max-width:230px; }
  .n81-chain .step .t { font-weight:600; font-size:.85rem; }
  .n81-chain .step .d { color:var(--n81-muted); font-size:.72rem; }
  .n81-chain .sep { align-self:center; color:var(--n81-off); font-size:1.3rem; }
  .n81-flowmap { font-family:var(--n81-mono,ui-monospace,monospace); font-size:.72rem;
      white-space:pre; overflow-x:auto; background:#0a0d12; border:1px solid var(--n81-border);
      border-radius:8px; padding:.7rem .8rem; color:var(--n81-muted); line-height:1.5; }
  .n81-legend { display:flex; gap:1rem; flex-wrap:wrap; margin-top:.7rem; font-size:.75rem;
      color:var(--n81-muted); }
  .n81-legend span { display:inline-flex; align-items:center; gap:.35rem; }
  .n81-legend i { width:12px; height:12px; border-radius:50%; display:inline-block;
      border:2px solid; }

  .n81-foot { color:var(--n81-muted); font-size:.72rem; text-align:center;
      padding:.6rem; font-family:var(--n81-mono,ui-monospace,monospace); }
  @media (max-width:720px){ .n81-sec{ grid-template-columns:1fr; } .n81-sec-tab{ flex-direction:row; align-items:center; gap:.6rem; } }
  `;

  function ensureStyles() {
    if (!document.getElementById("n81-styles")) {
      const s = document.createElement("style");
      s.id = "n81-styles";
      s.textContent = CSS;
      document.head.appendChild(s);
    }
  }

  // ------------------------------------------------------------------ helpers
  function el(tag, cls, html) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (html != null) e.innerHTML = html;
    return e;
  }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, c =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  }
  function roleColor(state, role) {
    return (state.role_colors && state.role_colors[role]) || "#39c5b2";
  }

  // classify one input/output channel -> visual state
  function ioState(io) {
    const live = io.live;
    if (live) {
      if (live.on) return io.veto ? "veto on" : "on";
      return io.on ? "offmm" : "off"; // live-off but design wanted it => mismatch
    }
    return io.on ? "design" : "off";
  }
  function isVeto(io) { return /veto/i.test(io.note || ""); }

  // ------------------------------------------------------------------ connector
  function lemo(io, role) {
    const st = ioState(io);
    const cls = "n81-lemo " + st + (io.scan ? " scan" : "");
    const w = el("div", cls);
    w.style.setProperty("--role", role);
    let inv = "";
    const invert = io.live ? io.live.invert : io.invert;
    if (invert) inv = '<span class="inv" title="inverted">⃠</span>';
    w.innerHTML = `<div class="ring"></div><div class="num">${io.lemo}</div>${inv}`;
    w.title = lemoTip(io);
    return w;
  }
  function lemoTip(io) {
    const bits = [`ch${io.lemo}`];
    if (io.src) bits.push("from " + io.src);
    if (io.dst) bits.push("to " + io.dst);
    if (io.live) {
      bits.push(io.live.on ? "LIVE: ON" : "LIVE: OFF");
      if (io.live.threshold != null) bits.push("thr " + io.live.threshold + " mV");
      if (io.live.mono != null) bits.push("mono " + io.live.mono + " ns");
      if (io.live.gd) bits.push("G&D gate " + io.live.gate + " / delay " + io.live.delay + " ns");
      if (io.live.invert) bits.push("inverted");
    } else if (io.on) {
      bits.push("design-only (no live read-back)");
    }
    if (io.note) bits.push(io.note);
    if (io.scan) bits.push("← driven by scan " + io.scan.target);
    return bits.join("  •  ");
  }

  // ------------------------------------------------------------------ section
  function sectionEl(mod, sec, role) {
    const wrap = el("div", "n81-sec");

    const tab = el("div", "n81-sec-tab");
    const fnMismatch = sec.fn_live && sec.fn_design &&
      !fnMatches(sec.fn_design, sec.fn_live);
    tab.innerHTML =
      `<span class="n81-sec-letter">${sec.id}</span>` +
      `<span class="n81-sec-role">${esc(sec.role)}</span>` +
      `<span class="n81-sec-fn${fnMismatch ? " mismatch" : ""}" title="${
        fnMismatch ? "live function '" + esc(sec.fn_live) + "' ≠ design" : "designed function"
      }">${esc(sec.fn_design)}${sec.fn_live && fnMismatch ? " ⚠" : ""}</span>`;
    wrap.appendChild(tab);

    const body = el("div", "n81-sec-body");
    const flow = el("div", "n81-flow");

    // from chips
    if (sec.from && sec.from.length) flow.appendChild(connCol(sec.from, "◄", role));

    // inputs
    const inCol = el("div", "n81-flow-col");
    inCol.appendChild(el("div", "n81-col-label", "Inputs"));
    const inLemos = el("div", "n81-lemos");
    sec.inputs.forEach(io => { io.veto = isVeto(io); inLemos.appendChild(lemo(io, role)); });
    inCol.appendChild(inLemos);
    flow.appendChild(inCol);

    flow.appendChild(el("div", "n81-arrow", "▶"));

    // core
    const core = el("div", "n81-core");
    const chips = [];
    const thr = sec.live_threshold;
    if (thr != null) chips.push(`<span class="n81-chip">thr <b>${thr > 0 ? "+" : ""}${thr} mV</b></span>`);
    if (sec.live_standard) chips.push(`<span class="n81-chip">in <b>${esc(sec.live_standard)}</b></span>`);
    if (sec.live_impedance) chips.push(`<span class="n81-chip"><b>${esc(sec.live_impedance)}</b></span>`);
    if (sec.fn_live) chips.push(`<span class="n81-chip">fn <b>${esc(sec.fn_live)}</b></span>`);
    core.innerHTML =
      `<div class="summary">${esc(sec.summary)}</div>` +
      `<div class="physics">${esc(sec.physics)}</div>` +
      (chips.length ? `<div class="n81-chips">${chips.join("")}</div>` : "") +
      (sec.note ? `<div class="cnote">${esc(sec.note)}</div>` : "");
    flow.appendChild(core);

    flow.appendChild(el("div", "n81-arrow", "▶"));

    // outputs
    const outCol = el("div", "n81-flow-col");
    outCol.appendChild(el("div", "n81-col-label", "Outputs"));
    const outLemos = el("div", "n81-lemos");
    sec.outputs.forEach(io => outLemos.appendChild(lemo(io, role)));
    outCol.appendChild(outLemos);
    flow.appendChild(outCol);

    // to chips
    if (sec.to && sec.to.length) flow.appendChild(connCol(sec.to, "►", role));

    body.appendChild(flow);

    // per-channel table (used channels only)
    const tbl = channelTable(sec, role);
    body.appendChild(tbl);

    wrap.appendChild(body);
    return wrap;
  }

  function connCol(items, arrow, role) {
    const c = el("div", "n81-conns");
    items.forEach(it => {
      const label = (arrow === "◄" ? arrow + " " : "") +
        (it.module ? "M" + it.module + (it.section ? "." + it.section : "") + " " : "") +
        (it.label || "") + (arrow === "►" ? " " + arrow : "");
      let node;
      if (it.module) {
        node = el("a", "n81-conn");
        node.href = "#n81-mod-" + it.module;
        node.innerHTML = `<span class="mm">M${it.module}${it.section ? "." + it.section : ""}</span> ${esc(it.label || "")}`;
        node.style.setProperty("--role", role);
      } else {
        node = el("div", "n81-conn", esc(label));
      }
      node.title = label;
      c.appendChild(node);
    });
    return c;
  }

  function channelTable(sec, role) {
    const rows = [];
    const add = (io, kind) => {
      const used = io.on || (io.live && io.live.on) || io.scan;
      if (!used) return;
      const st = io.live
        ? (io.live.on ? (io.scan ? "st-scan" : "st-on") : (io.on ? "st-mm" : "st-off"))
        : "st-off";
      const stTxt = io.live ? (io.live.on ? "ON" : "OFF") : "design";
      const where = kind === "in" ? (io.src || "") : (io.dst || "");
      const p = [];
      if (kind === "in") {
        if (io.live && io.live.threshold != null) p.push((io.live.threshold > 0 ? "+" : "") + io.live.threshold + "mV");
        else if (io.threshold != null) p.push((io.threshold > 0 ? "+" : "") + io.threshold + "mV*");
        const gd = io.live ? io.live.gd : io.gd;
        const gate = io.live ? io.live.gate : io.gate;
        const delay = io.live ? io.live.delay : io.delay;
        if (gd) p.push("G&D " + gate + "/" + delay + "ns");
        else if (delay) p.push("dly " + delay + "ns");
      } else {
        const mono = io.live ? io.live.mono : io.mono;
        if (mono != null) p.push("mono " + mono + "ns");
      }
      const inv = io.live ? io.live.invert : io.invert;
      if (inv) p.push("inv");
      if (io.scan) p.push("scan:" + io.scan.target);
      rows.push(
        `<tr><td class="io-io">${kind}${io.lemo}</td>` +
        `<td class="${st}">${stTxt}</td>` +
        `<td class="where">${esc(where)}</td>` +
        `<td class="params">${esc(p.join(" · "))}${io.note && kind === "in" && io.veto ? " — " + esc(io.note) : ""}</td></tr>`
      );
    };
    sec.inputs.forEach(io => add(io, "in"));
    sec.outputs.forEach(io => add(io, "out"));
    if (!rows.length) return el("div");
    const t = el("table", "n81-tbl");
    t.style.setProperty("--role", role);
    t.innerHTML = `<thead><tr><th>I/O</th><th>State</th><th>Where</th><th>Params</th></tr></thead>` +
      `<tbody>${rows.join("")}</tbody>`;
    return t;
  }

  function fnMatches(design, live) {
    if (!design || !live) return true;
    const d = design.toLowerCase().replace(/[^a-z]/g, "");
    const l = live.toLowerCase().replace(/[^a-z]/g, "");
    if (d === l) return true;
    // known aliases: or_veto reports as "or"; coincidence-gate may report "coincidence"/"logic"
    const alias = { orveto: ["or"], coincidencegate: ["coincidence", "coinc", "logic", "majority"],
      pulsegenerator: ["pulse", "pulser", "pulsegen"], counter: ["scaler"], scaler: ["counter"] };
    return (alias[d] || []).some(a => l.indexOf(a) >= 0) ||
           (alias[l] || []).some(a => d.indexOf(a) >= 0);
  }

  // ------------------------------------------------------------------ module page
  function moduleEl(mod, state) {
    const role = roleColor(state, mod.color);
    const m = el("div", "n81-mod");
    m.id = "n81-mod-" + mod.n;

    const head = el("div", "n81-mod-head");
    let statusBadge;
    if (!mod.online_expected) statusBadge = `<span class="n81-badge offl">OFFLINE (by design)</span>`;
    else if (mod.has_live && mod.online) statusBadge = `<span class="n81-badge on">LIVE</span>`;
    else if (mod.has_live && !mod.online) statusBadge = `<span class="n81-badge offl">NO READ-BACK</span>`;
    else statusBadge = `<span class="n81-badge des">DESIGN ONLY</span>`;
    const fwsn = [];
    if (mod.fw) fwsn.push("fw " + mod.fw);
    if (mod.sn) fwsn.push("sn " + mod.sn);
    head.innerHTML =
      `<span class="n81-mod-num">M${mod.n}</span>` +
      `<div class="n81-mod-titles">` +
        `<span class="n81-mod-role">${esc(mod.role)} <small>— ${esc(mod.role_long || "")}</small></span>` +
        `<span class="n81-mod-ip">${esc(mod.ip)}${fwsn.length ? "  ·  " + esc(fwsn.join("  ")) : ""}</span>` +
      `</div>` +
      `<div class="n81-mod-badges">${statusBadge}</div>`;
    m.appendChild(head);

    if (mod.note) m.appendChild(el("div", "n81-mod-note", mod.note.replace(/OFFLINE|not|NOT|non-functional|broken/g, s => "<b>" + s + "</b>")));

    mod.sections.forEach(sec => m.appendChild(sectionEl(mod, sec, role)));
    return m;
  }

  // ------------------------------------------------------------------ overview
  function overviewEl(state) {
    const ov = el("div", "n81-ov");
    ov.id = "n81-overview";
    const flowmap =
`SiPM walls ─428F─► M1 OR ─wall1..4─┐
                                    ├─► M3 AND ─sector1..4─► M4 ──► DREAM DAQ
L1 scint ─────────► M2 OR ─liq1..4──┘        (per sector)     │  A Singles / B Doubles
                                                              │  C OR+veto / D master
PS pickup ─► ext N1081B (9.6µs) ─► N93B (30ms gate) ─invert─► M4.C veto
M5 = scalers (tap every stage)      M6 = pulser / mesh-inject / SiPM-blank`;
    ov.innerHTML =
      `<h3>n_TOF X17 trigger — 6 × N1081B</h3>` +
      `<div class="sub">Split at the detector, sum on the trigger side (428F), discriminate, ` +
      `per-sector coincidence, then build Singles/Doubles and gate with the PS window. ` +
      `Panels below are drawn like the physical CAEN-red modules; colours mark signal category.</div>` +
      `<div class="n81-flowmap">${esc(flowmap)}</div>`;

    // PS veto chain
    const chainWrap = el("div");
    chainWrap.innerHTML = `<div class="n81-col-label" style="margin-top:.6rem">PS flash-veto chain (external)</div>`;
    const chain = el("div", "n81-chain");
    const steps = (state.external_chain && state.external_chain.ps_veto) || [];
    steps.forEach((s, i) => {
      if (i) chain.appendChild(el("div", "sep", "▶"));
      chain.appendChild(el("div", "step", `<div class="t">${esc(s.label)}</div><div class="d">${esc(s.detail)}</div>`));
    });
    chainWrap.appendChild(chain);
    ov.appendChild(chainWrap);

    // legend
    const legend = el("div", "n81-legend");
    legend.innerHTML =
      `<span><i style="border-color:var(--n81-on)"></i>live ON</span>` +
      `<span><i style="border-color:var(--n81-off);border-style:dashed"></i>design-only</span>` +
      `<span><i style="border-color:var(--n81-bad)"></i>off / mismatch / veto</span>` +
      `<span><i style="border-color:var(--n81-scan)"></i>driven by active scan</span>` +
      `<span>⃠ inverted</span>  <span>* = intended value (no live read-back)</span>`;
    ov.appendChild(legend);
    return ov;
  }

  // ------------------------------------------------------------------ banner + nav
  function bannerEl(state) {
    const sa = state.active_scan;
    const b = el("div", "n81-banner" + (sa && sa.tag ? "" : " idle"));
    if (sa && sa.tag) {
      b.innerHTML =
        `<span class="tag">▶ ${esc(sa.tag)}</span>` +
        `<span class="note">${esc(sa.note || "scan config currently applied to the boards")}</span>` +
        `<span class="age">applied ${esc(sa.at || "")}${sa.age_s != null ? " · " + fmtAge(sa.age_s) + " ago" : ""}</span>`;
    } else {
      b.innerHTML = `<span class="tag">No scan modulation active</span>` +
        `<span class="note">boards hold their static / last-applied config</span>`;
    }
    return b;
  }
  function fmtAge(s) {
    if (s == null) return "";
    if (s < 90) return s + "s";
    if (s < 5400) return Math.round(s / 60) + "m";
    return Math.round(s / 3600) + "h";
  }
  function navEl(state) {
    const nav = el("div", "n81-jump");
    const ov = el("a", null, "Overview"); ov.href = "#n81-overview"; nav.appendChild(ov);
    state.modules.forEach(m => {
      const a = el("a", null, `<span class="dot" style="background:${roleColor(state, m.color)}"></span>M${m.n} ${esc(m.role)}`);
      a.href = "#n81-mod-" + m.n;
      nav.appendChild(a);
    });
    const src = state.source || {};
    let srcTxt = "no snapshot";
    if (src.kind) {
      srcTxt = (src.kind === "run_snapshot" ? "run snapshot" : "manual dump") +
        (src.polled_at ? " · " + src.polled_at : "") +
        (src.age_s != null ? " · " + fmtAge(src.age_s) + " old" : "");
    }
    nav.appendChild(el("span", "n81-src", srcTxt));
    return nav;
  }

  // ------------------------------------------------------------------ render
  function render(state, root) {
    ensureStyles();
    if (!root) return;
    root.classList.add("n81-root");
    root.innerHTML = "";
    if (!state || !state.success) {
      root.appendChild(el("div", "n81-mod-note", "Trigger state unavailable: " +
        esc((state && state.message) || "no data")));
      return;
    }
    root.appendChild(navEl(state));
    root.appendChild(bannerEl(state));
    root.appendChild(overviewEl(state));
    state.modules.forEach(m => root.appendChild(moduleEl(m, state)));
    const src = state.source || {};
    root.appendChild(el("div", "n81-foot",
      "Design model: TRIGGER_SETUP_2026-07.md  ·  live overlay: " +
      esc(src.path || "—") + "  ·  rendered " + new Date().toLocaleString()));
  }

  window.N1081B = { render: render };
})();
