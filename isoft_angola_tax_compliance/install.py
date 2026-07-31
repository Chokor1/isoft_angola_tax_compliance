# Copyright (c) 2026, ISOFT and contributors
# For license information, please see license.txt
"""Install / migrate hooks.

The engine has no settings doctype: it applies to any company whose country is
Angola, because retencao na fonte and IVA cativo are Angolan law rather than a
preference. Installing on a site with no Angolan company therefore changes
nothing.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.custom.doctype.property_setter.property_setter import make_property_setter

from isoft_angola_tax_compliance.custom_fields import get_custom_fields


def after_install():
	setup_custom_fields()
	remove_retired_doctypes()
	hide_superseded_core_fields()
	seed_from_legacy_configuration()


def after_migrate():
	setup_custom_fields()
	remove_retired_doctypes()
	hide_superseded_core_fields()
	seed_from_legacy_configuration()


# Doctypes this app used to ship and has since dropped. Deleting the folder does
# not delete the DocType record, and a standard doctype whose Python module is
# gone makes Frappe raise ImportError the moment anyone opens its list view --
# the same failure the abandoned `Tax Withholding` doctype causes. Removing them
# here keeps the app from leaving that trap behind.
RETIRED_DOCTYPES = [
	# Replaced by the country rule: any company in Angola is subject to
	# withholding, so there was nothing left for this to configure.
	"Angola Tax Compliance Settings",
	# Recorded the legacy figure beside the new one during the cutover. The
	# legacy path no longer exists, so there is nothing to compare against.
	"Withholding Comparison Log",
]


def remove_retired_doctypes():
	removed = []
	for doctype in RETIRED_DOCTYPES:
		if not frappe.db.exists("DocType", doctype):
			continue

		# delete_doc drops the table itself; issuing DDL here as well would trip
		# Frappe's implicit-commit guard during migrate.
		frappe.delete_doc("DocType", doctype, force=True, ignore_permissions=True)
		removed.append(doctype)

	if removed:
		frappe.db.commit()
		print(f"  Angola withholding: removed retired doctype(s) {removed}")

	return removed


# Core ERPNext fields this app supersedes, hidden via Property Setter rather
# than removed: they belong to upstream TDS/TCS, which is simply not the Angolan
# mechanism. Hiding is reversible and touches no ERPNext file.
SUPERSEDED_FIELDS = [
	# Was gated on the fork's `withholding_tax` select. That select is gone, so
	# the field became permanently visible and sits confusingly next to the
	# `Angolan Withholding` table that actually drives the engine.
	("Customer", "tax_withholding_category"),
]


def hide_superseded_core_fields():
	for doctype, fieldname in SUPERSEDED_FIELDS:
		if not frappe.db.exists("DocType", doctype):
			continue

		field = frappe.get_meta(doctype, cached=False).get_field(fieldname)
		if not field or field.get("is_custom_field"):
			# Absent, or owned by someone else -- leave it alone.
			continue

		make_property_setter(
			doctype, fieldname, "hidden", 1, "Check", validate_fields_for_doctype=False
		)

	frappe.clear_cache()


def seed_from_legacy_configuration():
	"""Build the Tax Withholding Categories and customer rows from the old setup.

	Runs on every install and every migrate, so `bench update` alone is enough --
	no one has to remember a manual step, and a company or customer configured
	after the first deploy still gets picked up. The seeding is idempotent and
	additive, and it never changes a company's mode, so repeating it is free and
	changes no accounting.

	This replaced the one-shot patches: a patch that has already been logged
	never runs again, which is exactly how the first (broken) seeding stayed
	invisible on an updated site.
	"""
	# Imported here rather than at module scope: this runs during install, when
	# the app's own doctypes may not be importable yet on a fresh site.
	from isoft_angola_tax_compliance.migrate_legacy import autorun

	if not frappe.db.exists("DocType", "Party Tax Withholding"):
		return None

	return autorun()


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
