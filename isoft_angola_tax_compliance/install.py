# Copyright (c) 2026, ISOFT and contributors
# For license information, please see license.txt
"""Install / migrate hooks.

Deliberately does NOT create any Angola Tax Compliance Settings record. A
company with no settings row resolves to mode Off, so installing this app
changes nothing about how the site behaves until someone explicitly opts a
company into Shadow.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from isoft_angola_tax_compliance.custom_fields import get_custom_fields


def after_install():
	setup_custom_fields()


def after_migrate():
	setup_custom_fields()


def setup_custom_fields():
	frappe.clear_cache()

	fields, skipped = filter_conflicting_fields(get_custom_fields())

	for name in skipped:
		print(f"  skipping custom field {name}: a DocField of that name already exists")

	if fields:
		create_custom_fields(fields, ignore_validate=True)

	frappe.db.commit()
	return {"created": sorted(fields), "skipped": skipped}


def filter_conflicting_fields(fields):
	"""Drop definitions whose fieldname is already a core DocField.

	Two of this app's fields -- `apply_tax_withholding_on_service` and
	`total_tax_withholding_amount` -- exist as DocFields on installs whose
	ERPNext still carries the old in-core withholding customization. Frappe
	refuses to create a Custom Field over an existing DocField, which would
	otherwise abort the whole install.

	Skipping them is correct rather than merely tolerant: the field is already
	present and writable, so the engine works unchanged. Once the DocField is
	removed from ERPNext, the next `bench migrate` finds the name free and
	creates the Custom Field, taking ownership with no further action.

	Custom Fields this app already created are NOT skipped -- they are updated,
	so definition changes still propagate.
	"""
	filtered = {}
	skipped = []

	for doctype, definitions in fields.items():
		if not frappe.db.exists("DocType", doctype):
			skipped.append(f"{doctype} (doctype not installed)")
			continue

		meta = frappe.get_meta(doctype, cached=False)
		keep = []

		for df in definitions:
			existing = meta.get_field(df["fieldname"])
			if existing and not existing.get("is_custom_field"):
				skipped.append(f"{doctype}.{df['fieldname']}")
				continue
			keep.append(df)

		if keep:
			filtered[doctype] = keep

	return filtered, skipped
