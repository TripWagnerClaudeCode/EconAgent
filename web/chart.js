"use strict";
/* Small dependency-free SVG line chart: time or category x-axis, optional right-hand axis, hover crosshair + tooltip.
   All text is set with textContent (no innerHTML). */
(function () {
  const NS = "http://www.w3.org/2000/svg";
  const svgEl = (tag, attrs = {}) => { const e = document.createElementNS(NS, tag); for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v); return e; };

  function niceTicks(lo, hi, n = 5) {
    if (!isFinite(lo) || !isFinite(hi)) return [0, 1];
    if (lo === hi) { lo -= 1; hi += 1; }
    const raw = (hi - lo) / n, mag = Math.pow(10, Math.floor(Math.log10(raw)));
    const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) || 10 * mag;
    const out = []; for (let v = Math.ceil(lo / step) * step; v <= hi + step * 1e-9; v += step) out.push(+v.toFixed(10));
    return out;
  }
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  function fmtTime(ms, spanMs) {
    const d = new Date(ms), day = 864e5;
    if (spanMs <= 6 * day) return `${String(d.getUTCHours()).padStart(2, "0")}:${String(d.getUTCMinutes()).padStart(2, "0")}`;
    if (spanMs <= 150 * day) return `${MONTHS[d.getUTCMonth()]} ${d.getUTCDate()}`;
    return `${MONTHS[d.getUTCMonth()]} \u2019${String(d.getUTCFullYear()).slice(2)}`;   // e.g. Oct ’21: never confusable with a day of the month
  }
  const fmtFull = (ms, hasTime) => { const d = new Date(ms); const base = `${MONTHS[d.getUTCMonth()]} ${d.getUTCDate()}, ${d.getUTCFullYear()}`;
    return hasTime ? `${base} ${String(d.getUTCHours()).padStart(2, "0")}:${String(d.getUTCMinutes()).padStart(2, "0")}` : base; };
  const num = (v, dec) => (v == null ? "–" : Number(v).toLocaleString(undefined, { minimumFractionDigits: dec, maximumFractionDigits: dec }));
  // Parse "YYYY-MM-DD" or "YYYY-MM-DDTHH:MM" as UTC so labels read the same everywhere (the API sends market-local clock times).
  const parseT = (s) => Date.parse(s.length <= 10 ? s + "T00:00:00Z" : s + ":00Z");

  class LineChart {
    /* opts: { height, mode: "time"|"category", emptyText } ; series: [{name,color,points:[[x,y]],axis:"left"|"right",decimals,suffix}] */
    constructor(el, opts = {}) {
      this.el = el; this.opts = { height: 300, mode: "time", ...opts }; this.series = [];
      this.wrap = document.createElement("div"); this.wrap.className = "chart"; this.wrap.style.height = this.opts.height + "px";
      this.tip = document.createElement("div"); this.tip.className = "chart-tip"; this.tip.hidden = true;
      el.replaceChildren(this.wrap, this.tip);
      this.ro = new ResizeObserver(() => this.draw()); this.ro.observe(this.wrap);
    }
    destroy() { this.ro.disconnect(); this.el.replaceChildren(); }
    setSeries(series) { this.series = series.filter((s) => s.points && s.points.length); this.draw(); }

    draw() {
      const W = this.wrap.clientWidth, H = this.opts.height;
      this.wrap.replaceChildren();
      if (!W) return;
      if (!this.series.length) { const m = document.createElement("div"); m.className = "chart-empty"; m.textContent = this.opts.emptyText || "No data"; this.wrap.append(m); return; }
      const cat = this.opts.mode === "category";
      const hasRight = this.series.some((s) => s.axis === "right");
      const M = { l: 54, r: hasRight ? 54 : 14, t: 12, b: 26 }, pw = W - M.l - M.r, ph = H - M.t - M.b;
      const xs = this.series.flatMap((s) => s.points.map((p) => (cat ? p[0] : parseT(p[0]))));
      const labels = cat ? [...new Set(this.series.flatMap((s) => s.points.map((p) => p[0])))] : null;
      const xmin = cat ? 0 : Math.min(...xs), xmax = cat ? labels.length - 1 : Math.max(...xs);
      const X = (x) => M.l + ((cat ? labels.indexOf(x) : parseT(x) - xmin) / ((cat ? xmax : xmax - xmin) || 1)) * pw;
      const ranges = {};
      for (const ax of ["left", "right"]) {
        const vs = this.series.filter((s) => (s.axis || "left") === ax).flatMap((s) => s.points.map((p) => p[1]).filter((v) => v != null));
        if (!vs.length) continue;
        let lo = Math.min(...vs), hi = Math.max(...vs), pad = (hi - lo) * 0.08 || 1; lo -= pad; hi += pad;
        const ticks = niceTicks(lo, hi); ranges[ax] = { lo: Math.min(lo, ticks[0]), hi: Math.max(hi, ticks[ticks.length - 1]), ticks };
      }
      const Y = (ax, v) => M.t + ph - ((v - ranges[ax].lo) / (ranges[ax].hi - ranges[ax].lo)) * ph;
      const svg = svgEl("svg", { width: W, height: H, role: "img" });
      // grid + left/right ticks
      const L = ranges.left || ranges.right;
      for (const t of L.ticks) {
        const y = Y(ranges.left ? "left" : "right", t);
        svg.append(svgEl("line", { x1: M.l, x2: W - M.r, y1: y, y2: y, class: "grid" }));
        const lab = svgEl("text", { x: M.l - 8, y: y + 4, class: "axis", "text-anchor": "end" }); lab.textContent = num(t, Math.abs(t) < 10 && t % 1 ? 2 : 0); svg.append(lab);
      }
      if (ranges.left && ranges.right) for (const t of ranges.right.ticks) {
        const lab = svgEl("text", { x: W - M.r + 8, y: Y("right", t) + 4, class: "axis axis-r", "text-anchor": "start" }); lab.textContent = num(t, Math.abs(t) < 10 && t % 1 ? 2 : 0); svg.append(lab);
      }
      if (ranges.left && ranges.left.lo < 0 && ranges.left.hi > 0) svg.append(svgEl("line", { x1: M.l, x2: W - M.r, y1: Y("left", 0), y2: Y("left", 0), class: "zero" }));
      // x ticks
      const span = xmax - xmin, nT = Math.max(2, Math.min(6, Math.floor(pw / 90)));
      const xticks = cat ? labels.map((l) => [l, l]) : Array.from({ length: nT }, (_, i) => { const ms = xmin + (span * i) / (nT - 1); return [ms, fmtTime(ms, span)]; });
      xticks.forEach(([pos, lab], i) => {
        const x = cat ? X(pos) : M.l + (pw * i) / (nT - 1);
        const t = svgEl("text", { x, y: H - 8, class: "axis", "text-anchor": cat ? "middle" : i === 0 ? "start" : i === nT - 1 ? "end" : "middle" }); t.textContent = lab; svg.append(t);
      });
      // series
      this.series.forEach((s, si) => {
        const ax = s.axis || "left";
        const pts = s.points.filter((p) => p[1] != null);
        const d = pts.map((p, i) => `${i ? "L" : "M"}${X(p[0]).toFixed(1)},${Y(ax, p[1]).toFixed(1)}`).join("");
        if (si === 0 && !cat && pts.length > 1) {
          svg.append(svgEl("path", { d: `${d}L${X(pts[pts.length - 1][0]).toFixed(1)},${M.t + ph}L${X(pts[0][0]).toFixed(1)},${M.t + ph}Z`, fill: s.color, opacity: 0.08 }));
        }
        svg.append(svgEl("path", { d, fill: "none", stroke: s.color, "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }));
        if (cat) for (const p of pts) svg.append(svgEl("circle", { cx: X(p[0]), cy: Y(ax, p[1]), r: 2.6, fill: s.color }));
      });
      // hover layer
      const vline = svgEl("line", { y1: M.t, y2: M.t + ph, class: "vline", visibility: "hidden" }); svg.append(vline);
      const panelColor = getComputedStyle(document.documentElement).getPropertyValue("--panel").trim() || "#fff";
      const dots = this.series.map((s) => { const c = svgEl("circle", { r: 4.5, fill: s.color, stroke: panelColor, "stroke-width": 2, visibility: "hidden" }); svg.append(c); return c; });
      const hit = svgEl("rect", { x: M.l, y: M.t, width: pw, height: ph, fill: "transparent" }); svg.append(hit);
      const nearest = (s, tx) => { let best = null, bd = Infinity; for (const p of s.points) { if (p[1] == null) continue; const dx = Math.abs((cat ? labels.indexOf(p[0]) : parseT(p[0])) - tx); if (dx < bd) { bd = dx; best = p; } } return best; };
      const move = (ev) => {
        const r = svg.getBoundingClientRect(), px = ev.clientX - r.left;
        const frac = Math.min(1, Math.max(0, (px - M.l) / pw));
        const tx = cat ? Math.round(frac * xmax) : xmin + frac * span;
        const rows = []; let anchorX = null;
        this.series.forEach((s, i) => {
          const p = nearest(s, tx); if (!p) return;
          const cx = X(p[0]), cy = Y(s.axis || "left", p[1]); anchorX = anchorX ?? cx;
          dots[i].setAttribute("cx", cx); dots[i].setAttribute("cy", cy); dots[i].setAttribute("visibility", "visible");
          rows.push([s, p]);
        });
        if (anchorX == null) return;
        vline.setAttribute("x1", anchorX); vline.setAttribute("x2", anchorX); vline.setAttribute("visibility", "visible");
        const first = rows[0][1][0];
        const head = document.createElement("div"); head.className = "tip-head";
        head.textContent = cat ? first : fmtFull(parseT(first), first.length > 10);
        const body = rows.map(([s, p]) => { const line = document.createElement("div"); line.className = "tip-row";
          const sw = document.createElement("i"); sw.style.background = s.color; const t = document.createElement("span"); t.textContent = `${s.name}`;
          const v = document.createElement("b"); v.textContent = num(p[1], s.decimals ?? 2) + (s.suffix || ""); line.append(sw, t, v); return line; });
        this.tip.replaceChildren(head, ...body); this.tip.hidden = false;
        const tw = this.tip.offsetWidth, left = anchorX + 14 + tw > W ? anchorX - tw - 14 : anchorX + 14;
        this.tip.style.left = Math.max(4, left) + "px"; this.tip.style.top = M.t + 6 + "px";
      };
      const leave = () => { vline.setAttribute("visibility", "hidden"); dots.forEach((d) => d.setAttribute("visibility", "hidden")); this.tip.hidden = true; };
      hit.addEventListener("pointermove", move); hit.addEventListener("pointerleave", leave); hit.addEventListener("pointerdown", move);
      this.wrap.append(svg);
    }
  }
  window.LineChart = LineChart;
})();
