# Copyright (c) 2026, ISOFT and contributors
# For license information, please see license.txt
"""Works out *which* Tax Withholding Categories apply to a document.

Two independent scopes, because the two Angolan regimes are driven by
different things:

  Party Based   the customer is a withholding entity (IVA cativo: Estado 100%,
                grandes contribuintes 50%). Nothing to do with what you sell.

  Item Based    the line is a service subject to retencao na fonte (II 6.5%).
                Resolved per line, most specific wins:

                    Sales Invoice Item.atc_withholding_category
                 -> Sales Invoice Item.tax_withholding_category   (legacy field)
                 -> Item.atc_tax_withholding_category
                 -> Item.tax_withholding_category                 (legacy field)
                 -> Item Group tree, walking up to the root
                 -> Angola Tax Compliance Settings default

The legacy fallbacks exist so the resolver keeps working both before and after
the core ERPNext fields are removed in the cleanup phase.
"""

import frappe
from frappe.utils import getdate

from isoft_angola_tax_compliance.withholding.settings import get_settings

# Custom fields owned by this app.
ITEM_CATEGORY_FIELD = "atc_tax_withholding_category"
ITEM_GROUP_CATEGORY_FIELD = "atc_tax_withholding_category"
INVOICE_ITEM_CATEGORY_FIELD = "atc_withholding_category"
PARTY_TABLE_FIELD = "atc_withholdings"

# Pre-existing core fields on this fork, kept as a fallback.
LEGACY_ITEM_CATEGORY_FIELD = "tax_withholding_category"
LEGACY_INVOICE_ITEM_CATEGORY_FIELD = "tax_withholding_category"

MAX_ITEM_GROUP_DEPTH = 20


def get_customer(doc):
	"""The customer, whatever the document calls it.

	Sales Invoice has `customer`; Quotation has `quotation_to` + `party_name`
	and may point at a Lead, in which case there is no customer to resolve
	withholdings for.
	"""
	customer = doc.get("customer")
	if customer:
		return customer

	if doc.get("doctype") == "Quotation":
		if (doc.get("quotation_to") or "Customer") != "Customer":
			return None
		return doc.get("party_name")

	return None


def get_posting_date(doc):
	"""Sales Invoice posts on `posting_date`, Quotation on `transaction_date`."""
	return doc.get("posting_date") or doc.get("transaction_date")


def get_party_categories(doc):
	"""Categories attached to the customer and valid on the posting date."""
	party = get_customer(doc)
	if not party:
		return []

	rows = frappe.get_all(
		"Party Tax Withholding",
		filters={"parent": party, "parenttype": "Customer", "parentfield": PARTY_TABLE_FIELD},
		fields=["tax_withholding_category", "valid_from", "valid_to"],
		order_by="idx asc",
	)

	posting_date = getdate(get_posting_date(doc))
	categories = []

	for row in rows:
		if not row.tax_withholding_category:
			continue
		if row.valid_from and getdate(row.valid_from) > posting_date:
			continue
		if row.valid_to and getdate(row.valid_to) < posting_date:
			continue
		if row.tax_withholding_category not in categories:
			categories.append(row.tax_withholding_category)

	return categories


def resolve_item_category(item_row, settings=None):
	"""Resolve the withholding category for one invoice line."""
	category = item_row.get(INVOICE_ITEM_CATEGORY_FIELD) or item_row.get(
		LEGACY_INVOICE_ITEM_CATEGORY_FIELD
	)
	if category:
		return category

	item_code = item_row.get("item_code")
	if item_code:
		category = _get_item_category(item_code)
		if category:
			return category

	item_group = item_row.get("item_group")
	if item_group:
		category = _get_item_group_category(item_group)
		if category:
			return category

	if settings:
		return settings.default_service_withholding_category

	return None


def get_item_categories(doc, settings=None):
	"""Map category -> [invoice line, ...] for every line that resolves one."""
	if settings is None:
		settings = get_settings(doc.get("company"))

	mapping = {}
	for item_row in doc.get("items") or []:
		category = resolve_item_category(item_row, settings)
		if category:
			mapping.setdefault(category, []).append(item_row)

	return mapping


def _get_item_category(item_code):
	fields = _existing_fields("Item", [ITEM_CATEGORY_FIELD, LEGACY_ITEM_CATEGORY_FIELD])
	if not fields:
		return None

	values = frappe.get_cached_value("Item", item_code, fields, as_dict=True) or {}

	# An explicit "do not withhold" on the item wins over any group default.
	if _has_field("Item", "apply_tax_withholding"):
		apply_flag = frappe.get_cached_value("Item", item_code, "apply_tax_withholding")
		if apply_flag is not None and int(apply_flag or 0) == 0 and not values.get(ITEM_CATEGORY_FIELD):
			return None

	for field in fields:
		if values.get(field):
			return values[field]

	return None


def _get_item_group_category(item_group):
	if not _has_field("Item Group", ITEM_GROUP_CATEGORY_FIELD):
		return None

	current = item_group
	seen = set()

	for _ in range(MAX_ITEM_GROUP_DEPTH):
		if not current or current in seen:
			break
		seen.add(current)

		row = frappe.get_cached_value(
			"Item Group", current, [ITEM_GROUP_CATEGORY_FIELD, "parent_item_group"], as_dict=True
		)
		if not row:
			break
		if row.get(ITEM_GROUP_CATEGORY_FIELD):
			return row[ITEM_GROUP_CATEGORY_FIELD]

		current = row.get("parent_item_group")

	return None


def _has_field(doctype, fieldname):
	return bool(frappe.get_meta(doctype).get_field(fieldname))


def _existing_fields(doctype, fieldnames):
	meta = frappe.get_meta(doctype)
	return [f for f in fieldnames if meta.get_field(f)]
