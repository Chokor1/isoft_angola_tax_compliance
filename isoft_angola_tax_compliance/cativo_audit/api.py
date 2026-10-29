# Copyright (c) 2026, ISOFT and contributors
# For license information, please see license.txt
"""Server side of the Cativo Auditing page. Read-only."""

import frappe
from frappe import _

from isoft_angola_tax_compliance.cativo_audit import data, resolve

ROLES = ("System Manager", "Accounts Manager")


@frappe.whitelist()
def get_cativo_audit(company, from_date=None, to_date=None):
	"""Invoices with cativo in the period, and the cativo no invoice could be found for."""
	frappe.only_for(ROLES)
	if not company:
		frappe.throw(_("Choose a company."))

	accounts = data.get_accounts(company)
	warnings = []
	if accounts["derived"]:
		warnings.append(_("No saved accounts for {0}; using the company's setup.").format(company))
	if not accounts["cativo"]:
		warnings.append(_("No cativo account found for {0}. Add one in Cativo Audit Settings.").format(company))

	moves = data.load_movements(company, accounts)
	names = sorted({m["invoice"] for m in moves if m["invoice"]})
	invoices = data.load_invoices(names, accounts)
	return {
		"rows": resolve.build_rows(moves, invoices, from_date, to_date),
		"unassigned": resolve.unassigned(moves, from_date, to_date),
		"accounts": accounts,
		"warnings": warnings,
	}
