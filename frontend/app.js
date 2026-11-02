// FINN frontend: vanilla JS, no build step. Four tabs: Brief (push), Ask (pull), Holdings, Rules.
const $ = (s, el = document) => el.querySelector(s);
const api = async (p, opt) => { const r = await fetch(p, opt); const j = await r.json(); if (!r.ok) throw new Error(j.error || r.status); return j; };
const post = (p, body) => api(p, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const fmt = n => n == null ? "–" : Math.abs(n) >= 1e5 ? (n / 1e5).toFixed(2) + "L" : Math.abs(n) >= 1000 ? Math.round(n).toLocaleString("en-IN") : (+n).toFixed(n % 1 ? 1 : 0);
const pct = n => n == null ? "–" : `<span class="${n >= 0 ? "pos" : "neg"}">${n >= 0 ? "+" : ""}${(+n).toFixed(1)}%</span>`;
const ago = d => { const m = (Date.now() - new Date(d)) / 6e4; return m < 60 ? `${m | 0}m` : m < 1440 ? `${(m / 60) | 0}h` : `${(m / 1440) | 0}d`; };
const skel = (n = 3) => Array(n).fill('<div class="skel"></div>').join("");
const store = { get: (k, d) => { try { return JSON.parse(localStorage.getItem(k)) ?? d } catch { return d } }, set: (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)) } catch { } } };

function spark(vals, w = 80, h = 26) {
  const v = (vals || []).filter(x => x != null); if (v.length < 2) return "";
  const mn = Math.min(...v), mx = Math.max(...v), r = mx - mn || 1;
  const pts = v.map((x, i) => `${(i / (v.length - 1) * w).toFixed(1)},${(h - (x - mn) / r * h).toFixed(1)}`).join(" ");
  const c = v.at(-1) >= v[0] ? "var(--pos)" : "var(--neg)";
  return `<svg class="sp" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}"><polyline fill="none" stroke="${c}" stroke-width="1.6" points="${pts}"/></svg>`;
}
function bars(vals, labels, w = 300, h = 70) {
  const v = vals.map(x => x ?? 0); if (!v.length) return ""; const mx = Math.max(...v.map(Math.abs)) || 1, bw = w / v.length;
  return `<svg width="100%" viewBox="0 0 ${w} ${h + 14}">${v.map((x, i) => `<rect x="${i * bw + 2}" y="${h - Math.max(0, x) / mx * h}" width="${bw - 4}" height="${Math.abs(x) / mx * h}" rx="2" fill="${x >= 0 ? "var(--acc)" : "var(--neg)"}" opacity="${i === v.length - 1 ? 1 : .55}"/>`).join("")}
  <text x="0" y="${h + 12}" font-size="9" fill="var(--sub)">${esc(labels[0] || "")}</text><text x="${w}" y="${h + 12}" font-size="9" fill="var(--sub)" text-anchor="end">${esc(labels.at(-1) || "")}</text></svg>`;
}

let META = {}, DIGEST = null, TAB = "brief";
const done = new Set(store.get("finn-done", []));
$("#today").textContent = new Date().toLocaleDateString("en-IN", { weekday: "long", day: "numeric", month: "long" });

document.querySelectorAll(".tabs button").forEach(b => b.onclick = () => go(b.dataset.tab));
function go(tab, arg) {
  TAB = tab; document.querySelectorAll(".tabs button").forEach(b => b.classList.toggle("on", b.dataset.tab === tab));
  $("#title").textContent = { brief: "Brief", ask: "Ask", book: "Holdings", rules: "Rules" }[tab];
  $("#listen").style.visibility = tab === "brief" ? "visible" : "hidden";
  ({ brief: renderBrief, ask: renderAsk, book: renderBook, rules: renderRules })[tab](arg); scrollTo(0, 0);
}

// ── BRIEF (push) ─────────────────────────────────────────────────────────────
const WINDOWS = [[24, "24h"], [72, "3 days"], [168, "Week"], [720, "30 days"]];
let HOURS = store.get("finn-hours", 24);
async function renderBrief() {
  const v = $("#view");
  v.innerHTML = `<div class="seg">${WINDOWS.map(([h, l]) => `<button data-h="${h}" class="${h == HOURS ? "on" : ""}">${l}</button>`).join("")}</div><div id="b">${skel(6)}</div>`;
  v.querySelectorAll(".seg button").forEach(b => b.onclick = () => { HOURS = +b.dataset.h; store.set("finn-hours", HOURS); renderBrief(); });
  try { DIGEST = await api(`/api/digest?hours=${HOURS}`); } catch (e) { $("#b").innerHTML = `<p class="neg">Couldn't load: ${esc(e.message)}</p>`; return; }
  if (TAB !== "brief") return;
  const d = DIGEST, open = d.needs_you.filter(i => !done.has(i.id));
  $("#b").innerHTML = `
   <div class="hero"><div class="big">${d.counts.filings} filings across your 18 names. <b>${open.length}</b> need you.</div>
     <div class="sm">${d.counts.signals} price/volume trigger${d.counts.signals == 1 ? "" : "s"} · ${d.counts.routine} routine filed away · ~${d.minutes_saved} min of reading saved · prices as of ${esc(d.price_as_of || "–")}</div></div>
   <h2><span>Needs you</span><span>${open.length ? "swipe ← when done" : ""}</span></h2>
   ${open.map(card).join("") || `<div class="hero sm">Inbox zero. Nothing material in this window.</div>`}
   ${d.glance.length ? `<h2>Worth a glance</h2>${d.glance.map(row).join("")}` : ""}
   ${d.routine.length ? `<h2>Routine · ${d.counts.routine}</h2>${d.routine.map(g => `<details class="rt"><summary>${esc(g.label)} × ${g.count}</summary>${g.items.map(row).join("")}</details>`).join("")}` : ""}
   ${d.coverage_gaps.length && HOURS > 72 ? `<p class="src">Coverage: ${d.coverage_gaps.map(g => `${g.symbol} (${esc(g.reason)})`).join(", ")}</p>` : ""}`;
  $("#b").querySelectorAll("[data-id]").forEach(el => { el.onclick = () => openItem(el.dataset.id); swipe(el); });
}
const ITEMS = () => DIGEST ? [...DIGEST.needs_you, ...DIGEST.glance, ...DIGEST.routine.flatMap(g => g.items)] : [];
function card(i) {
  if (i.kind === "signal") return `<div class="card" data-id="${esc(i.id)}"><div class="row1"><span><span class="tick">${i.symbol}</span> · ${esc(i.sector)}</span><span class="tag sig">${i.reasons.join(" + ")}</span></div>
    <div style="display:flex;justify-content:space-between;align-items:center"><div><div class="hd">${esc(i.headline)}</div><div class="sum">Close ₹${fmt(i.snap.close)} · ${pct(i.snap.change_pct)} · 1m ${pct(i.snap.ret_1m_pct)}</div></div>${spark(i.snap.spark)}</div></div>`;
  return `<div class="card" data-id="${esc(i.id)}"><div class="row1"><span><span class="tick">${i.symbol}</span> · ${ago(i.date)} ago</span><span class="tag ${i.weight >= 95 ? "hot" : ""}">${esc(i.label)}</span></div>
    <div class="hd">${esc(i.subject)}</div><div class="sum">${esc(i.summary || "")}</div>
    <div class="more">${i.type === "results" ? "See Revenue · EBITDA · PBT · PAT →" : i.type === "mgmt" ? "Who is it? →" : "Brief me →"}${i.related ? ` &nbsp;+${i.related.length} related` : ""}</div></div>`;
}
const row = i => `<div class="row" data-id="${esc(i.id)}"><span class="tick" style="width:84px">${i.symbol}</span><div class="grow"><div class="t1">${esc(i.label)}</div><div class="t2">${esc(i.summary || i.subject)}</div></div><span class="sub" style="font-size:12px">${ago(i.date)}</span></div>`;
function swipe(el) {
  let x0 = null, dx = 0;
  el.addEventListener("pointerdown", e => { x0 = e.clientX; dx = 0; });
  el.addEventListener("pointermove", e => { if (x0 == null) return; dx = e.clientX - x0; if (dx < -10) el.style.transform = `translateX(${dx}px)`; });
  const end = () => { if (x0 == null) return; if (dx < -90) { done.add(el.dataset.id); store.set("finn-done", [...done]); el.style.opacity = 0; setTimeout(() => el.remove(), 200); el.onclick = null; } else el.style.transform = ""; x0 = null; };
  el.addEventListener("pointerup", end); el.addEventListener("pointerleave", end);
}

// ── item sheet with enrichment ──────────────────────────────────────────────
async function openItem(id) {
  const i = ITEMS().find(x => x.id === id); if (!i) return;
  if (i.kind === "signal") return go("ask", i.symbol);
  sheet(`<div class="row1 sub"><span class="tick">${i.symbol}</span> · ${new Date(i.date).toLocaleString("en-IN")}</div><h1 style="font-size:26px;margin:8px 0">${esc(i.subject)}</h1>
    <p class="sub">${esc(i.summary)}</p><div id="en">${i.lane === 3 ? "" : skel(4)}</div>
    ${(i.related || []).map(r => `<div class="row"><div class="grow"><div class="t1">${esc(r.label)}</div><div class="t2">${esc(r.summary)}</div></div><a href="${r.url}" target="_blank">PDF</a></div>`).join("")}
    <p style="display:flex;gap:8px;margin-top:16px"><a class="btn ghost" href="${i.url}" target="_blank">Open filing</a><button class="btn ghost" onclick="closeSheet();go('ask','${i.symbol}')">Research ${i.symbol}</button></p>`);
  if (i.lane === 3) return;
  try { const e = await post("/api/enrich", i); $("#en").innerHTML = renderEnrich(e); }
  catch (err) { $("#en").innerHTML = `<p class="src">Enrichment unavailable: ${esc(err.message)}</p>`; }
}
function renderEnrich(e) {
  const d = e.data; if (!d) return `<p class="src">No model configured — showing the filing as-is.</p>`;
  if (e.kind === "results") {
    return `<div class="hero"><div class="row1"><span>${esc(d.period)} · ${esc(d.basis)} · ${d.unit}</span>${d.crosscheck ? `<span class="tag">${esc(d.crosscheck)}</span>` : ""}</div>
      <div class="grid4"><span class="h"></span><span class="h">This qtr</span><span class="h">QoQ</span><span class="h">YoY</span>
      ${d.metrics.map(m => `<span>${m.metric}</span>${m.cur == null ? `<span class="sub" style="grid-column:span 3">${esc(m.note || "n/a")}</span>` : `<b>${fmt(m.cur)}</b><span>${pct(m.qoq_pct)}</span><span>${pct(m.yoy_pct)}</span>`}`).join("")}</div>
      ${d.margin ? `<p class="sum" style="margin:10px 0 0">Operating margin ${d.margin.cur}% vs ${d.margin.prev}% last qtr, ${d.margin.yoy ?? "–"}% a year ago.</p>` : ""}
      ${d.stale ? `<p class="neg src">${esc(d.stale)}</p>` : ""}<div class="src">Source: ${esc(d.source)}</div></div>`;
  }
  if (e.kind === "mgmt") return (d.people || []).map(p => `<div class="person"><div class="av">${esc((p.name || "?").split(" ").map(x => x[0]).slice(0, 2).join(""))}</div><div>
      <div class="t1"><b>${esc(p.name)}</b> <span class="tag">${esc(p.change)}</span></div><div class="sub">${esc(p.role)}${p.effective ? " · from " + esc(p.effective) : ""}</div>
      ${p.bio ? `<p style="margin:6px 0">${esc(p.bio)}</p>` : ""}${p.prior?.length ? `<div class="sub">Previously: ${p.prior.map(esc).join(" · ")}</div>` : ""}
      ${p.reason ? `<div class="sub">Reason: “${esc(p.reason)}”</div>` : ""}<a href="${p.linkedin}" target="_blank">LinkedIn ↗</a></div></div>`).join("") || `<p class="src">No named person found in the filing.</p>`;
  return `<div class="hero"><div class="hd">${esc(d.headline)}</div>${(d.numbers || []).map(n => `<span class="chip">${esc(n)}</span>`).join(" ")}<p class="sum">${esc(d.so_what)}</p></div>`;
}
function sheet(html) { $("#sheet-body").innerHTML = html; $("#sheet").hidden = false; }
function closeSheet() { $("#sheet").hidden = true; }
$("#sheet").onclick = e => { if (e.target.id === "sheet") closeSheet(); };

// ── ASK (pull) ──────────────────────────────────────────────────────────────
async function renderAsk(sym) {
  const v = $("#view");
  v.innerHTML = `<div class="askbar"><input id="q" placeholder="Any NSE company… e.g. Asian Paints" autocomplete="off"><button class="btn" id="mic" title="Speak">🎙</button></div>
   <div class="chips" id="sugg">${(META.research_demo || []).map(s => `<button class="chip" data-s="${s}">${s}</button>`).join("")}</div><div id="r"></div>`;
  v.querySelectorAll("[data-s]").forEach(b => b.onclick = () => dossier(b.dataset.s));
  const q = $("#q"); let t;
  q.oninput = () => { clearTimeout(t); t = setTimeout(async () => { if (q.value.length < 2) return; const res = await api(`/api/search?q=${encodeURIComponent(q.value)}`).catch(() => []); $("#sugg").innerHTML = res.slice(0, 6).map(s => `<button class="chip" data-s="${s.symbol}">${esc(s.name)}</button>`).join(""); $("#sugg").querySelectorAll("[data-s]").forEach(b => b.onclick = () => dossier(b.dataset.s)); }, 250); };
  q.onkeydown = e => { if (e.key === "Enter") { const f = $("#sugg [data-s]"); dossier(f ? f.dataset.s : q.value.trim().toUpperCase()); } };
  $("#mic").onclick = () => voice(txt => { q.value = txt; q.oninput(); });
  if (sym) dossier(sym);
}
async function dossier(sym) {
  const r = $("#r"); r.innerHTML = skel(8);
  let c; try { c = await api(`/api/research/${sym}/core`); } catch (e) { r.innerHTML = `<p class="neg">Couldn't find ${esc(sym)} on screener.in.</p>`; return; }
  const s = c.snapshot, t = c.trajectory, b = c.balance, k = c.cash, p = c.price;
  r.innerHTML = `
   <div class="hero"><div class="row1"><span class="tick">${c.symbol}</span><span>${c.consolidated ? "consolidated" : "standalone"}</span></div>
     <div class="big">${esc(c.name)}</div>
     <div class="sm">₹${fmt(s.price)} ${p ? pct(p.change_pct) : ""} · Mcap ₹${fmt(s.market_cap)} cr · P/E ${fmt(s.pe)} · ROCE ${fmt(s.roce)}%</div>
     <div class="chips">${c.verdict.map(v => `<span class="chip ${v.tone}">${v.k}<b>${esc(v.v)}</b></span>`).join("")}</div></div>
   <h2>Business</h2><div class="hero" id="biz">${skel(3)}</div>
   <h2><span>Numbers</span><span>${esc(s.latest_quarter || "")}</span></h2>
   <div class="hero"><div class="grid4" style="grid-template-columns:1fr 1fr 1fr">
     <div><div class="h sub">Revenue TTM</div><b>₹${fmt(s.revenue_ttm)} cr</b></div><div><div class="h sub">PAT TTM</div><b>₹${fmt(s.pat_ttm)} cr</b></div><div><div class="h sub">${c.lender ? "Fin. margin" : "OPM"} FY</div><b>${fmt(s.opm_fy)}%</b></div>
     <div><div class="h sub">Qtr rev YoY</div>${pct(s.q_rev_yoy)}</div><div><div class="h sub">Qtr PAT YoY</div>${pct(s.q_pat_yoy)}</div><div><div class="h sub">${c.lender ? "ROE" : "Debt/Equity"}</div><b>${c.lender ? fmt(s.roe) + "%" : fmt(s.debt_to_equity) + "x"}</b></div></div></div>
   <h2>What the numbers say</h2><div class="hero">${c.flags.map(f => `<div class="flag ${f.tone}">${esc(f.text)}</div>`).join("") || `<span class="sub">No strong signals either way.</span>`}</div>
   <h2>Trajectory · quarterly revenue</h2><div class="hero">${bars(t.quarters.revenue, t.quarters.cols)}
     <div class="grid4" style="grid-template-columns:auto 1fr 1fr"><span class="h">CAGR</span><span class="h">3y</span><span class="h">5y</span>${["revenue", "op_profit", "pat"].map(m => `<span>${m.replace("_", " ")}</span><span>${pct(t.cagr[m]["3y"])}</span><span>${pct(t.cagr[m]["5y"])}</span>`).join("")}</div></div>
   <h2>Balance sheet & cash</h2><div class="hero">
     ${b.lender ? `<div class="sum">Deposits: ${b.deposits.map(fmt).join(" → ")}</div>` : `<div class="sum">Debt/Equity: ${b.debt_to_equity.map(x => fmt(x) + "x").join(" → ")} <span class="sub">(${b.cols[0]}–${b.cols.at(-1)})</span></div>`}
     ${b.wc["Cash Conversion Cycle"]?.length ? `<div class="sum">Cash conversion cycle (days): ${b.wc["Cash Conversion Cycle"].map(fmt).join(" → ")}</div>` : ""}
     ${k.cols.length ? `<div class="sum" style="margin-top:8px">Operating cash flow vs profit</div>${bars(k.cfo.map((x, i) => x / (k.pat[i] || 1)), k.cols, 300, 40)}<div class="sub" style="font-size:12px">CFO/PAT, cumulative 5y: <b>${fmt(k.cfo_to_pat_5y)}x</b> ${k.verdict ? "· " + k.verdict : ""}</div>` : ""}</div>
   <h2><span>Promised vs delivered</span><span>last 4 calls</span></h2><div class="hero" id="led">${skel(5)}<div class="src">Reading transcripts…</div></div>
   <h2>Bull vs bear</h2><div id="bb">${skel(4)}</div>
   <h2>Ask the filings</h2><div class="askbar"><input id="qa" placeholder="e.g. What did they guide on capex?"><button class="btn" id="qago">Ask</button></div><div id="qaout"></div>
   <p class="src">Sources: ${[...c.sources.transcripts, c.sources.deck, c.sources.annual_report].filter(Boolean).map(x => `<a href="${x.url}" target="_blank">${esc(x.label)}</a>`).join(" · ")} · financials via <a href="${c.url}" target="_blank">screener.in</a></p>`;
  $("#qago").onclick = async () => { const q = $("#qa").value; if (!q) return; $("#qaout").innerHTML = skel(3); const a = await post(`/api/research/${sym}/ask`, { q }).catch(e => ({ answer: e.message, sources: [] }));
    $("#qaout").innerHTML = `<div class="qa">${esc(a.answer || a.note || "")}</div>${a.sources.map((s, i) => `<div class="src">[S${i + 1}] <a href="${s.url}" target="_blank">${esc(s.doc)} p${s.page}</a></div>`).join("")}`; };
  const off = x => x.disabled ? `<p class="src">${esc(x.reason)}</p>` : null;
  api(`/api/research/${sym}/business`).then(x => $("#biz").innerHTML = off(x) || `<div class="hd">${esc(x.one_liner)}</div>${(x.segments || []).map(g => `<div class="row"><div class="grow"><div class="t1">${esc(g.name)}</div><div class="t2">${esc(g.note)}</div></div><b>${esc(g.share)}</b></div>`).join("")}${(x.drivers || []).map(d => `<div class="flag neu">${esc(d)}</div>`).join("")}`).catch(e => $("#biz").innerHTML = `<p class="src">${esc(c.about)}</p>`);
  api(`/api/research/${sym}/ledger`).then(x => $("#led").innerHTML = off(x) || `
     ${x.credibility ? `<div class="big" style="font:400 24px 'Instrument Serif'">Credibility ${x.credibility}/5</div><p class="sum">${esc(x.credibility_note)}</p>` : `<p class="sub">${esc(x.credibility_note)}</p>`}
     ${(x.ledger || []).map(l => `<div class="led"><div class="row1"><span>${esc(l.said_in)} · target ${esc(l.target)}</span><span class="v ${String(l.verdict).split(" ")[0].replace("'", "")}">${esc(l.verdict)}</span></div><div class="t1">${esc(l.promise)}</div><div class="sum">${esc(l.actual)} — ${esc(l.why)}</div></div>`).join("")}
     ${(x.divergences || []).length ? `<div class="sum" style="margin-top:10px"><b>Story vs numbers</b></div>${x.divergences.map(d => `<div class="flag ${d.agree ? "pos" : "neg"}">“${esc(d.story)}” — ${esc(d.numbers)}</div>`).join("")}` : ""}
     ${(x.guidance_now || []).length ? `<div class="chips">${x.guidance_now.map(g => `<span class="chip">${esc(g.metric)}: <b>${esc(g.target)}</b> ${esc(g.horizon)}</span>`).join("")}</div>` : ""}`).catch(e => $("#led").innerHTML = `<p class="src">Ledger failed: ${esc(e.message)}</p>`);
  api(`/api/research/${sym}/bullbear`).then(x => $("#bb").innerHTML = off(x) || `<div class="bb"><div><b class="pos">Bull</b><ul>${(x.bull || []).map(p => `<li>${esc(p.point)} <span class="sub">${esc(p.evidence)}</span></li>`).join("")}</ul></div><div><b class="neg">Bear</b><ul>${(x.bear || []).map(p => `<li>${esc(p.point)} <span class="sub">${esc(p.evidence)}</span></li>`).join("")}</ul></div></div>${(x.watch || []).map(w => `<div class="flag neu">Watch: ${esc(w)}</div>`).join("")}`).catch(e => $("#bb").innerHTML = `<p class="src">${esc(e.message)}</p>`);
}

// ── HOLDINGS ────────────────────────────────────────────────────────────────
async function renderBook() {
  $("#view").innerHTML = skel(10);
  const h = await api("/api/holdings").catch(() => []);
  const counts = {}; (DIGEST?.needs_you || []).forEach(i => counts[i.symbol] = (counts[i.symbol] || 0) + 1);
  $("#view").innerHTML = h.sort((a, b) => Math.abs(b.change_pct || 0) - Math.abs(a.change_pct || 0)).map(x => `<div class="row" data-s="${x.symbol}">
    <div class="grow"><div class="t1">${x.symbol} ${counts[x.symbol] ? `<span class="tag hot">${counts[x.symbol]}</span>` : ""}</div><div class="t2">${esc(x.sector)}${x.vol_multiple >= 1.5 ? ` · vol ${x.vol_multiple}x` : ""}</div></div>
    ${spark(x.spark)}<div style="text-align:right;width:84px"><div><b>₹${fmt(x.close)}</b></div><div style="font-size:12.5px">${pct(x.change_pct)}</div></div></div>`).join("");
  $("#view").querySelectorAll("[data-s]").forEach(el => el.onclick = () => go("ask", el.dataset.s));
}

// ── RULES ───────────────────────────────────────────────────────────────────
const TYPES = { results: "Results", mgmt: "Management change", mna: "M&A", fundraise: "Fund raise", rating: "Ratings", legal: "Legal / regulatory", orders: "Orders", capex: "Capex", pledge: "Pledges", buyback: "Buybacks" };
async function renderRules() {
  const r = await api("/api/rules");
  $("#view").innerHTML = `
   <div class="rule">Tell me when a stock trades more than <input type="number" step="0.5" id="vm" value="${r.volume_multiple}">× its <input type="number" id="vl" value="${r.volume_lookback}">-day average volume,</div>
   <div class="rule">or moves more than ±<input type="number" step="0.5" id="pm" value="${r.price_move_pct}">% in a day,</div>
   <div class="rule">or files any of:<br>${Object.entries(TYPES).map(([k, l]) => `<span class="toggle ${r.material_types.includes(k) ? "on" : ""}" data-k="${k}">${l}</span>`).join("")}</div>
   <div class="rule">Check every <input type="number" id="pl" value="${r.poll_minutes}"> minutes.</div>
   <p style="display:flex;gap:8px;flex-wrap:wrap"><button class="btn" style="padding:12px 16px" id="save">Save rules</button><button class="btn ghost" id="notif">Enable notifications</button><button class="btn ghost" id="demo">Send test push</button></p>
   <p class="src">Models: fast = ${esc(META.llm?.fast || "none")}, smart = ${esc(META.llm?.smart || "none")} · ${META.usage?.calls ?? 0} calls, ${META.usage?.cache_hits ?? 0} cache hits this session.</p>`;
  $("#view").querySelectorAll(".toggle").forEach(t => t.onclick = () => t.classList.toggle("on"));
  $("#save").onclick = async () => { await post("/api/rules", { volume_multiple: +$("#vm").value, volume_lookback: +$("#vl").value, price_move_pct: +$("#pm").value, poll_minutes: +$("#pl").value, material_types: [...document.querySelectorAll(".toggle.on")].map(t => t.dataset.k) }); toast("Rules saved. FINN is watching."); DIGEST = null; };
  $("#notif").onclick = () => "Notification" in window && Notification.requestPermission().then(p => toast("Notifications: " + p));
  $("#demo").onclick = () => post("/api/demo/push", {});
}

// ── push stream, voice, toast ───────────────────────────────────────────────
function toast(msg, ms = 4000) { const t = $("#toast"); t.innerHTML = msg; t.hidden = false; clearTimeout(t._t); t._t = setTimeout(() => t.hidden = true, ms); }
function listen() {
  try {
    const es = new EventSource("/api/stream");
    es.onmessage = e => { const { item } = JSON.parse(e.data); const line = `${item.symbol}: ${item.headline || item.label + " — " + (item.summary || item.subject || "")}`;
      toast(`<b>FINN</b> · ${esc(line)}`, 7000); if (window.Notification?.permission === "granted") new Notification("FINN", { body: line }); };
  } catch { }
}
function voice(cb) {
  const R = window.SpeechRecognition || window.webkitSpeechRecognition; if (!R) return toast("Voice input isn't supported in this browser.");
  const r = new R(); r.lang = "en-IN"; r.onresult = e => cb(e.results[0][0].transcript); r.start(); toast("Listening…", 2500);
}
$("#listen").onclick = () => {
  if (!DIGEST) return; if (speechSynthesis.speaking) { speechSynthesis.cancel(); return; }
  const open = DIGEST.needs_you.filter(i => !done.has(i.id)).slice(0, 6);
  const text = `${open.length} things need you. ` + open.map(i => i.kind === "signal" ? i.headline : `${i.symbol}, ${i.label}. ${i.summary || i.subject}`).join(". ") + `. ${DIGEST.counts.routine} routine filings filed away.`;
  const u = new SpeechSynthesisUtterance(text); u.lang = "en-IN"; u.rate = 1.05; speechSynthesis.speak(u);
};

api("/api/meta").then(m => META = m).finally(() => { go("brief"); listen(); });
