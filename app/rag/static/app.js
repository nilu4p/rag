"use strict";
// Interface du chat : conversations, streaming des réponses, sources, documents.

const $ = (s, el = document) => el.querySelector(s);
const state = { conv: null, busy: false, currentMid: null, docsTimer: null };
const ROUTES = {
  docs: ["📄 Mes documents", "docs"],
  web: ["🌐 Trouvé sur internet", "web"],
  chat: ["💬 Conversation", "chat"],
  summary: ["📄 Résumé", "docs"],
  command: ["⌘ Commande", "command"],
};

// ------------------------------------------------------------------ utilitaires
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function api(path, opts = {}) {
  const r = await fetch(path, { credentials: "same-origin", ...opts });
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch {}
    throw new Error(`${r.status} : ${msg}`);
  }
  return r.json();
}

function fmtDate(ts) {
  const d = new Date(ts * 1000);
  return d.toLocaleString("fr-FR", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

// ------------------------------------------------------------------ Markdown (sûr : tout est échappé d'abord)
function inline(s) {
  s = esc(s);
  s = s.replace(/`([^`]+)`/g, (_, c) => `<code>${c}</code>`);
  s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>").replace(/__([^_]+)__/g, "<strong>$1</strong>");
  s = s.replace(/(^|[^*\w])\*([^*\n]+)\*(?!\*)/g, "$1<em>$2</em>");
  s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, (_, t, u) => `<a href="${u}" target="_blank" rel="noopener noreferrer">${t}</a>`);
  s = s.replace(/(^|[\s(])(https?:\/\/[^\s<)]+[^\s<).,;:!?])/g, (_, p, u) => `${p}<a href="${u}" target="_blank" rel="noopener noreferrer">${u}</a>`);
  s = s.replace(/\[(\d{1,2})\]/g, (_, n) => `<button type="button" class="cite" data-n="${n}">${n}</button>`);
  return s;
}

function markdown(src) {
  const lines = String(src || "").replace(/\r/g, "").split("\n");
  const out = [];
  let i = 0;
  const isTableSep = (l) => /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(l);
  const cells = (l) => l.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());
  while (i < lines.length) {
    const l = lines[i];
    if (/^```/.test(l)) {
      const buf = [];
      i++;
      while (i < lines.length && !/^```/.test(lines[i])) buf.push(lines[i++]);
      i++;
      out.push(`<pre><code>${esc(buf.join("\n"))}</code></pre>`);
      continue;
    }
    if (/^\s*$/.test(l)) { i++; continue; }
    let m;
    if ((m = /^(#{1,6})\s+(.*)$/.exec(l))) {
      const lv = Math.min(4, Math.max(2, m[1].length + 1));
      out.push(`<h${lv}>${inline(m[2])}</h${lv}>`);
      i++;
      continue;
    }
    if (l.includes("|") && i + 1 < lines.length && isTableSep(lines[i + 1])) {
      const head = cells(l);
      i += 2;
      const rows = [];
      while (i < lines.length && lines[i].includes("|") && lines[i].trim()) rows.push(cells(lines[i++]));
      out.push(`<table><thead><tr>${head.map((c) => `<th>${inline(c)}</th>`).join("")}</tr></thead><tbody>${rows
        .map((r) => `<tr>${r.map((c) => `<td>${inline(c)}</td>`).join("")}</tr>`).join("")}</tbody></table>`);
      continue;
    }
    if (/^\s*>/.test(l)) {
      const buf = [];
      while (i < lines.length && /^\s*>/.test(lines[i])) buf.push(lines[i++].replace(/^\s*>\s?/, ""));
      out.push(`<blockquote>${markdown(buf.join("\n"))}</blockquote>`);
      continue;
    }
    if (/^\s*([-*+]|\d+[.)])\s+/.test(l)) {
      const ordered = /^\s*\d+[.)]\s+/.test(l);
      const items = [];
      while (i < lines.length && (/^\s*([-*+]|\d+[.)])\s+/.test(lines[i]) || (/^\s{2,}\S/.test(lines[i]) && items.length))) {
        const cur = lines[i];
        if (/^\s*([-*+]|\d+[.)])\s+/.test(cur)) {
          const indent = cur.match(/^\s*/)[0].length;
          if (indent < 2 && items.length && /^\s*\d+[.)]\s+/.test(cur) !== ordered) break; // autre type de liste
          items.push({ text: cur.replace(/^\s*([-*+]|\d+[.)])\s+/, ""), sub: indent >= 2 && items.length > 0 });
        } else items[items.length - 1].text += " " + cur.trim();
        i++;
      }
      const tag = ordered ? "ol" : "ul";
      let html = `<${tag}>`;
      let inSub = false;
      for (const it of items) {
        if (it.sub && !inSub) { html = html.replace(/<\/li>$/, "") + "<ul>"; inSub = true; }
        else if (!it.sub && inSub) { html += "</ul></li>"; inSub = false; }
        html += `<li>${inline(it.text)}</li>`;
      }
      if (inSub) html += "</ul></li>";
      out.push(html + `</${tag}>`);
      continue;
    }
    const buf = [];
    while (i < lines.length && lines[i].trim() && !/^(#{1,6}\s|```|\s*>|\s*([-*+]|\d+[.)])\s+)/.test(lines[i])
      && !(lines[i].includes("|") && i + 1 < lines.length && isTableSep(lines[i + 1]))) buf.push(lines[i++]);
    out.push(`<p>${buf.map(inline).join("<br>")}</p>`);
  }
  return out.join("\n");
}

// ------------------------------------------------------------------ affichage des messages
const messagesEl = $("#messages");

function scrollDown(force = false) {
  const nearBottom = messagesEl.scrollHeight - messagesEl.scrollTop - messagesEl.clientHeight < 160;
  if (force || nearBottom) messagesEl.scrollTop = messagesEl.scrollHeight;
}

function addMessage(role) {
  const node = $("#tplMsg").content.firstElementChild.cloneNode(true);
  node.classList.add(role);
  messagesEl.appendChild(node);
  return node;
}

function renderUser(text) {
  const node = addMessage("user");
  $(".bubble", node).textContent = text;
  return node;
}

function sourceLink(s) {
  if (s.url) return `<a href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">${esc(s.title || s.url)}</a>`;
  const page = s.page && !s.label ? `#page=${s.page}` : "";
  return `<a href="/api/documents/file?path=${encodeURIComponent(s.path)}${page}" target="_blank" rel="noopener">${esc(s.name)}</a>`;
}

function sourceWhere(s) {
  if (s.url) { try { return new URL(s.url).hostname.replace(/^www\./, ""); } catch { return ""; } }
  const parts = [];
  if (s.label) parts.push(s.label); else if (s.page) parts.push(`page ${s.page}`);
  if (s.heading) parts.push(`« ${s.heading} »`);
  return parts.join(" · ");
}

function renderSources(meta) {
  const srcs = meta.sources || [];
  if (!srcs.length) return "";
  const cited = srcs.filter((s) => s.cited);
  const others = srcs.filter((s) => !s.cited);
  const line = (s) => `<div class="src" data-n="${s.n}"><span class="cite n">${s.n}</span><span>${sourceLink(s)} <span class="where">${esc(sourceWhere(s))}</span></span></div>`
    + (s.excerpt && !s.url ? `<div class="excerpt hidden" data-x="${s.n}">${esc(s.excerpt)}</div>` : "");
  let html = '<div class="sources">';
  if (cited.length) html += cited.map(line).join("");
  if (others.length) {
    const label = cited.length ? `${others.length} autre(s) source(s) consultée(s)` : `Sources consultées (${others.length})`;
    html += `<details${cited.length ? "" : " open"}><summary>${label}</summary>${others.map(line).join("")}</details>`;
  }
  return html + "</div>";
}

function renderAssistant(node, content, meta = {}, status = "done") {
  node.classList.toggle("error", status === "error");
  const route = meta.route && ROUTES[meta.route];
  node.classList.toggle("route-web", meta.route === "web");
  let html = "";
  if (route || meta.fallback) {
    html += '<div class="badges">';
    if (route) html += `<span class="badge ${route[1]}">${route[0]}</span>`;
    if (meta.fallback) html += `<span>${esc(meta.fallback)} → recherche sur internet</span>`;
    html += "</div>";
  }
  html += markdown(content);
  if (status === "done") {
    html += renderSources(meta);
    for (const w of meta.warnings || []) html += `<div class="note">⚠️ ${esc(w)}</div>`;
    if ((meta.unverified || []).length)
      html += `<div class="note">🔎 À vérifier : ${meta.unverified.map(esc).join(", ")} n'apparai${meta.unverified.length > 1 ? "ssent" : "t"} dans aucune source (calcul ou erreur possible).</div>`;
    const bits = [];
    if (meta.seconds) bits.push(`${meta.seconds} s`);
    if (meta.query && meta.route && meta.route !== "chat" && meta.route !== "command") bits.push(`recherche : « ${esc(meta.query)} »`);
    if (bits.length) html += `<div class="meta-line">${bits.join(" · ")}</div>`;
  }
  $(".bubble", node).innerHTML = html;
}

messagesEl.addEventListener("click", (e) => {
  const c = e.target.closest(".cite");
  if (!c) return;
  const msg = c.closest(".msg");
  const n = c.dataset.n;
  const src = msg && $(`.src[data-n="${n}"]`, msg);
  if (!src) return;
  const det = src.closest("details");
  if (det) det.open = true;
  const ex = $(`.excerpt[data-x="${n}"]`, msg);
  if (ex && c.classList.contains("n")) { ex.classList.toggle("hidden"); return; }
  src.scrollIntoView({ behavior: "smooth", block: "center" });
  src.classList.remove("flash"); void src.offsetWidth; src.classList.add("flash");
  if (ex) ex.classList.remove("hidden");
});

function emptyState() {
  messagesEl.innerHTML = `<div class="empty"><h2>Que veux-tu savoir ?</h2>
    <p>Je réponds à partir de <strong>tes documents</strong> (avec les sources), d'<strong>internet</strong> si besoin, ou directement pour rédiger, traduire, expliquer.</p>
    <div class="suggestions">
      <button type="button">Quels documents as-tu ?</button>
      <button type="button">Résume mon CV</button>
      <button type="button">Quelles sont mes compétences principales ?</button>
      <button type="button">/aide</button>
    </div></div>`;
  messagesEl.querySelectorAll(".suggestions button").forEach((b) => b.addEventListener("click", () => {
    const t = b.textContent;
    send(t === "Quels documents as-tu ?" ? "/docs" : t === "Résume mon CV" ? "/resume cv" : t);
  }));
}

// ------------------------------------------------------------------ conversations
async function loadConvs() {
  const { conversations } = await api("/api/conversations");
  const el = $("#convs");
  el.innerHTML = "";
  const groups = {};
  const now = new Date();
  for (const c of conversations) {
    const d = new Date(c.updated_at * 1000);
    const days = Math.floor((now - d) / 86400000);
    const g = d.toDateString() === now.toDateString() ? "Aujourd'hui" : days < 7 ? "7 derniers jours" : days < 31 ? "Ce mois-ci" : "Plus ancien";
    (groups[g] ||= []).push(c);
  }
  for (const [g, list] of Object.entries(groups)) {
    el.insertAdjacentHTML("beforeend", `<div class="conv-group">${g}</div>`);
    for (const c of list) {
      const a = document.createElement("div");
      a.className = "conv" + (c.id === state.conv ? " active" : "");
      a.innerHTML = `<span class="t">${esc(c.title)}</span><button class="act" data-a="rename" title="Renommer" type="button">✎</button><button class="act" data-a="del" title="Supprimer" type="button">🗑</button>`;
      a.addEventListener("click", async (e) => {
        const act = e.target.dataset.a;
        if (act === "del") {
          if (!confirm(`Supprimer la conversation « ${c.title} » ?`)) return;
          await api(`/api/conversations/${c.id}`, { method: "DELETE" });
          if (state.conv === c.id) newConversation();
          loadConvs();
        } else if (act === "rename") {
          const t = prompt("Nouveau titre", c.title);
          if (t && t.trim()) { await api(`/api/conversations/${c.id}`, { method: "PATCH", headers: { "content-type": "application/json" }, body: JSON.stringify({ title: t.trim() }) }); loadConvs(); }
        } else openConversation(c.id);
      });
      el.appendChild(a);
    }
  }
}

function newConversation() {
  state.conv = null;
  localStorage.removeItem("conv");
  $("#title").textContent = "Assistant RAG";
  emptyState();
  showChat();
  document.querySelectorAll(".conv.active").forEach((e) => e.classList.remove("active"));
  closeSide();
  $("#input").focus();
}

async function openConversation(id) {
  let data;
  try { data = await api(`/api/conversations/${id}`); } catch { return newConversation(); }
  state.conv = id;
  try { localStorage.setItem("conv", id); } catch {}
  $("#title").textContent = data.conversation.title;
  messagesEl.innerHTML = "";
  let live = null;
  for (const m of data.messages) {
    if (m.role === "user") renderUser(m.content);
    else {
      const node = addMessage("assistant");
      if (m.status === "pending" && m.live) live = { node, mid: m.id };
      else if (m.status === "pending") renderAssistant(node, "⚠️ Réponse interrompue.", {}, "error");
      else renderAssistant(node, m.content, m.meta, m.status);
    }
  }
  showChat();
  closeSide();
  loadConvs();
  scrollDown(true);
  if (live && !state.busy) follow(live.node, live.mid);
}

// ------------------------------------------------------------------ envoi et streaming
function setBusy(b) {
  state.busy = b;
  const btn = $("#send");
  btn.textContent = b ? "⏹ Arrêter" : "Envoyer";
  btn.classList.toggle("primary", !b);
  btn.type = b ? "button" : "submit";
}

function liveView(node) {
  const t0 = Date.now();
  let status = "Je réfléchis…";
  let text = "";
  let thinking = false;
  const draw = () => {
    const secs = Math.round((Date.now() - t0) / 1000);
    if (!text) {
      $(".bubble", node).innerHTML = `<div class="live"><span class="dots"><i></i><i></i><i></i></span><span>${esc(status)}${thinking ? " (réflexion du modèle)" : ""}</span><span>${secs} s</span></div>`;
    } else {
      $(".bubble", node).innerHTML = `<div class="cursor">${markdown(text)}</div>`;
    }
  };
  const timer = setInterval(() => { if (!text) draw(); }, 1000);
  draw();
  return {
    status(s) { status = s; thinking = false; if (!text) draw(); },
    thinking() { thinking = true; if (!text) draw(); },
    token(t) { text += t; draw(); scrollDown(); },
    reset() { text = ""; draw(); },
    stop() { clearInterval(timer); },
  };
}

async function readStream(resp, view, onMeta) {
  const reader = resp.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  let final = null;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let nl;
    while ((nl = buf.indexOf("\n")) >= 0) {
      const line = buf.slice(0, nl).trim();
      buf = buf.slice(nl + 1);
      if (!line) continue;
      let ev;
      try { ev = JSON.parse(line); } catch { continue; }
      if (ev.type === "meta") onMeta && onMeta(ev);
      else if (ev.type === "status") view.status(ev.text);
      else if (ev.type === "thinking") view.thinking();
      else if (ev.type === "token") view.token(ev.text);
      else if (ev.type === "reset") view.reset();
      else if (ev.type === "done" || ev.type === "error") final = ev;
    }
  }
  return final;
}

function finish(node, final) {
  if (!final) return false;
  if (final.type === "done") renderAssistant(node, final.content, final.meta || {});
  else renderAssistant(node, final.text, {}, "error");
  scrollDown();
  return true;
}

async function follow(node, mid) {
  // Raccroche à une réponse en cours (après coupure réseau ou rechargement de la page)
  setBusy(true);
  state.currentMid = mid;
  const view = liveView(node);
  try {
    for (let attempt = 0; attempt < 120; attempt++) {
      let resp;
      try { resp = await fetch(`/api/chat/${mid}/stream`, { credentials: "same-origin" }); } catch { await sleep(3000); continue; }
      if (resp.status === 404) break; // terminée : on relit la conversation
      view.reset();
      try { if (finish(node, await readStream(resp, view))) return; } catch {}
      await sleep(2000);
    }
    if (state.conv) await openConversation(state.conv);
  } finally {
    view.stop();
    setBusy(false);
    state.currentMid = null;
  }
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function send(text) {
  text = (text || "").trim();
  if (!text || state.busy) return;
  if (messagesEl.querySelector(".empty")) messagesEl.innerHTML = "";
  renderUser(text);
  const node = addMessage("assistant");
  scrollDown(true);
  setBusy(true);
  const view = liveView(node);
  let mid = null;
  try {
    const resp = await fetch("/api/chat", {
      method: "POST",
      credentials: "same-origin",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ message: text, conversation_id: state.conv, mode: $("#mode").value }),
    });
    if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);
    const final = await readStream(resp, view, (m) => {
      mid = m.message_id;
      state.currentMid = mid;
      if (state.conv !== m.conversation_id) {
        state.conv = m.conversation_id;
        try { localStorage.setItem("conv", state.conv); } catch {}
        $("#title").textContent = text.slice(0, 60);
        loadConvs();
      }
    });
    view.stop();
    if (!finish(node, final)) {
      // Connexion coupée avant la fin : la réponse continue côté serveur
      setBusy(false);
      if (mid) return await follow(node, mid);
      renderAssistant(node, "⚠️ Connexion perdue avant la réponse.", {}, "error");
    }
  } catch (e) {
    view.stop();
    if (mid) { setBusy(false); return await follow(node, mid); }
    renderAssistant(node, `⚠️ Impossible de joindre l'assistant : ${e.message}`, {}, "error");
  } finally {
    view.stop();
    setBusy(false);
    state.currentMid = null;
    loadConvs().catch(() => {});
  }
}

// ------------------------------------------------------------------ saisie
const input = $("#input");
function autosize() { input.style.height = "auto"; input.style.height = Math.min(input.scrollHeight, window.innerHeight * 0.4) + "px"; }
input.addEventListener("input", autosize);
input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing && !matchMedia("(max-width: 820px)").matches) {
    e.preventDefault();
    $("#composer").requestSubmit();
  }
});
$("#composer").addEventListener("submit", (e) => {
  e.preventDefault();
  if (state.busy) return;
  const t = input.value;
  input.value = "";
  autosize();
  send(t);
});
$("#send").addEventListener("click", async (e) => {
  if (!state.busy) return;
  e.preventDefault();
  if (state.currentMid) await fetch(`/api/chat/${state.currentMid}/stop`, { method: "POST", credentials: "same-origin" }).catch(() => {});
});
try { $("#mode").value = localStorage.getItem("mode") || "auto"; } catch {}
$("#mode").addEventListener("change", () => { try { localStorage.setItem("mode", $("#mode").value); } catch {} });

// ------------------------------------------------------------------ navigation
function showChat() { $("#chatView").classList.remove("hidden"); $("#docsView").classList.add("hidden"); clearInterval(state.docsTimer); }
function showDocs() { $("#chatView").classList.add("hidden"); $("#docsView").classList.remove("hidden"); closeSide(); loadDocs(); clearInterval(state.docsTimer); state.docsTimer = setInterval(loadDocs, 3000); }
function closeSide() { $("#app").classList.remove("side-open"); }
$("#newChat").addEventListener("click", newConversation);
$("#openDocs").addEventListener("click", showDocs);
$("#openDocs2").addEventListener("click", showDocs);
$("#closeDocs").addEventListener("click", showChat);
$("#toggleSide").addEventListener("click", () => $("#app").classList.toggle("side-open"));
$("#scrim").addEventListener("click", closeSide);

// ------------------------------------------------------------------ documents
function fmtSize(n) { return n > 1e6 ? `${(n / 1e6).toFixed(1)} Mo` : `${Math.max(1, Math.round(n / 1e3))} ko`; }

async function loadDocs() {
  let data;
  try { data = await api("/api/documents"); } catch (e) { $("#docList").textContent = `Erreur : ${e.message}`; return; }
  const ix = data.indexing;
  const prog = $("#progress");
  if (ix.running) {
    prog.classList.remove("hidden");
    prog.textContent = `⟳ Indexation : ${ix.done}/${ix.total}${ix.current ? ` — ${ix.current}` : ""} (les photos et scans prennent environ une minute)`;
  } else if (ix.message && ix.finished_at && Date.now() / 1000 - ix.finished_at < 120) {
    prog.classList.remove("hidden");
    prog.textContent = `✔ ${ix.message}`;
  } else prog.classList.add("hidden");

  const files = data.files;
  $("#docCount").textContent = files.filter((f) => f.status === "ok").length || "";
  if (!files.length) { $("#docList").innerHTML = '<p class="hint">Aucun document pour l\'instant.</p>'; return; }
  $("#docList").innerHTML = files.map((f) => {
    const ok = f.status === "ok";
    const sub = ok
      ? `${f.chunks} morceaux · ${f.pages > 1 ? f.pages + " pages · " : ""}${esc(f.method || "")} · ${fmtSize(f.size)} · ${f.indexed_at ? fmtDate(f.indexed_at) : ""}`
      : `${esc(f.error || f.status)}`;
    return `<div class="doc ${ok ? "" : "err"}"><span class="st">${ok ? "✅" : f.status === "empty" ? "⚪" : "⚠️"}</span>
      <div class="info"><div class="name"><a href="/api/documents/file?path=${encodeURIComponent(f.path)}" target="_blank" rel="noopener">${esc(f.path)}</a></div><div class="sub">${sub}</div></div>
      <button class="btn ghost danger" data-del="${esc(f.path)}" type="button" title="Supprimer le fichier">🗑</button></div>`;
  }).join("");
}

$("#docList").addEventListener("click", async (e) => {
  const p = e.target.dataset.del;
  if (!p) return;
  if (!confirm(`Supprimer définitivement le fichier « ${p} » (du disque et de l'index) ?`)) return;
  try { await api(`/api/documents?path=${encodeURIComponent(p)}`, { method: "DELETE" }); } catch (err) { alert(err.message); }
  loadDocs();
});

async function upload(files) {
  if (!files.length) return;
  const fd = new FormData();
  for (const f of files) fd.append("files", f, f.name);
  const prog = $("#progress");
  prog.classList.remove("hidden");
  prog.textContent = `⬆ Envoi de ${files.length} fichier(s)…`;
  try {
    const r = await api("/api/documents", { method: "POST", body: fd });
    prog.textContent = `✔ ${r.saved.length} fichier(s) ajouté(s), indexation en cours…` + (r.refused.length ? ` Refusé(s) : ${r.refused.join(", ")}` : "");
  } catch (e) { prog.textContent = `✘ Envoi impossible : ${e.message}`; }
  setTimeout(loadDocs, 1500);
}

const drop = $("#drop");
$("#fileInput").addEventListener("change", (e) => { upload([...e.target.files]); e.target.value = ""; });
["dragenter", "dragover"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", (e) => upload([...e.dataTransfer.files]));
$("#syncBtn").addEventListener("click", async () => { await api("/api/index", { method: "POST", headers: { "content-type": "application/json" }, body: "{}" }); setTimeout(loadDocs, 800); });
$("#reindexBtn").addEventListener("click", async () => {
  if (!confirm("Vider l'index et relire tous les documents ? (plusieurs minutes avec des scans ou des photos)")) return;
  await api("/api/index", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ full: true }) });
  setTimeout(loadDocs, 800);
});

// ------------------------------------------------------------------ état des services
async function health() {
  try {
    const s = await api("/api/status");
    const parts = [];
    parts.push(s.ollama ? `<span class="ok">●</span> ${esc(s.chat_model)}` : `<span class="ko">●</span> Ollama injoignable`);
    if ((s.models_missing || []).length) parts.push(`<span class="ko">Modèle manquant : ${esc(s.models_missing.join(", "))}</span>`);
    parts.push(s.qdrant ? `<span class="ok">●</span> ${s.documents} document(s), ${s.chunks} morceaux` : `<span class="ko">●</span> Qdrant injoignable`);
    if (s.indexing.running) parts.push(`⟳ indexation ${s.indexing.done}/${s.indexing.total}`);
    $("#health").innerHTML = parts.join("<br>");
    $("#docCount").textContent = s.documents || "";
  } catch { $("#health").innerHTML = '<span class="ko">●</span> Service injoignable'; }
}

// ------------------------------------------------------------------ démarrage
(async () => {
  let saved = null;
  try { saved = localStorage.getItem("conv"); } catch {}
  await loadConvs().catch(() => {});
  if (saved) await openConversation(saved); else emptyState();
  health();
  setInterval(health, 15000);
  input.focus();
})();
