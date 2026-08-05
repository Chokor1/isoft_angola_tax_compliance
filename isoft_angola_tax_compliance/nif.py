# Copyright (c) 2026, ISOFT and contributors
# For license information, please see license.txt
"""Angolan NIF (Tax ID) format validation.

    Individual   9 digits, 2 capital letters, 3 digits   002282100LA037
    Company      10 digits, all numeric, starting with 5  5002751267

Invalid NIFs are the usual cause of an invoice AGT rejects, so the cheapest
place to catch one is where it is typed.

Two things this is careful about, both learned from the real data:

* `999999999` and similar placeholders are in legitimate use for consumidor
  final. They are exempted by configuration, not by a special case in code.

* A large share of "invalid" NIFs are actually valid NIFs on a record whose
  `customer_type` is wrong -- an individual's `005364819LA044` sitting on a
  customer typed as Company. The validator says so explicitly rather than
  reporting a bad NIF, because the fix is the type, not the number.
"""

import re

import frappe
from frappe import _

SETTINGS = "Angola NIF Validation Settings"

INDIVIDUAL = re.compile(r"^\d{9}[A-Z]{2}\d{3}$")
COMPANY = re.compile(r"^5\d{9}$")

PATTERNS = {"Individual": INDIVIDUAL, "Company": COMPANY}

DESCRIPTIONS = {
	"Individual": "9 digits, 2 capital letters, then 3 digits (14 characters), e.g. 002282100LA037",
	"Company": "10 digits, all numeric, starting with 5, e.g. 5002751267",
}


def get_settings():
	try:
		return frappe.get_cached_doc(SETTINGS)
	except frappe.DoesNotExistError:
		return None


def is_enabled():
	settings = get_settings()
	return bool(settings and settings.enabled)


def get_exempt():
	settings = get_settings()
	raw = (settings.exempt_tax_ids or "") if settings else ""
	return {line.strip() for line in raw.splitlines() if line.strip()}


def check(tax_id, customer_type, settings=None):
	"""Return (ok, message). `message` is None when ok.

	Never raises -- the caller decides whether a failure warns or blocks.
	"""
	settings = settings or get_settings()
	tax_id = (tax_id or "").strip()

	if not tax_id:
		if not settings or settings.allow_blank:
			return True, None
		return False, _("Tax ID (NIF) is required.")

	if tax_id in get_exempt():
		return True, None

	customer_type = customer_type or "Company"
	pattern = PATTERNS.get(customer_type)
	if not pattern:
		return True, None

	if pattern.match(tax_id):
		return True, None

	# The number may be well-formed for the *other* type, which means the
	# customer type is what is wrong. Saying so saves a pointless hunt.
	for other, other_pattern in PATTERNS.items():
		if other != customer_type and other_pattern.match(tax_id):
			return False, _(
				"NIF {0} is a valid <b>{1}</b> NIF, but this customer is set as <b>{2}</b>. "
				"Change the Customer Type rather than the NIF."
			).format(frappe.bold(tax_id), other, customer_type)

	return False, _("NIF {0} is not valid for a <b>{1}</b>. Expected {2}.").format(
		frappe.bold(tax_id), customer_type, DESCRIPTIONS.get(customer_type, "")
	)


def _report(doc, message, settings):
	title = _("Invalid NIF")
	if settings and settings.enforcement == "Block":
		frappe.throw(message, title=title)
	frappe.msgprint(message, title=title, indicator="orange")


def _changed(doc, *fieldnames):
	"""True for a new document, or when any of these fields changed."""
	if doc.get("__islocal") or not doc.get("name"):
		return True

	before = doc.get_doc_before_save()
	if not before:
		return True

	return any(doc.get(f) != before.get(f) for f in fieldnames)


def validate_customer(doc, method=None):
	settings = get_settings()
	if not (settings and settings.enabled and settings.validate_on_customer):
		return

	if settings.only_on_change and not _changed(doc, "tax_id", "customer_type"):
		return

	ok, message = check(doc.get("tax_id"), doc.get("customer_type"), settings)
	if not ok:
		_report(doc, message, settings)


def validate_transaction(doc, method=None):
	"""Quotation / Sales Invoice.

	Checks the NIF that will actually be printed and filed. Sales Invoice keeps
	its own `tax_id` snapshot, so that is what matters there; Quotation has no
	such field, so the customer's current NIF is used.
	"""
	settings = get_settings()
	if not (settings and settings.enabled):
		return

	flag = {
		"Quotation": "validate_on_quotation",
		"Sales Invoice": "validate_on_sales_invoice",
	}.get(doc.doctype)
	if not flag or not settings.get(flag):
		return

	customer = doc.get("customer")
	if not customer and doc.doctype == "Quotation":
		if (doc.get("quotation_to") or "Customer") != "Customer":
			return
		customer = doc.get("party_name")
	if not customer:
		return

	customer_type, customer_tax_id = frappe.get_cached_value(
		"Customer", customer, ["customer_type", "tax_id"]
	)
	tax_id = doc.get("tax_id") or customer_tax_id

	ok, message = check(tax_id, customer_type, settings)
	if not ok:
		_report(
			doc,
			_("{0}: {1}").format(frappe.bold(customer), message),
			settings,
		)


@frappe.whitelist()
def check_tax_id(tax_id=None, customer_type=None):
	"""Whitelisted single check, for client-side feedback."""
	ok, message = check(tax_id, customer_type)
	return {"valid": ok, "message": message}
