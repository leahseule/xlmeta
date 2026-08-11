"use strict";

// ── 상태 ─────────────────────────────────────────────────────
let DATA = null;
let METRIC_BY_ID = {};
let STRUCT = [];
let CUR_SHEET = 0;
let SELECTED = { kind: "overview", key: null };   // overview | region | metric

const CONF_KO = { high: "높음", medium: "보통", low: "낮음" };

// ── DOM 헬퍼 ─────────────────────────────────────────────────
const $ = (id) => document.getElementById(id);
const esc = (s) =>
  String(s ?? "").replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const show = (el) => el.classList.remove("hidden");
const hide = (el) => el.classList.add("hidden");

function toast(msg) {
  const t = $("toast");
  t.textContent = msg;
  show(t);
  clearTimeout(t._timer);
  t._timer = setTimeout(() => hide(t), 2600);
}

// 클립보드 복사 (권한 없으면 textarea 폴백). 성공/실패 관계없이 알림.
function copyText(text) {
  const done = () => toast("복사됐어요");
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(done).catch(() => { fallbackCopy(text); done(); });
  } else {
    fallbackCopy(text); done();
  }
}
function fallbackCopy(text) {
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.style.position = "fixed";
  ta.style.opacity = "0";
  document.body.appendChild(ta);
  ta.select();
  try { document.execCommand("copy"); } catch (e) { /* ignore */ }
  document.body.removeChild(ta);
}

// ── 서버 호출 ────────────────────────────────────────────────
async function analyzeFile(file) {
  const fd = new FormData();
  fd.append("file", file);
  return runRequest("/api/analyze", { method: "POST", body: fd });
}
const analyzeSample = () => runRequest("/api/sample", { method: "POST" });

async function runRequest(url, opts) {
  hide($("landing"));
  hide($("results"));
  show($("loading"));
  try {
    const res = await fetch(url, opts);
    const json = await res.json();
    if (!res.ok || json.error) throw new Error(json.error || `HTTP ${res.status}`);
    onData(json);
  } catch (e) {
    hide($("loading"));
    show($("landing"));
    toast("⚠ " + e.message);
  }
}

// ── 데이터 수신 ──────────────────────────────────────────────
function onData(data) {
  DATA = data;
  METRIC_BY_ID = {};
  data.metrics.forEach((m) => (METRIC_BY_ID[m.id] = m));
  STRUCT = data.structure || [];

  hide($("loading"));
  show($("results"));
  hide($("topTag"));
  show($("topResults"));
  $("srcFile").textContent = data.source_file;

  // 핵심(계산 값)이 가장 많은 시트를 기본으로 연다 — 원시 데이터 시트에서 시작하지 않게
  let best = 0, bestScore = -1;
  STRUCT.forEach((s, i) => {
    const nCalc = data.metrics.filter((m) => m.sheet === s.name && m.md_key).length;
    const nFormula = s.cells.reduce((a, row) => a + row.filter((c) => c.t === "f").length, 0);
    const score = nCalc * 1000 + nFormula;
    if (score > bestScore) { bestScore = score; best = i; }
  });

  renderLegend();
  renderSheetTabs();
  selectSheet(best);
}

// ── 시트 탭 ──────────────────────────────────────────────────
function renderSheetTabs() {
  const tabs = $("sheetTabs");
  tabs.innerHTML = STRUCT.map((s, i) =>
    `<button class="sheettab ${i === CUR_SHEET ? "active" : ""}" data-sheet="${i}" title="${esc(s.name)}">
       ${esc(s.name)} <span class="muted">${s.rows}×${s.cols}</span>
     </button>`).join("");
  tabs.onclick = (e) => {
    const b = e.target.closest(".sheettab");
    if (b) selectSheet(+b.dataset.sheet);
  };
  const cnt = $("sheetCount");
  if (cnt) cnt.textContent = STRUCT.length > 1 ? `시트 ${STRUCT.length}개` : "";
  const act = tabs.querySelector(".sheettab.active");
  if (act) act.scrollIntoView({ inline: "nearest", block: "nearest" });
}

function buildSheet(i) {
  CUR_SHEET = i;
  renderSheetTabs();
  renderGrid(STRUCT[i]);
  tagMetricCells(STRUCT[i]);
}
function selectSheet(i) {
  buildSheet(i);
  SELECTED = { kind: "overview", key: null };
  renderInterp();
  applyHighlight();
}
// 다른 시트의 지표로도 안전하게 이동
function goToMetric(id) {
  const m = METRIC_BY_ID[id];
  if (!m) return;
  const idx = STRUCT.findIndex((s) => s.name === m.sheet);
  if (idx >= 0 && idx !== CUR_SHEET) buildSheet(idx);
  select("metric", id);
}
// 참조하는 셀로 이동 → 그 시트에서 파랗게 표시
function selectRef(sheet, a1, name, fromId) {
  const idx = STRUCT.findIndex((s) => s.name === sheet);
  if (idx < 0) { toast(`${sheet} 시트는 이 파일에 없어요`); return; }
  if (idx !== CUR_SHEET) buildSheet(idx);
  SELECTED = { kind: "ref", key: a1, sheet, a1, name, from: fromId };
  renderInterp();
  applyHighlight();
}
// 오른쪽 패널은 그대로 두고, 격자에서 해당 셀만 하이라이트(포커스)
function flashCell(sheetName, a1) {
  const idx = STRUCT.findIndex((s) => s.name === sheetName);
  if (idx < 0) return;
  if (idx !== CUR_SHEET) buildSheet(idx);
  const table = $("gridScroll").querySelector("table");
  if (!table) return;
  table.querySelectorAll("td.sel, td.selgroup").forEach((td) => td.classList.remove("sel", "selgroup"));
  table.classList.add("dimmed");
  let first = null;
  refCells(STRUCT[CUR_SHEET], a1)
    .map(([r, c]) => table.querySelector(`td[data-r="${r}"][data-c="${c}"]`))
    .filter(Boolean)
    .forEach((td) => { td.classList.add("sel"); if (!first) first = td; });
  if (first) first.scrollIntoView({ block: "nearest", inline: "nearest" });
}

// 한 칸 선택 (수식/수기 칸별 해석)
function selectCell(r, c, metricId) {
  SELECTED = { kind: "cell", r, c, metricId };
  renderInterp();
  applyHighlight();
}

// 셀 주소 클릭 → 그 시트로 돌아가 그 셀을 다시 선택
function focusCell(sheetName, r, c, metricId) {
  const idx = STRUCT.findIndex((s) => s.name === sheetName);
  if (idx >= 0 && idx !== CUR_SHEET) buildSheet(idx);
  selectCell(r, c, metricId);
}

// 참조 표기(G:G · A6 · G6:G11)를 격자 좌표 목록으로
function refCells(sheet, a1) {
  const cols = sheet.col_letters;
  const [p0, p1] = a1.split(":");
  const end = p1 || p0;
  const c0m = /([A-Z]+)/.exec(p0), c1m = /([A-Z]+)/.exec(end);
  const r0m = /(\d+)/.exec(p0), r1m = /(\d+)/.exec(end);
  const c0 = c0m ? cols.indexOf(c0m[1]) + 1 : 0;
  const c1 = c1m ? cols.indexOf(c1m[1]) + 1 : c0;
  const r0 = r0m ? +r0m[1] : 1;
  const r1 = r1m ? +r1m[1] : (r0m ? +r0m[1] : sheet.rows);
  const out = [];
  for (let c = Math.min(c0, c1); c <= Math.max(c0, c1); c++) {
    if (c <= 0) continue;
    for (let r = Math.min(r0, r1); r <= Math.max(r0, r1); r++) out.push([r, c]);
  }
  return out;
}

// ── 왼쪽: 원본 격자 ──────────────────────────────────────────
function renderGrid(sheet) {
  if (!sheet) return;
  let head = `<tr><th></th>` + sheet.col_letters.map((L) => `<th>${L}</th>`).join("") + `</tr>`;
  let body = "";
  sheet.cells.forEach((row, ri) => {
    body += `<tr><th>${ri + 1}</th>`;
    row.forEach((cell, ci) => {
      const g = cell.g === null ? "" : ` data-g="${cell.g}"`;
      const disp = (cell.cv !== undefined && cell.cv !== null) ? cell.cv : cell.v;
      const isTitle = cell.title_of != null;
      const ttl = isTitle ? `표 제목으로 추측된 칸 — ${cell.v || ""}` : (cell.v || "");
      const title = ttl ? ` title="${esc(ttl)}"` : "";
      const cls = `role-${cell.r} t-${cell.t}${isTitle ? " is-title" : ""}`;
      body += `<td class="${cls}" data-r="${ri + 1}" data-c="${ci + 1}"${g}${title}>${esc(disp)}</td>`;
    });
    body += `</tr>`;
  });
  const wrap = $("gridScroll");
  wrap.innerHTML = `<table class="xlgrid"><thead>${head}</thead><tbody>${body}</tbody></table>`;
  wrap.querySelector("table").onclick = (e) => {
    const td = e.target.closest("td");
    if (!td) return;
    const r = +td.dataset.r, c = +td.dataset.c;
    if (td.dataset.metric) return selectCell(r, c, td.dataset.metric);
    // 진짜 표(데이터 행이 있는 영역)의 머리글만 표 구조 보기로. 그 외엔 칸 내용.
    if (td.classList.contains("role-header") && td.hasAttribute("data-g")) {
      const reg = sheet.regions[+td.dataset.g];
      if (reg && reg.row_count > 0) return select("region", td.dataset.g);
    }
    // 어떤 칸이든 내용이 있으면 그 칸에 뭐가 들었는지 보여준다 (표가 아니어도)
    const co = (sheet.cells[r - 1] || [])[c - 1];
    if (co && co.v !== "" && co.v !== null && co.v !== undefined)
      return selectCell(r, c, null);
    select("overview");
  };
}

// 계산되는 값이 있는 셀에 표식 (클릭하면 오른쪽에서 풀이)
function parseApplies(a1) {
  const rng = a1.split("!").pop();
  const [a, b] = rng.split(":");
  const col = /([A-Z]+)/.exec(a)[1];
  const r0 = +/(\d+)/.exec(a)[1];
  const r1 = b ? +/(\d+)/.exec(b)[1] : r0;
  return { col, r0, r1 };
}

function tagMetricCells(sheet) {
  const table = $("gridScroll").querySelector("table");
  DATA.metrics.filter((m) => m.sheet === sheet.name).forEach((m) => {
    const { col, r0, r1 } = parseApplies(m.applies_to);
    const c = sheet.col_letters.indexOf(col) + 1;
    if (c <= 0) return;
    for (let r = r0; r <= r1; r++) {
      const td = table.querySelector(`td[data-r="${r}"][data-c="${c}"]`);
      if (td) {
        td.classList.add("calc");
        td.dataset.metric = m.id;
        td.title = `${m.title || m.id} — 클릭하면 계산 풀이`;
      }
    }
  });
}

function renderLegend() {
  const roles = [
    ["머리글", "var(--accent-soft)"],
    ["행 식별", "#eef3f0"],
    ["소계/합계", "var(--warn-bg)"],
    ["표 밖", "#fcfcfd"],
  ];
  $("legend").innerHTML =
    `<span class="lg-lead">원본 위 색:</span>` +
    roles.map(([l, c]) => `<span class="lg"><span class="sw" style="background:${c}"></span>${l}</span>`).join("") +
    `<span class="lg"><span class="sw calc-sw"></span>계산되는 값</span>`;
}

// ── 선택 & 하이라이트 ────────────────────────────────────────
function select(kind, key) {
  SELECTED = { kind, key };
  renderInterp();
  applyHighlight();
}

function applyHighlight() {
  const table = $("gridScroll").querySelector("table");
  if (!table) return;
  table.querySelectorAll("td.sel, td.selgroup").forEach((td) => td.classList.remove("sel", "selgroup"));
  table.classList.remove("dimmed");
  if (SELECTED.kind === "overview") return;
  table.classList.add("dimmed");
  let cells;
  if (SELECTED.kind === "region")
    cells = [...table.querySelectorAll(`td[data-g="${SELECTED.key}"]`)];
  else if (SELECTED.kind === "metric")
    cells = [...table.querySelectorAll(`td[data-metric="${SELECTED.key}"]`)];
  else if (SELECTED.kind === "cell") {
    // 누른 셀 하나만 하이라이트
    const one = table.querySelector(`td[data-r="${SELECTED.r}"][data-c="${SELECTED.c}"]`);
    cells = one ? [one] : [];
  } else if (SELECTED.kind === "ref")
    cells = refCells(STRUCT[CUR_SHEET], SELECTED.a1)
      .map(([r, c]) => table.querySelector(`td[data-r="${r}"][data-c="${c}"]`))
      .filter(Boolean);
  else cells = [];
  let first = null;
  cells.forEach((td) => { td.classList.add("sel"); if (!first) first = td; });
  if (first) first.scrollIntoView({ block: "nearest", inline: "nearest" });
}

// ── 오른쪽: 해석 ─────────────────────────────────────────────
function renderInterp() {
  const sheet = STRUCT[CUR_SHEET];
  const crumbs = [{ label: `${sheet.name} 개요`, kind: "overview", key: null }];
  let body = "";

  if (SELECTED.kind === "overview") {
    body = interpOverview(sheet);
  } else if (SELECTED.kind === "region") {
    const reg = sheet.regions[+SELECTED.key];
    crumbs.push({ label: reg.title || reg.a1.split("!")[1], kind: "region", key: SELECTED.key });
    body = interpRegion(sheet, reg);
  } else if (SELECTED.kind === "metric") {
    const m = METRIC_BY_ID[SELECTED.key];
    const reg = sheet.regions.find((r) => r.a1 === m.region);
    if (reg) crumbs.push({ label: reg.title || reg.a1.split("!")[1], kind: "region", key: String(reg.id) });
    crumbs.push({ label: m.title || m.id, kind: "metric", key: SELECTED.key });
    body = interpMetric(m);
  } else if (SELECTED.kind === "cell") {
    const m = SELECTED.metricId ? METRIC_BY_ID[SELECTED.metricId] : null;
    const colL = sheet.col_letters[SELECTED.c - 1];
    const reg = m ? sheet.regions.find((rr) => rr.a1 === m.region)
                  : sheet.regions.find((rr) => rr.r0 <= SELECTED.r && SELECTED.r <= rr.r1 && rr.c0 <= SELECTED.c && SELECTED.c <= rr.c1);
    if (reg) crumbs.push({ label: reg.title || reg.a1.split("!")[1], kind: "region", key: String(reg.id) });
    if (m) crumbs.push({ label: m.title || m.id, kind: "metric", key: SELECTED.metricId });
    crumbs.push({ label: `${colL}${SELECTED.r}`, kind: "cell", key: "" });
    body = interpCell(sheet, SELECTED.r, SELECTED.c, SELECTED.metricId);
  } else if (SELECTED.kind === "ref") {
    const from = METRIC_BY_ID[SELECTED.from];
    crumbs.push({ label: from ? (from.title || from.id) : "값", kind: "metric", key: SELECTED.from });
    crumbs.push({ label: `참조: ${SELECTED.name || SELECTED.a1}`, kind: "ref", key: SELECTED.key });
    body = interpRef(SELECTED, from);
  }

  const crumbHtml = crumbs.map((c, i) =>
    i === crumbs.length - 1
      ? `<span class="cr-cur">${esc(c.label)}</span>`
      : `<span class="cr-link" data-kind="${c.kind}" data-key="${c.key ?? ""}">${esc(c.label)}</span>`
  ).join(`<span class="cr-sep">›</span>`);

  const panel = $("interp");
  panel.innerHTML = `<nav class="crumb">${crumbHtml}</nav>${body}`;
  panel.onclick = (e) => {
    const el = e.target.closest(".cr-link, [data-ref-a1], [data-focus-r], [data-goto], [data-region], [data-back-metric], [data-copy]");
    if (!el) return;
    if (el.dataset.focusR) return focusCell(el.dataset.focusSheet, +el.dataset.focusR, +el.dataset.focusC, el.dataset.focusMetric);
    if (el.dataset.goto) return goToMetric(el.dataset.goto);
    if (el.dataset.backMetric) return goToMetric(el.dataset.backMetric);
    if (el.dataset.refA1)                                   // 모든 참조: 하이라이트만
      return flashCell(el.dataset.refSheet, el.dataset.refA1);
    if (el.hasAttribute("data-region")) return select("region", el.dataset.region);
    if (el.classList.contains("cr-link")) {
      if (el.dataset.kind === "metric") return goToMetric(el.dataset.key);
      return select(el.dataset.kind, el.dataset.key || null);
    }
    if (el.dataset.copy) copyText(el.dataset.copy);
  };
}

// ── 해석 공통 컴포넌트 (모든 뷰가 같은 문법을 씀) ────────────
// 헤더: 위치+상태(eyebrow) · 제목 · 부제
function ihead(eyebrowHtml, title, sub) {
  return `<header class="ihead">
    <div class="ihead-eyebrow">${eyebrowHtml}</div>
    <h2 class="ihead-title">${esc(title)}</h2>
    ${sub ? `<div class="ihead-sub">${esc(sub)}</div>` : ""}
  </header>`;
}
// 섹션: 동일한 라벨 + 내용. cls "card"면 테두리 카드, "warn"이면 주의 색.
function blk(label, content, cls) {
  if (!content) return "";
  return `<section class="blk ${cls || ""}"><h3 class="blk-h">${esc(label)}</h3>${content}</section>`;
}
// 지금 보는 범위 표식 (열 전체 / 셀 하나 / 표 / 시트)
function scopePill(text) {
  return `<span class="scope">${esc(text)}</span>`;
}
// 구조 인식이 확실하지 않을 때(보통·낮음)만 '확인 권장' 표시
function confTag(level) {
  if (level === "high") return "";
  const label = level === "low" ? "구조 확인 필요" : "구조 확인 권장";
  return `<span class="tag warn" title="xlmeta가 이 표의 구조(머리글·행 범위 등)를 얼마나 확실히 읽었는지예요. 인식 결과가 실제와 맞는지 한번 확인하는 게 좋아요.">${label}</span>`;
}

function interpRef(sel, from) {
  const fromName = from ? (from.title || from.id) : "이 값";
  let h = ihead(`${scopePill("참조 셀")} ${esc(sel.sheet)} 시트 · ${esc(sel.a1)}`,
    sel.name || sel.a1, null);
  h += blk("무엇인가",
    `<p class="blk-lead"><b>${esc(fromName)}</b> 계산이 참조하는 원천이에요. 왼쪽 <b>${esc(sel.sheet)}</b> 시트에서 파랗게 표시된 칸이 그곳이에요.</p>`, "");
  h += `<div class="collink"><button class="btn" data-back-metric="${esc(sel.from)}">← ${esc(fromName)}(으)로 돌아가기</button></div>`;
  return h;
}

function aiHandoffBlk() {
  const s = DATA.share;
  if (!s) return "";
  const q = encodeURIComponent(s.prefill);
  const claude = `https://claude.ai/new?q=${q}`;
  const gpt = `https://chatgpt.com/?q=${q}`;
  return blk("AI에게 넘기기",
    `<p class="blk-lead">이 엑셀의 <b>구조·업무규칙 요약</b>을 만들었어요 (시트 ${s.totals.sheets}·표 ${s.totals.tables}·지표 ${s.totals.metrics}). 버튼을 누르면 그 요약 링크가 담긴 채 대화가 열려요.</p>
     <div class="ai-actions">
       <a class="btn ai" href="${claude}" target="_blank" rel="noopener">Claude에서 열기</a>
       <a class="btn ai" href="${gpt}" target="_blank" rel="noopener">ChatGPT에서 열기</a>
       <a class="btn ghost" href="${esc(s.path)}" target="_blank" rel="noopener">요약 페이지 보기</a>
     </div>
     <div class="ai-links">
       <button class="chip link" data-copy="${esc(s.prefill)}">AI에게 보낼 문구 복사</button>
       <button class="chip link" data-copy="${esc(s.url)}">링크만 복사</button>
     </div>
     <p class="blk-note">버튼이 자동으로 안 열리면, 위 <b>문구를 복사</b>해 AI 대화창에 붙여넣으면 돼요. AI가 링크를 읽으려면 <b>공개 주소</b>여야 해요(배포된 주소에서 동작).</p>`,
    "card");
}

// OKF — 엑셀 하나 = 한 문서. 에이전트가 한 번에 읽는다.
function okfBlk() {
  const okf = DATA.okf_single || "";
  if (!okf) return "";
  return blk("OKF · 엑셀 파일 전체 (한 문서)",
    `<p class="blk-lead">엑셀 전체 지식을 <b>하나의 문서</b>로 합쳤어요 — 지표마다 흩어지지 않아, 에이전트가 한 번에 읽어요.</p>
     <div class="okf-bar">
       <button class="chip link" id="okfCopy">전체 복사</button>
       <button class="chip link" id="okfDownload">.md 내려받기</button>
     </div>
     <pre class="okf-view" id="okfView">${esc(okf)}</pre>`, "card");
}

function downloadOkf() {
  const okf = DATA.okf_single || "";
  if (!okf) return;
  const name = (DATA.source_file || "okf").replace(/\.[^.]+$/, "") + ".okf.md";
  const blob = new Blob([okf], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = name;
  document.body.appendChild(a); a.click(); document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

function renderExport() {
  const body = $("exportBody");
  if (!DATA) { body.innerHTML = `<p class="muted">먼저 엑셀을 분석하세요.</p>`; return; }
  body.innerHTML = aiHandoffBlk() + okfBlk();       // 위: AI에게 넘기기 / 아래: OKF 한 문서
  body.onclick = (e) => {
    if (e.target.closest("#okfCopy")) return copyText(DATA.okf_single || "");
    if (e.target.closest("#okfDownload")) return downloadOkf();
    const cp = e.target.closest("[data-copy]");
    if (cp) copyText(cp.dataset.copy);
  };
}
function openExport() { renderExport(); $("exportOverlay").classList.remove("hidden"); }
function closeExport() { $("exportOverlay").classList.add("hidden"); }

function interpOverview(sheet) {
  const nCalc = DATA.metrics.filter((m) => m.sheet === sheet.name && m.md_key).length;
  const nFormula = sheet.cells.flat().filter((c) => c.t === "f").length;
  let h = ihead(scopePill("시트"), sheet.name,
    `표 ${sheet.regions.length}개 · 계산 값 ${nCalc}개 · 수식 셀 ${nFormula}개`);

  // 이 시트는 무엇을 관리하는 문서인지 — 결정론적 한 문단
  const card = (DATA.summary && DATA.summary.sheets || []).find((c) => c.sheet === sheet.name);
  if (card && card.paragraph) h += blk("이 시트는", `<p class="blk-lead">${esc(card.paragraph)}</p>`, "card");

  if (sheet.regions.length) {
    const rows = sheet.regions.map((reg) => {
      const name = reg.title || reg.a1.split("!")[1];
      const cnt = DATA.metrics.filter((m) => m.region === reg.a1 && m.md_key).length;
      return `<div class="ov-row" data-region="${reg.id}">
        <div class="ov-row-main"><span class="ov-name">${esc(name)}</span>
          ${confTag(reg.confidence)}</div>
        <div class="ov-row-sub">${esc(reg.a1.split("!")[1])} · ${reg.row_count}행${cnt ? ` · 계산 값 ${cnt}개` : ""}</div>
      </div>`;
    }).join("");
    h += blk("표 영역", rows, "");
  }
  if (sheet.refs_out.length) {
    h += blk("다른 시트 참조",
      `<p class="blk-lead">이 시트는 <b>${sheet.refs_out.map(esc).join("</b>, <b>")}</b> 시트의 값을 끌어와 계산해요.</p>`, "");
  }
  h += `<p class="tip-line">왼쪽에서 <b>색칠된 표</b>나 <b>계산되는 값(셀)</b>을 누르면 여기서 풀어 설명해요.</p>`;
  return h;
}

function interpRegion(sheet, reg) {
  const name = reg.title || reg.a1.split("!")[1];
  let h = ihead(`${scopePill("표")} ${esc(sheet.name)} 시트 · ${esc(reg.a1.split("!")[1])} ${confTag(reg.confidence)}`,
    name, `${reg.row_count}행`);

  if (reg.confidence !== "high") {
    h += `<p class="conf-note">이 표는 구조(머리글·행 범위 등)를 <b>확실히 읽지 못했어요.</b> 아래 인식 결과가 실제와 맞는지 한번 확인해 주세요.</p>`;
  }

  const cols = Object.entries(reg.columns);
  h += blk("열 구성",
    `<table class="grid"><thead><tr><th>열</th><th>이름</th></tr></thead><tbody>` +
    cols.map(([c, nm]) => `<tr><td><code>${esc(c)}</code></td><td>${esc(nm)}</td></tr>`).join("") +
    `</tbody></table>`, "");

  const meta = [];
  if (reg.title_cell) meta.push(`<span class="pill">제목 칸 · ${esc(reg.title_cell)} <span class="pill-note">(추측)</span></span>`);
  if (reg.key_cols.length) meta.push(`<span class="pill">행 식별 · ${reg.key_cols.map(esc).join(", ")}</span>`);
  if (reg.header_rows.length) meta.push(`<span class="pill">머리글 행 · ${reg.header_rows.join(", ")}</span>`);
  if (reg.subtotal_rows.length) meta.push(`<span class="pill warn">소계 행 · ${reg.subtotal_rows.join(", ")}</span>`);
  if (meta.length) h += blk("구조", `<div class="rc-meta">${meta.join("")}</div>`, "");

  const mine = DATA.metrics.filter((m) => m.region === reg.a1 && m.md_key);
  if (mine.length) h += blk("자동 계산되는 값",
    `<div class="chips">${mine.map((m) => `<span class="chip link" data-goto="${esc(m.id)}">${esc(m.title || m.id)}</span>`).join("")}</div>`, "");
  return h;
}

// ── 계산 풀이 (일상 언어) ────────────────────────────────────
const AGG_VERB = {
  SUMIFS: "모두 더한", SUMIF: "모두 더한",
  COUNTIFS: "센", COUNTIF: "센",
  AVERAGEIFS: "평균 낸", AVERAGEIF: "평균 낸",
  MAXIFS: "가장 큰", MINIFS: "가장 작은",
};
const colLetterOf = (a1) => (/([A-Z]{1,3})\d+/.exec(a1 || "") || [, ""])[1];
const fmtNum = (v) => {
  const n = Number(v);
  return Number.isFinite(n) && String(v).trim() !== "" ? n.toLocaleString("ko-KR") : String(v);
};

function hasBatchim(word) {
  if (!word) return false;
  const ch = word.charCodeAt(word.length - 1);
  if (ch < 0xac00 || ch > 0xd7a3) return false;
  return (ch - 0xac00) % 28 !== 0;
}
const subjJosa = (w) => (hasBatchim(w) ? "이" : "가");
const objJosa = (w) => (hasBatchim(w) ? "을" : "를");

// 문장 속 컬럼 이름 → 누르면 그 컬럼을 격자에서 하이라이트 (다른 시트면 전환)
function wordRef(text, ref) {
  if (!ref || !ref.includes("!")) return `<b>${esc(text)}</b>`;
  const [sh, a1] = ref.split("!");
  return `<span class="ref-word" data-ref-sheet="${esc(sh)}" data-ref-a1="${esc(a1)}">${esc(text)}</span>`;
}

function condPhrase(c) {
  const raw = c.target_name || c.target_ref;
  const name = wordRef(raw, c.target_ref);
  const j = subjJosa(raw);
  const v = esc(c.value);
  switch (c.operator) {
    case "=":  return `${name}${j} <b>‘${v}’</b>인`;
    case "<>": return `${name}${j} <b>‘${v}’</b>${subjJosa(c.value)} 아닌`;
    case ">":  return `${name}${j} <b>${v}</b> 초과인`;
    case ">=": return `${name}${j} <b>${v}</b> 이상인`;
    case "<":  return `${name}${j} <b>${v}</b> 미만인`;
    case "<=": return `${name}${j} <b>${v}</b> 이하인`;
    default:   return `${name} ${esc(c.operator)} <b>${v}</b>`;
  }
}

function namedFormula(m) {
  let f = m.formula.replace(/^=/, "");
  const reps = m.reads
    .map((r) => ({ ref: r.ref, bare: r.ref.split("!").pop(), name: r.name || r.ref.split("!").pop() }))
    .sort((a, b) => b.ref.length - a.ref.length);
  reps.forEach(({ ref, bare, name }) => {
    f = f.split(ref).join(name);
    f = f.replace(new RegExp("(?<![A-Za-z0-9_!.$])" + bare.replace(/\$/g, "\\$") + "(?![A-Za-z0-9_(])", "g"), name);
  });
  return f.replace(/\*/g, " × ").replace(/\//g, " ÷ ").replace(/\+/g, " + ").replace(/-/g, " − ");
}

// ── 복잡한 수식(IF·LEFT·& 등) → 일상 언어 해석 ──────────────
function tokenizeFormula(f) {
  const re = /"(?:[^"]|"")*"|\d+(?:\.\d+)?|<>|<=|>=|[()+\-*/&=<>%,]|[^\s()+\-*/&=<>%,]+/g;
  const out = []; let m;
  while ((m = re.exec(f)) !== null) out.push(m[0]);
  return out;
}
function parseFormula(tokens) {
  let i = 0;
  const peek = () => tokens[i];
  const eat = () => tokens[i++];
  function expr() { return comparison(); }
  function comparison() {
    let l = concat();
    while (["=", "<>", "<", ">", "<=", ">="].includes(peek())) l = { t: "op", op: eat(), l, r: concat() };
    return l;
  }
  function concat() {
    let l = add();
    while (peek() === "&") { eat(); l = { t: "op", op: "&", l, r: add() }; }
    return l;
  }
  function add() {
    let l = mul();
    while (peek() === "+" || peek() === "-") l = { t: "op", op: eat(), l, r: mul() };
    return l;
  }
  function mul() {
    let l = atom();
    while (peek() === "*" || peek() === "/") l = { t: "op", op: eat(), l, r: atom() };
    return l;
  }
  function atom() {
    const tk = peek();
    if (tk === undefined) return { t: "empty" };
    if (tk === "(") { eat(); const e = expr(); if (peek() === ")") eat(); return e; }
    if (/^"/.test(tk)) { eat(); return { t: "str", v: tk.replace(/^"|"$/g, "").replace(/""/g, '"') }; }
    if (/^\d/.test(tk)) return { t: "num", v: eat() };
    eat();
    if (peek() === "(") {
      eat(); const args = [];
      if (peek() !== ")") { args.push(expr()); while (peek() === ",") { eat(); args.push(expr()); } }
      if (peek() === ")") eat();
      return { t: "fn", name: tk.toUpperCase(), args };
    }
    return { t: "ref", v: tk };
  }
  try { return expr(); } catch (e) { return null; }
}
function condText(node, rm) {
  if (node && node.t === "op" && ["=", "<>", "<", ">", "<=", ">="].includes(node.op)) {
    const l = trNode(node.l, rm), r = trNode(node.r, rm);
    return ({ "=": `${l}가 ${r}이면`, "<>": `${l}가 ${r}이 아니면`, ">": `${l}가 ${r}보다 크면`,
      "<": `${l}가 ${r}보다 작으면`, ">=": `${l}가 ${r} 이상이면`, "<=": `${l}가 ${r} 이하이면` })[node.op];
  }
  return `${trNode(node, rm)}이면`;
}
function trFn(node, rm) {
  const a = node.args.map((x) => trNode(x, rm));
  switch (node.name) {
    case "IF": return `${condText(node.args[0], rm)} → ${a[1]}, 아니면 → ${a[2]}`;
    case "IFS": { let s = ""; for (let k = 0; k + 1 < node.args.length; k += 2) s += `${condText(node.args[k], rm)} → ${a[k + 1]}; `; return s.trim(); }
    case "IFERROR": return `${a[0]} (오류가 나면 ${a[1]})`;
    case "LEFT": return `${a[0]}의 왼쪽 ${a[1] || "1"}글자`;
    case "RIGHT": return `${a[0]}의 오른쪽 ${a[1] || "1"}글자`;
    case "MID": return `${a[0]}의 ${a[1]}번째부터 ${a[2]}글자`;
    case "LEN": return `${a[0]}의 글자 수`;
    case "ROUND": return `${a[0]}을(를) 소수 ${a[1]}자리로 반올림한 값`;
    case "ROUNDUP": return `${a[0]}을(를) 소수 ${a[1]}자리로 올림한 값`;
    case "ROUNDDOWN": return `${a[0]}을(를) 소수 ${a[1]}자리로 버림한 값`;
    case "SUM": return `${a.join(", ")}의 합계`;
    case "MAX": return `${a.join(", ")} 중 가장 큰 값`;
    case "MIN": return `${a.join(", ")} 중 가장 작은 값`;
    case "AVERAGE": return `${a.join(", ")}의 평균`;
    case "ABS": return `${a[0]}의 절댓값`;
    case "CONCATENATE": return `${a.join("와 ")}를 이어붙인 값`;
    case "VLOOKUP": return `${a[1]}에서 ${a[0]}을(를) 찾아 ${a[2]}번째 열의 값`;
    default: return `${esc(node.name)}(${a.join(", ")})`;
  }
}
function trNode(node, rm) {
  if (!node) return "";
  switch (node.t) {
    case "str": return `‘${esc(node.v)}’`;
    case "num": return esc(node.v);
    case "ref": return `<b>${esc(rm[node.v] || rm[node.v.split("!").pop()] || node.v)}</b>`;
    case "op": {
      if (node.op === "&") {          // 연속 이어붙이기는 하나로 펼침
        const parts = [];
        (function collect(n) {
          if (n.t === "op" && n.op === "&") { collect(n.l); collect(n.r); }
          else parts.push(trNode(n, rm));
        })(node);
        return `${parts.join(", ")}를 이어붙인 값`;
      }
      const l = trNode(node.l, rm), r = trNode(node.r, rm);
      switch (node.op) {
        case "+": return `${l} + ${r}`;
        case "-": return `${l} − ${r}`;
        case "*": return `${l} × ${r}`;
        case "/": return `${l} ÷ ${r}`;
        default: return `${l} ${esc(node.op)} ${r}`;
      }
    }
    case "fn": return trFn(node, rm);
    default: return "";
  }
}
function interpretFormula(formula, m) {
  try {
    const rm = {};
    if (m) m.reads.forEach((r) => {
      if (!r.name) return;
      rm[r.ref] = r.name;
      rm[r.ref.split("!").pop()] = r.name;
    });
    const ast = parseFormula(tokenizeFormula(String(formula).replace(/^=/, "")));
    if (!ast) return null;
    const out = trNode(ast, rm).trim();
    return out || null;
  } catch (e) { return null; }
}

function explainSentence(m, rules, keys) {
  const aggFn = m.functions.find((f) => AGG_VERB[f]);
  if (aggFn) {
    const srcSheet = esc((m.reads[0]?.ref || "").split("!")[0] || m.sheet);
    const isCount = aggFn.startsWith("COUNT");
    const targetRaw = isCount ? "행의 개수" : (m.reads[0]?.name || "값");
    const targetWord = isCount ? `<b>${esc(targetRaw)}</b>` : wordRef(targetRaw, m.reads[0]?.ref);
    const target = `${targetWord}${objJosa(targetRaw)}`;
    const conds = [
      ...keys.map((c) => {
        const nm = c.target_name || c.target_ref;
        return `${wordRef(nm, c.target_ref)}${subjJosa(nm)} 같고`;
      }),
      ...rules.map(condPhrase),
    ];
    const condStr = conds.length ? conds.join(", ") + " 행의 " : "";
    return `<b>${srcSheet}</b> 시트에서 ${condStr}${target} ${AGG_VERB[aggFn]} 값이에요.`;
  }
  if (m.functions.length === 0) {
    return `이 값은 아래 계산식으로 나와요: <span class="named-formula">${esc(namedFormula(m))}</span>`;
  }
  const interp = interpretFormula(m.formula, m);
  if (interp) return interp;
  return `이 값은 이렇게 계산돼요: <span class="named-formula">${esc(namedFormula(m))}</span>`;
}

// 계산법 섹션 내용: 쉬운 설명 + 실제 수식 (+ 상수)
function methodContent(m) {
  const rules = m.conditions.filter((c) => c.kind === "business_rule");
  const keys = m.conditions.filter((c) => c.kind === "match_key");
  let h = `<p class="blk-lead">${explainSentence(m, rules, keys)}</p>`;
  h += `<p class="blk-code">${esc(m.formula)}</p>`;
  if (!m.functions.find((f) => AGG_VERB[f]) && m.constants.length) {
    const notes = m.constants.map((x) => {
      const n = Number(x);
      const pct = n > 0 && n < 1 ? ` (${+(n * 100).toFixed(4)}%)` : "";
      return `<b>${esc(x)}</b>${pct}`;
    }).join(", ");
    h += `<p class="blk-note">수식에 박힌 숫자 · ${notes}</p>`;
  }
  return h;
}
function rulesBlk(rules) {
  if (!rules.length) return "";
  return blk("숨은 규칙 · 수식에만 있는 조건",
    `<ul class="blk-list">${rules.map((c) => `<li>${condPhrase(c)} 것만 포함</li>`).join("")}</ul>`, "card");
}
// 지표의 각 행은 같은 패턴(행만 이동)이라, 보고 있는 행에 맞춰 참조 행을 옮긴다.
// reads는 앵커(맨 윗 행) 기준으로 저장돼 있어, 그대로 쓰면 어느 행을 봐도 앵커 참조가 나온다.
function readsForRow(m, r) {
  const anchorRow = +(/(\d+)/.exec((m.anchor_cell || "").split("!").pop()) || [])[1];
  if (!anchorRow || !r || r === anchorRow) return m.reads;
  const d = r - anchorRow;
  return m.reads.map((rd) => {
    const [sh, a1] = rd.ref.split("!");
    // 절대행($6)과 행 없는 범위(G:G)는 고정, 상대 행 참조만 이동
    const shifted = a1.replace(/(\$?)([A-Z]{1,3})(\$?)(\d+)/g,
      (mm, dc, col, dr, row) => dr ? mm : `${dc}${col}${(+row) + d}`);
    return Object.assign({}, rd, { ref: `${sh}!${shifted}` });
  });
}

function refsBlk(m, r) {
  const seen = new Set(), rows = [];
  readsForRow(m, r).forEach((rd) => {
    if (seen.has(rd.ref)) return;
    seen.add(rd.ref);
    const [sh, a1] = rd.ref.split("!");
    rows.push({ ref: rd.ref, sheet: sh, a1, name: rd.name });
  });
  if (!rows.length) return "";
  return blk("참조하는 셀",
    `<div class="reflist">${rows.map((r) =>
      `<button class="refrow" data-ref-sheet="${esc(r.sheet)}" data-ref-a1="${esc(r.a1)}" data-ref-name="${esc(r.name || "")}" data-ref-from="${esc(m.id)}">
        <span class="rf-name">${esc(r.name || "(이름 미확인)")}</span>
        <span class="rf-loc">${esc(r.ref)}</span>
      </button>`).join("")}</div>`, "");
}
function flowBlk(m) {
  if (!m.depends_on.length && !m.used_by.length) return "";
  const chips = (ids) => ids.map((id) => {
    const t = METRIC_BY_ID[id];
    return `<span class="chip link" data-goto="${esc(id)}">${esc(t ? (t.title || id) : id)}</span>`;
  }).join("");
  let c = "";
  if (m.depends_on.length) c += `<div class="flow-row"><span class="flow-k">재료</span><span class="chips">${chips(m.depends_on)}</span></div>`;
  if (m.used_by.length) c += `<div class="flow-row"><span class="flow-k">쓰이는 곳</span><span class="chips">${chips(m.used_by)}</span></div>`;
  return blk("값의 흐름", c, "");
}
function devBlk(m) {
  if (!(m.md_key && DATA.bundle_md[m.md_key])) return "";
  return `<details class="dev"><summary>개발자용 · OKF 문서 원문</summary><pre>${esc(DATA.bundle_md[m.md_key])}</pre></details>`;
}
function colLinkBlk(m) {
  return `<div class="collink"><span class="chip link" data-goto="${esc(m.id)}">‘${esc(m.title || m.id)}’ 열 전체 보기</span></div>`;
}

// ── 함수 사전 + Python 변환 ──────────────────────────────────
const FUNC_DOC = {
  SUM: { how: "범위의 숫자를 모두 더해요.", syntax: "SUM(숫자1, [숫자2], …)" },
  SUMIF: { how: "조건 하나에 맞는 행의 값만 더해요.", syntax: "SUMIF(조건범위, 조건, [합계범위])" },
  SUMIFS: { how: "여러 조건을 모두 만족하는 행의 값만 더해요.", syntax: "SUMIFS(합계범위, 조건범위1, 조건1, [조건범위2, 조건2], …)" },
  COUNT: { how: "숫자가 든 칸의 개수를 세요.", syntax: "COUNT(값1, [값2], …)" },
  COUNTA: { how: "비어 있지 않은 칸의 개수를 세요.", syntax: "COUNTA(값1, [값2], …)" },
  COUNTIF: { how: "조건에 맞는 칸의 개수를 세요.", syntax: "COUNTIF(범위, 조건)" },
  COUNTIFS: { how: "여러 조건을 모두 만족하는 칸의 개수를 세요.", syntax: "COUNTIFS(조건범위1, 조건1, [조건범위2, 조건2], …)" },
  AVERAGE: { how: "범위의 평균을 내요.", syntax: "AVERAGE(숫자1, [숫자2], …)" },
  AVERAGEIF: { how: "조건에 맞는 값의 평균을 내요.", syntax: "AVERAGEIF(조건범위, 조건, [평균범위])" },
  AVERAGEIFS: { how: "여러 조건을 만족하는 값의 평균을 내요.", syntax: "AVERAGEIFS(평균범위, 조건범위1, 조건1, …)" },
  MAX: { how: "범위에서 가장 큰 값을 골라요.", syntax: "MAX(숫자1, [숫자2], …)" },
  MAXIFS: { how: "조건에 맞는 값 중 가장 큰 값을 골라요.", syntax: "MAXIFS(최댓값범위, 조건범위1, 조건1, …)" },
  MIN: { how: "범위에서 가장 작은 값을 골라요.", syntax: "MIN(숫자1, [숫자2], …)" },
  MINIFS: { how: "조건에 맞는 값 중 가장 작은 값을 골라요.", syntax: "MINIFS(최솟값범위, 조건범위1, 조건1, …)" },
  IF: { how: "조건이 참이면 A, 거짓이면 B를 돌려줘요.", syntax: "IF(조건, 참일_때, 거짓일_때)" },
  IFS: { how: "여러 조건을 차례로 검사해 처음 맞는 값을 돌려줘요.", syntax: "IFS(조건1, 값1, [조건2, 값2], …)" },
  IFERROR: { how: "계산이 오류면 대체 값을 돌려줘요.", syntax: "IFERROR(값, 오류일_때)" },
  ROUND: { how: "지정한 자리에서 반올림해요.", syntax: "ROUND(숫자, 자릿수)" },
  ROUNDUP: { how: "지정한 자리에서 올림해요.", syntax: "ROUNDUP(숫자, 자릿수)" },
  ROUNDDOWN: { how: "지정한 자리에서 버림해요.", syntax: "ROUNDDOWN(숫자, 자릿수)" },
  VLOOKUP: { how: "표 첫 열에서 값을 찾아 같은 행의 다른 열 값을 가져와요.", syntax: "VLOOKUP(찾을값, 표범위, 열번호, [정확도])" },
  HLOOKUP: { how: "표 첫 행에서 값을 찾아 같은 열의 다른 행 값을 가져와요.", syntax: "HLOOKUP(찾을값, 표범위, 행번호, [정확도])" },
  XLOOKUP: { how: "범위에서 값을 찾아 대응하는 값을 가져와요.", syntax: "XLOOKUP(찾을값, 찾을범위, 반환범위, [없을때])" },
  INDEX: { how: "행·열 번호로 표에서 값을 꺼내요.", syntax: "INDEX(범위, 행번호, [열번호])" },
  MATCH: { how: "범위에서 값의 위치(번호)를 찾아요.", syntax: "MATCH(찾을값, 범위, [일치유형])" },
  LEN: { how: "글자 수를 세요.", syntax: "LEN(문자열)" },
  LEFT: { how: "왼쪽에서 몇 글자를 잘라요.", syntax: "LEFT(문자열, [개수])" },
  RIGHT: { how: "오른쪽에서 몇 글자를 잘라요.", syntax: "RIGHT(문자열, [개수])" },
  MID: { how: "가운데에서 몇 글자를 잘라요.", syntax: "MID(문자열, 시작위치, 개수)" },
  TODAY: { how: "오늘 날짜를 돌려줘요.", syntax: "TODAY()" },
  ABS: { how: "절댓값(부호를 뗀 값)을 돌려줘요.", syntax: "ABS(숫자)" },
};

function functionsInFormula(f) {
  return [...new Set((String(f).match(/\b[A-Z][A-Z0-9_.]+(?=\s*\()/g) || []).map((s) => s.toUpperCase()))];
}

const PY_AGG = { SUMIFS: "sum", SUMIF: "sum", SUM: "sum", COUNTIFS: "count", COUNTIF: "count",
  AVERAGEIFS: "mean", AVERAGEIF: "mean", AVERAGE: "mean", MAXIFS: "max", MAX: "max", MINIFS: "min", MIN: "min" };

function pythonize(m) {
  const aggFn = m.functions.find((f) => PY_AGG[f]);
  if (aggFn) {
    const src = (m.reads[0]?.ref || "").split("!")[0] || m.sheet;
    const target = m.reads[0]?.name || "값";
    const conds = m.conditions.map((c) => {
      const nm = c.target_name || c.target_ref;
      const op = c.operator === "=" ? "==" : c.operator === "<>" ? "!=" : c.operator;
      const rhs = c.kind === "match_key" ? `이_행["${nm}"]` : `"${c.value}"`;
      return `row["${nm}"] ${op} ${rhs}`;
    });
    const cond = conds.length ? ` if ${conds.join(" and ")}` : "";
    const kind = PY_AGG[aggFn];
    if (kind === "count") return `sum(1 for row in ${src}${cond})`;
    return `${kind}(row["${target}"] for row in ${src}${cond})`;
  }
  // 사칙연산만: 함수 호출도, 문자 연결(&)도 없을 때만 정확히 변환 가능
  if (m.functions.length === 0 && !m.formula.includes("&")) {
    return namedFormula(m).replace(/ × /g, " * ").replace(/ ÷ /g, " / ").replace(/ − /g, " - ");
  }
  return null;   // 복잡한 수식은 정확히 못 바꿈 → 지어내지 않는다
}

// 계산 방식 덩어리의 조각들
function calcSub(label, codeCls, code) {
  return `<div class="calc-sub"><p class="vd-k">${esc(label)}</p>`
    + `<div class="vd-formula-row"><code class="${codeCls}">${esc(code)}</code>`
    + `<button class="copy-btn" data-copy="${esc(code)}" title="${esc(label)} 복사">복사</button></div></div>`;
}
function funcItems(funcs) {
  return funcs.map((fn) => {
    const d = FUNC_DOC[fn];
    const how = d ? d.how : "엑셀 함수";
    const syntax = d ? d.syntax : `${fn}(…)`;
    return `<div class="func-item"><span class="func-name">${esc(fn)}</span>`
      + `<span class="func-body"><span class="func-how">${esc(how)}</span>`
      + `<code class="func-syntax">${esc(syntax)}</code></span></div>`;
  }).join("");
}
// 쉬운 설명 · 계산식 · 쓰인 함수 · Python을 한 덩어리로
function calcChunk(m, formula, manual) {
  const rules = m.conditions.filter((c) => c.kind === "business_rule");
  const keys = m.conditions.filter((c) => c.kind === "match_key");
  const isAgg = !!m.functions.find((f) => AGG_VERB[f]);
  let inner = "";
  if (manual) inner += `<p class="chunk-note">이 칸은 수기값이에요. 아래는 <b>이 열의 다른 칸</b>이 계산되는 방식이에요.</p>`;
  inner += `<p class="blk-lead">${explainSentence(m, rules, keys)}</p>`;
  if (!isAgg && m.constants.length) {
    const notes = m.constants.map((x) => {
      const n = Number(x);
      const pct = n > 0 && n < 1 ? ` (${+(n * 100).toFixed(4)}%)` : "";
      return `${esc(x)}${pct}`;
    }).join(", ");
    inner += `<p class="blk-note">수식에 박힌 숫자 · ${notes}</p>`;
  }
  inner += calcSub("계산식", "vd-formula", formula);
  if (m.functions.length) inner += `<div class="calc-sub"><p class="vd-k">쓰인 함수</p>${funcItems(m.functions)}</div>`;
  const py = pythonize(m);
  inner += py
    ? calcSub("Python으로 보면", "py-code", py)
    : `<div class="calc-sub"><p class="vd-k">Python으로 보면</p><p class="blk-note">함수 구성이 복잡해 정확한 Python 변환은 만들지 않았어요. (틀린 코드를 지어내지 않기 위해서예요.)</p></div>`;
  return blk("계산 방식", inner, "card");
}

// 행 라벨: 식별 열 값들을 이어붙임
function rowLabelAt(sheet, reg, r) {
  if (!reg || !reg.key_cols.length) return "";
  const parts = [];
  reg.key_cols.forEach((L) => {
    const c = sheet.col_letters.indexOf(L) + 1;
    const cell = c > 0 && sheet.cells[r - 1] && sheet.cells[r - 1][c - 1];
    if (cell && cell.v) parts.push(cell.v);
  });
  return parts.join(" / ");
}

// 이 값이 어느 행의 것인지 (참조 셀 누르면 그 셀로 포커스 이동)
function rowIdentityBlk(sheet, reg, r, fromId) {
  if (!reg || !reg.key_cols.length) return "";
  const rows = reg.key_cols.map((L) => {
    const c = sheet.col_letters.indexOf(L) + 1;
    const v = (c > 0 && sheet.cells[r - 1] && sheet.cells[r - 1][c - 1] || {}).v || "";
    const name = reg.columns[L] || L;
    const ref = `${L}${r}`;
    return `<div class="idrow"><span class="id-k">${esc(name)}</span><span class="id-v">${esc(v)}</span>`
      + `<button class="id-ref-btn" data-ref-sheet="${esc(sheet.name)}" data-ref-a1="${esc(ref)}" data-ref-name="${esc(name)}" data-ref-from="${esc(fromId)}" title="이 셀로 이동">${esc(ref)}</button></div>`;
  }).join("");
  return blk("이 값은 어느 행의 것인지", rows, "");
}

// 열(계산 값) 전체 뷰
function interpMetric(m) {
  if (!m) return `<p class="muted">항목을 찾을 수 없습니다.</p>`;
  let h = ihead(
    `${scopePill("열 전체")} ${esc(m.sheet)} 시트 · ${esc(colLetterOf(m.anchor_cell))}열 <span class="tag ok">자동 계산</span>`,
    m.title || m.id, `${m.applies_to.split("!").pop()} · ${m.confidence.row_repeat}개 행`);
  h += calcChunk(m, m.formula, false);
  if (m.manual_overrides.length) {
    const items = m.manual_overrides.map((o) =>
      `<div class="mo"><span class="mo-cell">${esc(o.cell.split("!").pop())}</span>
        ${o.row_label ? `<span class="mo-lbl">${esc(o.row_label)}</span>` : ""}
        <span class="mo-val">${esc(fmtNum(o.value))}</span></div>`).join("");
    h += blk(`사람이 손댄 칸 ${m.manual_overrides.length}곳 · 자동 계산 아님`, items, "card warn");
  }
  h += refsBlk(m);
  h += flowBlk(m);
  h += devBlk(m);
  return h;
}

// 한 칸(셀) 뷰 — 계산 값(metric)이면 풍부하게, 순수 입력 값이면 간단하게
function interpCell(sheet, r, c, metricId) {
  const m = metricId ? METRIC_BY_ID[metricId] : null;
  const colL = sheet.col_letters[c - 1];
  const cellRef = `${colL}${r}`;
  const fullRef = `${sheet.name}!${cellRef}`;
  const cell = (sheet.cells[r - 1] && sheet.cells[r - 1][c - 1]) || {};
  const raw = String(cell.v || "");
  const isFormula = cell.t === "f";   // 값이 '='로 시작해도 텍스트일 수 있음 (백엔드 유형 사용)
  const reg = m ? sheet.regions.find((rg) => rg.a1 === m.region)
                : sheet.regions.find((rg) => rg.r0 <= r && r <= rg.r1 && rg.c0 <= c && c <= rg.c1);
  const rules = m ? m.conditions.filter((cc) => cc.kind === "business_rule") : [];
  const keys = m ? m.conditions.filter((cc) => cc.kind === "match_key") : [];
  const override = m ? m.manual_overrides.find((o) => o.cell === fullRef) : null;

  const titleReg = (cell.title_of != null) ? sheet.regions[cell.title_of] : null;
  const title = m ? (m.title || m.id)
              : titleReg ? (titleReg.title || raw)
              : ((reg && reg.columns[colL]) || `${colL}열`);
  const tag = titleReg ? `<span class="tag">표 제목</span>`
            : override ? `<span class="tag warn">수기 입력</span>`
            : (m || isFormula) ? `<span class="tag ok">자동 계산</span>`
            : `<span class="tag">직접 입력</span>`;
  const addr = `<button class="addr-btn" data-focus-sheet="${esc(sheet.name)}" data-focus-r="${r}" data-focus-c="${c}" data-focus-metric="${esc(metricId || "")}" title="이 셀로 돌아가기">${esc(sheet.name)} 시트 · ${esc(cellRef)}</button>`;
  let h = ihead(`${scopePill("셀 하나")} ${addr} ${tag}`, title, null);

  // 1) 이 칸의 값
  const shownVal = String(override ? fmtNum(override.value)
    : ((cell.cv !== undefined && cell.cv !== null) ? cell.cv : raw));
  const isNumeric = /^[\d,.\-]+$/.test(shownVal);
  const copyVal = isNumeric ? shownVal.replace(/,/g, "") : shownVal;
  const longText = !isNumeric && shownVal.length > 24;   // 긴 글은 큰 숫자체 대신 읽기체
  let vb = `<div class="cval-row${longText ? " tall" : ""}"><span class="cell-value${longText ? " text" : ""}">${esc(shownVal || "(빈 칸)")}</span>`
    + `<button class="copy-btn" data-copy="${esc(copyVal)}" title="값 복사">복사</button></div>`;
  if (override) vb += `<p class="vd-manual">사람이 직접 넣은 값이에요. 수식으로 자동 계산된 게 아니에요.</p>`;
  else if (!isFormula) vb += `<p class="vd-manual">직접 입력된 값이에요 (수식 아님).</p>`;
  h += blk("이 칸의 값", vb, "card");

  // 표 제목으로 추측된 칸이면 그 사실 + 대상 표 링크
  if (titleReg) {
    h += blk("이 칸의 쓰임",
      `<p class="blk-lead">이 칸은 아래 표의 <b>제목으로 추측</b>돼요. 단독으로 떠 있는 텍스트라 데이터가 아니라 표 이름으로 판단했어요.</p>`
      + `<div class="collink"><span class="chip link" data-region="${titleReg.id}">‘${esc(titleReg.title || titleReg.a1.split("!")[1])}’ 표 보기</span></div>`,
      "card");
  }

  // 2) 이 값은 어느 행의 것인지
  h += rowIdentityBlk(sheet, reg, r, metricId || "");

  // 3) 계산 방식 — 설명·계산식·함수·Python 한 덩어리
  if (m) {
    h += calcChunk(m, override ? m.formula : raw, !!override);
  } else if (isFormula) {
    let inner = "";
    const interp = interpretFormula(raw, null);
    if (interp) inner += `<p class="blk-lead">${interp}</p>`;
    inner += calcSub("계산식", "vd-formula", raw);
    const funcs = functionsInFormula(raw);
    if (funcs.length) inner += `<div class="calc-sub"><p class="vd-k">쓰인 함수</p>${funcItems(funcs)}</div>`;
    h += blk("계산 방식", inner, "card");
  }

  // 4) 계산 값이면 참조 · 흐름까지 (참조는 '보고 있는 행' 기준으로)
  if (m) {
    h += refsBlk(m, r);
    h += flowBlk(m);
    h += devBlk(m);
    h += colLinkBlk(m);
  }
  return h;
}

// 기능 쇼케이스: 화면에 들어오면 항목별로 스태거 등장 (안전장치 포함)
let _showcaseIO = null, _showcaseFb = null;
function initShowcase() {
  const root = $("landing");
  const wrap = document.querySelector(".features");
  const feats = Array.prototype.slice.call(document.querySelectorAll(".feat"));
  if (!wrap || !feats.length) return;
  feats.forEach((f) => { f.classList.remove("in"); f.style.transitionDelay = ""; });
  const reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (reduce || !("IntersectionObserver" in window)) { wrap.classList.remove("anim"); return; }
  wrap.classList.add("anim");
  const show = (f, i) => { f.style.transitionDelay = Math.min(i, 4) * 80 + "ms"; f.classList.add("in"); };
  if (_showcaseIO) _showcaseIO.disconnect();
  _showcaseIO = new IntersectionObserver((entries) => {
    entries.forEach((e) => {
      if (!e.isIntersecting) return;
      show(e.target, feats.indexOf(e.target));
      _showcaseIO.unobserve(e.target);
    });
  }, { root: root, threshold: 0.2 });
  feats.forEach((f) => _showcaseIO.observe(f));
  // 관측이 안 되는 환경에서도 절대 숨은 채로 남지 않게
  clearTimeout(_showcaseFb);
  _showcaseFb = setTimeout(() => feats.forEach((f, i) => { if (!f.classList.contains("in")) show(f, i); }), 1800);
}

// ── 시작 화면 이벤트 ─────────────────────────────────────────
function initLanding() {
  const dz = $("dropzone");
  const input = $("fileInput");
  dz.onclick = () => input.click();
  input.onchange = () => { if (input.files[0]) analyzeFile(input.files[0]); };
  dz.addEventListener("dragover", (e) => { e.preventDefault(); dz.classList.add("drag"); });
  dz.addEventListener("dragleave", () => dz.classList.remove("drag"));
  dz.addEventListener("drop", (e) => {
    e.preventDefault();
    dz.classList.remove("drag");
    if (e.dataTransfer.files[0]) analyzeFile(e.dataTransfer.files[0]);
  });

  $("sampleBtn").onclick = analyzeSample;
  $("resetBtn").onclick = () => {
    hide($("results"));
    hide($("topResults"));
    show($("topTag"));
    show($("landing"));
    $("fileInput").value = "";
    initShowcase();                 // 첫 화면 돌아오면 다시 등장
  };

  // 내보내기 드로어 (파일 전체: AI에게 넘기기 + OKF)
  $("exportBtn").onclick = openExport;
  $("exportClose").onclick = closeExport;
  $("exportOverlay").onclick = (e) => { if (e.target.id === "exportOverlay") closeExport(); };
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeExport(); });

  initShowcase();
}

initLanding();
