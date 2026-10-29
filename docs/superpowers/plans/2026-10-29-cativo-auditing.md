# Cativo Auditing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A read-only desk page `/app/cativo-auditing` in `isoft_angola_tax_compliance` listing every sales invoice with IVA cativo in the ERP, by source, with its cativo documents, alerts, a pinned totals row, the unmatched-cativo line and Excel export, configured by a per-company Cativo Audit Settings.

**Architecture:** A pure-Python module (`cativo_audit/resolve.py`, no frappe import) holds every rule: note parsing, invoice matching, VAT-closing detection, row derivation. A frappe-side module (`cativo_audit/data.py`) runs the queries and feeds it. `cativo_audit/api.py` exposes the whitelisted methods. The desk page renders what the server returns and filters client-side.

**Tech Stack:** Frappe/ERPNext v13, Python 3.8, unittest (no pytest in the bench env), openpyxl, plain desk Page JS (IIFE) + CSS.

**Spec:** `docs/superpowers/specs/2026-10-29-cativo-auditing-design.md` (read it first; this plan does not repeat its rules).

## Global Constraints

- Frappe v13 / Python 3.8: no builtin generics like `list[str]` in annotations (use `typing.List`), no v14 APIs.
- Amounts in account / document currency: GL `debit_in_account_currency - credit_in_account_currency`, SI `net_total`, `grand_total`, `tax_amount_after_discount_amount`, SIW `withholding_amount`. Never `base_*`.
- Roles for page, settings and every whitelisted method: System Manager, Accounts Manager (`frappe.only_for`).
- Read-only: no method of this feature writes to the database except saving Cativo Audit Settings from its own form.
- Period: invoice rows by **invoice** posting date in [from, to]; unassigned entries by their **own** posting date. Default from/to: 1 Jan – 31 Dec of the current year.
- Rate tolerance: |pct − 1| ≤ 0.01 → `100%`; |pct − 0.5| ≤ 0.01 → `50%`; else `Other`. Non-zero threshold: |x| > 0.005. Round money to 2 decimals.
- VAT closing keywords default: `apuramento, abertura` (case- and accent-insensitive).
- Tests run only as `../../env/bin/python -m unittest ...` from the app folder, or the read-only dev check script. **Never** `bench run-tests` without `--app` (it wipes Item Price / Leave / Salary Slip data on dev).
- Page JSON slug `cativo-auditing`; no Workspace may use that slug.
- Python changes need `bench restart` (gunicorn --preload). Doctype/page JSON reaches the DB with `bench --site dev.isoft.ao reload-doc`.
- User-facing strings in English wrapped in `_()` / `__()`.
- Commit steps run only if the user has asked for commits; otherwise leave changes staged-free and report them.

## Review Focus

1. **A manual JE whose ERP link and note disagree** (`Iva Cativo FT2026/41` linked to `FT FT3526S17184N/2019`): the note wins when its invoice belongs to the same customer, and the link text says the ERP link differs. Pinned in Task 1 (`test_note_beats_erp_link`).
2. **Ambiguous notes** (`FT2026/38` matching both `FT 2026/38` and `FT FT3526S17184N/38` of the same customer, ERP link elsewhere): the entry is unassigned with both candidates named, never silently assigned. Pinned in Task 1 (`test_ambiguous_note_is_unassigned`).
3. **A 2025 invoice whose manual cativo was posted in 2026**, viewed with 2025 selected: the row includes it; with 2026 selected, neither the row nor the unassigned line shows it. Pinned in Task 2 (`test_period_uses_invoice_date`).
4. **A company with no saved settings**: the page still works on derived accounts and shows a notice. Pinned in Task 3 (dev check prints `derived: True` for a company without rows).
5. **erp.itec.ao-style books (USD company, Kz cativo account)**: amounts come from account/document currency. Pinned in Task 3 (`test_load_uses_account_currency` asserts the GL query text selects `debit_in_account_currency` and the invoice query selects `tax_amount_after_discount_amount`, not `base_`).

---

## File Structure

```
isoft_angola_tax_compliance/
  cativo_audit/
    __init__.py
    resolve.py        # pure rules: tokens, candidates, picking, reversals, closings, rows, unassigned
    data.py           # frappe queries -> movement dicts, invoice dicts, account sets
    api.py            # whitelisted: get_cativo_audit, export_cativo_audit
  tests/
    __init__.py
    test_cativo_resolve.py   # unittest, pure
    test_cativo_data.py      # unittest, query-text checks with frappe.db mocked
    check_cativo_audit_dev.py  # read-only smoke on dev.isoft.ao
  isoft_angola_tax_compliance/
    doctype/cativo_audit_settings/   (Single)  .json .py .js __init__.py
    doctype/cativo_audit_account/    (child)   .json .py __init__.py
    page/cativo_auditing/            .json .js .css __init__.py
```

### Shared shapes (all tasks)

```python
Movement = {  # one per (voucher, account) and per PE reference split
  "source": "engine" | "legacy_je" | "manual_je" | "manual_pe",
  "voucher_type": "Sales Invoice" | "Journal Entry" | "Payment Entry",
  "voucher": str, "posting_date": "YYYY-MM-DD", "account": str,
  "amount": float, "invoice": Optional[str], "link": str,
  "customer": Optional[str], "note": str, "erp_refs": List[str],
}
Invoice = {
  "name": str, "posting_date": "YYYY-MM-DD", "customer": str, "customer_name": str,
  "tax_id": str, "docstatus": int, "is_return": int, "net_total": float,
  "grand_total": float, "iva": float, "category": Optional[str],
}
Row = {
  "invoice", "date", "customer", "nif", "status": "Submitted" | "Cancelled",
  "iva", "engine", "legacy_je", "manual_je", "manual_pe", "cativo",
  "pct": Optional[float], "rate": "100%" | "50%" | "Other" | "",
  "creation": "System" | "Manual" | "System + Manual" | "Reversed",
  "documents": List[{"voucher", "doctype", "tag"}],   # tag: invoice | automatic JE | manual JE | receipt
  "category": str, "alerts": List[str],
}
Accounts = {"cativo": List[str], "iva": List[str], "closing": List[str], "keywords": List[str], "derived": bool}
```

---

### Task 1: Matching rules (pure)

**Files:**
- Create: `isoft_angola_tax_compliance/cativo_audit/__init__.py`, `isoft_angola_tax_compliance/cativo_audit/resolve.py`
- Test: `isoft_angola_tax_compliance/tests/__init__.py`, `isoft_angola_tax_compliance/tests/test_cativo_resolve.py`

**Interfaces:**
- Produces (in `resolve.py`):
  - `note_tokens(text: str) -> List[Tuple[str, str, str]]` — (kind upper, series upper or "", number); regex from the spec.
  - `candidate_patterns(token) -> List[Tuple[str, str]]` — ("exact" | "like", SQL value).
  - `pick_invoice(candidates: List[str], erp_refs: List[str]) -> Tuple[Optional[str], str]` — (invoice, link text).
  - `named_receipts(text: str) -> List[str]` — `\b(?:RG|REC|RC) [A-Z0-9]+/\d+`.
  - `named_vouchers(text: str) -> List[str]` — `\b(?:CBL|REC|RC|RG|RE)\s?[\w/-]+`, for reversals.
  - `match_reversal(move: Movement, moves: List[Movement]) -> Tuple[Optional[str], str]`.
  - `is_vat_closing(has_customer_row: bool, other_accounts: List[str], closing_accounts: Set[str], note: str, keywords: List[str]) -> bool`.
  - `normalise(text: str) -> str` — lower-case, accents stripped (NFD).

- [ ] **Step 1: Write the failing tests** in `test_cativo_resolve.py` (`class TestResolve(unittest.TestCase)`):

```python
def test_note_tokens(self):
    self.assertEqual(note_tokens("Iva Cativo FT2026/38"), [("FT", "2026", "38")])
    self.assertEqual(note_tokens("fr VIA2025/7202 correcção"), [("FR", "VIA2025", "7202")])
    self.assertEqual(note_tokens("Regularização iva cativo FT FT3526S17184N/1660"), [("FT", "FT3526S17184N", "1660")])
    self.assertEqual(note_tokens("iva não lançado da FT398"), [("FT", "", "398")])
    self.assertEqual(note_tokens("cliente pagou a totalidade"), [])

def test_candidate_patterns(self):
    self.assertEqual(candidate_patterns(("FT", "2026", "38")), [("exact", "FT 2026/38"), ("like", "FT FT%N/38")])
    self.assertEqual(candidate_patterns(("FT", "2025", "1924")), [("exact", "FT 2025/1924")])
    self.assertEqual(candidate_patterns(("FR", "VIA2025", "5372")), [("exact", "FR VIA2025/5372")])
    self.assertEqual(candidate_patterns(("FT", "", "398")), [("like", "FT %/398")])

def test_note_beats_erp_link(self):
    inv, link = pick_invoice(["FT FT3526S17184N/41"], ["FT FT3526S17184N/2019"])
    self.assertEqual(inv, "FT FT3526S17184N/41")
    self.assertIn("FT FT3526S17184N/2019", link)          # "From the note (ERP link differs: …)"

def test_ambiguous_note_is_unassigned(self):
    inv, link = pick_invoice(["FT 2026/38", "FT FT3526S17184N/38"], ["FT FT3526S17184N/2019"])
    self.assertIsNone(inv)
    self.assertIn("FT 2026/38", link); self.assertIn("FT FT3526S17184N/38", link)

def test_ambiguous_note_uses_erp_link_when_it_is_a_candidate(self):
    self.assertEqual(pick_invoice(["FT 2026/38", "FT FT3526S17184N/38"], ["FT 2026/38"])[0], "FT 2026/38")

def test_no_candidates_falls_back_to_erp_link(self):
    self.assertEqual(pick_invoice([], ["FT 2025/413"])[0], "FT 2025/413")
    self.assertEqual(pick_invoice([], []), (None, "Not identified"))

def test_named_receipts(self):
    self.assertEqual(named_receipts("Iva Cativo do recebimento RG RG9726S2895N/8\nDevido ao erp"), ["RG RG9726S2895N/8"])

def test_reversal_same_customer_and_amount(self):
    pe = dict(source="manual_pe", voucher="REC 2025/12628", posting_date="2025-08-08", amount=3766273.0,
              invoice="FT 2025/1309", customer="CABINDA OIL REFINERY, LDA", note="")
    rev = dict(source="manual_je", voucher="CBL2026/85", posting_date="2025-12-23", amount=-3766273.0,
               invoice=None, customer="CABINDA OIL REFINERY, LDA", note="cliente pagou a totalidade da ft")
    self.assertEqual(match_reversal(rev, [pe, rev])[0], "FT 2025/1309")

def test_reversal_named_voucher(self):
    src = dict(source="manual_je", voucher="CBL2026/3278", posting_date="2026-05-31", amount=24444.0,
               invoice="FT 2025/1924", customer="CFE", note="")
    rev = dict(source="manual_je", voucher="CBL2026/4605", posting_date="2026-08-01", amount=-24444.0,
               invoice=None, customer="CFE", note="Valor estava cativo no CBL2026/3278")
    inv, link = match_reversal(rev, [src, rev])
    self.assertEqual(inv, "FT 2025/1924"); self.assertIn("CBL2026/3278", link)

def test_reversal_with_two_possible_invoices_stays_unassigned(self):
    a = dict(source="manual_pe", voucher="A", posting_date="2025-01-01", amount=100.0, invoice="FT 1", customer="C", note="")
    b = dict(source="manual_pe", voucher="B", posting_date="2025-01-02", amount=100.0, invoice="FT 2", customer="C", note="")
    rev = dict(source="manual_je", voucher="R", posting_date="2025-02-01", amount=-100.0, invoice=None, customer="C", note="x")
    self.assertIsNone(match_reversal(rev, [a, b, rev])[0])

def test_vat_closing(self):
    closing = {"34531 - IVA Liquidado MN - ITEC"}
    kw = ["apuramento", "abertura"]
    self.assertTrue(is_vat_closing(False, ["34531 - IVA Liquidado MN - ITEC"], closing, "", kw))
    self.assertTrue(is_vat_closing(False, ["431021 - BAI"], closing, "IVA APURAMENTO MARÇO 24", kw))
    self.assertTrue(is_vat_closing(False, [], closing, "ABERTURA ANO 2024", kw))
    self.assertFalse(is_vat_closing(True, ["34531 - IVA Liquidado MN - ITEC"], closing, "apuramento", kw))
    self.assertFalse(is_vat_closing(False, ["45102 - Caixa"], closing, "Correcção ao lançamento da FR ALV2025/1229", kw))
```

- [ ] **Step 2: Run to verify they fail**

Run (from `apps/isoft_angola_tax_compliance`): `../../env/bin/python -m unittest isoft_angola_tax_compliance.tests.test_cativo_resolve -v`
Expected: ERROR — `ImportError: cannot import name 'note_tokens'`.

- [ ] **Step 3: Implement the Task 1 functions in `resolve.py`** (no `import frappe`). `pick_invoice` link texts: `"From the note"`, `"From the note (ERP link differs: <ref>)"`, `"ERP link (ambiguous note)"`, `"Ambiguous: <a>, <b>"`, `"ERP link"`, `"Not identified"`. `match_reversal` only acts on `amount < 0` and `invoice is None`; named voucher first (`"Reverses <voucher> (named in the note)"`), else latest earlier movement of the same customer with `abs(x.amount + move.amount) < 0.005` and an invoice, only if all such matches share one invoice (`"Reverses <voucher> (same customer and amount)"`).

- [ ] **Step 4: Run tests to verify they pass** — same command. Expected: `OK` (11 tests).

- [ ] **Step 5: Commit** — `git add isoft_angola_tax_compliance/cativo_audit isoft_angola_tax_compliance/tests && git commit -m "feat(cativo-audit): matching rules"`

---

### Task 2: Invoice rows and unassigned line (pure)

**Files:**
- Modify: `isoft_angola_tax_compliance/cativo_audit/resolve.py`
- Test: `isoft_angola_tax_compliance/tests/test_cativo_resolve.py` (add `class TestRows`)

**Interfaces:**
- Consumes: Movement, Invoice shapes.
- Produces:
  - `build_rows(moves: List[Movement], invoices: Dict[str, Invoice], from_date: Optional[str], to_date: Optional[str]) -> List[Row]` — sorted by (date, invoice).
  - `unassigned(moves: List[Movement], from_date: Optional[str], to_date: Optional[str]) -> Dict` → `{"amount": float, "count": int, "entries": [{"voucher", "voucher_type", "posting_date", "customer", "amount", "reason"}]}` (`reason` = the movement's `link`).

- [ ] **Step 1: Write the failing tests** (helpers `mv(source, voucher, date, amount, invoice=None, link="")`, `inv(name, date, iva, docstatus=1)` build the shapes):

```python
def test_mixed_sources_and_other_rate(self):
    rows = build_rows([mv("engine", "FT 1", "2025-03-01", 100, "FT 1"), mv("manual_pe", "REC 1", "2025-04-01", 50, "FT 1")],
                      {"FT 1": inv("FT 1", "2025-03-01", 100)}, "2025-01-01", "2025-12-31")
    r = rows[0]
    self.assertEqual((r["engine"], r["manual_pe"], r["cativo"]), (100, 50, 150))
    self.assertEqual(r["creation"], "System + Manual"); self.assertEqual(r["rate"], "Other")
    self.assertIn("System and manual cativo on the same invoice", r["alerts"])
    self.assertIn("Cativo = 150.0% of IVA", r["alerts"])
    self.assertEqual([d["tag"] for d in r["documents"]], ["invoice", "receipt"])

def test_rates(self):
    rows = build_rows([mv("legacy_je", "RE 1", "2025-01-02", 50, "FT A"), mv("legacy_je", "RE 2", "2025-01-02", 100.4, "FT B")],
                      {"FT A": inv("FT A", "2025-01-02", 100), "FT B": inv("FT B", "2025-01-02", 100)}, None, None)
    self.assertEqual([r["rate"] for r in rows], ["50%", "100%"]); self.assertEqual(rows[0]["alerts"], [])

def test_reversed_manual(self):
    r = build_rows([mv("manual_pe", "REC 1", "2025-04-24", 129124.8, "FT 535"), mv("manual_je", "CBL 1", "2026-01-28", -129124.8, "FT 535")],
                   {"FT 535": inv("FT 535", "2025-04-20", 129124.8)}, "2025-01-01", "2025-12-31")[0]
    self.assertEqual((r["cativo"], r["creation"]), (0, "Reversed"))
    self.assertIn("Manual cativo reversed (net zero)", r["alerts"])

def test_cancelled_and_no_iva(self):
    r = build_rows([mv("legacy_je", "RE 9", "2025-06-01", 10, "FT 9")], {"FT 9": inv("FT 9", "2025-06-01", 0, docstatus=2)}, None, None)[0]
    self.assertEqual(r["status"], "Cancelled"); self.assertIsNone(r["pct"]); self.assertEqual(r["rate"], "")
    self.assertIn("Cancelled invoice with active cativo", r["alerts"]); self.assertIn("Invoice without IVA", r["alerts"])

def test_period_uses_invoice_date(self):
    moves = [mv("manual_je", "CBL 1", "2026-05-31", 24444, "FT 2025/1924")]
    invs = {"FT 2025/1924": inv("FT 2025/1924", "2025-12-15", 24444)}
    self.assertEqual(len(build_rows(moves, invs, "2025-01-01", "2025-12-31")), 1)
    self.assertEqual(build_rows(moves, invs, "2026-01-01", "2026-12-31"), [])
    self.assertEqual(unassigned(moves, "2026-01-01", "2026-12-31")["count"], 0)

def test_erp_link_differs_alert(self):
    r = build_rows([mv("manual_je", "CBL 2", "2026-05-31", 107100, "FT 41", link="From the note (ERP link differs: FT 2019)")],
                   {"FT 41": inv("FT 41", "2026-02-06", 107100)}, None, None)[0]
    self.assertIn("ERP link differs from the note", r["alerts"])

def test_unassigned_by_own_date(self):
    moves = [mv("manual_pe", "RG 19", "2025-02-15", 7648136.16, None, link="Not allocated"),
             mv("manual_je", "RE 194", "2026-07-31", 1842105.26, None, link="Not identified")]
    u = unassigned(moves, "2025-01-01", "2025-12-31")
    self.assertEqual((u["count"], u["amount"]), (1, 7648136.16)); self.assertEqual(u["entries"][0]["reason"], "Not allocated")
```

- [ ] **Step 2: Run** `../../env/bin/python -m unittest isoft_angola_tax_compliance.tests.test_cativo_resolve -v` — Expected: ERROR `cannot import name 'build_rows'`.
- [ ] **Step 3: Implement `build_rows` and `unassigned`** per the spec's "Invoice rows" and "Unassigned" sections; alert strings exactly as in the tests; `pct` formatted in the alert with one decimal and a dot.
- [ ] **Step 4: Run** — Expected: `OK` (18 tests).
- [ ] **Step 5: Commit** — `git commit -am "feat(cativo-audit): invoice rows and unassigned line"`

---

### Task 3: Settings doctypes, data loading and the API

**Files:**
- Create: `isoft_angola_tax_compliance/isoft_angola_tax_compliance/doctype/cativo_audit_account/{__init__.py, cativo_audit_account.json, cativo_audit_account.py}`
- Create: `isoft_angola_tax_compliance/isoft_angola_tax_compliance/doctype/cativo_audit_settings/{__init__.py, cativo_audit_settings.json, cativo_audit_settings.py, cativo_audit_settings.js}`
- Create: `isoft_angola_tax_compliance/cativo_audit/data.py`, `isoft_angola_tax_compliance/cativo_audit/api.py`
- Test: `isoft_angola_tax_compliance/tests/test_cativo_data.py`, `isoft_angola_tax_compliance/tests/check_cativo_audit_dev.py`

**Interfaces:**
- Consumes: Task 1–2 functions.
- Produces:
  - Child doctype **Cativo Audit Account** (istable): `company` Link Company reqd, `account` Link Account reqd, `purpose` Select `Cativo\nIVA charged\nVAT closing` reqd. Settings controller `validate` rejects an account whose `company` differs from the row's company.
  - Single **Cativo Audit Settings**: `accounts` Table (Cativo Audit Account), `vat_closing_keywords` Small Text default `apuramento, abertura`. Permissions: System Manager, Accounts Manager (read/write).
  - `data.derive_accounts(company: str) -> Accounts` (spec "Fill from company setup" sources; `derived=True`).
  - `data.get_accounts(company: str) -> Accounts` (saved rows; falls back to `derive_accounts` when the company has no Cativo rows).
  - `data.load_movements(company: str, accounts: Accounts) -> List[Movement]` (spec "Movements" 1–4 + manual JE resolution steps 1–5, using Task 1; candidate lookups query `tabSales Invoice` with `docstatus > 0` and the JE's customer).
  - `data.load_invoices(names: List[str], accounts: Accounts) -> Dict[str, Invoice]` (IVA from tax rows on `accounts["iva"]`; category from Party Tax Withholding with `atc_withholding_type = 'IVA'`).
  - `api.get_cativo_audit(company, from_date=None, to_date=None) -> {"rows": List[Row], "unassigned": dict, "accounts": Accounts, "warnings": List[str]}` — whitelisted; warning `"No saved accounts for <company>; using the company's setup"` when `derived`.
  - `cativo_audit_settings.fill_from_company_setup(company=None) -> List[{"company", "account", "purpose"}]` — whitelisted, returns proposals only (no write); JS "Fill from company setup" button appends rows not already present.

- [ ] **Step 1: Write the failing tests** in `test_cativo_data.py`, mocking `frappe.db.sql` (record query texts, return canned rows):

```python
def test_load_uses_account_currency(self):
    load_movements("ITEC LDA", ACCOUNTS)          # with frappe.db.sql patched to capture SQL
    gl = next(q for q in self.queries if "tabGL Entry" in q)
    self.assertIn("debit_in_account_currency", gl); self.assertNotIn("base_", gl)
    load_invoices(["FT 1"], ACCOUNTS)
    si = next(q for q in self.queries if "tabSales Taxes and Charges" in q)
    self.assertIn("tax_amount_after_discount_amount", si); self.assertNotIn("base_tax", si)

def test_pe_split_by_allocation(self):
    # canned: PE "REC 2025/13149" GL net 151032.0, references [("FT 2025/865", 58965.79)]
    moves = [m for m in load_movements("ITEC LDA", ACCOUNTS) if m["voucher"] == "REC 2025/13149"]
    self.assertEqual([(m["invoice"], round(m["amount"], 2)) for m in moves], [("FT 2025/865", 58965.79), (None, 92066.21)])

def test_closing_je_excluded(self):
    # canned: JE "PR2025/10027" on 34543, no customer row, other rows on 345211/34531
    self.assertNotIn("PR2025/10027", [m["voucher"] for m in load_movements("ITEC LDA", ACCOUNTS)])
```

- [ ] **Step 2: Run** `../../env/bin/python -m unittest isoft_angola_tax_compliance.tests.test_cativo_data -v` — Expected: ERROR `No module named ...cativo_audit.data`.
- [ ] **Step 3: Create the two doctype JSONs** (module `Isoft Angola Tax Compliance`, `custom: 0`, `modified` = now), controllers, settings JS (button + `frappe.call` to `fill_from_company_setup`, push rows into `frm.doc.accounts`, `frm.refresh_field`).
- [ ] **Step 4: Implement `data.py` and `api.py`.** Chunk every `IN (...)` list at 200 names; parameterise values with `%s`/`%(name)s`; `frappe.only_for(("System Manager", "Accounts Manager"))` at the top of each whitelisted method.
- [ ] **Step 5: Run unit tests** — Expected: `OK` for both test modules.
- [ ] **Step 6: Load into dev and restart**

Run (from `frappe-bench`):
`bench --site dev.isoft.ao reload-doc isoft_angola_tax_compliance doctype cativo_audit_account && bench --site dev.isoft.ao reload-doc isoft_angola_tax_compliance doctype cativo_audit_settings && bench restart`
Expected: no traceback; `Cativo Audit Settings` opens at `/app/cativo-audit-settings`.

- [ ] **Step 7: Write and run the read-only dev check** `tests/check_cativo_audit_dev.py`: `frappe.init(site="dev.isoft.ao")` with cwd `sites`, `frappe.connect()`, `frappe.set_user("Administrator")`; for every Company with `country = "Angola"` call `api.get_cativo_audit(company, "2026-01-01", "2026-12-31")`, print company, `len(rows)`, `sum(cativo)`, `unassigned.amount`, `accounts.derived`, elapsed seconds; assert each call < 5 s and that `sum(r["cativo"]) + unassigned.amount == sum of counted movements for rows/entries in period` computed from `load_movements`; end with `frappe.db.rollback()`.

Run: `cd sites && ../env/bin/python ../apps/isoft_angola_tax_compliance/isoft_angola_tax_compliance/tests/check_cativo_audit_dev.py`
Expected: one line per company, no assertion error, `derived: True` for companies without saved settings.

- [ ] **Step 8: Commit** — `git add -A isoft_angola_tax_compliance && git commit -m "feat(cativo-audit): settings, data loading and API"`

---

### Task 4: Excel export

**Files:**
- Modify: `isoft_angola_tax_compliance/cativo_audit/api.py`
- Test: `isoft_angola_tax_compliance/tests/test_cativo_data.py` (add `test_export_layout`)

**Interfaces:**
- Produces: `api.export_cativo_audit(payload: str) -> None` — whitelisted; `payload` JSON `{"company", "from_date", "to_date", "columns": [{"key","label","kind": "text"|"money"|"pct"}], "rows": [...], "totals": {...}, "unassigned": {"amount", "count"}}`; builds one sheet "Invoices with cativo" with openpyxl (header row, rows, totals row, a blank row, "Cativo with no invoice identified" row), money cells number format `#,##0.00`, text cells written as strings (no formulas), and sets `frappe.response.filename = "Cativo_Audit_<company>_<from>_<to>.xlsx"`, `filecontent`, `type = "binary"`.

- [ ] **Step 1: Write the failing test**: call `export_cativo_audit(json.dumps(sample))` with `frappe.response` patched to a dict; load `filecontent` with `openpyxl.load_workbook(BytesIO(...))`; assert sheet title, header labels equal the sample labels, the last row's first cell is `"Cativo with no invoice identified"`, a cell whose value starts with `"="` in the sample is stored as text.
- [ ] **Step 2: Run** — Expected: FAIL (`export_cativo_audit` missing).
- [ ] **Step 3: Implement** `export_cativo_audit` as specified.
- [ ] **Step 4: Run** — Expected: `OK`.
- [ ] **Step 5: Commit** — `git commit -am "feat(cativo-audit): Excel export"`

---

### Task 5: The Cativo Auditing page

**Files:**
- Create: `isoft_angola_tax_compliance/isoft_angola_tax_compliance/page/cativo_auditing/{__init__.py, cativo_auditing.json, cativo_auditing.js, cativo_auditing.css}`

**Interfaces:**
- Consumes: `isoft_angola_tax_compliance.cativo_audit.api.get_cativo_audit`, `...export_cativo_audit` (via `open_url_post`), Row / unassigned shapes.

- [ ] **Step 1: Page JSON** like `apps/saft_xml/saft_xml/saft_xml/page/saft_center/saft_center.json`: `name`/`page_name` `cativo-auditing`, `title` "Cativo Auditing", module `Isoft Angola Tax Compliance`, roles System Manager + Accounts Manager, `standard: "Yes"`.
- [ ] **Step 2: Page JS** (one IIFE, no globals; Invenza shell copied from SAF-T Center's `.sx-root` structure and palette, prefix `.ca-`):
  - Header: title, settings gear linking `/app/cativo-audit-settings`, Refresh, Export.
  - Filters: Company (Link, default `frappe.defaults.get_user_default("Company")`), From/To dates (default current year), search input, Creation select (All / System / Manual / System + Manual / Reversed), Rate select (All / 100% / 50% / Other), "Only with alerts" checkbox. Company/date change calls the server; the rest filter client-side.
  - Table "Invoices with cativo", columns in this order: Invoice, Date, Customer, NIF, Status, IVA, New engine, Automatic JE, Manual JE, Manual receipt, Cativo total, % of IVA, Rate, Creation, Cativo documents, Customer IVA category, Alerts. Sortable headers (numbers numeric). Money `format_currency`-style with 2 decimals, no symbol, header says "(Kz)" via the company currency of the account (show the currency code from the first row's company default currency only if not AOA/AKZ).
  - Documents cell: one link per line `<voucher> (<tag>)`, click → `window.open("/app/" + frappe.router.slug(doctype) + "/" + encodeURIComponent(voucher))` with `preventDefault` (v13 router ignores `target=_blank`).
  - Cancelled rows struck through; alerts red.
  - Totals row in `<tfoot>`, `position: sticky; bottom: 0`: "Total · N invoices shown", sums of IVA and the five money columns, overall % (cativo ÷ IVA of shown rows).
  - Under the table: "Cativo with no invoice identified: X (N entries) — posted in the period; not affected by the filters above" with a details toggle listing voucher, date, type, customer, amount, reason; and the note "Invoices are selected by invoice date; their cativo counts whatever its posting date."
  - Warnings from the server shown as a notice bar with the settings link.
  - States: skeleton while loading; empty state "No invoices with cativo in this period"; error state with the server message. Every `frappe.call` settles in `always` (v13 may call neither callback).
- [ ] **Step 3: CSS** — scoped under `.ca-root`, sticky `thead th` and `tfoot td`, `font-variant-numeric: tabular-nums`, table in an `overflow: auto; max-height: 70vh` box.
- [ ] **Step 4: Syntax check** — `node --check isoft_angola_tax_compliance/isoft_angola_tax_compliance/page/cativo_auditing/cativo_auditing.js` — Expected: no output.
- [ ] **Step 5: Load into dev** — `bench --site dev.isoft.ao reload-doc isoft_angola_tax_compliance page cativo_auditing && bench --site dev.isoft.ao clear-cache` — Expected: no traceback; `frappe.db.exists("Page", "cativo-auditing")` true; no Workspace named "Cativo Auditing".
- [ ] **Step 6: Hand to the user to open** `/app/cativo-auditing` on dev.isoft.ao (developer_mode is on, so page JS is not cached) and confirm table, totals, unassigned line, document links and Export behave.
- [ ] **Step 7: Commit** — `git add -A isoft_angola_tax_compliance/isoft_angola_tax_compliance/page && git commit -m "feat(cativo-audit): Cativo Auditing page"`
