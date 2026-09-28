# Invoice Ninja v5 API – Erkenntnisse

Getestet gegen Invoice Ninja v5 (self-hosted, `invoiceninja/invoiceninja-debian`), Stand September 2026.

Header: `X-API-TOKEN: <token>`, `X-Requested-With: XMLHttpRequest`. Basis: `https://<instanz>/api/v1`.

| Aufruf | Ergebnis |
|---|---|
| `GET /vendors?per_page=100&status=active` | Lieferanten, IDs sind Hashids (z. B. `7LDdwRb1YK`) |
| `GET /expense_categories` | Kategorien – in frischen Instanzen **leer** |
| `POST /expenses` (JSON) | legt Ausgabe an, Nummer wird fortlaufend vergeben |
| `POST /expenses/{id}/upload` multipart `documents[]` **+ `_method=PUT`** | hängt Dokument an |
| dasselbe **ohne** `_method=PUT` | **404** |
| `POST /expenses/bulk {"action":"delete","ids":[…]}` | nur Soft-Delete |
| `GET /expenses?vendor_id=<id>` | filtert korrekt nach Lieferant |

## Eigenheiten

- **Löschen ist nur ein Soft-Delete**: Die Ausgabe bleibt mit Status `deleted` in der Datenbank, ihre
  Nummer ist verbraucht. Deshalb löscht die App bei fehlgeschlagenem Upload nicht, sondern markiert.
- **`GET /expenses` ohne `status=`** liefert auch gelöschte Einträge mit.
- **`?transaction_reference=` wird ignoriert** (liefert alle Ausgaben) – Dubletten müssen clientseitig
  gefiltert werden.
- `amount` als Zahl; Validierungsfehler kommen als HTTP 422 mit `errors.<feld>[]` (lokalisiert).
- `uses_inclusive_taxes=true` + `amount` = Brutto funktioniert wie erwartet.
- `tax_name1` ist Freitext – die App verwendet einheitlich „USt“.
