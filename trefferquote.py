#!/usr/bin/env python3
"""Trefferquote der Auslese: vergleicht Vorschlag (extraction) mit dem gespeicherten Wert.

Aufruf im Projektverzeichnis:  python3 trefferquote.py [datenordner]
"""

import json
import sys
from decimal import Decimal
from pathlib import Path

FIELDS = {  # Feld im Vorschlag -> Feld im Sidecar
    "vendor": "vendor", "date": "date", "gross": "amount", "vat_rate": "tax_rate", "invoice_number": "invoice_number",
}


def same(key, proposed, saved):
    if proposed in (None, ""):
        return None  # nichts vorgeschlagen
    if key == "gross":
        return Decimal(str(proposed)) == Decimal(str(saved))
    if key == "vat_rate":
        return int(proposed) == int(saved)
    return str(proposed).strip().casefold() == str(saved).strip().casefold()


def main():
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "data/belege")
    stats = {k: [0, 0, 0] for k in FIELDS}  # richtig, falsch, leer
    rows = 0
    for sc in sorted(root.glob("*/*/*.json")):
        meta = json.loads(sc.read_text(encoding="utf-8"))
        x = meta.get("extraction")
        if not x or meta.get("status") != "ok":
            continue
        rows += 1
        marks = []
        for key, saved_key in FIELDS.items():
            r = same(key, x.get(key), meta.get(saved_key))
            stats[key][0 if r else 1 if r is False else 2] += 1
            marks.append("✓" if r else "✗" if r is False else "–")
        print(f"{' '.join(marks)}  {x.get('method', '?'):9} {x.get('overall', '?'):8} {sc.name[:-5]}")
    if not rows:
        print("Noch keine Belege mit Auslese-Vorschlag gespeichert.")
        return
    print(f"\n{rows} Belege  (✓ richtig, ✗ falsch, – nicht erkannt)")
    for key, (ok, bad, empty) in stats.items():
        print(f"  {key:15} {ok:3} richtig  {bad:3} falsch  {empty:3} leer   = {100 * ok // rows:3} %")


if __name__ == "__main__":
    main()
