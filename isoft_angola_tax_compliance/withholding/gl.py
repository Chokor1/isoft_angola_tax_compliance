# Copyright (c) 2026, ISOFT and contributors
# For license information, please see license.txt
"""Books the withholding inside the Sales Invoice's own GL entries.

    Dr  Clientes  [party = customer]          114,000   <- ERPNext's own row
    Cr  IVA Liquidado                          14,000
    Cr  Vendas                                100,000
    Cr  Clientes  [party = customer]            6,500   <- this module, against 3419
    Cr  Clientes  [party = customer]            7,000   <- this module, against 34551
    Dr  Imposto Industrial Retido na Fonte      6,500   <- this module
    Dr  IVA Cativo Retido                       7,000   <- this module

For every withholding row: one debit on its own account, and one credit on the
receivable against that account.

The customer's debit keeps the **fiscal total of the invoice**, and each amount
retained -- retencao na fonte, IVA cativo -- sits on a **separate row of its
own**. Nothing is subtracted from the debit and the retentions are never lumped
together: the customer ledger, the statement and the aging must show the invoice
as it was issued and each retention as a distinct movement, exactly as they
appear on the printed invoice. Sales Invoice calls `make_gl_entries` with
`merge_entries=False`, so the rows on the receivable are never folded into one.

`grand_total` is untouched, so SAF-T GrossTotal and the AGT payload stay
correct; `outstanding_amount` drops automatically because ERPNext derives it
from the GL balance on `debit_to` with `against_voucher`, and every row carries it.

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
	if not total_base:
		return gl_entries

	default_cost_center = frappe.get_cached_value("Company", doc.company, "cost_center")
	party_in_company_currency = doc.party_account_currency == doc.company_currency
	against_voucher = doc.return_against if cint(doc.is_return) and doc.return_against else doc.name

	# ERPNext's own receivable row is left exactly as booked: the fiscal total,
	# against the income accounts. The withholdings face the customer on rows of
	# their own below, so they are not named on the invoice row too.
	#
	# One credit on the customer per withholding: retencao and IVA cativo each on
	# a line of their own, against the account that withholding is booked to.
	for row in rows:
		if not flt(row.base_withholding_amount):
			continue

		gl_entries.append(
			doc.get_gl_dict(
				{
					"account": doc.debit_to,
					"party_type": "Customer",
					"party": doc.customer,
					"against": row.account_head,
					"credit": flt(row.base_withholding_amount),
					"credit_in_account_currency": (
						flt(row.base_withholding_amount)
						if party_in_company_currency
						else flt(row.withholding_amount)
					),
					"against_voucher": against_voucher,
					"against_voucher_type": doc.doctype,
					"cost_center": doc.cost_center or default_cost_center,
					"project": doc.get("project"),
					"remarks": row.description or None,
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
