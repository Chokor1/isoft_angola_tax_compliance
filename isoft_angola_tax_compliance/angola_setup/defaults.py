# Copyright (c) 2026, ISOFT and contributors
# For license information, please see license.txt
"""Default Angolan tax records, so a new site is usable without hand-keying them.

Three datasets, created when this app is installed and whenever a company in
Angola is created (setup wizard or Company form):

  * Tax Categories `Sales` and `Purchase` -- only on a site that has none at all;
  * the IVA Item Tax Templates (14% normal, 7% regime simplificado, 5% cesta
    básica, and one 0% template per exemption reason M00..M94), carrying the
    SAF-T fields `tax_code` / `tax_type` / `description` / `tax_exemption_code`;
  * the three Tax Withholding Categories the engine works with: retenção na
    fonte II 6,5%, IVA cativo 50% and IVA cativo 100%.

Strictly additive and idempotent: nothing that exists is edited, a template the
company already has (same title, or same code / exemption / rate) is not
duplicated, and a category already configured for a company is left alone.

    bench --site <site> execute isoft_angola_tax_compliance.angola_setup.defaults.setup_site
"""
import json
import os

import frappe
from frappe.utils import flt

from isoft_angola_tax_compliance.migrate_legacy import (
	CAT_II,
	CAT_IVA,
	FROM_DATE,
	II_RATE,
	TO_DATE,
	_ensure_company_row,
	_get_company_account,
	detect_vat_accounts,
)

COUNTRY = "Angola"
ITEM_TAX_TEMPLATES = os.path.join(
	os.path.dirname(os.path.abspath(__file__)), "data", "item_tax_templates.json"
)

TAX_CATEGORIES = ["Sales", "Purchase"]

# Where each regime is booked, by PGC account number. Used as-is when the company
# has the account; otherwise it is created under the nearest existing group.
II_ACCOUNT = {"account_number": "3414", "account_name": "Imposto Industrial - Retenção de Clientes"}
CATIVO_ACCOUNT = {"account_number": "34543", "account_name": "IVA Regularizações - Cativo"}

WITHHOLDING_CATEGORIES = {
	CAT_II: {
		"type": "II",
		"base": "Item Net Amount",
		"scope": "Item Based",
		# Withheld by the acquirer, and a private individual does not withhold.
		"customer_type": "Company",
		"rate": II_RATE,
		"single_threshold": 20000,
		"account": II_ACCOUNT,
	},
	CAT_IVA["50"]: {
		"type": "IVA",
		"base": "Tax Amount",
		"scope": "Party Based",
		"customer_type": "All",
		"rate": 50,
		"single_threshold": 0,
		"account": CATIVO_ACCOUNT,
	},
	CAT_IVA["100"]: {
		"type": "IVA",
		"base": "Tax Amount",
		"scope": "Party Based",
		"customer_type": "All",
		"rate": 100,
		"single_threshold": 0,
		"account": CATIVO_ACCOUNT,
	},
}


# ------------------------------------------------------------------ entry points


def setup_site():
	"""Seed the defaults for every company in Angola. Install hook and manual entry point.

	Never aborts: a seeding step must not be able to break an install, so a failure
	is logged and reported, and whatever it had written for that company is rolled back.
	"""
	from isoft_angola_tax_compliance.angola_setup import _enabled

	if not _enabled():
		return None

	created = {"tax_categories": ensure_tax_categories(), "companies": {}}
	frappe.db.commit()

	for company in frappe.get_all("Company", filters={"country": COUNTRY}, pluck="name"):
		try:
			created["companies"][company] = _setup_company(company)
			frappe.db.commit()
		except Exception:
			frappe.db.rollback()
			frappe.log_error(frappe.get_traceback(), "Angola tax defaults")
			print(f"  !! Angola tax defaults: seeding FAILED for {company}; see Error Log.")

	_report(created)
	return created


def setup_company(company):
	"""Seed the defaults for one newly created company. No commit: runs inside its save."""
	if frappe.db.get_value("Company", company, "country") != COUNTRY:
		return None

	created = {"tax_categories": ensure_tax_categories(), "companies": {}}
	created["companies"][company] = _setup_company(company)
	return created


def _setup_company(company):
	return {
		"item_tax_templates": ensure_item_tax_templates(company),
		"withholding_categories": ensure_withholding_categories(company),
	}


def _report(created):
	if created["tax_categories"]:
		print(f"  Angola tax defaults: created Tax Categories {created['tax_categories']}")
	for company, result in created["companies"].items():
		templates, categories = result["item_tax_templates"], result["withholding_categories"]
		if templates or categories:
			print(
				f"  Angola tax defaults: {company} -- {len(templates)} item tax template(s), "
				f"withholding categories {categories or 'unchanged'}"
			)


# ---------------------------------------------------------------- tax categories


def ensure_tax_categories():
	"""`Sales` and `Purchase`, but only on a site with no Tax Category of its own."""
	if frappe.db.count("Tax Category"):
		return []

	for title in TAX_CATEGORIES:
		frappe.get_doc({"doctype": "Tax Category", "title": title}).insert(ignore_permissions=True)
	return list(TAX_CATEGORIES)


# ----------------------------------------------------------- item tax templates


def _read_item_tax_templates():
	with open(ITEM_TAX_TEMPLATES, "r", encoding="utf-8") as f:
		return json.load(f)


def _template_key(tax_code, tax_exemption_code, tax_rate):
	return (tax_code or "", tax_exemption_code or "", flt(tax_rate))


def ensure_item_tax_templates(company):
	"""Create the IVA Item Tax Templates this company does not have yet."""
	meta = frappe.get_meta("Item Tax Template")
	if not all(meta.has_field(f) for f in ("tax_code", "tax_type", "tax_exemption_code")):
		# The custom fields are created by install.setup_custom_fields; without them
		# the templates would carry no SAF-T data, so there is nothing worth seeding.
		return []

	vat_accounts, source = detect_vat_accounts(company)
	if source != "account_number":
		print(f"  Angola tax defaults: {company} has no IVA Liquidado (3453) account; item tax templates skipped.")
		return []
	vat_account = vat_accounts[0]

	existing = frappe.db.sql(
		"""
		select t.title, t.disabled, t.tax_type, t.tax_code, t.tax_exemption_code, d.tax_rate
		from `tabItem Tax Template` t
		left join `tabItem Tax Template Detail` d
			on d.parent = t.name and d.parenttype = 'Item Tax Template'
		where t.company = %s
		""",
		company,
		as_dict=True,
	)
	titles = {(row.title or "").strip().lower() for row in existing}
	keys = {
		_template_key(row.tax_code, row.tax_exemption_code, row.tax_rate)
		for row in existing
		if row.tax_type == "IVA" and not row.disabled
	}

	created = []
	for template in _read_item_tax_templates():
		key = _template_key(template["tax_code"], template["tax_exemption_code"], template["tax_rate"])
		if template["title"].lower() in titles or key in keys:
			continue

		doc = frappe.get_doc(
			{
				"doctype": "Item Tax Template",
				"title": template["title"],
				"company": company,
				"tax_code": template["tax_code"],
				"tax_type": template["tax_type"],
				"description": template["description"],
				"tax_exemption_code": template["tax_exemption_code"],
				"taxes": [{"tax_type": vat_account, "tax_rate": template["tax_rate"]}],
			}
		)
		doc.insert(ignore_permissions=True)
		created.append(doc.name)

	return created


# -------------------------------------------------------- withholding categories


def ensure_withholding_categories(company):
	"""Create the three categories, or give this company its row on the existing ones.

	A company that already has an account on a category -- set by hand or by the
	legacy migration -- is left exactly as it is.
	"""
	if not frappe.get_meta("Tax Withholding Category").has_field("atc_withholding_type"):
		return []

	vat_accounts, source = detect_vat_accounts(company)
	if source != "account_number":
		# Guessed from the tax templates: fine for a human to review, not to seed.
		vat_accounts = []

	changed = []
	for name, spec in WITHHOLDING_CATEGORIES.items():
		exists = frappe.db.exists("Tax Withholding Category", name)
		if exists and _get_company_account(name, company):
			continue

		account = get_or_create_withholding_account(company, spec["account"])
		if not account:
			continue

		base_accounts = vat_accounts if spec["type"] == "IVA" else None
		if exists:
			_ensure_company_row(name, company, account, base_accounts)
		else:
			_create_withholding_category(name, spec, company, account, base_accounts)
		changed.append(name)

	return changed


def _create_withholding_category(name, spec, company, account, base_accounts):
	doc = frappe.get_doc(
		{
			"doctype": "Tax Withholding Category",
			"__newname": name,
			"category_name": name,
			"atc_withholding_type": spec["type"],
			"atc_base_type": spec["base"],
			"atc_applies_to": spec["scope"],
			"atc_customer_type": spec["customer_type"],
			"rates": [
				{
					"tax_withholding_rate": spec["rate"],
					"from_date": FROM_DATE,
					"to_date": TO_DATE,
					"single_threshold": spec["single_threshold"],
					"cumulative_threshold": 0,
				}
			],
			"accounts": [{"company": company, "account": account}],
			"atc_base_tax_accounts": [
				{"company": company, "account": vat_account} for vat_account in base_accounts or []
			],
		}
	)
	doc.insert(ignore_permissions=True)
	return doc.name


def get_or_create_withholding_account(company, account):
	"""The company's account with this PGC number, created if it has none."""
	number = account["account_number"]
	existing = frappe.db.get_value(
		"Account", {"company": company, "account_number": number}, ["name", "is_group"], as_dict=True
	)
	if existing:
		if existing.is_group:
			print(f"  Angola tax defaults: {existing.name} is a group account; cannot book withholding to it.")
			return None
		return existing.name

	parent = _nearest_group(company, number)
	if not parent:
		print(f"  Angola tax defaults: {company} has no group account to hold {number}; skipped.")
		return None

	doc = frappe.get_doc(
		{
			"doctype": "Account",
			"company": company,
			"account_name": account["account_name"],
			"account_number": number,
			"parent_account": parent,
			"account_type": "Tax",
			"is_group": 0,
		}
	)
	doc.insert(ignore_permissions=True)
	return doc.name


def _nearest_group(company, number):
	"""The group account whose number is the longest prefix of `number`.

	In the PGC the number is the hierarchy, so 34543 belongs under 3454, failing
	that 345, failing that 34. A company on another chart falls back to ERPNext's
	own tax group.
	"""
	for length in range(len(number) - 1, 0, -1):
		parent = frappe.db.get_value(
			"Account", {"company": company, "account_number": number[:length], "is_group": 1}
		)
		if parent:
			return parent

	from erpnext.setup.setup_wizard.operations.taxes_setup import get_or_create_tax_group

	try:
		return get_or_create_tax_group(company, "Liability")
	except IndexError:
		# No root account at all: the company has no chart yet.
		return None
