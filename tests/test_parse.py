"""Tests für die Beleg-Heuristik. Laufen ohne Tesseract: python -m unittest discover -s tests"""

import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from extractors.parse import parse_fields  # noqa: E402

TODAY = date(2026, 9, 24)
OWN = ["Muster IT", "Max Muster"]
VENDORS = [{"id": "v_ovh", "name": "OVH GmbH"}, {"id": "v_amz", "name": "Amazon"}]


def parse(text, vendors=VENDORS):
    return parse_fields(text, vendors, OWN, today=TODAY)


class RechnungenTest(unittest.TestCase):
    def test_zweispaltiges_layout_mit_bekanntem_lieferanten(self):
        r = parse("""OVH GmbH                                   Muster IT
St. Johanner Str. 41-43                    Max Muster
Rechnungsnummer: DE1895521            Rechnungsdatum: 01.08.2026
Fällig am: 15.08.2026
Summe netto                  30,42 €
MwSt. 19 %                    5,78 €
Gesamtbetrag                 36,20 €""")
        self.assertEqual((r["vendor"], r["vendor_id"]), ("OVH GmbH", "v_ovh"))
        self.assertEqual(r["date"], "2026-08-01")  # nicht das Fälligkeitsdatum
        self.assertEqual(r["gross"], "36.20")
        self.assertEqual((r["vat_rate"], r["vat_amount"]), (19, "5.78"))
        self.assertEqual(r["invoice_number"], "DE1895521")
        self.assertEqual(r["overall"], "hoch")

    def test_unbekannter_lieferant_und_ausgeschriebener_monat(self):
        r = parse("""Hetzner Online GmbH · Industriestr. 25 · 91710 Gunzenhausen
Muster IT, Max Muster
Rechnung Nr. R0012345678
Datum: 3. September 2026
Leistungszeitraum 01.08.2026 - 31.08.2026
Zwischensumme 10,00 EUR
Umsatzsteuer 19 % 1,90 EUR
Rechnungsbetrag 11,90 EUR""")
        self.assertEqual(r["vendor"], "Hetzner Online GmbH")
        self.assertIsNone(r["vendor_id"])
        self.assertEqual(r["date"], "2026-09-03")
        self.assertEqual(r["gross"], "11.90")
        self.assertEqual(r["invoice_number"], "R0012345678")

    def test_kleinunternehmer_ohne_ust(self):
        r = parse("""Max Mustermann Grafikdesign
Rechnung 2026-17 vom 12.09.2026
Logo-Entwurf 250,00 €
Gesamt 250,00 €
Gemäß § 19 UStG wird keine Umsatzsteuer berechnet.""")
        self.assertEqual(r["vat_rate"], 0)
        self.assertEqual(r["gross"], "250.00")
        self.assertEqual(r["invoice_number"], "2026-17")

    def test_ermaessigter_satz(self):
        r = parse("Fachbuchhandlung Beispiel e.K.\nBelegnr. 889-2\nSumme 49,90\nenth. MwSt 7% 3,26\nDatum 2026-09-01")
        self.assertEqual((r["gross"], r["vat_rate"]), ("49.90", 7))

    def test_usd_wird_gewarnt(self):
        r = parse("Shodan\nInvoice number: INV-2026-0042\nDate: 2026-09-02\nTotal $49.00")
        self.assertEqual(r["currency"], "USD")
        self.assertTrue(r["warnings"])

    def test_marktplatz_verkaeuferzeile(self):
        r = parse("""Rechnung
Verkauft von Amazon EU S.a r.l., Niederlassung Deutschland
Rechnungsdatum 07 September 2026
MAX MUSTER Rechnungsnummer LU651X7W9AEUI
Gesamtpreis 13,99 €
19% 11,76 € 2,23 €""", vendors=[])
        self.assertEqual(r["vendor"], "Amazon EU S.a r.l.")
        self.assertEqual((r["date"], r["gross"], r["vat_rate"]), ("2026-09-07", "13.99", 19))


class KassenbonTest(unittest.TestCase):
    def test_gegeben_und_rueckgeld_werden_ignoriert(self):
        r = parse("""MediaMarkt Leipzig
USB-C Kabel         12,99
Maus                24,99
SUMME EUR           37,98
Gegeben Bar         50,00
Rückgeld            12,02
MwSt 19%  netto 31,92  MwSt 6,06
24.09.26 14:33  Bon-Nr 4711""")
        self.assertEqual(r["gross"], "37.98")
        self.assertEqual(r["date"], "2026-09-24")
        self.assertEqual(r["invoice_number"], "4711")

    def test_ocr_ziffernfehler_werden_ueber_mwst_abgefangen(self):
        # Tesseract las 61,05 einmal als 61,95 und 2026 als 2926.
        r = parse("""ARAL Tankstelle
Betrag 61,05 EUR
Nettobetrag 51,38
MwSt 19,00 % 9,75
Gesamt 61,95 EUR
Datum: 22.09.2926 Beleg-Nr 88213""", vendors=[])
        self.assertEqual(r["gross"], "61.05")
        self.assertEqual(r["date"], "2026-09-22")


class ZahlungTest(unittest.TestCase):
    def test_kassenbon_bar_ist_bezahlt_am_belegdatum(self):
        r = parse("MediaMarkt Leipzig\nSUMME EUR 37,98\nGegeben BAR 50,00\nRückgeld 12,02\n24.09.26 14:33 Bon-Nr 4711")
        self.assertEqual((r["paid"], r["payment_date"], r["payment_type_id"]), (True, "2026-09-24", "2"))

    def test_kartenzahlung_schlaegt_bar(self):
        r = parse("GLOBUS\nSUMME 25,89\nGegeben girocard 25,89\nKundenbeleg\nDatum 31.07.2026")
        self.assertEqual((r["paid"], r["payment_type_id"]), (True, "3"))

    def test_online_rechnung_mit_zahlungsdatum(self):
        r = parse("""HB-DIGITAL GmbH
Rechnung AU20263733709 02.09.2026
Gesamtpreis Brutto 7,20 €
Zahlung (Amazon Payment) vom 03.09.2026 7,20 €""")
        self.assertTrue(r["paid"])
        self.assertEqual(r["payment_date"], "2026-09-03")

    def test_paypal(self):
        r = parse("Shop GmbH\nRechnungsdatum 10.09.2026\nGesamt 19,99 €\nBezahlt per PayPal")
        self.assertEqual((r["paid"], r["payment_date"], r["payment_type_id"]), (True, "2026-09-10", "13"))

    def test_lastschrift_ist_noch_offen(self):
        r = parse("STRATO GmbH\nRechnungsdatum: 13.09.2026\nGesamtbetrag 18,00 EUR\n"
                  "Der Betrag wird von Ihrem Konto abgebucht.")
        self.assertEqual((r["paid"], r["payment_date"], r["payment_type_id"]), (False, None, "42"))

    def test_zahlungsziel_ist_offen(self):
        r = parse("Firma X GmbH\nRechnungsdatum 01.09.2026\nGesamtbetrag 119,00 €\n"
                  "Zahlbar bis 15.09.2026 per Überweisung auf das Konto DE12 3456")
        self.assertEqual((r["paid"], r["payment_type_id"]), (False, "1"))

    def test_ohne_hinweis_nichts_vorschlagen(self):
        r = parse("Firma X GmbH\nRechnungsdatum 01.09.2026\nGesamtbetrag 119,00 €")
        self.assertEqual((r["paid"], r["payment_type_id"]), (False, None))


class ZahlungEchteBelegeTest(unittest.TestCase):
    """Formulierungen aus echten Belegen (anonymisiert)."""

    def test_amazon_rechnung_ist_bezahlt(self):
        r = parse("Amazon EU S.a r.l.\nRechnungsdatum 04.08.2026\nZahlungsreferenznummer 4HN3YN8OUCJBH700\nZahlbetrag 23,70 €")
        self.assertEqual((r["paid"], r["payment_date"]), (True, "2026-08-04"))

    def test_kartenzahlung_zerlegt_mit_sepa_ist_ec(self):
        r = parse("GLOBUS\nSUMME 25,89\nKartenzah | ung\nSEPA Lastschrift\nZahlung erfolgt\nDatum 31.07.2026")
        self.assertEqual((r["paid"], r["payment_type_id"]), (True, "3"))

    def test_abbuchung_von_konto_ist_lastschrift(self):
        for satz in ("Der oben ausgewiesene Rechnungsbetrag wird von dem folgenden Konto abgebucht:",
                     "Der Rechnungsbetrag wird von folgendem Konto eingezogen:",
                     "Wir buchen den Betrag am 30.09.2026 unter Ihrer Mandatsreferenz MAN-1 von Ihrem Konto ab."):
            r = parse(f"Firma X GmbH\nRechnungsdatum 01.09.2026\nGesamtbetrag 21,60 €\n{satz}")
            self.assertEqual((r["paid"], r["payment_type_id"]), (False, "42"), satz)

    def test_angegeben_ist_nicht_bar(self):
        r = parse("Shop GmbH\nRechnungsdatum 20.09.2026\nGesamt 12,95 €\n"
                  "Wir bitten diese Rechnung spätestens bis zum oben angegebenem Zahlungsziel zu bezahlen.")
        self.assertEqual((r["paid"], r["payment_type_id"]), (False, None))


class RobustheitTest(unittest.TestCase):
    def test_eigene_firma_wird_nie_lieferant(self):
        r = parse("Muster IT GmbH\nRechnung\nGesamt 10,00 €", vendors=[{"id": "x", "name": "Muster IT"}])
        self.assertNotEqual(r["vendor"], "Muster IT")

    def test_zukunftsdatum_und_prozent_sind_kein_betrag(self):
        r = parse("Firma X GmbH\nDatum 01.01.2030\nMwSt 19,00 %")
        self.assertIsNone(r["date"])
        self.assertIsNone(r["gross"])

    def test_leerer_text(self):
        r = parse("")
        self.assertEqual(r["overall"], "niedrig")
        self.assertIsNone(r["gross"])


if __name__ == "__main__":
    unittest.main()
