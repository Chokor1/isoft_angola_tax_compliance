# Copyright (c) 2026, ISOFT and contributors
# For license information, please see license.txt
"""Show the fiscal total, not the net receivable, as the invoice amount.

ERPNext's `get_outstanding_invoices` (accounts/utils.py) does not read the
grand total. It takes the debit booked on the receivable account as the
invoice amount, and any credit against that voucher as the amount already
paid. Payment Entry ("Get Outstanding Invoices") and Payment Reconciliation
both build their rows from it.

Since 2026-09-09 the engine nets the withholding into the receivable debit
(see gl.py), so for a 114,000 invoice with 13,500 withheld ERPNext would show
an invoice of 100,500. The customer, the accountant and the AGT all know the
invoice as 114,000, so this wrapper restores that presentation:

    invoice_amount   100,500 -> 114,000   (grand total)
    payment_amount         0 ->  13,500   (withholding, already settled)
    outstanding      100,500    unchanged

Nothing is written; the GL and `outstanding_amount` are untouched. Invoices
without engine withholding rows (pre-cutover ones, whose withholding sits in
a Journal Entry that ERPNext already counts as a payment) are left alone, and
so is any row whose amounts do not add up to the invoice total exactly --
that guards against double counting an invoice that still has the old
two-row ledger.

Installed by `install()` from the package __init__, following the same
runtime-wrapping pattern as angola_setup: patch the defining module and every
module that imported the name directly (payment_entry.py does).
"""

import importlib
import sys

import frappe
from frappe.utils import flt

_upstream = None
_installed = False

_TARGET_MODULES = (
	"erpnext.accounts.utils",
	"erpnext.accounts.doctype.payment_entry.payment_entry",
)

TOLERANCE = 0.011


def get_outstanding_invoices(party_type, party, account, company, condition=None, filters=None):
	invoices = _upstream(party_type, party, account, company, condition=condition, filters=filters)
	try:
		_present_fiscal_totals(invoices, party_type, company)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "atc: outstanding invoice presentation")
	return invoices


def _present_fiscal_totals(invoices, party_type, company):
	if party_type != "Customer" or not invoices:
		return

	names = [d.voucher_no for d in invoices if d.voucher_type == "Sales Invoice"]
	if not names:
		return

	rows = frappe.db.sql(
		"""SELECT si.name,
		          si.grand_total, si.base_grand_total,
		          si.rounded_total, si.base_rounded_total,
		          si.rounding_adjustment, si.base_rounding_adjustment,
		          SUM(w.withholding_amount) AS wh, SUM(w.base_withholding_amount) AS base_wh
		   FROM `tabSales Invoice` si
		   INNER JOIN `tabSales Invoice Withholding` w
		           ON w.parent = si.name AND w.parenttype = 'Sales Invoice'
		   WHERE si.name IN %(names)s
		   GROUP BY si.name""",
		{"names": names},
		as_dict=True,
	)
	if not rows:
		return

	company_currency = frappe.get_cached_value("Company", company, "default_currency")
	by_name = {r.name: r for r in rows}

	for d in invoices:
		r = by_name.get(d.voucher_no)
		if not r:
			continue

		in_company_currency = d.get("currency") == company_currency
		withheld = flt(r.base_wh if in_company_currency else r.wh)
		if not withheld:
			continue

		if in_company_currency:
			total = r.base_rounded_total if (r.base_rounding_adjustment and r.base_rounded_total) else r.base_grand_total
		else:
			total = r.rounded_total if (r.rounding_adjustment and r.rounded_total) else r.grand_total
		total = flt(total)

		# Only when net + withholding is exactly the fiscal total. Anything else
		# (old two-row ledger, partial repost) is already presented correctly.
		if abs(flt(d.invoice_amount) + withheld - total) > TOLERANCE:
			continue

		d.invoice_amount = total
		d.payment_amount = flt(d.get("payment_amount")) + withheld


def install():
	"""Wrap the upstream function. Idempotent; no-op if ERPNext is not importable."""
	global _upstream, _installed
	if _installed:
		return True

	try:
		utils = importlib.import_module("erpnext.accounts.utils")
	except Exception:
		return False

	if utils.get_outstanding_invoices is get_outstanding_invoices:
		_installed = True
		return True

	_upstream = utils.get_outstanding_invoices
	utils.get_outstanding_invoices = get_outstanding_invoices

	# Modules that did `from erpnext.accounts.utils import get_outstanding_invoices`
	# hold their own reference. Patch those already imported; later imports
	# pick up the wrapped name from utils on their own.
	for module_name in _TARGET_MODULES[1:]:
		mod = sys.modules.get(module_name)
		if mod is not None and getattr(mod, "get_outstanding_invoices", None) is _upstream:
			mod.get_outstanding_invoices = get_outstanding_invoices

	_installed = True
	return True
