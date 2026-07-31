# Copyright (c) 2026, ISOFT and contributors
# For license information, please see license.txt
"""Per-company mode switch for the Angolan withholding engine.

Mode semantics
--------------
Off     engine does nothing at all; the legacy core path owns everything.
Shadow  engine computes and stores its result on the invoice and writes a
        Withholding Comparison Log row, but posts NO GL entries and does NOT
        suppress the legacy path. Behaviour of the site is unchanged.
Active  engine posts the withholding GL inside the invoice and the legacy
        core methods are neutralised by the Sales Invoice override.
"""

import frappe

SETTINGS_DOCTYPE = "Angola Tax Compliance Settings"

OFF = "Off"
SHADOW = "Shadow"
ACTIVE = "Active"


def get_settings(company):
	"""Return the settings doc for a company, or None when not configured."""
	if not company:
		return None

	name = frappe.db.exists(SETTINGS_DOCTYPE, {"company": company})
	if not name:
		return None

	return frappe.get_cached_doc(SETTINGS_DOCTYPE, name)


def get_mode(company):
	settings = get_settings(company)
	if not settings:
		return OFF

	return settings.mode or OFF


def is_enabled(company):
	"""True when the engine should compute (Shadow or Active)."""
	return get_mode(company) in (SHADOW, ACTIVE)


def is_active(company):
	"""True only when the engine owns the accounting."""
	return get_mode(company) == ACTIVE


POS_PROFILE_FIELD = "atc_enable_withholding"


def applies_to_document(doc):
	"""Gate a specific document: mode on, plus the per-doctype rules below."""
	settings = get_settings(doc.get("company"))
	if not settings or (settings.mode or OFF) == OFF:
		return False

	if doc.get("doctype") == "Quotation":
		# Quotations never post GL -- this is a quoted figure only, so it is
		# shown in every mode the engine is on, including Shadow.
		return bool(settings.get("apply_on_quotation"))

	if doc.get("is_pos"):
		return pos_withholding_enabled(doc.get("pos_profile"), settings)

	return True


def pos_withholding_enabled(pos_profile=None, settings=None):
	"""Is withholding enabled for POS?

	The POS Profile checkbox is the authority when it is set, so a shop can be
	switched on or off independently of the rest of the company. The
	company-wide `apply_on_pos` is only the fallback for profiles that predate
	the field or have it unset.
	"""
	if pos_profile and frappe.get_meta("POS Profile").get_field(POS_PROFILE_FIELD):
		value = frappe.db.get_value("POS Profile", pos_profile, POS_PROFILE_FIELD)
		if value is not None:
			return bool(int(value or 0))

	return bool(settings and settings.apply_on_pos)
