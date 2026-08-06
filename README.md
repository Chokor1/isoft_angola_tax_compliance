# Isoft Angola Tax Compliance

Angolan withholding-at-source for ERPNext v13: **retenção na fonte (Imposto
Industrial, 6,5% on services)** and **IVA cativo (50% / 100% of the VAT)**.

Both regimes are the same mechanism with a different base, so this app runs
them through one engine. Everything that differs — the rate, the validity
window, the account, the SAF-T type, the base, the scope — is configuration on
a `Tax Withholding Category`, not code. A change in the law is a new dated rate
row, not an edit to every customer record.

|  | Retenção II | IVA Cativo |
|---|---|---|
| SAF-T `WithholdingTaxType` | `II` | `IVA` |
| Base | net amount of service lines | the VAT charged |
| Scope | item / item group | customer |
| Changes `grand_total`? | no | no |
| Changes the receivable? | yes | yes |

## Why not a separate Journal Entry

The withholding is posted **inside the invoice's own GL entries**:

```
Dr  3419  Imposto Industrial Retido na Fonte      6,500
Dr  34551 IVA Cativo Retido                       7,000
Cr  3119  Clientes  [party = customer]           13,500
```

`grand_total` stays at the full document value, so SAF-T `GrossTotal` and the
AGT payload remain correct. `outstanding_amount` drops on its own because
ERPNext derives it from the GL balance on `debit_to` with `against_voucher`.

One voucher means cancel, amend and repost are atomic, and the posting date,
conversion rate and cost centre are the invoice's by construction. This is the
same shape ERPNext itself uses in `make_write_off_gl_entry`, and the same thing
core TDS does on the purchase side with `add_deduct_tax: "Deduct"`.

## The legacy path is gone

The in-core customization has been **deleted** from ERPNext — there is now
exactly one code path for retenção and IVA cativo. Removed:

| File | Removed |
|---|---|
| `sales_invoice.py` | `apply_vat_exemption`, `cancel_vat_exemption_journal_entry`, `calculate_withholding_tax_amount`, `create_withholding_tax_journal_entry_auto`, the three whitelisted duplicates, and all four call sites |
| `accounts_controller.py` | `evaluate_item_withholding_tax` |
| `sales_invoice.js` | the `apply_tax_withholding_on_service` client handlers |
| `company.json` | `default_vat_exempt_account`, `default_tax_withholding_account` |
| `customer.json` | `enable_vat_exemption`, `vat_exemption_percent`, `withholding_tax` |
| `item.json` | `apply_tax_withholding` |

Kept on purpose:

- **`Sales Invoice.apply_tax_withholding_on_service` and
  `total_tax_withholding_amount`** — moved from core DocFields to **hidden,
  read-only Custom Fields owned by this app**, because print formats read them.
  The engine writes them; nothing reads them back. The write is one-directional
  by design, so the old flag can no longer influence any calculation.
  `total_tax_withholding_amount` keeps its original meaning of *retenção II
  only* — IVA cativo is not folded into it.
- **`Journal Entry.is_tax_withholding` / `is_vat_exemption`** — historical
  vouchers still carry them, and `api.get_withholdings()` falls back to them for
  invoices booked before the cutover.
- **`Customer` / `Item` / `Sales Invoice Item.tax_withholding_category`** —
  upstream ERPNext fields used by core TDS/TCS, not fork additions. The dangling
  `depends_on: doc.withholding_tax == 'Party Based'` on the Customer one was
  cleared, since it referenced a removed field and would have hidden it forever.
- **Everything on the purchase side** — a separate legacy customization that this
  app does not replace. Core TDS already posts inside the Purchase Invoice.

### Consequences for the modes

With no legacy path left, two mode semantics changed:

- **`Off` now means no withholding at all**, not "fall back to the old
  behaviour".
- **`Shadow`'s comparison log has nothing left to compare against** — every row
  reads `New Only`. Shadow is still useful as "compute and store, post no GL",
  but its reconciliation purpose ended at the cutover.

## No ERPNext files are modified by the app itself

Three hook points only:

| Need | Mechanism |
|---|---|
| Fields | Custom Fields, all prefixed `atc_`, created in `after_install` / `after_migrate` |
| Computation | `AngolaSalesInvoice.validate`; `doc_events` validate for Quotation |
| GL entries | `AngolaSalesInvoice.get_gl_entries` |

`hooks.py` registers `override_doctype_class` for Sales Invoice.

During the cutover the same subclass also neutralised the legacy in-core path at
runtime, by shadowing the method names ERPNext called on `self`. That scaffolding
has been removed along with the legacy code it was suppressing.

> POS Invoice subclasses SalesInvoice by direct import, so the override does
> not reach it. POS Awesome issues Sales Invoice with `is_pos = 1`, which is
> covered — see the POS section.

## When it applies

There is no settings doctype and no mode. Retenção na fonte and IVA cativo are
Angolan law, so the condition is the company's country:

```
Company.country == "Angola"   ->   the engine computes and books
```

Nothing to switch on, nothing to forget. A company anywhere else is untouched,
so installing on a multi-country bench is safe.

The one remaining switch is POS, on the **POS Profile**, because a shop may
legitimately not want withholding on counter sales. It is off by default.

A misconfigured category — no rate in force, no account for the company, no base
tax account for cativo — raises on submit rather than booking a silently wrong
amount on a fiscal document.

### Superseded core fields

`Customer.tax_withholding_category` is hidden by a Property Setter. It belongs to
upstream ERPNext TDS/TCS, which is not the Angolan mechanism; the
**Angolan Withholding** table on the customer is what drives this engine.
Hiding is reversible and touches no ERPNext file.

### Abandoned doctypes

`Tax Withholding` and `Tax Withholding Item` are left over from an unfinished
item-based experiment. They were created as *standard* doctypes in ERPNext's
`Accounts` module, so Frappe looks for a Python module that no longer exists and
opening the list view raises `ImportError`. `cleanup.py` reports them, can stop
the crash without touching data, and can remove them outright — explicitly, never
from a hook, because dropping a table must not be a side effect of `bench update`.

## Installing

```bash
bench --site <site> install-app isoft_angola_tax_compliance
```

Installing changes no behaviour: with no settings record a company resolves to
mode `Off`.

Two of this app's fields — `apply_tax_withholding_on_service` and
`total_tax_withholding_amount` — exist as **DocFields** on installs whose
ERPNext still carries the older in-core withholding customization. Frappe
refuses to create a Custom Field over an existing DocField, so the installer
detects that case and skips those two, printing:

```
skipping custom field Sales Invoice.apply_tax_withholding_on_service:
a DocField of that name already exists
```

This is correct, not a workaround: the field is already present and writable, so
the engine works unchanged. Once the DocField is removed from ERPNext, the next
`bench migrate` finds the name free and creates the Custom Field, taking
ownership with no further action. Custom Fields this app already owns are
updated rather than skipped, so definition changes still propagate.

## Configuration

1. **Accounts** — one asset account per regime, e.g. `3419 Imposto Industrial
   Retido na Fonte` and `34551 IVA Cativo Retido`, following the Angolan PGC.
2. **Tax Withholding Category** — one per regime:
   - *Retenção II*: type `II`, base `Item Net Amount`, scope `Item Based`,
     rate row 6.5% with from/to dates, account per company.
   - *IVA Cativo 50%*: type `IVA`, base `Tax Amount`, scope `Party Based`,
     base tax accounts = the IVA Liquidado account(s), rate row 50%.
3. **Customer → Withholdings** — a child table, so a customer can be subject to
   several regimes at once. Close `valid_to` rather than deleting a row when a
   dispensa is granted.
4. **Item / Item Group → Withholding Category (Angola)** — for the item-based
   regime. Resolution at invoice time, most specific first:
   `SI Item → Item → Item Group tree`.

   Tagging thousands of items by hand is not the intent. Each category can claim
   items itself, under **Automatic Item Assignment** on the category:

   | Rule | Matches |
   |---|---|
   | All Non-Stock Items | every item with `is_stock_item = 0` |
   | Item Groups | the listed groups |

   Each item group row carries two qualifiers:

   - **Maintain Stock** — `Any`, `No` or `Yes`. A services group routinely holds
     both kinds (boxed software beside the support contract), so "the group"
     alone is often too blunt.
   - **Include Child Groups** — also match groups nested underneath.

   A group rule beats the non-stock rule, and among group rules the nearest
   ancestor of the item's group wins. That ordering is deliberate: *non-stock*
   is not *service* — a re-billed expense or a non-stock good is also non-stock,
   and conflating the two is exactly the defect in the legacy code this app
   replaced. The group rule is the precise one; non-stock is the convenient
   blanket.

   New items are stamped on insert. Existing items are left alone, so switching
   a rule on cannot quietly re-tag history — backfill them once, deliberately.

   **From the UI:** open the category and use **Assign to Existing Items**. It
   shows how many items match, a sample of them, and the rule being applied,
   then asks to confirm. The preview writes nothing.

   **From the shell**, same computation behind it:

   ```bash
   bench --site <site> execute isoft_angola_tax_compliance.auto_assign.backfill
   bench --site <site> execute isoft_angola_tax_compliance.auto_assign.backfill \
       --kwargs '{"confirm": true}'
   ```

   The dry run prints the rules, how many items have no category, and which ones
   each rule would claim. Only empty fields are filled; a value set by hand is
   never overwritten.

   If an item did not get a category, ask why rather than guess:

   ```bash
   bench --site <site> execute isoft_angola_tax_compliance.auto_assign.diagnose \
       --kwargs '{"item": "SRV-004"}'
   ```

   It walks the whole chain — are the rule fields migrated, is the checkbox
   ticked, is the scope `Item Based`, what is the item's group ancestry, what
   would a rule assign — and names the reason.
5. **Angola Tax Compliance Settings** — set the company's mode.

## NIF validation

Invalid Tax IDs are the usual reason AGT rejects an invoice, so the format is
checked where it is typed. Configured in **Angola NIF Validation Settings**
(a Single), off until enabled.

| Customer Type | Format | Example |
|---|---|---|
| Individual | 9 digits, 2 capital letters, 3 digits (14 chars) | `002282100LA037` |
| Company | 10 digits, all numeric, starting with 5 | `5002751267` |

Settings:

- **Enforcement** — `Warn` (message, save proceeds) or `Block` (refuses the save).
- **Only Validate New or Changed Values** (on by default) — without it, *every*
  save of an existing record with a bad NIF is refused, including edits that
  have nothing to do with the NIF. Clean up history through the report instead.
- **Where** — Customer, Quotation, Sales Invoice, independently.
- **Exemptions** — blank NIFs, plus a list of accepted placeholders
  (`999999999` for consumidor final by default).

Sales Invoice is checked against its own `tax_id` snapshot, since that is what
is printed and filed; Quotation has no such field, so the customer's current NIF
is used.

### Wrong type vs wrong number

A large share of invalid NIFs are *valid* NIFs on a record whose Customer Type
is wrong — an individual's `005364819LA044` on a customer set to Company. The
message says so, because the fix is the type, not the number:

> NIF **005364819LA044** is a valid **Individual** NIF, but this customer is set
> as **Company**. Change the Customer Type rather than the NIF.

### Finding the bad ones

Report **Invalid NIF** lists every customer that fails, split into
`Wrong Customer Type` and `Invalid Format`, with a suggested type and the number
of submitted invoices already issued against that customer — most-invoiced
first, since those are the ones already reaching AGT.

**Fix Customer Types** on that report bulk-corrects every customer whose NIF is
valid under the other type, after showing the count, the direction of each
change and a sample. Same from the shell:

```bash
bench --site <site> execute isoft_angola_tax_compliance.nif.fix_customer_types
bench --site <site> execute isoft_angola_tax_compliance.nif.fix_customer_types \
    --kwargs '{"confirm": true}'
```

It writes with `db.set_value`, not a full save: this is a one-field correction
over thousands of records, and a full save would fire every Customer hook —
including this app's own validation — on each one.

## Before updating an existing site

Enablement is the company's country, so an Angolan company goes live the moment
the code lands. If a category resolves for a customer but has no account on that
company, `set_withholdings` throws and **that customer cannot be invoiced**.

Check first — it writes nothing:

```bash
bench --site <site> execute isoft_angola_tax_compliance.readiness.check
```

It reports which companies go live, which categories exist, who is subject to
withholding, and — the part that matters — whether every category that can
resolve has an account on every Angolan company. It ends in `READY` or
`NOT READY` with the specific gaps.

## Migrating the legacy configuration

`migrate_legacy.py` converts the old setup into categories and customer rows.
It runs **automatically on every install and every migrate** from the
`after_install` / `after_migrate` hooks, so `bench update` on its own is enough
and a company or customer configured later still gets picked up.

It is deliberately *not* a patch. A patch runs once and is logged as done
forever — which is precisely how the first, broken version of this seeding
stayed invisible: it could not read the old fields, migrated nothing, and no
later `bench migrate` would ever retry it.

Output stays out of the way: one line when there is nothing to do, one line
when a config issue is outstanding, and the full report only when something was
actually created. Failures are logged and reported but never abort a migrate — a
seeding step must not be able to break every future update.

| Legacy | Becomes |
|---|---|
| `Company.default_vat_exempt_account` | `accounts` row on the IVA cativo category, per company |
| `Company.default_tax_withholding_account` | `accounts` row on the retenção II category, per company |
| `Customer.enable_vat_exemption` + `vat_exemption_percent` | a `Party Tax Withholding` row (50% or 100% category) |
| — | `Base Tax Accounts` detected from the `3453*` IVA Liquidado accounts |

**Always dry-run against a copy of production first:**

```bash
bench --site <site> execute isoft_angola_tax_compliance.migrate_legacy.plan     # report only
bench --site <site> execute isoft_angola_tax_compliance.migrate_legacy.execute  # apply
```

Properties worth relying on:

- **Reads the database columns, not meta.** `bench migrate` syncs DocTypes
  *before* it runs patches, so the same deploy that carries this migration is
  also the one that removes `enable_vat_exemption` and the two `Company`
  account fields from ERPNext. A `meta.get_field()` guard would see nothing and
  migrate nothing — silently, because "nothing to migrate" and "cannot see
  anything to migrate" look identical. Frappe never drops columns, so the data
  is still there and is read directly.
- **Seeds all three categories** — retenção II 6,5% plus IVA cativo at 50% and
  100% — whenever the company has an account for the regime. They are not
  conditioned on which percentages happen to appear on customers today: a
  missing category is a silent under-withholding.
- **Idempotent** — re-running reports `0 / 0 / 0`. Safe on every deploy.
- **Additive only** — never edits an existing rate row, never removes a customer
  row, never overwrites an account already configured by hand. Where the
  configured account differs from the legacy default, the report says so
  explicitly rather than implying an overwrite.
- **Changes no mode**, so it alters no accounting by itself.
- **Refuses to migrate a bad account.** If a company's legacy retenção II
  account is the *same* as its cativo account — a real configuration we hit,
  where both pointed at the IVA regularisation account — it reports `BLOCKED`
  instead of creating a category that would book Imposto Industrial into an IVA
  account. Create a separate account and add the row by hand.
- **Raises rather than swallowing.** A data migration that fails quietly is
  worse than a blocked deploy, and this one is safe to re-run.

Percentages other than 50 and 100 are reported as skipped rather than guessed.

**Items are deliberately not migrated.** The legacy rule was `is_stock_item = 0`,
which is not the same as "service" — reproducing it would carry the defect
forward. Tag the service Item Groups by hand.

## Quotation

Enabled per company: **Angola Tax Compliance Settings → Show on Quotations**
(default on). The same engine computes the same figures, but a Quotation posts
no GL, so this is **display and print only** — nothing reaches the ledger until
the invoice is raised.

The Quotation carries `atc_withholdings`, `atc_total_withholding_amount` and
**Net Amount Payable by Customer**, so a quote can show the client what they will
actually transfer versus the document total. There is no controller override —
just a `doc_events` validate handler, because there is no GL to hook.

Because it is indicative only, it is shown in **every mode the engine is on**,
including `Shadow`.

**A Quotation addressed to a Lead computes nothing.** Party-based regimes (IVA
cativo) cannot be evaluated without a customer, so showing only the item-based
half would silently understate the total. Convert the Lead first.

## POS (POS Awesome)

Enabled per shop: **POS Profile → Angolan Withholding → Enable Withholding
(Retenção / IVA Cativo)**. The checkbox is the authority for POS documents; the
company-wide `apply_on_pos` in the settings is only the fallback for profiles
that have it unset.

When **off**, POS behaves exactly as before: nothing is withheld, and customers
subject to a withholding regime are hidden from the POS customer list and
blocked at submit — selling to them would silently under-withhold. The
exclusion covers both the legacy `enable_vat_exemption` flag and the newer
`Party Tax Withholding` table.

When **on**:

- the payment screen shows a *Withholding* panel with one line per regime
  (type, rate, base, amount) plus *Net Amount Receivable*;
- every payment calculation is rebased from `grand_total` onto `net_payable`
  (= `grand_total − withholding`): the prefilled default payment, the "full
  amount" button, the To-Be-Paid / Change figure, the partial-payment guard and
  the customer-credit cap;
- `grand_total` itself is untouched, so the fiscal total is still correct.

The cashier therefore collects the net, the withheld part is debited to its
account inside the invoice, and the receivable closes to zero:

```
Dr  Clientes                     57,000.00
    Cr  IVA Liquidado MN                      7,000.00
    Cr  Vendas                               50,000.00
    Cr  Clientes                             50,250.00   ← cash collected
Dr  Caixa                        50,250.00
    Cr  Clientes                              6,750.00   ← withholding
Dr  Imposto Industrial Retido     3,250.00               ← II  6.5% of 50,000
Dr  IVA Cativo Retido             3,500.00               ← IVA 50% of 7,000
```

Changes live in `posawesome`: `api/posapp.py` (`pos_withholding_enabled`,
`get_withholding_customer_exclusion`, and the two gates) and
`public/js/posapp/components/pos/Payments.vue`. Both degrade safely on a bench
without this app installed — the POS Profile field is probed via meta and the
`Party Tax Withholding` exclusion via `frappe.db.exists`.

> POS Awesome issues **Sales Invoice** with `is_pos = 1`, not POS Invoice, so
> the `override_doctype_class` does reach it. A native ERPNext POS Invoice would
> not be covered.

## Exporters

Both fiscal outputs now read `isoft_angola_tax_compliance.api` — one owner of
the numbers, one row per regime, so a document subject to both II and IVA cativo
declares both.

| Consumer | Call | Was |
|---|---|---|
| `saft_xml` (`saft_xport_v2_dv.py`) | `get_withholdings_for_documents(company, names)` | `total_tax_withholding_amount` for II, plus a SQL scan of `tabJournal Entry Account` for cativo |
| `isoft_agt_electronic_invoicing` (`registar_factura.py`) | `get_withholdings(name, company)` | `total_tax_withholding_amount`, II only |

What this fixed:

- **IVA cativo now reaches AGT.** It was never declared electronically at all.
- **`WithholdingTaxType` is no longer hardcoded** to `II`, and
  `WithholdingTaxDescription` is emitted.
- **The unbounded Journal Entry scan is gone.** It had no date filter (it read
  every cativo JE in the company on every export) and filtered
  `credit_in_account_currency > 0`, which dropped credit notes.
- **Credit notes and wrong-signed legacy entries are handled.** Amounts are
  unsigned in the XML/JSON, taken as `abs()` of a signed value.
- **Keyed on the exact invoice list**, not a date range: SAF-T selects by
  `creation` while a period filter uses `posting_date`, and the two disagree at
  period edges.

Historical invoices keep exporting: when a document has no engine rows the API
falls back to its legacy Journal Entries, so re-exporting a closed period still
produces the same declarations.

Both integrations degrade safely — the import is guarded, so they still run on a
bench without this app installed (withholding simply is not declared there).

`DocumentTotals` is untouched throughout. `WithholdingTax` is a sibling of it in
the SAF-T schema, deliberately outside: the withheld amount is remitted to AGT
by the customer, so `GrossTotal` still carries the full invoice value.

Still open: `WithholdingTax` inside the SAF-T `Payment` element is commented out
and there is no Payment Entry support.

## Not yet built

- `Withholding Certificate` — the client's *comprovativo de retenção*, needed to
  reconcile the two asset accounts. IVA cativo offsets the monthly IVA
  declaration; II retido offsets the annual Imposto Industrial liquidation.
- *Mapa de Retenções* / IVA cativo reconciliation reports.
- Purchase side (core TDS already posts inside the invoice; needs the Angolan
  categories seeded plus a Mapa de Retenções).
- Payment-time recognition. If the accountant wants it, ERPNext's Payment Entry
  `deductions` table already does this natively with no custom code.

## Verification

The engine was validated end to end against a live install — sales invoices,
POS (both regimes on one ticket), quotations, credit notes, cancellations, the
legacy-config migration, and a full-year SAF-T declaration diff showing zero
regressions against the previous exporter.

Those harnesses are deliberately **not** part of this repository: they are
hardcoded to one deployment's companies, customers, chart of accounts and
document numbers, so they neither run nor mean anything elsewhere.

#### License

MIT
