"use strict";

const $ = (s) => document.querySelector(s);
const el = (t, c, txt) => { const e = document.createElement(t); if (c) e.className = c; if (txt != null) e.textContent = txt; return e; };

let ZUSTAND = { items: [], stats: null, proposals: [], offen: null, poller: null };

async function api(pfad, body) {
  const opt = body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {};
  const r = await fetch(pfad, opt);
  if (!r.ok) {
    let m = r.statusText;
    try { m = (await r.json()).detail || m; } catch (e) {}
    throw new Error(m);
  }
  return r.json();
}

const ntok = (n) => n >= 1000 ? (n / 1000).toFixed(1).replace(".", ",") + "k" : String(n);

/* ------------------------------------------------------------------ baum */
// Der Baum ordnet nach Projektordner (cwd), nicht nach Speicherort. Wer nicht
// weiss, wo er gearbeitet hat, soll trotzdem sofort seine Sessions sehen:
// darum oben "Alle Sessions", und ein Ordner zeigt alles, was darunter liegt.
async function baumLaden() {
  const wurzeln = await api("/api/tree");
  const ziel = $("#baum");
  ziel.innerHTML = "";
  const alle = { name: "Alle Sessions", path: "", children: wurzeln, sessions: [],
                 count: wurzeln.reduce((a, w) => a + w.count, 0) };
  const z = el("div", "knoten alle aktiv");
  z.appendChild(el("span", "pfeil", ""));
  z.appendChild(el("span", null, alle.name));
  z.appendChild(el("span", "zahl", String(alle.count)));
  z.onclick = () => { aktivSetzen(z); sessionsZeigen(alle); };
  ziel.appendChild(z);
  wurzeln.forEach((w) => ziel.appendChild(knoten(w, 0)));
  sessionsZeigen(alle);
}

function aktivSetzen(z) {
  document.querySelectorAll(".knoten.aktiv").forEach((x) => x.classList.remove("aktiv"));
  z.classList.add("aktiv");
}

function knoten(n, tiefe) {
  const wrap = el("div");
  const z = el("div", "knoten");
  z.style.paddingLeft = (6 + tiefe * 13) + "px";
  const hatKinder = n.children.length > 0;
  const pfeil = el("span", "pfeil", "");
  z.appendChild(pfeil);
  z.appendChild(el("span", null, n.name));
  z.appendChild(el("span", "zahl", String(n.count)));
  const kinder = el("div");
  // Ketten ohne Abzweig (C: > Users > name > ...) gleich aufklappen
  kinder.hidden = !(tiefe === 0 || (n.children.length === 1 && !n.sessions.length));
  if (hatKinder) pfeil.textContent = kinder.hidden ? "▸" : "▾";
  n.children.forEach((c) => kinder.appendChild(knoten(c, tiefe + 1)));

  z.onclick = () => {
    if (hatKinder) {
      kinder.hidden = !kinder.hidden;
      pfeil.textContent = kinder.hidden ? "▸" : "▾";
    }
    aktivSetzen(z);
    sessionsZeigen(n);
  };
  wrap.appendChild(z);
  wrap.appendChild(kinder);
  return wrap;
}

function sessionsDarunter(n) {
  const out = n.sessions.slice();
  n.children.forEach((c) => out.push(...sessionsDarunter(c)));
  return out;
}

function sessionsZeigen(n) {
  $("#ordnername").textContent = n.path ? "  " + n.path : "";
  const ziel = $("#sessions");
  ziel.innerHTML = "";
  const liste = sessionsDarunter(n).sort((x, y) => y.mtime - x.mtime);
  if (!liste.length) {
    ziel.appendChild(el("div", "leer", n.path ? "keine Sessions in diesem Ordner"
      : "Keine Claude-Code-Sessions gefunden (gesucht in %USERPROFILE%\\.claude\\projects)"));
    return;
  }
  liste.forEach((s) => {
    const d = el("div", "sitem");
    d.appendChild(el("div", "t", s.title));
    const dt = new Date(s.mtime * 1000).toLocaleString("de-DE", { dateStyle: "short", timeStyle: "short" });
    d.appendChild(el("div", "m", dt + "   " + (s.size / 1024).toFixed(0) + " KB"));
    if (s.cwd !== n.path) d.appendChild(el("div", "m pfad", s.cwd));
    d.title = s.cwd;
    d.onclick = () => {
      document.querySelectorAll(".sitem.aktiv").forEach((x) => x.classList.remove("aktiv"));
      d.classList.add("aktiv");
      sessionOeffnen(s);
    };
    ziel.appendChild(d);
  });
}

/* --------------------------------------------------------------- session */
async function sessionOeffnen(s) {
  $("#liste").innerHTML = "<div class='leer gross'>laedt ...</div>";
  let r;
  try { r = await api("/api/open", { file: s.file }); }
  catch (e) { $("#liste").innerHTML = "<div class='leer gross'>Fehler: " + e.message + "</div>"; return; }
  ZUSTAND.titel = s.title;
  const h = $("#hinweis");
  h.hidden = false;
  h.className = "hinweis";
  h.textContent = "Sicherung angelegt (" + r.backup.split(/[\\/]/).pop() + "). "
    + r.purge.anzahl + " Denkprozesse entfernt, ~" + ntok(r.purge.tokens) + " Tokens gespart. "
    + "Die Datei selbst ist noch unveraendert – erst „speichern“ schreibt.";
  $("#run").disabled = false;
  await aktualisieren();
}

async function aktualisieren() {
  const d = await api("/api/items");
  ZUSTAND.items = d.items;
  ZUSTAND.stats = d.stats;
  ZUSTAND.proposals = d.proposals;
  kopfZeichnen(d);
  listeZeichnen(d.items);
  vorschlaegeZeichnen(d.proposals);
  $("#log").innerHTML = "";
  (d.log || []).forEach((z) => $("#log").appendChild(el("div", null, z)));
}

function kopfZeichnen(d) {
  const s = d.stats;
  const k = $("#kopf");
  k.innerHTML = "";
  k.appendChild(el("b", null, ZUSTAND.titel || ""));
  const sp = el("span", null,
    "   " + s.live + "/" + s.items + " Nachrichten · ~" + ntok(s.tokens_live) +
    " von ~" + ntok(s.tokens_total) + " Tokens" +
    (s.tokens_total > s.tokens_live ? "  (−" + ntok(s.tokens_total - s.tokens_live) + ")" : ""));
  sp.style.color = "var(--dim)";
  k.appendChild(sp);
  const f = s.flags || {};
  const anzFlags = (f.stops || 0) + (f.fallbacks || 0);
  if (anzFlags || s.active_model) {
    const kats = Object.entries(f.kategorien || {}).map(([kk, v]) => v + "× " + kk).join(", ");
    const badge = el("span", "flagbadge",
      (anzFlags ? "⚑ " + anzFlags + " geflaggt" + (kats ? " (" + kats + ")" : "") + "  " : "") +
      (s.active_model ? "aktiv: " + s.active_model.replace("claude-", "") : ""));
    k.appendChild(badge);
  }
  const p = $("#pruefung");
  if (d.probleme.length) {
    p.className = "pruef fehler";
    p.textContent = "⚠ " + d.probleme.length + " Strukturproblem(e)";
    p.title = d.probleme.join("\n");
  } else {
    p.className = "pruef ok";
    p.textContent = "✓ Struktur in Ordnung";
    p.title = "Tool-Paare vollstaendig, Kette geschlossen";
  }
  $("#save").disabled = !d.dirty || d.probleme.length > 0;
}

/* ---------------------------------------------------------------- liste */
function listeZeichnen(items) {
  const ziel = $("#liste");
  ziel.innerHTML = "";
  const frag = document.createDocumentFragment();
  items.forEach((i) => { if (!(i.kind === "thinking" && i.deleted)) frag.appendChild(nachricht(i)); });
  ziel.appendChild(frag);
}

function nachricht(i) {
  const m = el("div", "msg " + i.role + (i.deleted ? " geloescht" : "") + (i.edited ? " bearbeitet" : "") +
    (i.marker === "classifier_stop" ? " markstop" : "") + (i.flagged ? " markflag" : ""));
  m.id = "msg" + i.idx;
  const z = el("div", "zeile");
  z.appendChild(el("span", "idx", "#" + i.idx));
  z.appendChild(el("span", "rolle " + i.role, i.role === "meta" ? "system" : i.role));
  z.appendChild(el("span", "art", i.name || i.meta_type || i.kind));
  if (i.edited) z.appendChild(el("span", "marke edit", "bearbeitet"));
  if (i.marker === "classifier_stop") z.appendChild(el("span", "marke stop", "⚑ Classifier-Abbruch (nicht Nutzer)"));
  else if (i.marker === "refusal_fallback") z.appendChild(el("span", "marke fb", "Modellwechsel-Hinweis"));
  else if (i.marker) z.appendChild(el("span", "marke stop", "⚑ " + i.marker));
  if (i.flagged) z.appendChild(el("span", "marke flag", "getriggert: " + (i.flag_category || "?")));
  if (i.kind === "thinking" && i.has_signature) z.appendChild(el("span", "marke enc", i.enc_bytes + " B verschluesselt"));
  z.appendChild(el("span", "tok", "~" + ntok(i.tokens)));

  const k = el("div", "knoepfe");
  if (i.deleted) {
    const b = el("button", null, "zurueckholen");
    b.onclick = async () => { await api("/api/restore", { idxs: [i.idx] }); aktualisieren(); };
    k.appendChild(b);
  } else {
    if (i.editable) {
      const b = el("button", null, "bearbeiten");
      b.onclick = () => bearbeiten(i);
      k.appendChild(b);
    }
    if (i.edited) {
      const b = el("button", null, "zuruecksetzen");
      b.onclick = async () => { await api("/api/revert", { idx: i.idx, text: "" }); aktualisieren(); };
      k.appendChild(b);
    }
    const b = el("button", null, "loeschen");
    b.onclick = async () => {
      try {
        const r = await api("/api/delete", { idxs: [i.idx] });
        if (r.mitgezogen.length) meldung("Tool-Paar: #" + r.mitgezogen.map((x) => x.idx).join(", #") + " ging mit.");
      } catch (e) { meldung(e.message, true); }
      aktualisieren();
    };
    k.appendChild(b);
  }
  z.appendChild(k);
  m.appendChild(z);

  let txt = i.text || "";
  if (i.kind === "thinking" && !txt) txt = "[Denkprozess – kein Klartext lokal vorhanden]";
  const t = el("div", "txt", txt.length > 2000 ? txt.slice(0, 2000) : txt);
  m.appendChild(t);
  if (txt.length > 700) {
    const b = el("button", "mehr", "ganzen Text zeigen (" + txt.length + " Zeichen)");
    b.onclick = () => { t.classList.toggle("offen"); t.textContent = txt; b.textContent = t.classList.contains("offen") ? "einklappen" : "ganzen Text zeigen"; };
    m.appendChild(b);
  }
  return m;
}

/* --------------------------------------------------------- vorschlaege */
function vorschlaegeZeichnen(ps) {
  const ziel = $("#vorschlaege");
  ziel.innerHTML = "";
  const offen = ps.filter((p) => p.status === "offen").length;
  $("#vzahl").textContent = ps.length ? "(" + offen + " offen von " + ps.length + ")" : "";
  if (!ps.length) { ziel.appendChild(el("div", "leer", "noch keine")); return; }
  ps.slice().reverse().forEach((p) => ziel.appendChild(vorschlag(p)));
}

function vorschlag(p) {
  const d = el("div", "vs " + p.status);
  const k = el("div", "kopfz");
  k.appendChild(el("span", "pid", "#" + p.pid));
  let titel;
  if (p.kind === "delete") titel = "loeschen " + p.idxs.map((x) => "#" + x).join(" ");
  else if (p.kind === "switch_model") titel = "Modell wechseln → " + p.new_text + (p.idx != null ? " (bei #" + p.idx + ")" : "");
  else titel = "umschreiben #" + p.idx;
  k.appendChild(el("b", null, titel));
  k.appendChild(el("span", "zahl", p.status));
  d.appendChild(k);
  d.appendChild(el("div", "grund", p.reason || ""));
  if (p.based_on && p.based_on.length) d.appendChild(el("div", "basis", "stuetzt sich auf: " + p.based_on.map((x) => "#" + x).join(", ")));

  if (p.kind === "rewrite") {
    const alt = (ZUSTAND.items[p.idx] || {}).text || "";
    const dd = el("div", "diff");
    dd.appendChild(el("div", "alt", alt.slice(0, 1200)));
    dd.appendChild(el("div", "neu", p.new_text.slice(0, 1200)));
    d.appendChild(dd);
  }

  if (p.status === "offen") {
    const a = el("div", "akt");
    const ja = el("button", "primaer", "annehmen");
    ja.onclick = () => entscheiden(p.pid, "angenommen");
    const nein = el("button", null, "ablehnen");
    nein.onclick = () => entscheiden(p.pid, "abgelehnt");
    const nach = el("button", null, "nachbessern");
    nach.onclick = () => {
      const n = prompt("Was soll der Agent anders machen?");
      if (n) entscheiden(p.pid, "abgelehnt", n);
    };
    const hin = el("button", null, "hinspringen");
    hin.onclick = () => {
      const t = document.getElementById("msg" + (p.kind === "delete" ? p.idxs[0] : p.idx));
      if (t) { t.scrollIntoView({ behavior: "smooth", block: "center" }); t.classList.add("trefferziel"); setTimeout(() => t.classList.remove("trefferziel"), 2200); }
    };
    a.append(ja, nein, nach, hin);
    d.appendChild(a);
  } else if (p.note) {
    d.appendChild(el("div", "basis", "Anmerkung: " + p.note));
  }
  return d;
}

async function entscheiden(pid, status, note) {
  try {
    const r = await api("/api/proposal/decide", { pid, status, note: note || "" });
    if (r.hinfaellig && r.hinfaellig.length) meldung("Vorschlag #" + pid + " abgelehnt – " + r.hinfaellig.length + " darauf aufbauende Vorschlaege sind hinfaellig (#" + r.hinfaellig.join(", #") + "). Agent erneut starten, er bekommt das mit.", true);
    if (r.angewandt && r.angewandt.mitgezogen && r.angewandt.mitgezogen.length) meldung("Tool-Paar mitgezogen: #" + r.angewandt.mitgezogen.map((x) => x.idx).join(", #"));
  } catch (e) { meldung(e.message, true); }
  aktualisieren();
}

/* ---------------------------------------------------------------- modal */
function bearbeiten(i) {
  ZUSTAND.offen = i;
  $("#modaltitel").textContent = "#" + i.idx + "  ·  " + i.role + "  ·  " + (i.name || i.kind);
  $("#modaltext").value = i.text || "";
  $("#modalinfo").textContent = i.kind === "tool_use" ? "muss gueltiges JSON bleiben – input und wireToolInputs werden beide geschrieben" : "~" + ntok(i.tokens) + " Tokens";
  $("#modal").hidden = false;
  $("#modaltext").focus();
}
$("#modalzu").onclick = $("#modalabbruch").onclick = () => { $("#modal").hidden = true; };
$("#modalok").onclick = async () => {
  try { await api("/api/rewrite", { idx: ZUSTAND.offen.idx, text: $("#modaltext").value }); $("#modal").hidden = true; aktualisieren(); }
  catch (e) { meldung(e.message, true); }
};

/* ---------------------------------------------------------------- chat */
let CHAT = [];

function chatModelle() {
  const src = $("#model");
  const dst = $("#c-model");
  dst.innerHTML = "";
  Array.from(src.options).forEach((o) => { if (o.value) dst.appendChild(new Option(o.text, o.value)); });
}

function chatZeichnen() {
  const v = $("#c-verlauf");
  v.innerHTML = "";
  if (!CHAT.length) { v.appendChild(el("div", "leer", "Kein Systemprompt, keine Werkzeuge — einfach reden.")); return; }
  CHAT.forEach((m) => {
    const d = el("div", "cmsg " + m.role);
    d.appendChild(el("div", "crolle", m.role === "user" ? "du" : "modell"));
    d.appendChild(el("div", "ctext", m.content));
    v.appendChild(d);
  });
  v.scrollTop = v.scrollHeight;
}

$("#chat-btn").onclick = () => { chatModelle(); chatZeichnen(); $("#cmodal").hidden = false; $("#c-text").focus(); };
$("#cmodalzu").onclick = () => { $("#cmodal").hidden = true; };
$("#c-clear").onclick = () => { CHAT = []; chatZeichnen(); };

async function chatSenden() {
  const txt = $("#c-text").value.trim();
  const model = $("#c-model").value;
  if (!txt || !model) return;
  CHAT.push({ role: "user", content: txt });
  $("#c-text").value = "";
  chatZeichnen();
  const v = $("#c-verlauf");
  const warte = el("div", "cmsg assistant");
  warte.appendChild(el("div", "crolle", "modell"));
  warte.appendChild(el("div", "ctext dim", "…denkt"));
  v.appendChild(warte);
  v.scrollTop = v.scrollHeight;
  $("#c-send").disabled = true;
  try {
    const r = await api("/api/chat/send", { model, messages: CHAT });
    CHAT.push({ role: "assistant", content: r.reply || "(leere Antwort)" });
  } catch (e) {
    CHAT.push({ role: "assistant", content: "Fehler: " + e.message });
  }
  $("#c-send").disabled = false;
  chatZeichnen();
  $("#c-text").focus();
}
$("#c-send").onclick = chatSenden;
$("#c-text").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); chatSenden(); }
});

/* ------------------------------------------------------- prompts-editor */
// Ein Sonderfall = Erkennung (eingebaut oder eigener Trigger) + Modul-Anweisung.
// Gespeichert wird weiter im alten Format: modules{id} und triggers[].
let PROMPTS = null;
const EINGEBAUT = {
  classifier_stop: "eingebaut: Abbruchmeldung eines Sicherheits-Klassifikators, die als Nutzer-Nachricht im Verlauf steht",
  refusal_fallback: "eingebaut: Systemeintrag, dass die Session auf ein Fallback-Modell umgeschaltet wurde",
};

function fallKarte(f) {
  const d = el("div", "fall" + (f.eigen ? " eigen" : ""));
  d.dataset.id = f.id;
  d.dataset.eigen = f.eigen ? "1" : "";
  const k = el("div", "fallkopf");
  if (f.eigen) {
    const an = el("input", "f-an");
    an.type = "checkbox";
    an.checked = f.an !== false;
    an.title = "aktiv";
    k.appendChild(an);
    const id = el("input", "f-id");
    id.value = f.id;
    id.placeholder = "Name";
    id.size = 16;
    k.appendChild(id);
  } else {
    k.appendChild(el("b", null, f.id));
  }
  const beschr = el("input", "f-beschr");
  beschr.value = f.beschreibung || "";
  beschr.placeholder = "wofür ist das?";
  k.appendChild(beschr);
  k.appendChild(el("span", "treffer", ""));
  if (f.eigen) {
    const weg = el("button", null, "entfernen");
    weg.onclick = () => d.remove();
    k.appendChild(weg);
  }
  d.appendChild(k);

  const e = el("div", "fallerkennung");
  if (f.eigen) {
    e.appendChild(el("span", null, "erkannt, wenn"));
    const typ = el("select", "f-typ");
    typ.appendChild(new Option("eine Nachricht dieses Muster enthält (Regex, Groß/klein egal)", "regex"));
    typ.appendChild(new Option("ein Systemeintrag diesen subtype hat", "record_subtype"));
    typ.value = f.typ || "regex";
    e.appendChild(typ);
    const m = el("input", "f-muster");
    m.value = f.muster || "";
    m.placeholder = "z.B. ignore previous instructions";
    e.appendChild(m);
  } else {
    e.appendChild(el("span", "pdim", EINGEBAUT[f.id] || "eingebaut"));
  }
  d.appendChild(e);

  const t = el("textarea", "f-prompt");
  t.rows = 6;
  t.value = f.prompt || "";
  t.placeholder = "Anweisung an den Agenten, wenn dieser Fall im Verlauf vorkommt";
  d.appendChild(t);
  return d;
}

function serverZeile(sv) {
  const d = el("div", "srv");
  const an = el("input", "s-an"); an.type = "checkbox"; an.checked = sv.an !== false; an.title = "eingeschaltet";
  const name = el("input", "s-name"); name.value = sv.name || ""; name.placeholder = "Name"; name.size = 12;
  const typ = el("select", "s-typ");
  typ.appendChild(new Option("Ollama", "ollama"));
  typ.appendChild(new Option("OpenAI-kompatibel (LM Studio …)", "openai"));
  typ.value = sv.typ || "openai";
  const url = el("input", "s-url"); url.value = sv.url || ""; url.placeholder = "http://127.0.0.1:1234";
  const weg = el("button", null, "entfernen"); weg.onclick = () => d.remove();
  d.append(an, name, typ, url, weg);
  return d;
}

function serverSammeln() {
  const out = [];
  for (const d of document.querySelectorAll("#p-server .srv")) {
    const name = d.querySelector(".s-name").value.trim();
    const url = d.querySelector(".s-url").value.trim();
    if (!name && !url) continue;
    if (!name) throw new Error("Ein Server hat keinen Namen.");
    if (!/^https?:\/\//.test(url)) throw new Error("Adresse von „" + name + "“ muss mit http:// anfangen.");
    if (out.some((s) => s.name === name)) throw new Error("Servername doppelt: " + name);
    out.push({ name, typ: d.querySelector(".s-typ").value, url, an: d.querySelector(".s-an").checked });
  }
  return out;
}

function faelleAusConfig(cfg) {
  const mods = cfg.modules || {};
  const out = [];
  Object.keys(EINGEBAUT).forEach((id) => out.push({ ...(mods[id] || {}), id, eigen: false }));
  (cfg.triggers || []).forEach((t) => out.push({
    ...(mods[t.id] || {}), id: t.id, eigen: true, an: t.an, typ: t.typ,
    muster: t.typ === "record_subtype" ? t.wert : t.muster,
  }));
  // Module ohne Erkennung wuerden nie geladen -- trotzdem zeigen, als abgeschalteter Fall.
  Object.entries(mods).forEach(([id, m]) => {
    if (!out.some((f) => f.id === id)) out.push({ ...m, id, eigen: true, an: false, typ: "regex", muster: "" });
  });
  return out;
}

function fallId(d) {
  return d.dataset.eigen ? d.querySelector(".f-id").value.trim() : d.dataset.id;
}

function promptsSammeln() {
  const modules = {}, triggers = [];
  for (const d of document.querySelectorAll("#p-faelle .fall")) {
    const eigen = !!d.dataset.eigen;
    const id = fallId(d);
    if (!id) throw new Error("Ein eigener Sonderfall hat keinen Namen.");
    if (modules[id]) throw new Error("Name doppelt: " + id);
    if (eigen && EINGEBAUT[id]) throw new Error("Name ist für einen eingebauten Fall reserviert: " + id);
    modules[id] = { beschreibung: d.querySelector(".f-beschr").value, prompt: d.querySelector(".f-prompt").value };
    if (eigen) {
      const typ = d.querySelector(".f-typ").value;
      const muster = d.querySelector(".f-muster").value;
      if (typ === "regex") {
        try { new RegExp(muster); } catch (e) { throw new Error("Muster von „" + id + "“ ist kein gültiger Regex: " + e.message); }
      }
      const t = { id, typ, an: d.querySelector(".f-an").checked };
      if (typ === "regex") t.muster = muster; else t.wert = muster;
      triggers.push(t);
    }
  }
  return {
    base_system: $("#p-base").value,
    modules,
    triggers,
    server: serverSammeln(),
    num_ctx: parseInt($("#p-numctx").value) || 16384,
    think: $("#p-think").checked,
    keep_alive: $("#p-keep").value.trim() || "30m",
  };
}

async function trefferZeigen(mitText) {
  let data;
  try { data = promptsSammeln(); } catch (e) { $("#p-fehler").textContent = e.message; return; }
  $("#p-fehler").textContent = "";
  let r;
  try { r = await api("/api/prompts/vorschau", { data }); } catch (e) { $("#p-fehler").textContent = e.message; return; }
  document.querySelectorAll("#p-faelle .fall").forEach((d) => {
    const s = d.querySelector(".treffer");
    if (r.treffer == null) { s.textContent = ""; return; }
    const n = r.treffer[fallId(d)] || 0;
    s.textContent = n ? n + "× im Verlauf → wird geladen" : "kommt nicht vor";
    s.className = "treffer" + (n ? " ja" : "");
  });
  $("#p-info").textContent = r.treffer == null
    ? "Keine Session offen – Treffer und Vorschau gibt es erst, wenn links eine Session geöffnet ist." : "";
  if (mitText) {
    const v = $("#p-vorschautext");
    v.hidden = false;
    v.textContent = r.system == null ? "Keine Session offen." : r.system;
  }
}

async function promptsOeffnen() {
  PROMPTS = await api("/api/prompts");
  $("#p-numctx").value = PROMPTS.num_ctx || 16384;
  $("#p-think").checked = PROMPTS.think !== false;
  $("#p-keep").value = PROMPTS.keep_alive || "30m";
  $("#p-base").value = PROMPTS.base_system || "";
  $("#p-server").innerHTML = "";
  (PROMPTS.server || []).forEach((sv) => $("#p-server").appendChild(serverZeile(sv)));
  const box = $("#p-faelle");
  box.innerHTML = "";
  faelleAusConfig(PROMPTS).forEach((f) => box.appendChild(fallKarte(f)));
  $("#p-vorschautext").hidden = true;
  $("#p-fehler").textContent = "";
  $("#pmodal").hidden = false;
  trefferZeigen(false);
}
$("#prompts-btn").onclick = promptsOeffnen;
$("#p-neu").onclick = () => {
  const k = fallKarte({ id: "", eigen: true, an: true, typ: "regex" });
  $("#p-faelle").appendChild(k);
  k.querySelector(".f-id").focus();
};
$("#p-server-neu").onclick = () => $("#p-server").appendChild(serverZeile({ typ: "openai", an: true }));
$("#p-vorschau").onclick = () => trefferZeigen(true);
$("#pmodalzu").onclick = $("#pmodalabbruch").onclick = () => { $("#pmodal").hidden = true; };
$("#pmodalok").onclick = async () => {
  let data;
  try { data = promptsSammeln(); } catch (e) { $("#p-fehler").textContent = e.message; return; }
  try {
    await api("/api/prompts", { data });
    $("#pmodal").hidden = true;
    meldung("Agent-Einstellungen gespeichert.");
    modelleLaden();   // Server koennen sich geaendert haben
    if (ZUSTAND.stats) aktualisieren();   // Marker in der Liste neu zeigen
  } catch (e) { $("#p-fehler").textContent = e.message; }
};

function meldung(text, warn) {
  const h = $("#hinweis");
  h.hidden = false;
  h.className = "hinweis" + (warn ? " warn" : "");
  h.textContent = text;
}

/* ---------------------------------------------------------------- agent */
async function modelleLaden() {
  const { models } = await api("/api/models");
  const s = $("#model");
  s.innerHTML = "";
  if (!models.length) {
    s.appendChild(new Option("kein Modell-Server erreichbar", ""));
    $("#run").disabled = true;
  } else {
    models.forEach((m) => s.appendChild(new Option(m, m)));
  }
}
$("#reload-models").onclick = modelleLaden;

$("#run").onclick = async () => {
  const model = $("#model").value;
  const auftrag = $("#auftrag").value.trim();
  if (!model) { meldung("Kein Modell – laeuft Ollama oder LM Studio? Adressen stehen in den Agent-Einstellungen.", true); return; }
  if (!auftrag) { meldung("Was soll der Agent tun?", true); return; }
  $("#run").disabled = true;
  $("#agentstatus").innerHTML = "";
  try { await api("/api/agent/run", { auftrag, model }); } catch (e) { meldung(e.message, true); $("#run").disabled = false; return; }
  ZUSTAND.poller = setInterval(agentPollen, 700);
};

async function agentPollen() {
  const d = await api("/api/agent/status");
  const z = $("#agentstatus");
  z.innerHTML = "";
  d.events.slice(-40).forEach((e) => {
    let t = "", c = "";
    if (e.typ === "werkzeug") { c = "w"; t = "→ " + e.name + " " + JSON.stringify(e.args).slice(0, 90); }
    else if (e.typ === "fehler") { c = "f"; t = "✗ " + e.text.slice(0, 200); }
    else if (e.typ === "text") t = e.text.slice(0, 200);
    else if (e.typ === "vorschlag") t = "✓ Vorschlag #" + e.pid;
    if (t) z.appendChild(el("div", c, t));
  });
  z.scrollTop = z.scrollHeight;
  vorschlaegeZeichnen(d.proposals);
  if (!d.aktiv) {
    clearInterval(ZUSTAND.poller);
    $("#run").disabled = false;
    if (d.ergebnis && d.ergebnis.ok === false) meldung("Agent: " + d.ergebnis.fehler, true);
    else if (d.ergebnis) meldung("Agent fertig nach " + d.ergebnis.runden + " Runden: " + (d.ergebnis.zusammenfassung || "") + " – " + d.ergebnis.vorschlaege + " Vorschlaege warten.");
    aktualisieren();
  }
}

/* --------------------------------------------------------------- speichern */
$("#save").onclick = async () => {
  if (!confirm("Aenderungen jetzt in die Session-Datei schreiben?\n\nEine Sicherung liegt bereits daneben.")) return;
  try {
    const r = await api("/api/save", {});
    meldung("Gespeichert: " + r.records + " Records geschrieben. Sicherung: " + r.backup.split(/[\\/]/).pop());
    aktualisieren();
  } catch (e) { meldung(e.message, true); }
};

baumLaden();
modelleLaden();
