# Copyright (c) 2026, ISOFT and contributors
# For license information, please see license.txt
"""When does the withholding engine apply?

There is no settings doctype and no mode. Retencao na fonte and IVA cativo are
Angolan law, so the condition is simply the company's country:

    Company.country == "Angola"  ->  the engine computes and books.

The only per-document switch left is POS, because a shop may legitimately not
want withholding on counter sales; that lives on the POS Profile.
"""

import frappe

ANGOLA = "Angola"
POS_PROFILE_FIELD = "atc_enable_withholding"


def is_enabled(company):
	"""True when this company is subject to Angolan withholding."""
	if not company:
		return False

	return frappe.get_cached_value("Company", company, "country") == ANGOLA


def applies_to_document(doc):
	"""Gate a specific document."""
	company = doc.get("company")
	if not is_enabled(company):
		return False

	if doc.get("doctype") == "Quotation":
		# A Quotation posts no GL; the figure is quoted, never booked.
		return True

	if doc.get("is_pos"):
		return pos_withholding_enabled(doc.get("pos_profile"))

	return True


def pos_withholding_enabled(pos_profile=None):
	"""Is withholding enabled for this POS Profile?

	Off by default: a counter sale to a walk-in customer is the common case, and
	silently withholding on it would be worse than not offering it. Tick
	`Enable Withholding` on the profile for shops that need it.
	"""
	if not pos_profile:
		return False

	if not frappe.get_meta("POS Profile").get_field(POS_PROFILE_FIELD):
		return False

	return bool(int(frappe.db.get_value("POS Profile", pos_profile, POS_PROFILE_FIELD) or 0))
