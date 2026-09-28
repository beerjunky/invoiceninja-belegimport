"""Austauschbare Beleg-Auslese. Provider per EXTRACTOR in der .env (tesseract | none).

Jeder Provider liefert ein dict mit den Feldern
vendor, vendor_id, date, gross, vat_rate, vat_amount, invoice_number, currency,
confidence (pro Feld: hoch/mittel/niedrig), overall, method, warnings.
Werte sind immer nur Vorschläge – gespeichert wird erst nach Bestätigung im Formular.
"""

from .parse import parse_fields


def get_extractor(name):
    name = (name or "none").strip().lower()
    if name == "none":
        return None
    if name == "tesseract":
        from .tesseract import TesseractExtractor
        return TesseractExtractor()
    raise ValueError(f"Unbekannter EXTRACTOR: {name}")


__all__ = ["get_extractor", "parse_fields"]
