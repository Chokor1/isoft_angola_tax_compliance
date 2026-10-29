# Copyright (c) 2026, ISOFT and contributors
# For license information, please see license.txt
"""Read the cativo movements of one company out of the ledger.

Amounts are taken in the account's or the document's own currency, never the company
base: erp.itec.ao keeps its books in USD while its cativo accounts and invoices are in Kz.
The rules that decide which invoice a movement belongs to live in resolve.py.
"""

import re
from typing import Dict, List

import frappe

from isoft_angola_tax_compliance.cativo_audit import resolve

DEFAULT_KEYWORDS = "apuramento, abertura"
LEGACY_REMARK_RE = re.compile(r"Sales Invoice (.+)$")
CHUNK = 200


def _sql(query, values=None):
	return frappe.db.sql(query, values or {}, as_dict=True)


def _chunks(seq, size=CHUNK):
	seq = list(seq)
	for i in range(0, len(seq), size):
		yield seq[i : i + size]


def _date(value):
	return str(value)[:10] if value else ""


def _like_regex(pattern):
	return re.compile("^" + "".join(".*" if c == "%" else "." if c == "_" else re.escape(c) for c in pattern) + "$")


def _move(source, voucher_type, voucher, posting_date, account, amount, invoice=None, link="", customer=None, note="", erp_refs=None):
	return {
		"source": source,
		"voucher_type": voucher_type,
		"voucher": voucher,
		"posting_date": _date(posting_date),
		"account": account,
		"amount": float(amount or 0),
		"invoice": invoice,
		"link": link,
		"customer": customer,
		"note": note or "",
		"erp_refs": erp_refs or [],
	}


# ---------------------------------------------------------------- accounts


def _keywords(text):
	return [k.strip() for k in (text or DEFAULT_KEYWORDS).split(",") if k.strip()]


def _vat_assessment_row(company):
	if not frappe.db.exists("DocType", "VAT Assessment Settings"):
		return {}
	for row in frappe.get_single("VAT Assessment Settings").get("company_accounts") or []:
		if row.company == company:
			return row
	return {}


def _leaf_accounts(company, prefixes=(), name_like=None):
	filters = {"company": company, "is_group": 0}
	accounts = frappe.get_all("Account", filters=filters, fields=["name", "account_name"])
	out = []
	for a in accounts:
		if any(a.name.startswith(p) for p in prefixes) or (name_like and name_like.lower() in (a.account_name or "").lower()):
			out.append(a.name)
	return out


def derive_accounts(company: str) -> Dict:
	"""Accounts proposed from the company's existing setup (nothing is saved)."""
	vat = _vat_assessment_row(company)

	cativo = [
		r.account
		for r in frappe.db.sql(
			"""select distinct a.account from `tabTax Withholding Account` a
			join `tabTax Withholding Category` c on c.name = a.parent
			where c.atc_withholding_type = 'IVA' and a.company = %(company)s and ifnull(a.account, '') != ''""",
			{"company": company},
			as_dict=True,
		)
	]
	if vat and vat.get("withheld_vat_recoverable_account"):
		cativo.append(vat.withheld_vat_recoverable_account)
	cativo += _leaf_accounts(company, name_like="Recuperar de Cativo")

	iva = []
	if vat and vat.get("output_vat_account"):
		iva.append(vat.output_vat_account)
	iva += _leaf_accounts(company, prefixes=("3453", "34541"))

	closing = [vat.get(f) for f in ("deductible_vat_account", "output_vat_account") if vat and vat.get(f)]
	closing += _leaf_accounts(company, prefixes=("3452", "3453"))

	settings_keywords = frappe.db.get_single_value("Cativo Audit Settings", "vat_closing_keywords") if frappe.db.exists(
		"DocType", "Cativo Audit Settings"
	) else None

	return {
		"cativo": list(dict.fromkeys(cativo)),
		"iva": list(dict.fromkeys(iva)),
		"closing": list(dict.fromkeys(closing)),
		"keywords": _keywords(settings_keywords),
		"derived": True,
	}


def get_accounts(company: str) -> Dict:
	"""The company's saved accounts; a purpose with no saved rows uses the derived ones."""
	settings = frappe.get_single("Cativo Audit Settings")
	saved = {"Cativo": [], "IVA charged": [], "VAT closing": []}
	for row in settings.get("accounts") or []:
		if row.company == company and row.purpose in saved:
			saved[row.purpose].append(row.account)

	derived = None
	out = {"keywords": _keywords(settings.vat_closing_keywords), "derived": False}
	for key, purpose in (("cativo", "Cativo"), ("iva", "IVA charged"), ("closing", "VAT closing")):
		if saved[purpose]:
			out[key] = list(dict.fromkeys(saved[purpose]))
		else:
			derived = derived or derive_accounts(company)
			out[key] = derived[key]
			out["derived"] = True
	return out


# ---------------------------------------------------------------- movements


def load_movements(company: str, accounts: Dict) -> List[Dict]:
	"""Every counted cativo movement of the company, each resolved to an invoice when possible."""
	cativo = set(accounts.get("cativo") or [])
	closing = set(accounts.get("closing") or [])
	moves = []

	for r in _sql(
		"""select w.parent as invoice, w.account_head as account, w.withholding_amount as amount,
			s.posting_date, s.customer
		from `tabSales Invoice Withholding` w join `tabSales Invoice` s on s.name = w.parent
		where w.withholding_type = 'IVA' and s.docstatus = 1 and s.company = %(company)s""",
		{"company": company},
	):
		moves.append(_move("engine", "Sales Invoice", r["invoice"], r["posting_date"], r["account"], r["amount"],
			invoice=r["invoice"], link="Own invoice", customer=r["customer"]))

	if not cativo:
		return moves

	gl = [
		r
		for r in _sql(
			"""select voucher_type, voucher_no, account, min(posting_date) as posting_date,
				sum(debit_in_account_currency - credit_in_account_currency) as amount
			from `tabGL Entry`
			where company = %(company)s and is_cancelled = 0
				and voucher_type in ('Journal Entry', 'Payment Entry') and account in %(accounts)s
			group by voucher_type, voucher_no, account
			order by posting_date, voucher_no""",
			{"company": company, "accounts": tuple(cativo)},
		)
		if abs(float(r["amount"] or 0)) >= resolve.ZERO
	]

	je_names = sorted({r["voucher_no"] for r in gl if r["voucher_type"] == "Journal Entry"})
	pe_names = sorted({r["voucher_no"] for r in gl if r["voucher_type"] == "Payment Entry"})
	je_head, je_rows = {}, {}
	for chunk in _chunks(je_names):
		for r in _sql(
			"""select name, is_vat_exemption, user_remark, remark from `tabJournal Entry` where name in %(names)s""",
			{"names": tuple(chunk)},
		):
			je_head[r["name"]] = r
		for r in _sql(
			"""select parent, account, party_type, party, reference_type, reference_name
			from `tabJournal Entry Account` where parent in %(names)s order by parent, idx""",
			{"names": tuple(chunk)},
		):
			je_rows.setdefault(r["parent"], []).append(r)

	pe_head, pe_refs = {}, {}
	for chunk in _chunks(pe_names):
		for r in _sql("""select name, party, remarks from `tabPayment Entry` where name in %(names)s""", {"names": tuple(chunk)}):
			pe_head[r["name"]] = r
		for r in _sql(
			"""select parent, reference_doctype, reference_name, allocated_amount
			from `tabPayment Entry Reference` where parent in %(names)s order by parent, idx""",
			{"names": tuple(chunk)},
		):
			pe_refs.setdefault(r["parent"], []).append(r)

	pending = []
	for g in gl:
		voucher, amount = g["voucher_no"], float(g["amount"])
		if g["voucher_type"] == "Journal Entry":
			head = je_head.get(voucher) or {}
			rows = je_rows.get(voucher, [])
			customers = [r for r in rows if r["party_type"] == "Customer"]
			customer = customers[0]["party"] if customers else None
			erp_refs = [r["reference_name"] for r in customers if r["reference_type"] == "Sales Invoice" and r["reference_name"]]
			note = (head.get("user_remark") or "").strip() or (head.get("remark") or "").strip().split("\n")[0]

			if int(head.get("is_vat_exemption") or 0):
				found = LEGACY_REMARK_RE.search(head.get("user_remark") or "")
				invoice = erp_refs[0] if erp_refs else (found.group(1).strip() if found else None)
				link = "ERP link" if erp_refs else ("From the note" if found else "Not identified")
				moves.append(_move("legacy_je", "Journal Entry", voucher, g["posting_date"], g["account"], amount,
					invoice=invoice, link=link, customer=customer, note=note, erp_refs=erp_refs))
				continue

			others = [r["account"] for r in rows if r["account"] not in cativo]
			if resolve.is_vat_closing(bool(customers), others, closing, note, accounts.get("keywords") or []):
				continue

			m = _move("manual_je", "Journal Entry", voucher, g["posting_date"], g["account"], amount,
				customer=customer, note=note, erp_refs=erp_refs)
			moves.append(m)
			pending.append(m)
		else:
			head = pe_head.get(voucher) or {}
			base = dict(customer=head.get("party"), note=(head.get("remarks") or "").split("\n")[0])
			left = amount
			for ref in pe_refs.get(voucher, []):
				if ref["reference_doctype"] != "Sales Invoice" or left <= resolve.ZERO:
					continue
				take = min(float(ref["allocated_amount"] or 0), left)
				if take <= 0:
					continue
				moves.append(_move("manual_pe", "Payment Entry", voucher, g["posting_date"], g["account"], take,
					invoice=ref["reference_name"], link="Receipt allocation", **base))
				left -= take
			if abs(left) >= resolve.ZERO:
				moves.append(_move("manual_pe", "Payment Entry", voucher, g["posting_date"], g["account"], left,
					link="Not allocated", **base))

	_resolve_manual(company, pending, moves)
	return moves


def _resolve_manual(company, pending, moves):
	"""Give manual journal entries their invoice: note, then named receipt, then reversal."""
	tokens = {id(m): resolve.note_tokens(m["note"]) for m in pending}
	exact, likes = set(), set()
	for toks in tokens.values():
		for tok in toks:
			for kind, value in resolve.candidate_patterns(tok):
				(exact if kind == "exact" else likes).add(value)

	invoices = []
	if exact or likes:
		likes = sorted(likes)
		clauses = ["name in %(exact)s"] + ["name like %%(l%d)s" % i for i in range(len(likes))]
		values = {"company": company, "exact": tuple(exact) or ("",)}
		values.update({"l%d" % i: p for i, p in enumerate(likes)})
		invoices = _sql(
			"""select name, customer from `tabSales Invoice`
			where docstatus > 0 and company = %(company)s and ({})""".format(" or ".join(clauses)),
			values,
		)

	for m in pending:
		candidates = []
		for tok in tokens[id(m)]:
			for kind, value in resolve.candidate_patterns(tok):
				test = (lambda n, v=value: n == v) if kind == "exact" else _like_regex(value).match
				candidates += [i["name"] for i in invoices if test(i["name"]) and (not m["customer"] or i["customer"] == m["customer"])]
			if candidates:
				break
		m["invoice"], m["link"] = resolve.pick_invoice(candidates, m["erp_refs"])

	named = {}
	for m in pending:
		if not m["invoice"]:
			for receipt in resolve.named_receipts(m["note"]):
				named.setdefault(receipt, []).append(m)
	if named:
		paid = {}
		for chunk in _chunks(sorted(named)):
			for r in _sql(
				"""select parent, reference_name from `tabPayment Entry Reference`
				where reference_doctype = 'Sales Invoice' and docstatus = 1 and parent in %(names)s""",
				{"names": tuple(chunk)},
			):
				paid.setdefault(r["parent"], []).append(r["reference_name"])
		for receipt, ms in named.items():
			invoices_paid = list(dict.fromkeys(paid.get(receipt, [])))
			for m in ms:
				if m["invoice"]:
					continue
				if len(invoices_paid) == 1:
					m["invoice"], m["link"] = invoices_paid[0], "Invoice paid by receipt %s (named in the note)" % receipt
				elif invoices_paid:
					m["link"] = "Ambiguous (receipt %s paid: %s)" % (receipt, ", ".join(invoices_paid))

	for m in pending:
		if not m["invoice"] and m["amount"] < 0:
			invoice, link = resolve.match_reversal(m, moves)
			if invoice:
				m["invoice"], m["link"] = invoice, link


# ---------------------------------------------------------------- invoices


def load_invoices(names: List[str], accounts: Dict) -> Dict[str, Dict]:
	"""The invoices the movements point at, with their IVA and the customer's IVA category."""
	iva_accounts = tuple(accounts.get("iva") or []) or ("",)
	invoices = {}
	for chunk in _chunks(names):
		for r in _sql(
			"""select s.name, s.posting_date, s.customer, s.customer_name, s.tax_id, s.docstatus, s.is_return,
				s.net_total, s.grand_total,
				(select sum(t.tax_amount_after_discount_amount) from `tabSales Taxes and Charges` t
				 where t.parent = s.name and t.parenttype = 'Sales Invoice' and t.account_head in %(iva)s) as iva
			from `tabSales Invoice` s where s.name in %(names)s""",
			{"names": tuple(chunk), "iva": iva_accounts},
		):
			invoices[r["name"]] = dict(
				name=r["name"], posting_date=_date(r["posting_date"]), customer=r["customer"],
				customer_name=r["customer_name"], tax_id=r["tax_id"] or "", docstatus=int(r["docstatus"] or 0),
				is_return=int(r["is_return"] or 0), net_total=float(r["net_total"] or 0),
				grand_total=float(r["grand_total"] or 0), iva=float(r["iva"] or 0), category=None,
			)

	customers = sorted({i["customer"] for i in invoices.values() if i["customer"]})
	category = {}
	for chunk in _chunks(customers):
		for r in _sql(
			"""select p.parent as customer, p.tax_withholding_category as category
			from `tabParty Tax Withholding` p
			join `tabTax Withholding Category` c on c.name = p.tax_withholding_category
			where p.parenttype = 'Customer' and c.atc_withholding_type = 'IVA' and p.parent in %(customers)s""",
			{"customers": tuple(chunk)},
		):
			category[r["customer"]] = r["category"]
	for i in invoices.values():
		i["category"] = category.get(i["customer"])
	return invoices
