"""Felder aus Beleg-Text ziehen (deutsche Rechnungen und Kassenbons).

Reine Heuristik ohne externe Abhängigkeiten, damit sie sich ohne Tesseract testen lässt.
"""

import re
from datetime import date, timedelta
from decimal import Decimal

MONTHS = {
    "jan": 1, "januar": 1, "feb": 2, "februar": 2, "mär": 3, "mar": 3, "märz": 3, "maerz": 3,
    "apr": 4, "april": 4, "mai": 5, "may": 5, "jun": 6, "juni": 6, "jul": 7, "juli": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "okt": 10, "oct": 10, "oktober": 10,
    "nov": 11, "november": 11, "dez": 12, "dec": 12, "dezember": 12,
}

# 1.234,56 | 1234,56 | 1,234.56 | 1234.56 | 1 234,56 – genau zwei Nachkommastellen.
AMOUNT_RE = re.compile(r"(?<![\d.,])(-?\d{1,3}(?:[.,' ]\d{3})+|-?\d+)([.,])(\d{2})(?![\d.,]*\d)(?!\s*%)")

GROSS_STRONG = re.compile(
    r"gesamtbetrag|rechnungsbetrag|endbetrag|zu\s*zahlen|zahlbetrag|bruttobetrag|summe\s*brutto|"
    r"gesamt\s*brutto|brutto\s*gesamt|total\s*brutto|betrag\s*brutto|amount\s*due|grand\s*total|"
    r"total\s*(?:incl|inkl)|gesamtsumme|rechnungssumme|endsumme|zu\s*begleichen", re.I)
GROSS_WEAK = re.compile(r"\b(?:gesamt|summe|total|betrag|brutto|eur\s*gesamt)\b", re.I)
NOT_GROSS = re.compile(
    r"netto|zwischensumme|subtotal|sub-total|rabatt|gegeben|rückgeld|rueckgeld|wechselgeld|"
    r"bar\b|kartenzahlung\s*gegeben|anzahlung|guthaben|stamm|kapital|iban|bic|konto", re.I)
VAT_WORDS = re.compile(r"mwst|mw\.?-?st|ust\b|ust\.|umsatzsteuer|mehrwertsteuer|\bvat\b|\btax\b", re.I)
ZERO_VAT = re.compile(
    r"steuerfrei|§\s*19\s*ustg|kleinunternehmer|reverse\s*charge|steuerschuldnerschaft\s*des\s*leistungsempf|"
    r"nicht\s*steuerbar|innergemeinschaftliche|0\s*%\s*(?:mwst|ust)|umsatzsteuer\s*0", re.I)
RATE_RE = re.compile(r"(?<!\d)(19|7|16|5)(?:[.,]0{1,2})?\s*%")

DATE_KEY = re.compile(r"rechnungsdatum|belegdatum|datum|invoice\s*date|date\b|ausgestellt", re.I)
DATE_BAD = re.compile(r"fällig|faellig|zahlbar\s*bis|zahlungsziel|leistungszeitraum|lieferdatum|geburt|gültig|"
                      r"due|period|bis\s*zum|vertragsbeginn|laufzeit", re.I)
DATE_NUM = re.compile(r"(?<!\d)(\d{1,2})[./](\d{1,2})[./](\d{4}|\d{2})(?!\d)")
DATE_ISO = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")
DATE_TXT = re.compile(r"(?<!\d)(\d{1,2})\.?\s+([A-Za-zäÄ]{3,9})\.?\s+(\d{4})(?!\d)")

INV_RE = re.compile(
    r"(?:rechnungs?[-\s]?(?:nr|nummer|no)|rechnung\s*(?:nr|no)|invoice\s*(?:no|number|nr|#)|"
    r"beleg[-\s]?(?:nr|nummer)|bon[-\s]?(?:nr|nummer)|re[-\s]?nr|rg[-\s]?nr|belegnummer)\.?\s*[:#]?\s*"
    r"([A-Z0-9][A-Z0-9\-/_.]{2,30})", re.I)
# "Rechnung 2026-17 vom …" – nur wenn der Wert direkt folgt und eine Ziffer enthält.
INV_BARE = re.compile(r"\brechnung\s+([A-Z0-9][A-Z0-9\-/_.]*\d[A-Z0-9\-/_.]*)", re.I)

LEGAL = re.compile(r"\b(gmbh(?:\s*&\s*co\.?\s*kg)?|ag|kg|ug|se|e\.\s?k\.?|ohg|gbr|inc\.?|ltd\.?|llc|b\.v\.|s\.a\.r\.l\.|sarl|s\.a\.)(?=\W|$)", re.I)
SELLER = re.compile(r"(?:verkauft\s+von|sold\s+by|umsatzsteuer\s+erkl\S*\s+durch|rechnungssteller)\s*:?\s*(\S.{2,})", re.I)
LEGAL_WORDS = re.compile(r"\b(gmbh|co|kg|ag|ug|se|ek|ohg|gbr|inc|ltd|llc|bv|sarl|sa|haftungsbeschränkt|mbh)\b")


def to_decimal(int_part, sep, cents):
    return Decimal(re.sub(r"[.,' ]", "", int_part) + "." + cents)


def amounts_in(line):
    return [to_decimal(*m.groups()) for m in AMOUNT_RE.finditer(line)]


def norm(s):
    s = s.casefold().replace("ß", "ss")
    s = re.sub(r"[^a-z0-9äöü]+", " ", s)
    return " ".join(s.split())


def core_name(name):
    return " ".join(w for w in norm(name).split() if not LEGAL_WORDS.fullmatch(w))


def gross_candidates(lines):
    """Brutto-Kandidaten, bestes zuerst: [(betrag, konfidenz), ...]."""
    cands = []
    for i, line in enumerate(lines):
        score = 3 if GROSS_STRONG.search(line) else 1 if GROSS_WEAK.search(line) else 0
        if not score or NOT_GROSS.search(line):
            continue
        if VAT_WORDS.search(line) and not re.search(r"inkl|incl|brutto", line, re.I):
            continue
        vals = [a for a in amounts_in(line) if a > 0]
        if not vals and i + 1 < len(lines):
            vals = [a for a in amounts_in(lines[i + 1]) if a > 0]
        if vals:
            cands.append((score, vals[-1]))
    # Gleiches Gewicht: höherer Betrag zuerst (Summe steht meist über Teilbeträgen).
    out = [(a, "mittel" if s >= 3 else "niedrig") for s, a in sorted(cands, reverse=True)]
    rest = sorted({a for line in lines if not NOT_GROSS.search(line) for a in amounts_in(line) if a > 0}, reverse=True)
    return out + [(a, "niedrig") for a in rest if a not in {c[0] for c in out}]


def vat_consistency(gross, text_amounts):
    """Prüft, ob zum Brutto passende MwSt-Beträge im Text stehen -> (satz, mwst)."""
    for rate in (19, 7):
        vat = (gross * rate / (100 + rate)).quantize(Decimal("0.01"))
        net = gross - vat
        for a in text_amounts:
            if abs(a - vat) <= Decimal("0.02") or abs(a - net) <= Decimal("0.02"):
                return rate, vat
    return None, None


def find_rate(text):
    if ZERO_VAT.search(text):
        return 0
    rates = {}
    for line in text.splitlines():
        if VAT_WORDS.search(line):
            for m in RATE_RE.finditer(line):
                rates[int(m.group(1))] = rates.get(int(m.group(1)), 0) + 1
    if not rates:
        return None
    return max(rates, key=rates.get)


def parse_date_match(m, kind):
    try:
        if kind == "num":
            d, mo, y = (int(x) for x in m.groups())
            y += 2000 if y < 100 else 0
            if 2800 <= y < 3000:  # OCR liest 0 gern als 8/9: 2926 -> 2026
                y -= 900 if y >= 2900 else 800
        elif kind == "iso":
            y, mo, d = (int(x) for x in m.groups())
        else:
            mo = MONTHS.get(m.group(2).casefold().rstrip("."))
            if not mo:
                return None
            d, y = int(m.group(1)), int(m.group(3))
        return date(y, mo, d)
    except ValueError:
        return None


def find_date(lines, today):
    earliest = today - timedelta(days=3 * 365)
    candidates = []
    for i, line in enumerate(lines):
        for rx, kind in ((DATE_NUM, "num"), (DATE_ISO, "iso"), (DATE_TXT, "txt")):
            for m in rx.finditer(line):
                d = parse_date_match(m, kind)
                if not d or not earliest <= d <= today:
                    continue
                prev = lines[i - 1] if i else ""
                if DATE_BAD.search(line[:m.start()]) or DATE_BAD.search(line):
                    score = 0
                elif DATE_KEY.search(line[:m.start()]) or DATE_KEY.search(prev):
                    score = 3
                else:
                    score = 1
                candidates.append((score, -i, d))
    if not candidates:
        return None, None
    score, _, d = max(candidates)
    return d, {3: "hoch", 1: "mittel", 0: "niedrig"}[score]


def find_invoice_number(lines):
    for i, line in enumerate(lines):
        m = INV_RE.search(line)
        tok = m.group(1).rstrip(".") if m else None
        if m and not re.search(r"\d", tok) and i + 1 < len(lines):
            # Kopfzeile "Rechnungsnummer   Datum", Wert eine Zeile darunter.
            nxt = re.search(r"[A-Z0-9][A-Z0-9\-/_.]{2,30}", lines[i + 1].strip(), re.I)
            tok = nxt.group(0) if nxt else None
        if tok and re.search(r"\d", tok) and not DATE_NUM.fullmatch(tok):
            return tok, "mittel"
    for line in lines:
        m = INV_BARE.search(line)
        if m and not DATE_NUM.fullmatch(m.group(1)):
            return m.group(1).rstrip("."), "niedrig"
    return None, None


def find_vendor(lines, vendors, own):
    own_n = [norm(o) for o in own if o.strip()]
    text_n = norm("\n".join(lines))
    hits = []
    for v in vendors:
        c = core_name(v["name"])
        if len(c) >= 3 and re.search(r"(?<![a-z0-9])" + re.escape(c) + r"(?![a-z0-9])", text_n) \
                and not any(c in o or o in c for o in own_n):
            hits.append((len(c), v))
    if hits:
        v = max(hits, key=lambda h: h[0])[1]
        return v["name"], v["id"], "hoch"

    # Unbekannt: Segmente (Spalten bei -layout) mit Rechtsform, zuerst Kopf, dann Fußzeile.
    segs = [s.strip() for line in lines for s in re.split(r"\s{3,}", line) if s.strip()]
    def ok(s):
        return len(s) >= 3 and not any(o in norm(s) for o in own_n) and re.search(r"[A-Za-z]{2}", s)
    # Marktplätze: "Verkauft von X" / "Umsatzsteuer erklärt durch X" nennt den Verkäufer ausdrücklich.
    for s in segs:
        m = SELLER.search(s)
        if m and ok(m.group(1)):
            name = m.group(1).strip(" ,.-·|")
            lf = LEGAL.search(name)
            name = name[:lf.end()] if lf else name.split(",")[0]
            if len(core_name(name)) >= 2:
                return name[:60], None, "niedrig"
    for pool in (segs[:15], segs[-25:]):
        for s in pool:
            m = LEGAL.search(s)
            if m and ok(s):
                name = s[:m.end()].strip(" ,-·|")
                name = re.sub(r"^(?:von|from|absender)\s*:?\s*", "", name, flags=re.I)
                if len(core_name(name)) >= 2:
                    return name, None, "niedrig"
    for s in segs[:5]:
        if ok(s) and not re.search(r"rechnung|invoice|quittung|beleg|kassenbon", s, re.I) and not AMOUNT_RE.search(s):
            return s[:60], None, "niedrig"
    return None, None, None


def parse_fields(text, vendors=(), own_names=(), today=None):
    today = today or date.today()
    lines = [l.rstrip() for l in text.splitlines() if l.strip()]
    conf, warnings = {}, []

    vendor, vendor_id, conf["vendor"] = find_vendor(lines, vendors, own_names)
    d, conf["date"] = find_date(lines, today)
    inv, conf["invoice_number"] = find_invoice_number(lines)

    # Kandidat, zu dem Netto/MwSt im Text rechnerisch passen, schlägt das Schlüsselwort-Ranking
    # (fängt OCR-Ziffernfehler wie 61,95 statt 61,05 ab).
    cands = gross_candidates(lines)
    gross, conf["gross"] = cands[0] if cands else (None, None)
    rate, vat = None, None
    all_amounts = [a for l in lines for a in amounts_in(l)]
    for a, _ in cands[:6]:
        rate, vat = vat_consistency(a, [x for x in all_amounts if x != a])
        if rate is not None:
            gross = a
            break
    if rate is not None:
        conf["gross"] = conf["vat_rate"] = "hoch"  # Brutto, Netto und MwSt passen zusammen
    else:
        rate = find_rate(text)
        conf["vat_rate"] = "mittel" if rate is not None else None
        if rate not in (None, 0, 7, 19):
            warnings.append(f"Ungewöhnlicher MwSt-Satz {rate} % erkannt")
            rate, conf["vat_rate"] = None, None

    currency = "EUR"
    if re.search(r"\bUSD\b|US\$|\$\s?\d", text) and not re.search(r"\bEUR\b|€", text):
        currency = "USD"
        warnings.append("Beleg scheint in USD zu sein – Betrag vor dem Speichern in EUR prüfen")

    found = [k for k in ("vendor", "date", "gross", "vat_rate") if conf.get(k)]
    levels = [conf[k] for k in found]
    overall = "niedrig" if len(found) < 3 or "niedrig" in levels else "hoch" if levels.count("hoch") >= 3 else "mittel"

    return {
        "vendor": vendor, "vendor_id": vendor_id,
        "date": d.isoformat() if d else None,
        "gross": f"{gross:.2f}" if gross else None,
        "vat_rate": rate, "vat_amount": f"{vat:.2f}" if vat else None,
        "invoice_number": inv, "currency": currency,
        "confidence": {k: v for k, v in conf.items() if v}, "overall": overall,
        "warnings": warnings,
        **find_payment(text, lines, d, today),
    }


# Zahlungsarten -> Invoice-Ninja-payment_type_id (statics). Reihenfolge = Priorität.
PAYMENT_PATTERNS = [
    ("13", re.compile(r"paypal", re.I)),
    ("7", re.compile(r"american\s*express|\bamex\b", re.I)),
    ("5", re.compile(r"\bvisa\b", re.I)),
    ("6", re.compile(r"master\s*card", re.I)),
    ("20", re.compile(r"\bmaestro\b", re.I)),
    # OCR zerlegt "Kartenzahlung" gern ("Kartenzah | ung", "Kartenzah lung").
    ("3", re.compile(r"girocard|\bec[-\s]?karte|\bec[-\s]?cash|karten\s*zah\W{0,3}l?\W{0,2}ung|debit\s*card|\bdebit\b", re.I)),
    ("12", re.compile(r"kreditkarte|credit\s*card", re.I)),
    ("42", re.compile(r"lastschrift|mandatsreferenz|sepa[-\s]?mandat|abgebucht|eingezogen|abbuchung|von\s+ihrem\s+konto\s+ab\b", re.I)),
    ("2", re.compile(r"\bbar\b|barzahlung|\bcash\b|\bgegeben\b", re.I)),
    ("1", re.compile(r"überweisung|ueberweisung|bank\s*transfer", re.I)),
]
# Sofort bezahlt: Bon/Kartenbeleg, Online-Zahlung, ausdrücklicher Vermerk.
PAID_HINTS = re.compile(
    r"rückgeld|rueckgeld|kundenbeleg|händlerbeleg|haendlerbeleg|bereits\s+bezahlt|betrag\s+(?:dankend\s+)?erhalten|"
    r"zahlung\s+erhalten|zahlung\s+erfolgt|bezahlt\s+(?:am|mit|per|via)|beglichen|\bpaid\b|zahlung\s*\(.*?\)\s*vom|amount\s+paid|"
    r"zahlungsreferenznummer", re.I)  # Amazon stellt Rechnungen erst nach der Zahlung aus
# Offen: Zahlungsaufforderung ohne Hinweis auf bereits erfolgte Zahlung.
OPEN_HINTS = re.compile(r"zahlbar\s+(?:bis|innerhalb)|bitte\s+überweisen|bitte\s+ueberweisen|zahlungsziel|fällig\s+am|faellig\s+am|due\s+date", re.I)
PAID_DATE = re.compile(r"(?:bezahlt\s+am|zahlung\s*(?:\(.*?\))?\s*vom|paid\s+on|zahlungsdatum)\s*:?\s*", re.I)


def find_payment(text, lines, invoice_date, today):
    """Vorschlag: bezahlt ja/nein, Datum, Zahlungsart. Nur Vorschlag – der Nutzer bestätigt."""
    ptype = next((pid for pid, rx in PAYMENT_PATTERNS if rx.search(text)), None)
    paid = bool(PAID_HINTS.search(text)) or ptype in ("2", "3", "5", "6", "7", "12", "13", "20")
    if OPEN_HINTS.search(text) and not PAID_HINTS.search(text):
        paid = False
    # Lastschrift wird erst später abgebucht -> Art vorschlagen, aber nicht als bezahlt markieren.
    if ptype == "42" and not PAID_HINTS.search(text):
        paid = False
    pdate = None
    if paid:
        for i, line in enumerate(lines):
            m = PAID_DATE.search(line)
            if m:
                rest = line[m.end():] + " " + (lines[i + 1] if i + 1 < len(lines) else "")
                pdate, _ = find_date([rest], today)
                if pdate:
                    break
        pdate = pdate or invoice_date
    return {
        "paid": paid,
        "payment_date": pdate.isoformat() if pdate else None,
        "payment_type_id": ptype,
    }
