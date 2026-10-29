# Cativo Auditing — design

Date: 2026-10-29 · App: `isoft_angola_tax_compliance` · Frappe/ERPNext v13, Python 3.8

## Intent

Accountants on each ERP need to see, for every sales invoice, how much IVA cativo the
books hold for it and where that cativo came from. On the live sites cativo reaches the
ledger four different ways, and only two of them are visible today (SAF-T Center's IVA
Summary shows the engine rows and the old tool's journal entries). Manual journal entries
and receipts paid into the cativo account are invisible per invoice, and their invoice
link is often wrong. The 2025 I-TEC review (AGT Campo 9 vs ERP, 12.2M Kz apart) was only
possible with an ad-hoc analysis run from dev.isoft.ao; this page puts the per-invoice part
of that analysis inside every ERP.

Reference result: the "Faturas" tab of the analysis dashboard
https://claude.ai/artifact/K7iKeLPpza7Du4vgbyj7aw#faturas

## Goals

- A desk page **Cativo Auditing** (`/app/cativo-auditing`) showing one table, **Invoices
  with cativo**: one row per sales invoice that has any cativo in the ERP.
- Per row: invoice, date, customer, NIF, status, IVA charged, cativo per source (new engine,
  old tool's automatic JE, manual JE, manual receipt), cativo total, % of IVA, effective rate
  (100% / 50% / Other), creation (System / Manual / System + Manual / Reversed), the cativo
  documents (clickable, one per line, with their type), the customer's current IVA category,
  and alerts.
- Filters: company, From/To (invoice date; default 1 Jan – 31 Dec of the current year),
  free-text search (invoice, customer, NIF, document number, alert, category), creation,
  rate, "only with alerts". Sortable columns.
- A totals row pinned to the bottom of the table, summing exactly the rows shown.
- One line under the table: **Cativo with no invoice identified: X Kz** (entries posted in
  the period that could not be matched to any invoice).
- The period rule is the reference dashboard's: an invoice in the period brings all its
  cativo, whenever posted; an unmatched entry counts by its own posting date. So the page
  does **not** equal the cativo accounts' ledger movement for the period, and says so in a
  one-line note under the table (Claude's call, after the builder check).
- Export to Excel of what is on screen (filtered, sorted rows plus totals and the line).
- A settings doctype **Cativo Audit Settings** holding, per company, the accounts the
  analysis needs, prefilled from the company's existing setup.

## Non-goals

- No comparison with AGT declarations (Campo 9), no stored declared amounts.
- No Resumo/Movimentos/Verificações/Excluídos tabs, no charts.
- No cross-site view; the page runs on each ERP for its own companies.
- Read-only: no fixing, re-allocating or posting.

## Anti-goals

- Losing or double counting cativo. Every counted movement on the cativo accounts belongs
  to exactly one place: its invoice's row (shown when the invoice date is in the period) or
  the "no invoice identified" line (shown when its own posting date is in the period). VAT
  closing entries are excluded by rule (see below) and never counted.
- Trusting the ERP link on manual journal entries blindly (it is often a reconciliation
  target, not the invoice the cativo is for).
- Mixing currencies: erp.itec.ao keeps its books in USD while its cativo accounts and
  invoices are in Kz. Amounts are taken in the account/document currency, never the USD base.

## Constraints

- Frappe v13, Python 3.8, no v14 APIs. Desk Page JS is cached in localStorage with no
  modified check (see memory: Desk Page JS localStorage cache); developer_mode or a cleared
  site cache ships changes.
- `frappe.call` in v13 may call neither callback on 502/508/403 — settle any promise in `always`.
- A Workspace must not share the slug `cativo-auditing`.
- Must run on newerp.itec.ao (≈600 cativo invoices across all years), erp.isoft.ao,
  erp.itec.ao. One request should stay well under the 120 s gunicorn timeout; target < 5 s
  for a year.
- Roles: System Manager, Accounts Manager (page and settings).

## Approaches considered

1. **Custom desk page + one whitelisted server method** (chosen). Python does the analysis;
   the page renders an Invenza-style shell like SAF-T Center. Full control of the pinned
   totals row, multi-document links, the unmatched line and export. Cost: most front-end work;
   desk page caching care.
2. **Script Report.** Same Python, Frappe's report view: filters, sorting, totals and export
   for free, no caching issues. Rejected because the user asked for a page in the Isoft style
   and clickable multi-document cells and the unmatched line fit poorly. Can be added later
   on top of the same server function.
3. Client-side analysis over raw rows: rejected (ships thousands of GL rows to the browser,
   duplicates logic).

## Data model

### Cativo Audit Settings (Single, module Isoft Angola Tax Compliance)

Child table **Cativo Audit Account** (`accounts`), rows:

| field | type | notes |
|---|---|---|
| company | Link Company | required |
| account | Link Account | required, must belong to the company |
| purpose | Select: `Cativo`, `IVA charged`, `VAT closing` | |

Plus a Small Text field **VAT closing keywords** (default `apuramento, abertura`): a journal
entry on a cativo account with no customer row whose note contains one of these words
(case- and accent-insensitive) is also a closing. This catches erp.itec.ao's 2024 closings
("IVA APURAMENTO MARÇO 24", "ABERTURA ANO 2024") that touch no closing account (Claude's
call, after the builder check).

- **Cativo**: accounts whose movement is cativo (34543 IVA Regularizações – Cativo, 34572
  IVA a Recuperar de Cativo).
- **IVA charged**: accounts whose sales-invoice tax rows are the invoice's IVA (34531… IVA
  Liquidado, 34541 IVA Regularizações a Favor da Empresa — where credit notes post IVA).
- **VAT closing**: accounts that identify a monthly VAT closing journal entry (IVA dedutível
  / liquidado accounts, e.g. 3452…, 3453…). A journal entry on a cativo account with **no
  customer row** and a row on one of these accounts is a closing and is excluded.

Button **Fill from company setup** (per company or all): proposes rows from what exists:
- Cativo: accounts in `Tax Withholding Account` of every `Tax Withholding Category` with
  `atc_withholding_type = 'IVA'`; VAT Assessment Settings `withheld_vat_recoverable_account`;
  leaf accounts named like `%Recuperar de Cativo%`.
- IVA charged: VAT Assessment Settings `output_vat_account`; leaf accounts starting `3453`
  or `34541`; Sales Taxes and Charges Template accounts whose account name contains
  "Liquidado".
- VAT closing: VAT Assessment Settings `deductible_vat_account` and `output_vat_account`;
  leaf accounts starting `3452` or `3453`.
It only adds missing rows; the user reviews and saves.

If a company has no rows when the page loads, the page uses the same derivation on the fly
and shows a notice linking to the settings.

## Server: `isoft_angola_tax_compliance/cativo_audit.py`

`@frappe.whitelist() get_cativo_audit(company, from_date=None, to_date=None)`
→ `{rows: [...], unassigned: {amount, count, entries: [...]}, accounts: {...}, warnings: [...]}`

Permission: `frappe.only_for(("System Manager", "Accounts Manager"))`.

### Movements (all amounts in account / document currency)

1. **New engine**: `tabSales Invoice Withholding` rows with `withholding_type = 'IVA'`,
   parent Sales Invoice `docstatus = 1`, amount `withholding_amount`. Cancelled invoices
   (docstatus 2) are ignored. Document = the invoice itself.
2. **GL on cativo accounts**: `tabGL Entry`, `is_cancelled = 0`, `voucher_type in ('Journal
   Entry', 'Payment Entry')`, account in the company's Cativo accounts; amount per voucher
   and account = `sum(debit_in_account_currency - credit_in_account_currency)`.
3. **Journal entries** are classified:
   - `is_vat_exemption = 1` → **old tool** (system). Invoice = Sales Invoice
     `reference_name` on its customer row, else the name after "Sales Invoice " in
     `user_remark`.
   - no customer row and (a row on a VAT closing account, or a note containing a VAT closing
     keyword) → **VAT closing**, excluded.
   - otherwise → **manual JE**, invoice resolved as below.
4. **Payment entries** → **manual receipt**. The amount is spread over its `Payment Entry
   Reference` rows to Sales Invoices in order, each up to its `allocated_amount`; any
   remainder is unassigned.

### Resolving a manual JE to an invoice

Inputs: the JE's customer (`party` of its customer row), its ERP links (Sales Invoice
`reference_name`s on customer rows), its note (`user_remark`, else first line of `remark`).

1. Extract invoice tokens from the note: regex
   `\b(FT|FR|NC)\s*([A-Z]{2,3}\d{4}|\d{4}|FT\d+S\d+N|FR\d+S\d+N|NC\d+S\d+N)?\s*/?\s*(\d+)\b`
   (case-insensitive). Candidates per token:
   - explicit series (`FR VIA2025/5372`, `FT FT3526S17184N/1660`) → that exact name;
   - year only (`FT2026/38`) → `FT 2026/38`, and for years ≥ 2026 also the AGT series
     `FT FT%N/38`;
   - bare (`FT398`) → `FT %/398`.
   Keep candidates that exist (docstatus > 0) and belong to the JE's customer. Use the first
   token that yields candidates.
2. One candidate → that invoice ("from the note"; if the ERP link differs, say so in the link
   text). Several → the ERP link if it is one of them, else unassigned ("ambiguous: …").
   None → the ERP link if any, else step 3.
3. The note names a receipt (`\b(RG|REC|RC) [A-Z0-9]+/\d+`) → the invoice that receipt was
   allocated to, if exactly one.
4. A negative unassigned JE (a reversal: "cliente pagou a totalidade", "Valor estava cativo
   no CBL…") → the invoice of the voucher named in its note, else of the latest earlier
   positive movement of the same customer and the exact opposite amount (only if all such
   matches share one invoice).
5. Otherwise unassigned ("not identified").

### Invoice rows

Group counted movements by invoice; keep invoices whose posting date is in [from, to]
(filter on the **invoice date**; movements count whatever their own posting date). For each:
`net_total`, `grand_total`, status (Submitted/Cancelled), `customer_name`, `tax_id`, IVA =
sum of `tax_amount_after_discount_amount` of its Sales Taxes and Charges rows on IVA charged
accounts, and the customer's current IVA category (`Party Tax Withholding` rows of the
customer whose category is `atc_withholding_type = 'IVA'`).

Derived (2 decimals): per-source sums; cativo = sum; pct = cativo / IVA (null when IVA = 0);
rate = `100%` if |pct − 1| ≤ 0.01, `50%` if |pct − 0.5| ≤ 0.01, else `Other` (blank when no
IVA); creation = `System + Manual` / `System` / `Manual` by which sides are non-zero (±0.005),
`Reversed` when a manual movement exists but everything nets to zero.

Alerts (joined with "; "): system and manual cativo on the same invoice; manual cativo
reversed (net zero); cancelled invoice with active cativo; cativo ≠ 50%/100% of IVA (shows the
%); invoice without IVA; ERP link differs from the note (for any of its manual JEs).

Documents: each movement's voucher with a tag — `invoice` (engine), `automatic JE`, `manual
JE`, `receipt` — distinct, in date order, each linking to its form.

### Unassigned

Movements with no invoice whose own posting date is in [from, to]: total, count and the list
(voucher, date, type, customer, amount, reason) for a tooltip/expand under the line.

## Page: `isoft_angola_tax_compliance/isoft_angola_tax_compliance/page/cativo_auditing/`

- Page JSON: title "Cativo Auditing", roles System Manager + Accounts Manager, module Isoft
  Angola Tax Compliance. IIFE JS + CSS, Invenza-style shell (slate palette, header with title,
  filters bar, card with the table), following SAF-T Center's structure.
- Filters bar: Company (default user default company), From, To (default current year), a
  Refresh action. Changing company or dates re-calls the server. Search, creation, rate and
  "only with alerts" filter client-side.
- Table: sticky header, sortable headers, numbers right-aligned in Kz with 2 decimals, the
  documents cell lists links (one per line) that open the document in a new tab (v13 router
  ignores `target=_blank`: catch clicks and `window.open`). Cancelled invoices struck through
  and red; alerts in red.
- Totals row pinned to the bottom (`position: sticky; bottom: 0`): count of invoices shown,
  IVA, each source, cativo, overall % (cativo ÷ IVA of shown rows).
- Under the table: "Cativo with no invoice identified: X Kz (N entries)" with an expandable
  list. Not affected by the client-side filters (it is about the period), stated in its label.
- Export: Excel of the visible table (headers, rows, totals row, the unassigned line), via a
  whitelisted method that only lays out what the page sends, text cells forced to string.
- States: loading skeleton, empty ("No invoices with cativo in this period"), error with the
  server message.
- Settings link in the header (gear) to Cativo Audit Settings; a notice when the company has
  no saved accounts and the derived defaults are in use.

## Decided

- Read-only ERP audit, no AGT data (user).
- Only the invoice table, named "Invoices with cativo", on a page called Cativo Auditing (user).
- Unassigned cativo shown as one line under the table (user).
- Custom desk page over a Script Report (Claude's call, user delegated).
- Settings: per-company account roles with derived defaults (Claude's call).

## Left to the builder

- Exact Invenza CSS tokens (copy SAF-T Center's shell and palette).
- Excel export library (openpyxl, already used by saft_xml exports).
- Query batching (chunk `IN (...)` lists; no row cap applies server-side).

## Verification

- On dev.isoft.ao, unit tests of the resolver (token parsing, candidate matching, reversal
  matching, receipt-named JEs) and of the row derivation, run **only** with
  `--app isoft_angola_tax_compliance` (never a bare run-tests).
- Cross-check against the Command Center analysis for newerp.itec.ao 2025:
  46,191,998.15 Kz total, of which 1,199,630.21 Kz unassigned and 181 invoices; erp.isoft.ao
  77,692,463.67 / 16,621,995.91 / 33; erp.itec.ao 54,248,140.87 / 5,856,236.17 / 67 — once
  deployed there, or by running the server function through the Command Center.
