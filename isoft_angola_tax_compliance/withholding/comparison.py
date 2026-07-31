# Copyright (c) 2026, ISOFT and contributors
# For license information, please see license.txt
"""Shadow-mode reconciliation: legacy Journal Entries vs the new engine.

While the site runs in Shadow mode the legacy core path still creates its
Journal Entries and the new engine posts nothing. This module records both
numbers side by side so every difference can be explained before anyone
switches a company to Active.

Expect non-zero deltas at first. Known legacy defects that will show up here:

  * IVA cativo is computed on `total_taxes_and_charges`, i.e. the WHOLE taxes
    table, so any freight / imposto de selo / rounding row is withheld too.
  * Retencao II uses `is_stock_item = 0` as its definition of "service", and
    `item.amount` instead of `net_amount`.
  * Both legacy paths hardcode their rates, so no dated rate applies.
  * The legacy retencao JE posts at `nowdate()`, not the invoice posting date.
  * FX invoices are booked at exchange rate 1.

A delta is therefore not automatically a bug in the new engine -- read the
`details` field, which records the engine's own breakdown.
"""

import json

import frappe
from frappe.utils import flt

from isoft_angola_tax_compliance.withholding.settings import SHADOW, get_mode, get_settings

LOG_DOCTYPE = "Withholding Comparison Log"
LEGACY_TOTAL_FIELD = "total_tax_withholding_amount"
TABLE_FIELD = "atc_withholdings"

TOLERANCE = 0.01


def log_comparison(doc):
	"""Upsert one comparison row for a submitted Sales Invoice."""
	if get_mode(doc.get("company")) != SHADOW:
		return

	settings = get_settings(doc.company)
	if not settings or not settings.log_comparison:
		return

	try:
		_write_log(doc)
	except Exception:
		# Never let a diagnostic break a submit.
		frappe.log_error(frappe.get_traceback(), "Angola withholding comparison log")


def _write_log(doc):
	legacy = get_legacy_amounts(doc.name, doc.company)

	rows = doc.get(TABLE_FIELD) or []
	new_ii = sum(flt(r.base_withholding_amount) for r in rows if r.withholding_type == "II")
	new_iva = sum(flt(r.base_withholding_amount) for r in rows if r.withholding_type == "IVA")

	ii_delta = flt(new_ii - legacy["ii"], 2)
	iva_delta = flt(new_iva - legacy["iva"], 2)

	values = {
		"sales_invoice": doc.name,
		"company": doc.company,
		"customer": doc.customer,
		"posting_date": doc.posting_date,
		"legacy_ii_amount": legacy["ii"],
		"new_ii_amount": new_ii,
		"ii_delta": ii_delta,
		"legacy_iva_amount": legacy["iva"],
		"new_iva_amount": new_iva,
		"iva_delta": iva_delta,
		"status": _status(legacy, new_ii, new_iva, ii_delta, iva_delta),
		"details": json.dumps(
			{
				"engine_rows": [
					{
						"category": r.tax_withholding_category,
						"type": r.withholding_type,
						"rate": r.rate,
						"base": r.base_taxable_amount,
						"amount": r.base_withholding_amount,
						"account": r.account_head,
					}
					for r in rows
				],
				"legacy_journal_entries": legacy["vouchers"],
				"grand_total": doc.base_grand_total,
				"total_taxes_and_charges": doc.base_total_taxes_and_charges,
				"currency": doc.currency,
				"conversion_rate": doc.conversion_rate,
			},
			indent=2,
			default=str,
		),
	}

	existing = frappe.db.exists(LOG_DOCTYPE, doc.name)
	if existing:
		log = frappe.get_doc(LOG_DOCTYPE, existing)
		log.update(values)
		log.save(ignore_permissions=True)
	else:
		log = frappe.get_doc(dict(doctype=LOG_DOCTYPE, **values))
		log.insert(ignore_permissions=True)


def _status(legacy, new_ii, new_iva, ii_delta, iva_delta):
	legacy_total = flt(legacy["ii"]) + flt(legacy["iva"])
	new_total = flt(new_ii) + flt(new_iva)

	if not legacy_total and not new_total:
		return "Both Zero"
	if not legacy_total:
		return "New Only"
	if not new_total:
		return "Legacy Only"
	if abs(ii_delta) <= TOLERANCE and abs(iva_delta) <= TOLERANCE:
		return "Match"

	return "Mismatch"


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
