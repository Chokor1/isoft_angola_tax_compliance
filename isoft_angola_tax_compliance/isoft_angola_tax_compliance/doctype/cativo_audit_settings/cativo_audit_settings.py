# Copyright (c) 2026, ISOFT and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

PURPOSES = (("cativo", "Cativo"), ("iva", "IVA charged"), ("closing", "VAT closing"))


class CativoAuditSettings(Document):
	def validate(self):
		seen = set()
		for row in self.get("accounts") or []:
			account_company = frappe.db.get_value("Account", row.account, "company")
			if account_company != row.company:
				frappe.throw(
					_("Accounts, row #{0}: {1} belongs to {2}, not {3}.").format(row.idx, row.account, account_company, row.company)
				)
			key = (row.company, row.account, row.purpose)
			if key in seen:
				frappe.throw(_("Accounts, row #{0}: {1} is listed twice as {2} for {3}.").format(row.idx, row.account, row.purpose, row.company))
			seen.add(key)


@frappe.whitelist()
def fill_from_company_setup(company=None):
	"""Rows the company setup implies. Proposals only: the form adds them and the user saves."""
	frappe.only_for(("System Manager", "Accounts Manager"))
	from isoft_angola_tax_compliance.cativo_audit.data import derive_accounts

	companies = [company] if company else frappe.get_all("Company", filters={"country": "Angola"}, pluck="name")
	rows = []
	for name in companies:
		derived = derive_accounts(name)
		for key, purpose in PURPOSES:
			rows += [{"company": name, "account": account, "purpose": purpose} for account in derived[key]]
	return rows
