# Copyright (c) 2026, ISOFT and contributors
# For license information, please see license.txt
"""Writes the engine result onto the Sales Invoice."""

import frappe
from frappe import _
from frappe.utils import flt

from isoft_angola_tax_compliance.withholding import engine
from isoft_angola_tax_compliance.withholding.settings import is_enabled

TABLE_FIELD = "atc_withholdings"
TOTAL_FIELD = "atc_total_withholding_amount"
BASE_TOTAL_FIELD = "atc_base_total_withholding_amount"
NET_RECEIVABLE_FIELD = "atc_net_amount_receivable"
ENGINE_FIELD = "atc_engine"

# Legacy reference fields. They used to be DocFields on the ERPNext fork driving
# the old calculation; that code is gone and the app now owns them as hidden,
# read-only Custom Fields. Written by the engine, never read by it.
LEGACY_TOTAL_FIELD = "total_tax_withholding_amount"
LEGACY_FLAG_FIELD = "apply_tax_withholding_on_service"

ENGINE_TAG = "isoft_angola_tax_compliance"


def set_withholdings(doc, method=None):
	"""Recompute and store the withholding rows on a Sales Invoice or Quotation.

	`method` is unused; it is there because this is also wired as a `doc_events`
	validate handler for Quotation, and Frappe calls those with (doc, method).
	"""
	if not frappe.get_meta(doc.doctype).get_field(TABLE_FIELD):
		# Custom fields not installed yet.
		return

	if not is_enabled(doc.get("company")):
		doc.set(TABLE_FIELD, [])
		doc.set(TOTAL_FIELD, 0)
		doc.set(BASE_TOTAL_FIELD, 0)
		doc.set(NET_RECEIVABLE_FIELD, flt(doc.get("grand_total")))
		doc.set(ENGINE_FIELD, None)
		return

	result = engine.evaluate(doc)
	rows = result["rows"]

	doc.set(TABLE_FIELD, [])
	for row in rows:
		doc.append(TABLE_FIELD, row)

	total = sum(flt(r["withholding_amount"]) for r in rows)
	base_total = sum(flt(r["base_withholding_amount"]) for r in rows)

	doc.set(TOTAL_FIELD, total)
	doc.set(BASE_TOTAL_FIELD, base_total)
	doc.set(NET_RECEIVABLE_FIELD, flt(doc.get("grand_total")) - total)
	doc.set(ENGINE_FIELD, ENGINE_TAG if rows else None)

	_set_item_amounts(doc, rows)

	_sync_legacy_reference_fields(doc, rows)

	# A misconfigured category is a hard error: the alternative is booking a
	# silently wrong amount, or none at all, on a fiscal document.
	for message in result["messages"]:
		frappe.throw(message, title=_("Withholding Configuration"))


def _sync_legacy_reference_fields(doc, rows):
	"""Mirror the engine result onto the two legacy fields.

	They are hidden, read-only reference fields now: print formats and the
	not-yet-migrated SAF-T / AGT exporters still read them, but nothing in the
	engine ever reads them back. The write is one-directional by design -- the
	old flag can no longer influence any calculation.

	`total_tax_withholding_amount` has always meant "retenção II only", so that
	meaning is preserved exactly; IVA cativo is not folded into it.
	"""
	meta = frappe.get_meta(doc.doctype)
	ii_total = sum(flt(r["withholding_amount"]) for r in rows if r["withholding_type"] == "II")

	if meta.get_field(LEGACY_TOTAL_FIELD):
		doc.set(LEGACY_TOTAL_FIELD, ii_total)

	if meta.get_field(LEGACY_FLAG_FIELD):
		doc.set(LEGACY_FLAG_FIELD, 1 if ii_total else 0)


def _set_item_amounts(doc, rows):
	"""Per-line breakdown for item-based categories."""
	field = "atc_withholding_amount"
	if not frappe.get_meta(doc.doctype + " Item").get_field(field):
		return

	for item in doc.get("items") or []:
		item.set(field, 0)

	from isoft_angola_tax_compliance.withholding import resolver

	by_category = {r["tax_withholding_category"]: r for r in rows}
	item_map = resolver.get_item_categories(doc)

	for category, items in item_map.items():
		row = by_category.get(category)
		if not row:
			continue

		base = sum(flt(i.get("net_amount")) for i in items)
		if not base:
			continue

		allocated = 0
		for idx, item in enumerate(items):
			if idx == len(items) - 1:
				# Last line absorbs the rounding remainder so the per-line
				# amounts always sum back to the row total.
				amount = flt(row["withholding_amount"]) - allocated
			else:
				amount = flt(
					flt(row["withholding_amount"]) * flt(item.get("net_amount")) / base,
					doc.precision("grand_total") or 2,
				)
				allocated += amount

			item.set(field, amount)
