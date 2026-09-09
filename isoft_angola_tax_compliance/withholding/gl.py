# Copyright (c) 2026, ISOFT and contributors
# For license information, please see license.txt
"""Books the withholding inside the Sales Invoice's own GL entries.

    Dr  Imposto Industrial Retido na Fonte      6,500
    Dr  IVA Cativo Retido                       7,000
    Cr  Clientes  [party = customer]           13,500

One debit per withholding row, one aggregate credit against the receivable.
`grand_total` is untouched, so SAF-T GrossTotal and the AGT payload stay
correct; `outstanding_amount` drops automatically because ERPNext derives it
from the GL balance on `debit_to` with `against_voucher`.

Doing it here rather than in a separate Journal Entry is what makes cancel,
amend and repost atomic, and what keeps the posting date, conversion rate and
cost centre identical to the invoice by construction.

Modelled on ERPNext's own `make_write_off_gl_entry`, which uses exactly this
shape. Negative amounts on credit notes need no special handling: ERPNext's
`process_gl_map` flips negative debit/credit pairs before insert.
"""

import frappe
from frappe.utils import cint, flt

from erpnext.accounts.utils import get_account_currency

from isoft_angola_tax_compliance.withholding.settings import is_enabled

WITHHOLDING_TABLE_FIELD = "atc_withholdings"


def add_withholding_gl_entries(doc, gl_entries):
	"""Append the withholding entries. No-op for non-Angolan companies."""
	if not is_enabled(doc.get("company")):
		return gl_entries

	rows = doc.get(WITHHOLDING_TABLE_FIELD) or []
	if not rows:
		return gl_entries

	total_base = sum(flt(r.base_withholding_amount) for r in rows)
	total = sum(flt(r.withholding_amount) for r in rows)
	if not total_base:
		return gl_entries

	default_cost_center = frappe.get_cached_value("Company", doc.company, "cost_center")
	withholding_accounts = sorted({r.account_head for r in rows if r.account_head})
	against = ", ".join(withholding_accounts)
	party_amount = total_base if doc.party_account_currency == doc.company_currency else total

	receivable = _find_receivable_entry(doc, gl_entries)
	if receivable:
		receivable.debit = flt(receivable.debit) - total_base
		receivable.debit_in_account_currency = (
			flt(receivable.debit_in_account_currency) - party_amount
		)
		existing_against = [a for a in (receivable.against or "").split(", ") if a]
		receivable.against = ", ".join(
			existing_against + [a for a in withholding_accounts if a not in existing_against]
		)
	else:
		gl_entries.append(
			doc.get_gl_dict(
				{
					"account": doc.debit_to,
					"party_type": "Customer",
					"party": doc.customer,
					"against": against,
					"credit": total_base,
					"credit_in_account_currency": party_amount,
					"against_voucher": (
						doc.return_against if cint(doc.is_return) and doc.return_against else doc.name
					),
					"against_voucher_type": doc.doctype,
					"cost_center": doc.cost_center or default_cost_center,
					"project": doc.get("project"),
				},
				doc.party_account_currency,
				item=doc,
			)
		)

	for row in rows:
		if not flt(row.base_withholding_amount):
			continue

		account_currency = get_account_currency(row.account_head)
		gl_entries.append(
			doc.get_gl_dict(
				{
					"account": row.account_head,
					"against": doc.customer,
					"debit": flt(row.base_withholding_amount),
					"debit_in_account_currency": (
						flt(row.base_withholding_amount)
						if account_currency == doc.company_currency
						else flt(row.withholding_amount)
					),
					"cost_center": row.cost_center or doc.cost_center or default_cost_center,
					"project": doc.get("project"),
					"remarks": row.description,
				},
				account_currency,
				item=doc,
			)
		)

	return gl_entries


def _find_receivable_entry(doc, gl_entries):
	"""The debit ERPNext booked on `debit_to` for this customer in `make_customer_gl_entry`.

	Matched on account + party + against_voucher so a POS payment credit or a
	write-off row on the same account is never picked by mistake.
	"""
	against_voucher = (
		doc.return_against if cint(doc.is_return) and doc.return_against else doc.name
	)
	for entry in gl_entries:
		if (
			entry.get("account") == doc.debit_to
			and entry.get("party_type") == "Customer"
			and entry.get("party") == doc.customer
			and entry.get("against_voucher") == against_voucher
			and flt(entry.get("debit"))
		):
			return entry
	return None
