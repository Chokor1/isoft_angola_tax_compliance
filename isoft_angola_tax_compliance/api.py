# Copyright (c) 2026, ISOFT and contributors
# For license information, please see license.txt
"""Single source of truth for the fiscal exporters.

`saft_xml` and `isoft_agt_electronic_invoicing` should both call this instead
of each deriving the numbers themselves. Today they don't agree:

  * saft_xml reads `Sales Invoice.total_tax_withholding_amount` for II, but
    recovers IVA cativo by SQL-joining `tabJournal Entry Account` -- an
    unfiltered scan, and one that drops credit notes because it filters on
    `credit_in_account_currency > 0`.
  * isoft_agt_electronic_invoicing sends II only. IVA cativo is never declared
    to AGT at all.

These functions return every withholding on a document, in transaction
currency (which is what SAF-T DocumentTotals and the AGT payload use), with a
fallback to the legacy Journal Entries so historical invoices keep exporting
correctly after the cutover.
"""

import frappe
from frappe import _
from frappe.utils import flt

from isoft_angola_tax_compliance.withholding.comparison import (
	get_legacy_amounts,
	get_legacy_amounts_bulk,
)

TABLE_FIELD = "atc_withholdings"
LEGACY_TOTAL_FIELD = "total_tax_withholding_amount"


@frappe.whitelist()
def get_withholdings(sales_invoice, company=None):
	"""Return [{type, description, category, rate, taxable_amount, amount}, ...].

	`amount` is signed: negative on a credit note. Callers that need an
	unsigned figure for the XML should apply abs() themselves.
	"""
	if isinstance(sales_invoice, str):
		fields = ["name", "company", LEGACY_TOTAL_FIELD]
		meta = frappe.get_meta("Sales Invoice")
		fields = [f for f in fields if f == "name" or meta.get_field(f)]
		doc = frappe.db.get_value("Sales Invoice", sales_invoice, fields, as_dict=True)
		if not doc:
			return []
		name = doc.name
		company = company or doc.get("company")
		legacy_total = flt(doc.get(LEGACY_TOTAL_FIELD))
	else:
		name = sales_invoice.name
		company = company or sales_invoice.company
		legacy_total = flt(sales_invoice.get(LEGACY_TOTAL_FIELD))

	rows = _get_engine_rows(name)
	if rows:
		return rows

	return _get_legacy_rows(name, company, legacy_total)


@frappe.whitelist()
def compute_withholdings(doc):
	"""Preview the withholding for an unsaved invoice, for the client form.

	Read-only: computes from the posted document state and writes nothing. The
	server-side `validate` remains authoritative on save, so this is purely a
	live preview -- the client must not treat the result as persisted.
	"""
	if isinstance(doc, str):
		doc = frappe.parse_json(doc)

	supported = ("Sales Invoice", "Quotation")
	if not isinstance(doc, dict) or doc.get("doctype") not in supported:
		frappe.throw(_("Only {0} are supported.").format(", ".join(supported)))

	frappe.has_permission(doc["doctype"], "read", throw=True)

	from isoft_angola_tax_compliance.withholding import engine
	from isoft_angola_tax_compliance.withholding.apply import _set_item_amounts

	invoice = frappe.get_doc(doc)
	result = engine.evaluate(invoice)
	rows = result["rows"]

	_set_item_amounts(invoice, rows)

	total = sum(flt(r["withholding_amount"]) for r in rows)

	return {
		"rows": rows,
		"total": total,
		"base_total": sum(flt(r["base_withholding_amount"]) for r in rows),
		"net_receivable": flt(invoice.get("grand_total")) - total,
		"items": [
			{"idx": item.idx, "amount": flt(item.get("atc_withholding_amount"))}
			for item in invoice.get("items") or []
		],
		"messages": [frappe.utils.strip_html(m) for m in result["messages"]],
	}


@frappe.whitelist()
def get_withholdings_for_documents(company, names):
	"""Bulk form: {invoice_name: [rows, ...]} for an explicit list of invoices.

	Prefer this over the period variant when the caller has already selected
	its documents -- SAF-T, for instance, selects by `creation` while a period
	filter here would use `posting_date`, and the two disagree at period edges.

	Two queries regardless of list size: one for engine rows, one for the
	legacy Journal Entries that back invoices booked before the cutover.
	"""
	if isinstance(names, str):
		names = frappe.parse_json(names)
	names = [n for n in (names or []) if n]
	if not names:
		return {}

	result = {}
	for name, rows in _get_engine_rows_bulk(names).items():
		if rows:
			result[name] = rows

	# Only invoices with no engine rows need the legacy fallback.
	remaining = [n for n in names if n not in result]
	if remaining:
		for name, legacy in get_legacy_amounts_bulk(remaining, company).items():
			rows = _build_legacy_rows(legacy, flt(legacy.get("field_total")))
			if rows:
				result[name] = rows

	return result


@frappe.whitelist()
def get_withholdings_for_period(company, from_date, to_date):
	"""Bulk form keyed on `posting_date`. See get_withholdings_for_documents."""
	names = frappe.get_all(
		"Sales Invoice",
		filters={
			"company": company,
			"posting_date": ["between", [from_date, to_date]],
			"docstatus": ["!=", 0],
		},
		pluck="name",
	)

	return get_withholdings_for_documents(company, names)


def _get_engine_rows_bulk(names):
	"""One query for every engine row across the given invoices."""
	if not frappe.get_meta("Sales Invoice").get_field(TABLE_FIELD):
		return {}

	rows = frappe.get_all(
		"Sales Invoice Withholding",
		filters={"parent": ["in", names], "parenttype": "Sales Invoice", "parentfield": TABLE_FIELD},
		fields=[
			"parent",
			"tax_withholding_category",
			"withholding_type",
			"description",
			"rate",
			"taxable_amount",
			"withholding_amount",
			"base_taxable_amount",
			"base_withholding_amount",
			"account_head",
		],
		order_by="parent asc, idx asc",
	)

	grouped = {}
	for row in rows:
		if not flt(row.withholding_amount):
			continue
		grouped.setdefault(row.parent, []).append(_format_engine_row(row))

	return grouped


def _get_engine_rows(invoice):
	if not frappe.get_meta("Sales Invoice").get_field(TABLE_FIELD):
		return []

	rows = frappe.get_all(
		"Sales Invoice Withholding",
		filters={"parent": invoice, "parenttype": "Sales Invoice", "parentfield": TABLE_FIELD},
		fields=[
			"tax_withholding_category",
			"withholding_type",
			"description",
			"rate",
			"taxable_amount",
			"withholding_amount",
			"base_taxable_amount",
			"base_withholding_amount",
			"account_head",
		],
		order_by="idx asc",
	)

	return [_format_engine_row(r) for r in rows if flt(r.withholding_amount)]


def _format_engine_row(r):
	return {
		"type": r.withholding_type or "Other",
		"description": r.description,
		"category": r.tax_withholding_category,
		"rate": flt(r.rate),
		"taxable_amount": flt(r.taxable_amount),
		"amount": flt(r.withholding_amount),
		"base_taxable_amount": flt(r.base_taxable_amount),
		"base_amount": flt(r.base_withholding_amount),
		"account_head": r.account_head,
		"source": "engine",
	}


def _get_legacy_rows(invoice, company, legacy_total):
	"""Reconstruct rows for invoices booked before the engine took over."""
	return _build_legacy_rows(get_legacy_amounts(invoice, company), legacy_total)


def _build_legacy_rows(legacy, legacy_total=0):
	rows = []

	ii_amount = flt(legacy.get("ii")) or flt(legacy_total)
	if ii_amount:
		rows.append(_legacy_row("II", "Retencao na Fonte", ii_amount))

	if flt(legacy.get("iva")):
		rows.append(_legacy_row("IVA", "IVA Cativo", flt(legacy["iva"])))

	return rows


def _legacy_row(wtype, description, amount):
	return {
		"type": wtype,
		"description": description,
		"category": None,
		"rate": 0.0,
		"taxable_amount": 0.0,
		"amount": amount,
		"base_taxable_amount": 0.0,
		"base_amount": amount,
		"account_head": None,
		"source": "legacy",
	}
