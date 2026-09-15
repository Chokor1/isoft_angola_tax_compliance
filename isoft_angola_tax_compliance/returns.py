# Copyright (c) 2026, ISOFT and contributors
# For license information, please see license.txt
"""Different accounts on credit notes and debit notes.

ERPNext copies the line accounts of the original invoice onto its return, so
a credit note against a sale booked on 61 Vendas is itself booked on 61.
Angolan bookkeeping often wants returns on their own account (e.g. 62), so
`Return Account Settings` holds, per company, "invoice account -> note
account" pairs: one table for Sales Invoice returns (income_account), one
for Purchase Invoice returns (expense_account).

Applied twice, for the same result:

  * on the server, from the `validate` doc_event of both invoices -- this is
    what the GL is posted from, so it is the authoritative one;
  * in the browser, when a new return opens (public/js/sales_invoice.js and
    purchase_invoice.js), so the user sees the note account before saving.

Lines whose account is not mapped are left untouched. Nothing happens on
ordinary invoices, or while the setting is switched off.
"""

import frappe
from frappe.utils import cint

SETTINGS = "Return Account Settings"

# doctype -> (settings table, item account field, controller recompute of "against")
TARGETS = {
	"Sales Invoice": ("sales_mappings", "income_account", "set_against_income_account"),
	"Purchase Invoice": ("purchase_mappings", "expense_account", "set_against_expense_account"),
}


@frappe.whitelist()
def get_return_account_map(doctype, company):
	"""{invoice account: note account} for one company, empty when off or unconfigured."""
	if doctype not in TARGETS or not company:
		return {}

	settings = frappe.get_single(SETTINGS)
	if not cint(settings.get("enabled")):
		return {}

	table = TARGETS[doctype][0]
	return {
		row.invoice_account: row.return_account
		for row in settings.get(table) or []
		if row.company == company and row.invoice_account and row.return_account
	}


def apply_to_sales_invoice(doc, method=None):
	_apply(doc)


def apply_to_purchase_invoice(doc, method=None):
	_apply(doc)


def _apply(doc):
	if doc.doctype not in TARGETS or not cint(doc.get("is_return")):
		return

	mapping = get_return_account_map(doc.doctype, doc.company)
	if not mapping:
		return

	_, field, recompute = TARGETS[doc.doctype]
	changed = False
	for item in doc.get("items") or []:
		target = mapping.get(item.get(field))
		if target and item.get(field) != target:
			item.set(field, target)
			changed = True

	# The invoice-level "against" summary was built from the old accounts.
	if changed and hasattr(doc, recompute):
		getattr(doc, recompute)()
