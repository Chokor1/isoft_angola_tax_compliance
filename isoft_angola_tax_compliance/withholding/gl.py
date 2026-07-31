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

from isoft_angola_tax_compliance.withholding.settings import is_active

WITHHOLDING_TABLE_FIELD = "atc_withholdings"


def add_withholding_gl_entries(doc, gl_entries):
	"""Append the withholding entries. No-op unless the engine is Active."""
	if not is_active(doc.get("company")):
		return gl_entries

	rows = doc.get(WITHHOLDING_TABLE_FIELD) or []
	if not rows:
		return gl_entries

	total_base = sum(flt(r.base_withholding_amount) for r in rows)
	total = sum(flt(r.withholding_amount) for r in rows)
	if not total_base:
		return gl_entries

	default_cost_center = frappe.get_cached_value("Company", doc.company, "cost_center")
	against = ", ".join(sorted({r.account_head for r in rows if r.account_head}))

	gl_entries.append(
		doc.get_gl_dict(
			{
				"account": doc.debit_to,
				"party_type": "Customer",
				"party": doc.customer,
				"against": against,
				"credit": total_base,
				"credit_in_account_currency": (
					total_base if doc.party_account_currency == doc.company_currency else total
				),
				"against_voucher": doc.return_against if cint(doc.is_return) else doc.name,
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
