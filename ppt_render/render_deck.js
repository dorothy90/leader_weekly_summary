/**
 * render_deck.js v2 — 슬라이드 스펙 JSON → 편집가능 아티팩트형 .pptx (pptxgenjs)
 *
 * 사용법: node render_deck.js <spec.json> <out.pptx> [theme.json]
 *
 * v2 목표: 캔버스를 꽉 채우는 밀도, 색 블로킹(헤더 바/칩/틴트 카드), 아이콘,
 *   면적 채운 차트, 풋터/키커 — Gamma/Claude 아티팩트 톤.
 */

const fs = require("fs");
const path = require("path");
const pptxgen = require("pptxgenjs");
const React = require("react");
const ReactDOMServer = require("react-dom/server");
const sharp = require("sharp");
const Fi = require("react-icons/fi");

const ICONS = {
  "chart-line": Fi.FiTrendingUp, "trending-up": Fi.FiTrendingUp, "trending-down": Fi.FiTrendingDown,
  "alert": Fi.FiAlertTriangle, "check": Fi.FiCheckCircle, "target": Fi.FiTarget,
  "gear": Fi.FiSettings, "flask": Fi.FiThermometer, "clock": Fi.FiClock,
  "layers": Fi.FiLayers, "activity": Fi.FiActivity, "info": Fi.FiInfo,
};
const _iconCache = new Map();
async function iconPng(name, colorHex, size = 256) {
  const key = `${name}|${colorHex}`;
  if (_iconCache.has(key)) return _iconCache.get(key);
  const Comp = ICONS[name] || Fi.FiCircle;
  const svg = ReactDOMServer.renderToStaticMarkup(React.createElement(Comp, { color: "#" + colorHex, size: String(size), strokeWidth: 2.4 }));
  const png = await sharp(Buffer.from(svg)).png().toBuffer();
  const data = "image/png;base64," + png.toString("base64");
  _iconCache.set(key, data);
  return data;
}

// ---------- 색 유틸 ----------
const hx = (h) => (h || "").replace("#", "");
function rgb(h) { h = hx(h); return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)]; }
function mix(h1, h2, t) { const a = rgb(h1), b = rgb(h2); const c = a.map((v, i) => Math.round(v + (b[i] - v) * t)); return c.map((v) => v.toString(16).padStart(2, "0")).join("").toUpperCase(); }
const tint = (h, t) => mix(h, "FFFFFF", t);
const shade = (h, t) => mix(h, "000000", t);
const makeShadow = (o = {}) => ({ type: "outer", color: o.color || "1A2230", blur: o.blur || 14, offset: o.offset || 4, angle: 90, opacity: o.opacity || 0.10 });

function loadTheme(themePath) {
  const raw = JSON.parse(fs.readFileSync(themePath, "utf-8"));
  const b = raw.brand || {};
  const primary = b.primary || "0B3B76", accent = b.accent || "1EA0A0";
  return {
    primary, primaryDark: b.primary_dark || shade(primary, 0.35), accent,
    ink: b.ink || "1A2230", muted: b.muted || "6B7480", line: b.line || "E6E9EF",
    bg: b.bg || "FFFFFF", card: b.card || "FFFFFF",
    up: b.up || "1B8A5A", down: b.down || "C0392B", flat: b.flat || "6B7480",
    onPrimary: b.on_primary || "FFFFFF",
    accentTint: tint(accent, 0.86), primaryTint: tint(primary, 0.93), cardTint: tint(primary, 0.965),
    upTint: tint(b.up || "1B8A5A", 0.85), downTint: tint(b.down || "C0392B", 0.85),
    titleFont: (raw.font && raw.font.title) || "Pretendard", bodyFont: (raw.font && raw.font.body) || "Pretendard",
    logo: raw.logo || null,
  };
}

const W = 13.333, H = 7.5, MX = 0.9, CW = W - MX * 2;

// ---------- 공통 요소 ----------
function header(slide, T, title, kicker) {
  if (kicker)
    slide.addText(kicker.toUpperCase(), { x: MX, y: 0.5, w: CW, h: 0.3, fontFace: T.bodyFont, fontSize: 12, bold: true, color: T.accent, charSpacing: 3, align: "left", margin: 0 });
  slide.addText(title || "", { x: MX, y: kicker ? 0.82 : 0.62, w: CW, h: 0.82, fontFace: T.titleFont, fontSize: 30, bold: true, color: T.ink, align: "left", valign: "middle", margin: 0, fit: "shrink" });
  return 1.95; // content top
}
function footer(slide, T, ctx) {
  const y = 6.98;
  slide.addShape(pres_LINE, { x: MX, y, w: CW, h: 0, line: { color: T.line, width: 1 } });
  slide.addText(ctx.title || "", { x: MX, y: y + 0.05, w: CW - 1.2, h: 0.3, fontFace: T.bodyFont, fontSize: 9, color: T.muted, align: "left", margin: 0 });
  slide.addText(String(ctx.page).padStart(2, "0"), { x: W - MX - 1.0, y: y + 0.05, w: 1.0, h: 0.3, fontFace: T.bodyFont, fontSize: 9, color: T.muted, align: "right", margin: 0 });
}
let pres_LINE; // set in main from pres.shapes.LINE

function chip(slide, T, text, x, y, bg, fg) {
  const w = Math.max(0.5, 0.22 + text.length * 0.11);
  slide.addShape(RR, { x, y, w, h: 0.34, rectRadius: 0.17, fill: { color: bg } });
  slide.addText(text, { x, y, w, h: 0.34, fontFace: T.bodyFont, fontSize: 12, bold: true, color: fg, align: "center", valign: "middle", margin: 0 });
  return w;
}
let RR, RECT, OVAL, LINE;

function dir(T, d) { return (d === "up" || d === "improving") ? [T.up, T.upTint] : (d === "down" || d === "declining") ? [T.down, T.downTint] : [T.flat, T.line]; }
function kpiSize(v) { const n = (v || "").length; if (n <= 5) return 40; if (n <= 8) return 32; if (n <= 12) return 24; return 18; }
async function badge(slide, T, icon, cx, cy, d, fill, ic) {
  slide.addShape(OVAL, { x: cx - d / 2, y: cy - d / 2, w: d, h: d, fill: { color: fill } });
  const img = await iconPng(icon || "info", ic, 256);
  slide.addImage({ data: img, x: cx - d * 0.27, y: cy - d * 0.27, w: d * 0.54, h: d * 0.54 });
}

// ---------- 레이아웃 ----------
function slCover(pres, T, s) {
  const sl = pres.addSlide();
  sl.background = { color: T.primaryDark };
  sl.addShape(RECT, { x: 0, y: 0, w: W, h: 2.2, fill: { color: shade(T.primary, 0.15) } });
  sl.addShape(RECT, { x: 0, y: 0, w: 0.4, h: H, fill: { color: T.accent } });
  // 거대한 고스트 워터마크
  sl.addText("YIELD", { x: W - 6.0, y: 4.4, w: 6.0, h: 2.6, fontFace: T.titleFont, fontSize: 130, bold: true, color: shade(T.primary, 0.08), align: "right", valign: "middle", margin: 0 });
  if (s.eyebrow) sl.addText(s.eyebrow.toUpperCase(), { x: MX, y: 2.9, w: CW, h: 0.4, fontFace: T.bodyFont, fontSize: 15, bold: true, color: T.accent, charSpacing: 4, margin: 0 });
  sl.addText(s.title || "", { x: MX, y: 3.35, w: CW - 1.0, h: 1.7, fontFace: T.titleFont, fontSize: 50, bold: true, color: T.onPrimary, align: "left", valign: "top", margin: 0, fit: "shrink" });
  sl.addShape(LINE, { x: MX, y: 5.25, w: 3.2, h: 0, line: { color: T.accent, width: 2.5 } });
  if (s.subtitle) sl.addText(s.subtitle, { x: MX, y: 5.5, w: CW - 1.0, h: 0.8, fontFace: T.bodyFont, fontSize: 17, color: tint(T.primary, 0.6), align: "left", margin: 0, fit: "shrink" });
  if (T.logo && fs.existsSync(T.logo)) sl.addImage({ path: T.logo, x: W - MX - 1.7, y: 0.6, w: 1.7, h: 0.62, sizing: { type: "contain", w: 1.7, h: 0.62 } });
  return sl;
}

function slSection(pres, T, s) {
  const sl = pres.addSlide();
  sl.background = { color: T.bg };
  const pw = 4.6;
  sl.addShape(RECT, { x: 0, y: 0, w: pw, h: H, fill: { color: T.primary } });
  sl.addShape(RECT, { x: pw, y: 0, w: 0.12, h: H, fill: { color: T.accent } });
  sl.addText(String(s.index || "01"), { x: 0.7, y: 2.2, w: pw - 1.0, h: 2.4, fontFace: T.titleFont, fontSize: 150, bold: true, color: shade(T.primary, 0.22), align: "left", valign: "middle", margin: 0 });
  sl.addText("SECTION", { x: pw + 0.7, y: 3.05, w: W - pw - 1.4, h: 0.4, fontFace: T.bodyFont, fontSize: 13, bold: true, color: T.accent, charSpacing: 4, margin: 0 });
  sl.addText(s.title || "", { x: pw + 0.7, y: 3.45, w: W - pw - 1.2, h: 1.4, fontFace: T.titleFont, fontSize: 36, bold: true, color: T.ink, align: "left", valign: "top", margin: 0, fit: "shrink" });
  return sl;
}

async function slKpiCallout(pres, T, s, ctx) {
  const sl = pres.addSlide();
  sl.background = { color: T.bg };
  const top = header(sl, T, s.title, "핵심 지표");
  const cards = (s.cards || []).slice(0, 4);
  const n = Math.max(cards.length, 1);
  const gap = 0.3, cy0 = top + 0.05, ch = 4.55;
  const cw = (CW - gap * (n - 1)) / n;
  for (let i = 0; i < cards.length; i++) {
    const c = cards[i];
    const x = MX + i * (cw + gap);
    sl.addShape(RR, { x, y: cy0, w: cw, h: ch, rectRadius: 0.09, fill: { color: T.cardTint }, line: { color: T.line, width: 1 }, shadow: makeShadow() });
    sl.addShape(RECT, { x, y: cy0, w: cw, h: 0.14, fill: { color: T.accent } });
    await badge(sl, T, c.icon || "activity", x + 0.55, cy0 + 0.72, 0.66, T.accentTint, hx(T.accent));
    sl.addText(c.value || "", { x: x + 0.28, y: cy0 + 1.35, w: cw - 0.56, h: 1.05, fontFace: T.titleFont, fontSize: kpiSize(c.value), bold: true, color: T.primary, align: "left", valign: "middle", margin: 0, fit: "shrink" });
    if (c.delta) { const [fg, bg] = dir(T, c.direction); chip(sl, T, c.delta, x + 0.28, cy0 + 2.45, bg, fg); }
    sl.addText(c.label || "", { x: x + 0.28, y: cy0 + 2.95, w: cw - 0.56, h: 0.9, fontFace: T.bodyFont, fontSize: 14, bold: true, color: T.ink, align: "left", valign: "top", margin: 0, fit: "shrink" });
    sl.addShape(LINE, { x: x + 0.28, y: cy0 + ch - 0.5, w: cw - 0.56, h: 0, line: { color: T.line, width: 1 } });
    if (c.cite) sl.addText(c.cite, { x: x + 0.28, y: cy0 + ch - 0.42, w: cw - 0.56, h: 0.3, fontFace: T.bodyFont, fontSize: 10, color: T.muted, margin: 0 });
  }
  footer(sl, T, ctx);
  return sl;
}

async function slIconRows(pres, T, s, ctx) {
  const sl = pres.addSlide();
  sl.background = { color: T.bg };
  const top = header(sl, T, s.title, "주요 내용");
  const rows = (s.rows || []).slice(0, 5);
  const gap = 0.22, bandBottom = 6.7;
  const rh = (bandBottom - top - gap * (rows.length - 1)) / Math.max(rows.length, 1);
  for (let i = 0; i < rows.length; i++) {
    const r = rows[i], y = top + i * (rh + gap), cy = y + rh / 2;
    sl.addShape(RR, { x: MX, y, w: CW, h: rh, rectRadius: 0.07, fill: { color: T.cardTint }, line: { color: T.line, width: 1 }, shadow: makeShadow({ opacity: 0.06 }) });
    sl.addShape(RECT, { x: MX, y, w: 0.1, h: rh, fill: { color: T.accent } });
    await badge(sl, T, r.icon || "info", MX + 0.85, cy, 0.82, T.primary, "FFFFFF");
    const tx = MX + 1.55, tw = CW - 1.55 - 2.2;
    sl.addText(r.heading || "", { x: tx, y: y + 0.22, w: tw, h: 0.42, fontFace: T.titleFont, fontSize: 17, bold: true, color: T.ink, align: "left", margin: 0, fit: "shrink" });
    sl.addText(r.body || "", { x: tx, y: y + 0.64, w: tw, h: rh - 0.86, fontFace: T.bodyFont, fontSize: 13.5, color: T.muted, align: "left", valign: "top", margin: 0, fit: "shrink" });
    if (r.cite) sl.addText(r.cite, { x: W - MX - 2.05, y: y + rh / 2 - 0.15, w: 1.85, h: 0.3, fontFace: T.bodyFont, fontSize: 10, color: T.muted, align: "right", margin: 0 });
  }
  footer(sl, T, ctx);
  return sl;
}

function panel(sl, T, x, y, w, h, heading, bullets, cite) {
  sl.addShape(RR, { x, y, w, h, rectRadius: 0.08, fill: { color: T.cardTint }, line: { color: T.line, width: 1 }, shadow: makeShadow({ opacity: 0.07 }) });
  sl.addShape(RECT, { x, y, w, h: 0.72, fill: { color: T.primary } });
  sl.addText(heading || "", { x: x + 0.3, y, w: w - 0.6, h: 0.72, fontFace: T.titleFont, fontSize: 18, bold: true, color: T.onPrimary, align: "left", valign: "middle", margin: 0, fit: "shrink" });
  const items = (bullets || []).slice(0, 6).map((b) => ({ text: typeof b === "string" ? b : (b.text || ""), options: { bullet: { code: "2022", indent: 18 }, color: T.ink, fontSize: 15, fontFace: T.bodyFont, breakLine: true, paraSpaceAfter: 10 } }));
  sl.addText(items.length ? items : [{ text: "" }], { x: x + 0.35, y: y + 0.95, w: w - 0.7, h: h - 1.35, align: "left", valign: "top", margin: 0, fit: "shrink" });
  if (cite) sl.addText(cite, { x: x + 0.35, y: y + h - 0.38, w: w - 0.7, h: 0.28, fontFace: T.bodyFont, fontSize: 10, color: T.muted, margin: 0 });
}
function slTwoCol(pres, T, s, ctx) {
  const sl = pres.addSlide();
  sl.background = { color: T.bg };
  const top = header(sl, T, s.title, "비교");
  const gap = 0.4, h = 6.7 - top, cw = (CW - gap) / 2;
  [["left", MX], ["right", MX + cw + gap]].forEach(([k, x]) => { const c = s[k] || {}; panel(sl, T, x, top, cw, h, c.heading, c.bullets, c.cite); });
  footer(sl, T, ctx);
  return sl;
}

function slTimeline(pres, T, s, ctx) {
  const sl = pres.addSlide();
  sl.background = { color: T.bg };
  const top = header(sl, T, s.title, "주차별 전개");
  const items = (s.items || []).slice(0, 5);
  const lx = MX + 1.5, bandBottom = 6.6, span = bandBottom - top - 0.4;
  const step = items.length > 1 ? span / (items.length - 1) : 0;
  sl.addShape(LINE, { x: lx, y: top + 0.3, w: 0, h: Math.max(span, 0.02), line: { color: T.line, width: 2.5 } });
  items.forEach((it, i) => {
    const y = top + 0.3 + i * step;
    sl.addShape(OVAL, { x: lx - 0.14, y: y - 0.14, w: 0.28, h: 0.28, fill: { color: T.accent }, line: { color: T.bg, width: 2 } });
    chip(sl, T, it.week || "", MX, y - 0.17, T.primaryTint, T.primary);
    const cx = lx + 0.45, cw = W - cx - MX;
    sl.addShape(RR, { x: cx, y: y - 0.42, w: cw, h: Math.min(step - 0.15, 0.86) || 0.7, rectRadius: 0.06, fill: { color: T.cardTint }, line: { color: T.line, width: 1 } });
    sl.addText(it.text || "", { x: cx + 0.25, y: y - 0.42, w: cw - 0.5, h: Math.min(step - 0.15, 0.86) || 0.7, fontFace: T.bodyFont, fontSize: 14, color: T.ink, align: "left", valign: "middle", margin: 0, fit: "shrink" });
    if (it.cite) sl.addText(it.cite, { x: W - MX - 1.9, y: y - 0.38, w: 1.7, h: 0.28, fontFace: T.bodyFont, fontSize: 9, color: T.muted, align: "right", margin: 0 });
  });
  footer(sl, T, ctx);
  return sl;
}

function slChart(pres, T, s, ctx) {
  const sl = pres.addSlide();
  sl.background = { color: T.bg };
  const top = header(sl, T, s.title, "데이터");
  const series = (s.series || []).map((se) => ({ name: se.name, labels: se.labels || [], values: se.values || [] }));
  const isBar = s.chartType === "bar";
  const type = isBar ? pres.charts.BAR : pres.charts.AREA;
  sl.addShape(RR, { x: MX, y: top, w: CW, h: 6.6 - top, rectRadius: 0.08, fill: { color: "FFFFFF" }, line: { color: T.line, width: 1 }, shadow: makeShadow({ opacity: 0.06 }) });
  const opts = {
    x: MX + 0.3, y: top + 0.3, w: CW - 0.6, h: 6.6 - top - (s.note ? 0.9 : 0.6),
    chartColors: [T.primary, T.accent, "8FB3D9", "F2B134"],
    chartArea: { fill: { color: "FFFFFF" } },
    catAxisLabelColor: T.muted, valAxisLabelColor: T.muted, catAxisLabelFontSize: 11, valAxisLabelFontSize: 11,
    catAxisLabelFontFace: T.bodyFont, valAxisLabelFontFace: T.bodyFont,
    valGridLine: { color: T.line, size: 0.5 }, catGridLine: { style: "none" },
    showLegend: series.length > 1, legendPos: "b", legendColor: T.muted, legendFontFace: T.bodyFont,
  };
  if (isBar) { opts.barDir = "col"; opts.showValue = true; opts.dataLabelColor = T.ink; opts.dataLabelPosition = "outEnd"; opts.dataLabelFontSize = 11; opts.dataLabelFontBold = true; opts.barGapWidthPct = 55; }
  else { opts.chartColorsOpacity = [28, 22]; opts.lineSize = 3; opts.lineSmooth = true; opts.lineDataSymbol = "circle"; opts.lineDataSymbolSize = 7; opts.showValue = true; opts.dataLabelColor = T.primary; opts.dataLabelPosition = "t"; opts.dataLabelFontSize = 11; opts.dataLabelFontBold = true; }
  if (series.length) sl.addChart(type, series, opts);
  if (s.note) sl.addText(s.note, { x: MX + 0.3, y: 6.6 - 0.55, w: CW - 0.6, h: 0.4, fontFace: T.bodyFont, fontSize: 12, italic: true, color: T.muted, align: "left", margin: 0 });
  footer(sl, T, ctx);
  return sl;
}

function slTable(pres, T, s, ctx) {
  const sl = pres.addSlide();
  sl.background = { color: T.bg };
  const top = header(sl, T, s.title, "요약");
  const headers = s.headers || [], bodyRows = (s.rows || []).slice(0, 8);
  const head = headers.map((h) => ({ text: String(h), options: { fill: { color: T.primary }, color: T.onPrimary, bold: true, fontFace: T.titleFont, fontSize: 13, align: "left", valign: "middle" } }));
  const rows = bodyRows.map((r, ri) => r.map((c) => ({ text: String(c), options: { color: T.ink, fontFace: T.bodyFont, fontSize: 13, fill: { color: ri % 2 ? T.cardTint : "FFFFFF" }, align: "left", valign: "middle" } })));
  const data = head.length ? [head, ...rows] : rows;
  if (data.length) sl.addTable(data, { x: MX, y: top, w: CW, border: { type: "solid", pt: 1, color: T.line }, rowH: Math.min(0.7, (6.5 - top) / data.length), autoPage: false, valign: "middle" });
  footer(sl, T, ctx);
  return sl;
}

function slTakeaways(pres, T, s) {
  const sl = pres.addSlide();
  sl.background = { color: T.primaryDark };
  sl.addShape(RECT, { x: 0, y: 0, w: 0.4, h: H, fill: { color: T.accent } });
  sl.addText("KEY TAKEAWAYS", { x: MX, y: 0.75, w: CW, h: 0.35, fontFace: T.bodyFont, fontSize: 13, bold: true, color: T.accent, charSpacing: 4, margin: 0 });
  sl.addText(s.title || "종합 및 시사점", { x: MX, y: 1.1, w: CW, h: 0.8, fontFace: T.titleFont, fontSize: 32, bold: true, color: T.onPrimary, align: "left", margin: 0, fit: "shrink" });
  const items = (s.bullets || []).slice(0, 5);
  const top = 2.3, bandBottom = 6.9, rh = (bandBottom - top) / Math.max(items.length, 1);
  items.forEach((b, i) => {
    const y = top + i * rh, txt = typeof b === "string" ? b : (b.text || "");
    sl.addText(String(i + 1).padStart(2, "0"), { x: MX, y, w: 0.9, h: rh - 0.1, fontFace: T.titleFont, fontSize: 26, bold: true, color: T.accent, align: "left", valign: "middle", margin: 0 });
    sl.addText(txt, { x: MX + 1.05, y, w: CW - 1.05, h: rh - 0.1, fontFace: T.bodyFont, fontSize: 17, color: tint(T.primary, 0.75), align: "left", valign: "middle", margin: 0, fit: "shrink" });
    if (i < items.length - 1) sl.addShape(LINE, { x: MX + 1.05, y: y + rh - 0.05, w: CW - 1.05, h: 0, line: { color: shade(T.primary, 0.1), width: 1 } });
  });
  return sl;
}

// ---------- 메인 ----------
async function main() {
  const [specPath, outPath, themeArg] = process.argv.slice(2);
  if (!specPath || !outPath) { console.error("usage: node render_deck.js <spec.json> <out.pptx> [theme.json]"); process.exit(2); }
  const T = loadTheme(themeArg || path.join(__dirname, "..", "themes", "exec.json"));
  const spec = JSON.parse(fs.readFileSync(specPath, "utf-8"));
  const meta = spec.meta || {};

  const pres = new pptxgen();
  pres.layout = "LAYOUT_WIDE";
  pres.author = "weekly_mail_agent";
  pres.title = meta.title || "주제 보고서";
  RR = pres.shapes.ROUNDED_RECTANGLE; RECT = pres.shapes.RECTANGLE; OVAL = pres.shapes.OVAL; LINE = pres.shapes.LINE; pres_LINE = LINE;

  const slides = spec.slides || [];
  const total = slides.length;
  for (let i = 0; i < slides.length; i++) {
    const s = slides[i];
    const ctx = { page: i + 1, total, title: meta.title || "" };
    try {
      switch (s.layout) {
        case "cover": slCover(pres, T, s); break;
        case "section": slSection(pres, T, s); break;
        case "kpi_callout": await slKpiCallout(pres, T, s, ctx); break;
        case "icon_rows": await slIconRows(pres, T, s, ctx); break;
        case "two_col": slTwoCol(pres, T, s, ctx); break;
        case "timeline": slTimeline(pres, T, s, ctx); break;
        case "chart": slChart(pres, T, s, ctx); break;
        case "table": slTable(pres, T, s, ctx); break;
        case "takeaways": slTakeaways(pres, T, s); break;
        default: console.error("unknown layout:", s.layout);
      }
    } catch (e) { console.error(`slide render failed (layout=${s.layout}):`, e.message); }
  }
  await pres.writeFile({ fileName: outPath });
  console.log("OK", outPath, slides.length, "slides");
}
main().catch((e) => { console.error(e); process.exit(1); });
