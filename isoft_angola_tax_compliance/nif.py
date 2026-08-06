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


def matching_type(tax_id):
	"""The customer type this NIF is unambiguously formatted for, or None."""
	tax_id = (tax_id or "").strip()
	if not tax_id:
		return None

	for customer_type, pattern in PATTERNS.items():
		if pattern.match(tax_id):
			return customer_type

	return None


def validate_customer(doc, method=None):
	settings = get_settings()
	if not (settings and settings.enabled and settings.validate_on_customer):
		return

	# Deliberately ahead of the only-on-change guard. That guard exists to stop
	# an unrelated edit being *refused* over pre-existing bad data; a correction
	# refuses nothing and only ever improves the record, so gating it there
	# would leave data wrong that we already know how to fix.
	if settings.auto_correct_customer_type:
		correct = matching_type(doc.get("tax_id"))
		if correct and correct != doc.get("customer_type"):
			was = doc.get("customer_type")
			doc.customer_type = correct
			frappe.msgprint(
				_("Customer Type changed from {0} to {1} to match the NIF.").format(was, correct),
				alert=True,
				indicator="blue",
			)

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


# ---------------------------------------------------------------------------
# Bulk correction of Customer Type
# ---------------------------------------------------------------------------
# Only applied where the NIF is well-formed for the *other* type, so the right
# answer is not in doubt. A malformed number is never guessed at -- those stay
# in the Invalid NIF report for a human to look at.


def find_type_fixes(include_disabled=False):
	"""Every customer whose NIF is valid under the other type. Writes nothing."""
	conditions = "" if include_disabled else "AND IFNULL(c.disabled, 0) = 0"
	rows = frappe.db.sql(
		"""SELECT c.name, c.customer_name, c.customer_type, IFNULL(c.tax_id, '') AS tax_id
		   FROM `tabCustomer` c
		   WHERE IFNULL(c.tax_id, '') <> '' {0}
		   ORDER BY c.name""".format(conditions),
		as_dict=True,
	)

	fixes = []
	for row in rows:
		correct = matching_type(row.tax_id)
		if correct and correct != row.customer_type:
			fixes.append(
				{
					"customer": row.name,
					"customer_name": row.customer_name,
					"tax_id": row.tax_id,
					"from_type": row.customer_type,
					"to_type": correct,
				}
			)

	return fixes


def summarise_fixes(fixes, sample=15):
	counts = {}
	for fix in fixes:
		key = "{0} -> {1}".format(fix["from_type"], fix["to_type"])
		counts[key] = counts.get(key, 0) + 1

	return {"total": len(fixes), "by_direction": counts, "sample": fixes[:sample]}


def apply_type_fixes(include_disabled=False):
	"""Write the corrected Customer Type.

	Uses db.set_value rather than doc.save: this is a one-field correction over
	potentially thousands of records, and a full save would fire every Customer
	hook -- including this app's own validation -- on each one.
	"""
	fixes = find_type_fixes(include_disabled=include_disabled)
	for fix in fixes:
		frappe.db.set_value(
			"Customer", fix["customer"], "customer_type", fix["to_type"], update_modified=False
		)

	frappe.db.commit()
	frappe.clear_cache()
	summary = summarise_fixes(fixes, sample=0)
	return {"updated": summary["total"], "by_direction": summary["by_direction"]}


@frappe.whitelist()
def get_type_fix_preview(include_disabled=0):
	frappe.has_permission("Customer", "write", throw=True)
	return summarise_fixes(find_type_fixes(include_disabled=int(include_disabled or 0)))


@frappe.whitelist()
def run_type_fix(include_disabled=0):
	frappe.has_permission("Customer", "write", throw=True)
	return apply_type_fixes(include_disabled=int(include_disabled or 0))


def fix_customer_types(confirm=False, include_disabled=False):
	"""Command-line entry point. Dry run unless confirm=True.

	    bench --site <site> execute isoft_angola_tax_compliance.nif.fix_customer_types
	    bench --site <site> execute isoft_angola_tax_compliance.nif.fix_customer_types \\
	        --kwargs '{"confirm": true}'
	"""
	summary = summarise_fixes(find_type_fixes(include_disabled=include_disabled))

	print("=" * 76)
	print("  CUSTOMER TYPE FIX" + ("" if confirm else "   [DRY RUN -- nothing written]"))
	print("=" * 76)
	print(f"\n  customers with a valid NIF under the wrong type : {summary['total']}")
	for direction, count in summary["by_direction"].items():
		print(f"      {direction:26} {count}")

	if summary["sample"]:
		print("\n  sample:")
		for fix in summary["sample"]:
			print(f"      {fix['customer'][:26]:28} {fix['tax_id']:16} "
				f"{fix['from_type']:11} -> {fix['to_type']}")

	if not summary["total"] or not confirm:
		if summary["total"]:
			print("\n  DRY RUN -- nothing written. Re-run with --kwargs '{\"confirm\": true}'.")
		return summary

	applied = apply_type_fixes(include_disabled=include_disabled)
	print(f"\n  Updated {applied['updated']} customer(s).")
	return applied
