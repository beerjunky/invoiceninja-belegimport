"""Ninja-Belegimport: Belege per Drag & Drop als Ausgabe in Invoice Ninja anlegen.

Copyright (c) 2026 Cornell Gräfe (CG-SEC) – MIT-Lizenz, siehe LICENSE.
"""

import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import sys
import threading
import time
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from functools import wraps
from pathlib import Path

import requests
from flask import Flask, abort, jsonify, redirect, render_template, request, session, url_for
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash

from extractors import get_extractor
from images import prepare_for_upload

MAX_UPLOAD_MB = 20
TAX_RATES = {"19": ("USt", 19), "7": ("USt", 7), "0": ("", 0)}

# Magic Bytes -> (Endung, MIME). HEIC/HEIF erkennt man am ftyp-Brand ab Offset 4.
HEIC_BRANDS = {b"heic", b"heix", b"hevc", b"hevx", b"mif1", b"msf1"}


def sniff_type(head: bytes):
    if head.startswith(b"%PDF-"):
        return "pdf", "application/pdf"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpg", "image/jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png", "image/png"
    if head[4:8] == b"ftyp" and head[8:12] in HEIC_BRANDS:
        return "heic", "image/heic"
    return None


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
log = logging.getLogger("belege")


class NinjaError(Exception):
    pass


class Ninja:
    """Schlanker Client für die Invoice-Ninja-v5-API. Token wird nie geloggt."""

    def __init__(self, base_url, token):
        self.base = base_url.rstrip("/") + "/api/v1"
        self.s = requests.Session()
        self.s.headers.update({"X-API-TOKEN": token, "X-Requested-With": "XMLHttpRequest", "Accept": "application/json"})

    def _call(self, method, path, **kw):
        try:
            r = self.s.request(method, self.base + path, timeout=30, **kw)
        except requests.RequestException as e:
            raise NinjaError(f"Invoice Ninja nicht erreichbar ({type(e).__name__})") from None
        if r.status_code >= 400:
            msg = f"HTTP {r.status_code}"
            try:
                body = r.json()
                if body.get("errors"):
                    msg += ": " + "; ".join(f"{k}: {' '.join(v)}" for k, v in body["errors"].items())
                elif body.get("message"):
                    msg += ": " + body["message"]
            except ValueError:
                pass
            raise NinjaError(msg)
        return r.json()

    def list_all(self, path):
        out, page = [], 1
        while True:
            data = self._call("GET", f"{path}?per_page=100&page={page}&status=active")
            out += data["data"]
            pag = data.get("meta", {}).get("pagination", {})
            if page >= pag.get("total_pages", 1):
                return out
            page += 1

    def vendors(self):
        return [{"id": v["id"], "name": v["name"]} for v in self.list_all("/vendors")]

    def categories(self):
        return [{"id": c["id"], "name": c["name"]} for c in self.list_all("/expense_categories")]

    def vendor_expenses(self, vendor_id):
        if not re.fullmatch(r"[A-Za-z0-9]{6,20}", vendor_id or ""):  # IN-Hashid, landet in der URL
            return []
        return self._call("GET", f"/expenses?vendor_id={vendor_id}&status=active&per_page=100&sort=date|desc")["data"]

    def last_category(self, vendor_id):
        """Kategorie der jüngsten Ausgabe dieses Lieferanten, die eine hat."""
        with_cat = [e for e in self.vendor_expenses(vendor_id) if e.get("category_id")]
        return max(with_cat, key=lambda e: e.get("date") or "")["category_id"] if with_cat else None

    def create_vendor(self, name):
        return self._call("POST", "/vendors", json={"name": name})["data"]

    def create_expense(self, payload):
        return self._call("POST", "/expenses", json=payload)["data"]

    def update_expense(self, expense_id, payload):
        return self._call("PUT", f"/expenses/{expense_id}", json=payload)["data"]

    def upload(self, expense_id, filename, content, mime):
        # Ohne _method=PUT antwortet IN mit 404.
        return self._call("POST", f"/expenses/{expense_id}/upload",
                          data={"_method": "PUT"}, files={"documents[]": (filename, content, mime)})["data"]


def create_app():
    app = Flask(__name__)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    secret = os.environ.get("SECRET_KEY")
    if not secret or len(secret) < 32:
        sys.exit("SECRET_KEY fehlt oder ist zu kurz (min. 32 Zeichen)")
    for var in ("NINJA_URL", "NINJA_TOKEN", "APP_USER", "APP_PASSWORD_HASH"):
        if not os.environ.get(var):
            sys.exit(f"{var} fehlt in der Umgebung")

    app.config.update(
        SECRET_KEY=secret,
        MAX_CONTENT_LENGTH=MAX_UPLOAD_MB * 1024 * 1024 + 64 * 1024,
        SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "1") == "1",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
        SESSION_COOKIE_NAME="belege_session",
        PERMANENT_SESSION_LIFETIME=8 * 3600,
    )

    ninja_url = os.environ["NINJA_URL"].rstrip("/")
    ninja = Ninja(ninja_url, os.environ["NINJA_TOKEN"])
    app_user = os.environ["APP_USER"]
    pw_hash = os.environ["APP_PASSWORD_HASH"]
    data_dir = Path(os.environ.get("DATA_DIR", "/data/belege"))
    extractor = get_extractor(os.environ.get("EXTRACTOR", "none"))
    # Eigene Namen (Rechnungsempfänger), damit die Auslese sie nicht für den Lieferanten hält.
    own_names = [n.strip() for n in os.environ.get("OWN_NAMES", "").split(",") if n.strip()]

    # Login-Drossel: 5 Fehlversuche pro IP in 15 Minuten, dann Sperre bis Fensterende.
    failures, fail_lock = {}, threading.Lock()
    FAIL_MAX, FAIL_WINDOW = 5, 15 * 60

    def locked_out(ip):
        with fail_lock:
            now = time.time()
            hits = [t for t in failures.get(ip, []) if now - t < FAIL_WINDOW]
            failures[ip] = hits
            return len(hits) >= FAIL_MAX

    def note_failure(ip):
        with fail_lock:
            failures.setdefault(ip, []).append(time.time())

    def csrf_token():
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(32)
        return session["csrf"]

    app.jinja_env.globals["csrf_token"] = csrf_token

    @app.before_request
    def check_csrf():
        if request.method == "POST":
            sent = request.headers.get("X-CSRF-Token") or request.form.get("csrf", "")
            if not session.get("csrf") or not hmac.compare_digest(sent, session["csrf"]):
                abort(400, "CSRF-Token ungültig")

    @app.after_request
    def security_headers(resp):
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' blob:; frame-src blob:; object-src 'none'; "
            "base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
        )
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Cache-Control"] = "no-store"
        return resp

    def login_required(f):
        @wraps(f)
        def wrapper(*a, **kw):
            if not session.get("user"):
                if request.path.startswith("/api/"):
                    return jsonify(error="Nicht angemeldet"), 401
                return redirect(url_for("login"))
            return f(*a, **kw)
        return wrapper

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.route("/login", methods=["GET", "POST"])
    def login():
        error = None
        if request.method == "POST":
            ip = request.remote_addr or "?"
            if locked_out(ip):
                log.warning("Login gesperrt fuer %s", ip)
                error = "Zu viele Fehlversuche. Bitte später erneut versuchen."
            else:
                user_ok = hmac.compare_digest(request.form.get("user", ""), app_user)
                pw_ok = check_password_hash(pw_hash, request.form.get("password", ""))
                if user_ok and pw_ok:
                    session.clear()
                    session.permanent = True
                    session["user"] = app_user
                    csrf_token()
                    log.info("Login ok von %s", ip)
                    return redirect(url_for("index"))
                note_failure(ip)
                log.warning("Login fehlgeschlagen von %s", ip)
                error = "Benutzer oder Passwort falsch."
        return render_template("login.html", error=error)

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.get("/")
    @login_required
    def index():
        return render_template("index.html", ninja_url=ninja_url, max_mb=MAX_UPLOAD_MB, extract=bool(extractor))

    @app.get("/api/lookups")
    @login_required
    def lookups():
        try:
            return jsonify(vendors=ninja.vendors(), categories=ninja.categories())
        except NinjaError as e:
            return jsonify(error=str(e)), 502

    def parse_amount(raw):
        raw = (raw or "").strip().replace(" ", "")
        if "," in raw:
            raw = raw.replace(".", "").replace(",", ".")
        try:
            val = Decimal(raw)
        except InvalidOperation:
            return None
        return val if val > 0 else None

    def store_local(content, ext, when, vendor, sha):
        folder = data_dir / f"{when:%Y}" / f"{when:%m}"
        folder.mkdir(parents=True, exist_ok=True)
        slug = re.sub(r"[^A-Za-z0-9]+", "-", vendor).strip("-")[:40] or "beleg"
        base = f"{when:%Y-%m-%d}_{slug}_{sha[:12]}"
        path, n = folder / f"{base}.{ext}", 1
        # Bewusst doppelt gespeichert ("trotzdem speichern"): eigener Name, damit kein Sidecar überschrieben wird.
        while path.with_suffix(path.suffix + ".json").exists():
            n += 1
            path = folder / f"{base}_{n}.{ext}"
        if not path.exists():
            path.write_bytes(content)
        return path

    def find_duplicates(sha=None, vendor_name="", vendor_id="", invoice_number="", amount=None, when=None):
        """Mögliche Dubletten aus dem lokalen Archiv und aus IN (auch Ausgaben ohne diese App)."""
        found, seen = [], set()
        inv = (invoice_number or "").strip().casefold()
        vname = (vendor_name or "").strip().casefold()
        amt = Decimal(str(amount)) if amount is not None else None

        for sc in data_dir.glob("*/*/*.json"):
            try:
                m = json.loads(sc.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not m.get("expense_id"):
                continue
            same_vendor = vname and m.get("vendor", "").casefold() == vname
            reason = None
            if sha and m.get("sha256") == sha:
                reason = "gleiche Datei"
            elif same_vendor and inv and m.get("invoice_number", "").casefold() == inv:
                reason = "gleiche Rechnungsnr."
            elif same_vendor and amt is not None and when and m.get("date") == when.isoformat() \
                    and Decimal(m.get("amount", "0")) == amt:
                reason = "gleicher Betrag am gleichen Tag"
            if reason and m["expense_id"] not in seen:
                seen.add(m["expense_id"])
                found.append({"reason": reason, "number": m.get("expense_number"), "date": m.get("date"),
                              "amount": m.get("amount"), "link": f"{ninja_url}/expenses/{m['expense_id']}/edit"})

        if vendor_id and (inv or (amt is not None and when)):
            try:
                for e in ninja.vendor_expenses(vendor_id):
                    if e["id"] in seen:
                        continue
                    ref = (e.get("transaction_reference") or "").strip().casefold()
                    if inv and ref == inv:
                        reason = "gleiche Rechnungsnr. in IN"
                    elif amt is not None and when and e.get("date") == when.isoformat() \
                            and Decimal(str(e.get("amount"))) == amt:
                        reason = "gleicher Betrag am gleichen Tag in IN"
                    else:
                        continue
                    seen.add(e["id"])
                    found.append({"reason": reason, "number": e.get("number"), "date": e.get("date"),
                                  "amount": str(e.get("amount")), "link": f"{ninja_url}/expenses/{e['id']}/edit"})
            except NinjaError as err:
                log.warning("Dublettenpruefung in IN nicht moeglich: %s", err)
        return found

    def write_sidecar(path, meta):
        path.with_suffix(path.suffix + ".json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    class UploadError(Exception):
        pass

    def read_upload():
        f = request.files.get("file")
        if not f or not f.filename:
            raise UploadError("Keine Datei übermittelt")
        content = f.read(MAX_UPLOAD_MB * 1024 * 1024 + 1)
        if len(content) > MAX_UPLOAD_MB * 1024 * 1024:
            raise UploadError(f"Datei größer als {MAX_UPLOAD_MB} MB")
        kind = sniff_type(content[:16])
        if not kind:
            raise UploadError("Dateityp nicht erlaubt (nur PDF, JPG, PNG, HEIC)")
        return f, content, kind

    @app.errorhandler(UploadError)
    def upload_error(e):
        return jsonify(error=str(e)), 400

    @app.post("/api/extract")
    @login_required
    def extract():
        if not extractor:
            return jsonify(error="Auslese ist deaktiviert"), 404
        _, content, (ext, _) = read_upload()
        try:
            vendors = ninja.vendors()
        except NinjaError:
            vendors = []
        started = time.monotonic()
        try:
            result = extractor.extract(content, ext, vendors, own_names)
        except Exception as e:  # OCR-Fehler dürfen den Import nie blockieren
            log.error("Auslese fehlgeschlagen: %s", type(e).__name__)
            return jsonify(error="Beleg konnte nicht ausgelesen werden – bitte manuell ausfüllen"), 422
        if result.get("vendor_id"):
            try:
                result["category_id"] = ninja.last_category(result["vendor_id"])
            except NinjaError:
                pass
        when = None
        if result.get("date"):
            when = date.fromisoformat(result["date"])
        result["duplicates"] = find_duplicates(
            hashlib.sha256(content).hexdigest(), result.get("vendor") or "", result.get("vendor_id") or "",
            result.get("invoice_number") or "", result.get("gross"), when)
        log.info("Auslese %s in %.1fs: Konfidenz %s", result.get("method"), time.monotonic() - started, result["overall"])
        return jsonify(result)

    @app.get("/api/category-suggestion")
    @login_required
    def category_suggestion():
        vendor_id = request.args.get("vendor_id", "")
        if not re.fullmatch(r"[A-Za-z0-9]{6,20}", vendor_id):
            return jsonify(category_id=None)
        try:
            return jsonify(category_id=ninja.last_category(vendor_id))
        except NinjaError as e:
            return jsonify(error=str(e)), 502

    @app.post("/api/expense")
    @login_required
    def create_expense():
        f, content, (ext, mime) = read_upload()

        form = request.form
        vendor_name = form.get("vendor_name", "").strip()
        vendor_id = form.get("vendor_id", "").strip()
        amount = parse_amount(form.get("amount"))
        tax = TAX_RATES.get(form.get("tax_rate", ""))
        try:
            when = datetime.strptime(form.get("date", ""), "%Y-%m-%d").date()
        except ValueError:
            when = None
        problems = [msg for ok, msg in (
            (vendor_name or vendor_id, "Lieferant fehlt"),
            (amount, "Betrag ungültig"),
            (tax, "MwSt-Satz ungültig"),
            (when and when <= date.today(), "Datum ungültig oder in der Zukunft"),
        ) if not ok]
        if problems:
            return jsonify(error=", ".join(problems)), 400

        sha = hashlib.sha256(content).hexdigest()
        invoice_number = form.get("invoice_number", "").strip()
        if form.get("force") != "1":
            if not vendor_id:
                try:
                    vendor_id = next((v["id"] for v in ninja.vendors() if v["name"].casefold() == vendor_name.casefold()), "")
                except NinjaError:
                    vendor_id = ""
            dups = find_duplicates(sha, vendor_name, vendor_id, invoice_number, amount, when)
            if dups:
                return jsonify(error="Möglicherweise schon erfasst", duplicates=dups), 409

        local = store_local(content, ext, when, vendor_name or vendor_id, sha)
        meta = {"sha256": sha, "original_name": f.filename, "vendor": vendor_name, "date": when.isoformat(),
                "amount": str(amount), "tax_rate": tax[1], "invoice_number": invoice_number,
                "category_id": form.get("category_id", ""), "imported_at": datetime.now().isoformat(timespec="seconds")}
        # Vorschlag der Auslese mitschreiben -> Trefferquote später auswertbar.
        raw = form.get("extraction", "")
        if raw and len(raw) < 8192:
            try:
                meta["extraction"] = json.loads(raw)
            except ValueError:
                pass

        try:
            if not vendor_id:
                match = next((v for v in ninja.vendors() if v["name"].casefold() == vendor_name.casefold()), None)
                if match:
                    vendor_id = match["id"]
                else:
                    vendor_id = ninja.create_vendor(vendor_name)["id"]
                    log.info("Vendor angelegt: %s", vendor_name)
            payload = {
                "vendor_id": vendor_id, "date": when.isoformat(), "amount": float(amount),
                "uses_inclusive_taxes": True, "tax_name1": tax[0], "tax_rate1": tax[1],
                "transaction_reference": meta["invoice_number"], "public_notes": form.get("note", "").strip(),
            }
            if form.get("category_id"):
                payload["category_id"] = form["category_id"]
            expense = ninja.create_expense(payload)
        except NinjaError as e:
            meta["status"] = f"fehlgeschlagen: {e}"
            write_sidecar(local, meta)
            log.error("Expense anlegen fehlgeschlagen (%s): %s", local.name, e)
            return jsonify(error=f"Ausgabe nicht angelegt: {e}. Beleg liegt lokal gesichert."), 502

        meta.update(expense_id=expense["id"], expense_number=expense.get("number"))
        link = f"{ninja_url}/expenses/{expense['id']}/edit"
        warning = None
        try:
            try:
                up, up_ext, up_mime, note = prepare_for_upload(content, ext, mime)
            except Exception as e:  # Umwandlung fehlgeschlagen -> Original hochladen
                log.warning("Bildaufbereitung fehlgeschlagen: %s", type(e).__name__)
                up, up_ext, up_mime, note = content, ext, mime, None
            if note:
                meta["upload_note"] = f"{note}: {len(content) // 1024} KB -> {len(up) // 1024} KB"
            ninja.upload(expense["id"], f"{local.stem}.{up_ext}", up, up_mime)
            meta["status"] = "ok"
        except NinjaError as e:
            # Vereinbart: Ausgabe bleibt bestehen (keine verbrauchten Nummern), wird aber markiert.
            meta["status"] = f"ohne Beleg: {e}"
            warning = f"Ausgabe angelegt, aber Beleg-Upload fehlgeschlagen ({e}). Als „Beleg fehlt“ markiert."
            try:
                ninja.update_expense(expense["id"], {"private_notes": f"BELEG FEHLT – Upload fehlgeschlagen, lokal: {local.name}"})
            except NinjaError as e2:
                warning += f" Markierung ebenfalls fehlgeschlagen ({e2})."
            log.error("Upload fehlgeschlagen fuer Expense %s: %s", expense["id"], e)
        write_sidecar(local, meta)
        log.info("Expense %s (%s) angelegt, %s EUR, Status %s", expense["id"], expense.get("number"), amount, meta["status"])
        return jsonify(ok=True, id=expense["id"], number=expense.get("number"), link=link, warning=warning)

    @app.errorhandler(413)
    def too_large(_):
        return jsonify(error=f"Datei größer als {MAX_UPLOAD_MB} MB"), 413

    @app.errorhandler(400)
    def bad_request(e):
        if request.path.startswith("/api/"):
            return jsonify(error=e.description), 400
        return e

    return app


if __name__ == "__main__":
    # Nur für lokale Tests; produktiv läuft die App unter gunicorn (siehe Dockerfile).
    # Passwort setzen: python3 set_password.py
    create_app().run(host="127.0.0.1", port=8013, debug=False)
