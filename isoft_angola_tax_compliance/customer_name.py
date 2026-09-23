# Copyright (c) 2026, ISOFT and contributors
# For license information, please see license.txt
"""Customer name rules, next to the NIF rules in Angola NIF Validation Settings.

A customer's name is printed on the invoice and filed in SAF-T, so records
named after a phone number, an e-mail address or a keying mistake are a
compliance problem, not just untidy data. Each rule is a separate switch and
they are all off until `validate_customer_name` is ticked.

Rules
-----
digits      no 0-9 in the name. On dev.isoft.ao 98 company names legitimately
            carry digits (ANGOLA 2000, 1 De AGOSTO, M3A) while the only
            individuals with digits were named after an e-mail address, so the
            rule is limited to individuals unless set to all customers.
e-mail      the name is an e-mail address or a link.
symbols     characters outside letters, digits, spaces and a small punctuation
            set. Off by default: (SU), N'KALA and 1 are ordinary here.
length      shorter than N characters.

Enforcement follows the same rules as the NIF check: Warn lets the record
save, Block refuses it, and inside POS the POS enforcement setting wins so a
cashier is never stopped mid-sale. `only_on_change` is honoured too, so
editing something unrelated on an old customer never refuses the save.
"""

import re

import frappe
from frappe import _
from frappe.utils import cint

from isoft_angola_tax_compliance import nif
from isoft_angola_tax_compliance.isoft_angola_tax_compliance.doctype.angola_nif_validation_settings.angola_nif_validation_settings import (
	CUSTOMER_NAME_DEFAULTS,
)

# Letters, digits, spaces and the punctuation Angolan trade names actually use:
# (SU), N'KALA KALA, 1, S.A., Irmaos & Filhos, CAP 38. Letters are spelled
# out rather than \w, which would also allow digits and underscores and so
# undo the digit rule.
SYMBOL_FORBIDDEN = re.compile(
	"[^0-9A-Za-zÀ-ɏḀ-ỿ \\.\\,\\-'´`&/()ºª+#:]"
)
EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}")
LINK = re.compile(r"(https?://|www\.)", re.IGNORECASE)
DIGIT = re.compile(r"\d")


def get_settings():
	return nif.get_settings()


def _setting(settings, fieldname):
	"""A rule's value, falling back to its default.

	A Single keeps one row per field, so a field nobody has saved yet reads as
	None rather than as the doctype default -- which would switch a rule off
	while its checkbox looks ticked on screen.
	"""
	value = settings.get(fieldname) if settings else None
	if value in (None, ""):
		return CUSTOMER_NAME_DEFAULTS.get(fieldname)
	return value


def is_enabled(settings=None):
	settings = settings or get_settings()
	return bool(settings and settings.get("validate_customer_name"))


def exempt_names(settings=None):
	settings = settings or get_settings()
	raw = (settings.get("name_exempt") or "") if settings else ""
	return {_squash(line) for line in raw.splitlines() if line.strip()}


def _squash(value):
	"""Compare names ignoring case and repeated or surrounding spaces."""
	return " ".join((value or "").split()).casefold()


def check(customer_name, customer_type=None, settings=None):
	"""Return (ok, message). Never raises: the caller decides warn or block."""
	settings = settings or get_settings()
	if not is_enabled(settings):
		return True, None

	name = (customer_name or "").strip()
	if not name:
		# An empty name is ERPNext's own mandatory-field error, not ours.
		return True, None

	if _squash(name) in exempt_names(settings):
		return True, None

	if _setting(settings, "name_reject_email") and (EMAIL.search(name) or LINK.search(name)):
		return False, _(
			"Customer name {0} looks like an e-mail address or a link, not a name. "
			"Put the e-mail on the contact and give the customer their real name."
		).format(frappe.bold(name))

	if _setting(settings, "name_reject_digits") and DIGIT.search(name):
		applies = _setting(settings, "name_applies_to")
		if applies == "All Customers" or (customer_type or "") == "Individual":
			return False, _(
				"Customer name {0} contains digits. A person's name is letters only -- "
				"if this is a company, set Customer Type to Company; if the name really "
				"carries digits, add it to Always Accept These Names in Angola NIF "
				"Validation Settings."
			).format(frappe.bold(name))

	if _setting(settings, "name_reject_symbols"):
		bad = sorted({c for c in SYMBOL_FORBIDDEN.findall(name)})
		if bad:
			return False, _("Customer name {0} contains characters that are not allowed: {1}").format(
				frappe.bold(name), frappe.bold(" ".join(bad))
			)

	minimum = cint(_setting(settings, "name_min_length"))
	if minimum and len(name) < minimum:
		return False, _("Customer name {0} is shorter than {1} characters.").format(
			frappe.bold(name), minimum
		)

	return True, None


def validate(doc, method=None):
	"""Customer.validate hook. Registered in hooks.py next to the NIF check."""
	settings = get_settings()
	if not is_enabled(settings):
		return

	if settings.get("only_on_change") and not nif._changed(doc, "customer_name", "customer_type"):
		return

	ok, message = check(doc.get("customer_name"), doc.get("customer_type"), settings)
	if ok:
		return

	title = _("Check the Customer Name")
	# In POS the POS setting decides, exactly as for the NIF check.
	level = (
		(settings.get("pos_enforcement") or "Warn")
		if nif.in_pos_context()
		else (_setting(settings, "customer_name_enforcement") or "Warn")
	)
	if level == "Block":
		frappe.throw(message, title=title)
	frappe.msgprint(message, title=title, indicator="orange")


@frappe.whitelist()
def scan_customer_names(include_disabled=0, limit=200):
	"""Existing customers the current rules would reject.

	Read-only, for the button on the settings form: it says what switching
	Enforcement to Block would refuse before anybody is refused by it.
	"""
	frappe.only_for(("System Manager", "Accounts Manager"))
	settings = get_settings()
	if not is_enabled(settings):
		return {"enabled": False, "total": 0, "checked": 0, "rows": []}

	filters = {} if cint(include_disabled) else {"disabled": 0}
	customers = frappe.get_all(
		"Customer",
		filters=filters,
		fields=["name", "customer_name", "customer_type", "disabled"],
		limit_page_length=0,
	)
	rows = []
	for c in customers:
		ok, message = check(c.customer_name, c.customer_type, settings)
		if not ok:
			rows.append({
				"customer": c.name,
				"customer_name": c.customer_name,
				"customer_type": c.customer_type,
				"message": frappe.utils.strip_html(message),
			})
	return {
		"enabled": True,
		"checked": len(customers),
		"total": len(rows),
		"rows": rows[: cint(limit) or 200],
	}
