"use strict";

const $ = (id) => document.getElementById(id);
const csrf = document.querySelector('meta[name="csrf"]').content;
const maxBytes = Number(document.querySelector('meta[name="max-mb"]').content) * 1024 * 1024;
const extractEnabled = document.querySelector('meta[name="extract"]').content === "1";

let vendors = [];
// { file, url, status: "offen" | "gespeichert" | "fehler", info, extraction, extracting }
let items = [];
let current = -1;

const FIELD_OF = { vendor: "vendor", date: "date", gross: "amount", vat_rate: "tax", invoice_number: "invoice" };

function setStatus(text, kind) {
  const s = $("extract-status");
  s.hidden = !text;
  s.className = "msg " + (kind || "");
  s.textContent = text || "";
}

function markSuggested(id, conf) {
  const el = $(id);
  el.classList.add("vorschlag");
  el.dataset.conf = conf || "niedrig";
  el.title = "Vorschlag aus dem Beleg (Sicherheit: " + (conf || "niedrig") + ") – bitte prüfen";
}

function clearSuggested() {
  document.querySelectorAll(".vorschlag").forEach((el) => { el.classList.remove("vorschlag"); el.removeAttribute("title"); });
}

document.querySelectorAll("#expense input, #expense select").forEach((el) =>
  el.addEventListener("input", () => { el.classList.remove("vorschlag"); el.removeAttribute("title"); }));

async function suggestCategory(vendorId) {
  if (!vendorId || $("category").value) return;
  try {
    const r = await fetch("/api/category-suggestion?vendor_id=" + encodeURIComponent(vendorId));
    const data = await r.json();
    if (data.category_id && !$("category").value && [...$("category").options].some((o) => o.value === data.category_id)) {
      $("category").value = data.category_id;
      markSuggested("category", "mittel");
    }
  } catch { /* Vorschlag ist optional */ }
}

function applyExtraction(x) {
  const conf = x.confidence || {};
  const set = (key, value) => {
    const id = FIELD_OF[key];
    const el = $(id);
    if (value === null || value === undefined || value === "") return;
    // Nur leere bzw. unveränderte Standardfelder befüllen.
    if (id === "date" && el.value !== today()) return;
    if (id === "tax" && el.value !== "19") return;
    if (!["date", "tax"].includes(id) && el.value.trim()) return;
    el.value = String(value);
    markSuggested(id, conf[key]);
  };
  set("vendor", x.vendor);
  set("date", x.date);
  set("gross", x.gross ? x.gross.replace(".", ",") : null);
  set("vat_rate", x.vat_rate);
  set("invoice_number", x.invoice_number);
  $("vendor").dispatchEvent(new Event("input"));
  if (conf.vendor) markSuggested("vendor", conf.vendor);
  if (x.category_id && [...$("category").options].some((o) => o.value === x.category_id)) {
    $("category").value = x.category_id;
    markSuggested("category", "mittel");
  }
  const found = Object.keys(FIELD_OF).filter((k) => x[k] !== null && x[k] !== undefined).length;
  const msg = found
    ? `Vorschlag aus dem Beleg (${found} von 5 Feldern, Sicherheit: ${x.overall}) – bitte prüfen.`
    : "Keine Werte erkannt – bitte manuell ausfüllen.";
  setStatus([msg, ...(x.warnings || [])].join(" "), found && x.overall !== "niedrig" ? "ok" : "warn");
  if (x.duplicates && x.duplicates.length) {
    const s = $("extract-status");
    s.className = "msg warn";
    s.append(document.createElement("br"), "Achtung, möglicherweise schon erfasst:", duplicateList(x.duplicates));
  }
}

async function runExtraction(idx) {
  const it = items[idx];
  if (!extractEnabled || it.status !== "offen" || it.extraction || it.extracting) return;
  it.extracting = true;
  if (idx === current) setStatus("Beleg wird gelesen …");
  const fd = new FormData();
  fd.append("file", it.file);
  try {
    const r = await fetch("/api/extract", { method: "POST", body: fd, headers: { "X-CSRF-Token": csrf } });
    if (r.status === 401) return location.assign("/login");
    const data = await r.json().catch(() => ({ error: "HTTP " + r.status }));
    if (!r.ok) throw new Error(data.error);
    it.extraction = data;
    if (idx === current) applyExtraction(data);
  } catch (e) {
    it.extraction = { failed: true };
    if (idx === current) setStatus(e.message, "warn");
  } finally {
    it.extracting = false;
    // Weitere Belege im Hintergrund vorlesen, einer nach dem anderen.
    const next = items.findIndex((x) => x.status === "offen" && !x.extraction && !x.extracting);
    if (next >= 0) runExtraction(next);
  }
}

function today() {
  const d = new Date();
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
}

function showMsg(text, kind) {
  const m = $("msg");
  m.hidden = !text;
  m.className = "msg " + (kind || "");
  m.replaceChildren();
  if (text instanceof Node) m.append(text); else m.textContent = text || "";
}

async function loadLookups() {
  try {
    const r = await fetch("/api/lookups");
    if (r.status === 401) return location.assign("/login");
    const data = await r.json();
    if (!r.ok) throw new Error(data.error);
    vendors = data.vendors.sort((a, b) => a.name.localeCompare(b.name, "de"));
    $("vendors").replaceChildren(...vendors.map((v) => Object.assign(document.createElement("option"), { value: v.name })));
    const cats = data.categories.sort((a, b) => a.name.localeCompare(b.name, "de"));
    $("category").append(...cats.map((c) => Object.assign(document.createElement("option"), { value: c.id, textContent: c.name })));
  } catch (e) {
    showMsg("Lieferanten/Kategorien konnten nicht geladen werden: " + e.message, "err");
  }
}

function vendorMatch() {
  const name = $("vendor").value.trim().toLocaleLowerCase("de");
  return vendors.find((v) => v.name.toLocaleLowerCase("de") === name);
}

$("vendor").addEventListener("input", () => {
  const v = $("vendor").value.trim();
  const match = vendorMatch();
  $("vendor-hint").textContent = !v ? "" : match ? "✓ vorhandener Lieferant" : "Neuer Lieferant wird angelegt";
  if (match) suggestCategory(match.id);
});

function addFiles(files) {
  for (const file of files) {
    if (file.size > maxBytes) {
      items.push({ file, url: null, status: "fehler", info: "zu groß" });
      continue;
    }
    items.push({ file, url: URL.createObjectURL(file), status: "offen", info: "" });
  }
  renderList();
  if (current === -1 || items[current].status !== "offen") selectNext();
}

function renderList() {
  $("list").replaceChildren(...items.map((it, i) => {
    const li = document.createElement("li");
    li.className = it.status + (i === current ? " active" : "");
    const name = document.createElement("span");
    name.textContent = it.file.name;
    const st = document.createElement("em");
    st.textContent = it.info || it.status;
    li.append(name, st);
    li.addEventListener("click", () => select(i));
    return li;
  }));
}

function select(i) {
  current = i;
  const it = items[i];
  const pv = $("preview");
  pv.replaceChildren();
  const type = it.file.type || "";
  if (!it.url) {
    pv.innerHTML = '<p class="hint">Keine Vorschau.</p>';
  } else if (type === "application/pdf" || it.file.name.toLowerCase().endsWith(".pdf")) {
    const fr = document.createElement("iframe");
    fr.src = it.url;
    fr.title = it.file.name;
    pv.append(fr);
  } else if (/^image\/(jpeg|png|gif|webp)$/.test(type)) {
    const img = document.createElement("img");
    img.src = it.url;
    img.alt = it.file.name;
    pv.append(img);
  } else {
    const p = document.createElement("p");
    p.className = "hint";
    p.textContent = `Keine Vorschau im Browser für ${it.file.name} (HEIC) – wird trotzdem hochgeladen.`;
    pv.append(p);
  }
  resetForm();
  $("save").disabled = it.status !== "offen";
  if (it.status === "gespeichert") showMsg("Bereits gespeichert: " + it.info, "ok");
  renderList();
  if (it.status === "offen") {
    if (it.extraction && !it.extraction.failed) applyExtraction(it.extraction);
    else if (it.extracting) setStatus("Beleg wird gelesen …");
    else runExtraction(i);
  }
}

function selectNext() {
  const i = items.findIndex((it) => it.status === "offen");
  if (i >= 0) select(i);
  else {
    current = -1;
    $("save").disabled = true;
    $("preview").innerHTML = '<p class="hint">Alle Belege abgearbeitet.</p>';
    renderList();
  }
}

function resetForm() {
  $("expense").reset();
  $("date").value = today();
  $("date").max = today();
  $("vendor-hint").textContent = "";
  clearSuggested();
  setStatus("");
  showMsg("");
}

function duplicateList(dups) {
  const ul = document.createElement("ul");
  ul.className = "dups";
  for (const d of dups) {
    const li = document.createElement("li");
    const a = Object.assign(document.createElement("a"), { href: d.link, target: "_blank", rel: "noopener",
      textContent: `Ausgabe ${d.number || "?"}` });
    li.append(a, ` vom ${d.date}, ${String(d.amount).replace(".", ",")} € – ${d.reason}`);
    ul.append(li);
  }
  return ul;
}

$("expense").addEventListener("submit", (ev) => {
  ev.preventDefault();
  save(false);
});

async function save(force) {
  if (current < 0) return;
  const it = items[current];
  const match = vendorMatch();
  const fd = new FormData();
  fd.append("file", it.file);
  fd.append("vendor_name", match ? match.name : $("vendor").value.trim());
  fd.append("vendor_id", match ? match.id : "");
  fd.append("date", $("date").value);
  fd.append("amount", $("amount").value);
  fd.append("tax_rate", $("tax").value);
  fd.append("category_id", $("category").value);
  fd.append("invoice_number", $("invoice").value.trim());
  fd.append("note", $("note").value.trim());
  if (it.extraction && !it.extraction.failed) {
    const { duplicates, ...x } = it.extraction;
    fd.append("extraction", JSON.stringify(x));
  }
  if (force) fd.append("force", "1");

  $("save").disabled = true;
  showMsg("Speichere …");
  try {
    const r = await fetch("/api/expense", { method: "POST", body: fd, headers: { "X-CSRF-Token": csrf } });
    if (r.status === 401) return location.assign("/login");
    const data = await r.json().catch(() => ({ error: "HTTP " + r.status }));
    if (r.status === 409 && data.duplicates) {
      const box = document.createElement("div");
      const btn = Object.assign(document.createElement("button"), { type: "button", className: "secondary",
        textContent: "Trotzdem speichern" });
      btn.addEventListener("click", () => save(true));
      box.append("Möglicherweise schon erfasst:", duplicateList(data.duplicates), btn);
      showMsg(box, "warn");
      $("save").disabled = false;
      return;
    }
    if (!r.ok) throw new Error(data.error);
    it.status = "gespeichert";
    it.info = "Nr. " + data.number;
    if (!match) loadLookups();
    const a = Object.assign(document.createElement("a"), { href: data.link, target: "_blank", rel: "noopener", textContent: `Ausgabe ${data.number} in Invoice Ninja öffnen ↗` });
    const box = document.createElement("span");
    if (data.warning) box.append(data.warning, " ");
    box.append(a);
    selectNext();
    showMsg(box, data.warning ? "warn" : "ok");
  } catch (e) {
    showMsg(e.message, "err");
    $("save").disabled = false;
  }
}

const drop = $("drop");
drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
drop.addEventListener("dragleave", () => drop.classList.remove("over"));
drop.addEventListener("drop", (e) => { e.preventDefault(); drop.classList.remove("over"); addFiles(e.dataTransfer.files); });
$("picker").addEventListener("change", (e) => { addFiles(e.target.files); e.target.value = ""; });
// Datei versehentlich neben die Drop-Zone gezogen: Browser soll sie nicht öffnen.
window.addEventListener("dragover", (e) => e.preventDefault());
window.addEventListener("drop", (e) => e.preventDefault());

resetForm();
loadLookups();
