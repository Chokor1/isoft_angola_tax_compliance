# Copyright (c) 2026, ISOFT and contributors
# For license information, please see license.txt

import frappe

ACCOUNT_FIELDS = [
	"deductible_vat_account",
	"output_vat_account",
	"withheld_vat_recoverable_account",
	"vat_assessment_account",
	"vat_payable_account",
	"vat_recoverable_account",
]


def execute():
	"""Move the single-company VAT account configuration into a per-company table.

	VAT Assessment Settings used to hold one company and one set of accounts, so
	only that company could ever run an assessment. The accounts now live in the
	`company_accounts` child table, one row per company, and each adjustment
	account row carries the company it belongs to.
	"""
	if not frappe.db.exists("DocType", "VAT Assessment Settings"):
		return

	# Read the old values before reloading the doctype drops the fields.
	# `tabSingles` has no `modified` column, so it cannot be read via get_value.
	stored = dict(
		frappe.db.sql(
			"""select field, value from `tabSingles` where doctype = %s""",
			("VAT Assessment Settings",),
		)
	)

	old = stored.get("company")
	old_accounts = {field: stored.get(field) for field in ACCOUNT_FIELDS}

	frappe.reload_doc("isoft_angola_tax_compliance", "doctype", "vat_assessment_company_account")
	frappe.reload_doc("isoft_angola_tax_compliance", "doctype", "vat_assessment_adjustment_account")
	frappe.reload_doc("isoft_angola_tax_compliance", "doctype", "vat_assessment_settings")

	if not old:
		# Nothing was configured; there is nothing to carry over.
		return

	settings = frappe.get_single("VAT Assessment Settings")

	# The adjustment rows all belonged to the one configured company.
	for row in settings.adjustment_accounts or []:
		if not row.company:
			row.db_set("company", old, update_modified=False)

	if not any(row.company == old for row in settings.company_accounts or []):
		if any(old_accounts.get(field) for field in ACCOUNT_FIELDS):
			settings.append("company_accounts", dict({"company": old}, **old_accounts))
			settings.flags.ignore_validate = True
			settings.flags.ignore_mandatory = True
			settings.save(ignore_permissions=True)

	# Drop the retired single fields so they cannot be read back by accident.
	retired = ["company"] + ACCOUNT_FIELDS
	frappe.db.sql(
		"""delete from `tabSingles` where doctype = %s and field in ({0})""".format(
			", ".join(["%s"] * len(retired))
		),
		["VAT Assessment Settings"] + retired,
	)

	frappe.clear_cache(doctype="VAT Assessment Settings")
