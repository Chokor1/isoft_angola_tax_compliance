# Copyright (c) 2026, ISOFT and contributors
# For license information, please see license.txt
"""Reads what the legacy Journal Entries posted, for invoices predating the engine.

This module used to also write a `Withholding Comparison Log` during the
Shadow-mode cutover, recording the legacy figure beside the new one. The legacy
path has since been deleted from ERPNext, so there is nothing left to compare
against and that logging is gone.

What remains is still needed: `api.get_withholdings()` falls back to these
readers so a SAF-T or AGT re-export of a pre-cutover period still declares the
withholding that was actually booked at the time.
"""

import frappe
from frappe.utils import flt

LEGACY_TOTAL_FIELD = "total_tax_withholding_amount"


def get_legacy_amounts(invoice, company):
	"""What the legacy Journal Entries actually posted against this invoice."""
	return get_legacy_amounts_bulk([invoice], company).get(invoice, _empty_legacy())


def get_legacy_amounts_bulk(invoices, company):
	"""Same, for many invoices at once: one query per legacy flag, not per invoice.

	Also carries `field_total`, the invoice's own `total_tax_withholding_amount`,
	as a last-resort source for pre-cutover invoices whose Journal Entry is
	missing (the legacy retencao path could fail to post -- see the README).

	Amounts are the SIGNED net movement on the referenced row (credit - debit),
	not the credit alone. Some legacy retencao Journal Entries picked the wrong
	side -- there are normal, non-return invoices whose JE debits the receivable
	-- and a credit-only sum silently reported those as zero. The old SAF-T
	query had exactly that bug (`credit_in_account_currency > 0`), so such
	invoices were never declared. Consumers that need a magnitude apply abs().
	"""
	invoices = [i for i in (invoices or []) if i]
	result = {name: _empty_legacy() for name in invoices}
	if not invoices:
		return result

	flags = []
	je_meta = frappe.get_meta("Journal Entry")
	if je_meta.get_field("is_tax_withholding"):
		flags.append(("is_tax_withholding", "ii"))
	if je_meta.get_field("is_vat_exemption"):
		flags.append(("is_vat_exemption", "iva"))

	for flag, key in flags:
		rows = frappe.db.sql(
			"""
			SELECT jea.reference_name AS invoice, je.name,
				(jea.credit_in_account_currency - jea.debit_in_account_currency) AS amount,
				(jea.credit - jea.debit) AS base_amount
			FROM `tabJournal Entry Account` jea
			INNER JOIN `tabJournal Entry` je ON je.name = jea.parent
			WHERE jea.reference_type = 'Sales Invoice'
				AND jea.reference_name IN %(invoices)s
				AND je.company = %(company)s
				AND je.docstatus = 1
				AND je.{flag} = 1
			""".format(flag=flag),
			{"invoices": invoices, "company": company},
			as_dict=True,
		)
		for row in rows:
			entry = result.setdefault(row.invoice, _empty_legacy())
			entry[key] += flt(row.base_amount) or flt(row.amount)
			entry["vouchers"].append(
				{"journal_entry": row.name, "type": key, "amount": flt(row.base_amount)}
			)

	if frappe.get_meta("Sales Invoice").get_field(LEGACY_TOTAL_FIELD):
		for row in frappe.get_all(
			"Sales Invoice",
			filters={"name": ["in", invoices]},
			fields=["name", LEGACY_TOTAL_FIELD],
		):
			result.setdefault(row.name, _empty_legacy())["field_total"] = flt(row.get(LEGACY_TOTAL_FIELD))

	return result


def _empty_legacy():
	return {"ii": 0.0, "iva": 0.0, "field_total": 0.0, "vouchers": []}
