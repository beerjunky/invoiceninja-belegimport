# Changelog

## 1.1.0 – 2026-09-30

- Zahlung erfassen: „Bereits bezahlt“, Zahlungsdatum und Zahlungsart; Vorschlag aus dem Beleg
  (Kassenbon/Kartenbeleg, Amazon, PayPal, Lastschrift-Hinweise)
- Lieferanten-Rechnungsnummer in einem benutzerdefinierten Feld statt in `transaction_reference`
  (Dublettenprüfung berücksichtigt beide Felder)
- Neue Lieferanten erhalten das Land der eigenen Firma (vorher setzte Invoice Ninja das erste Land der Liste)

## 1.0.0 – 2026-09-25

Erste öffentliche Version.

- Drag & Drop für PDF, JPG, PNG, HEIC; Ausgabe + Dokument in Invoice Ninja v5
- Lokale Auslese (pdftotext / Tesseract) mit Gegenrechnung Brutto/Netto/MwSt
- Lieferanten-Abgleich, Kategorie-Vorschlag, Dubletten-Warnung (Archiv + Invoice Ninja)
- Lokales Archiv der Originale mit JSON-Metadaten, HEIC → JPG für den Upload
- Login mit scrypt-Hash, Login-Sperre, CSRF, CSP, gehärteter Container
