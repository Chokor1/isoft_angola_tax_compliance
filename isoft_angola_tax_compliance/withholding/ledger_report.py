# Copyright (c) 2026, ISOFT and contributors
# For license information, please see license.txt
"""Keep a party's debit and its credit on separate lines of the General Ledger.

`withholding/gl.py` books two rows on the customer account: the invoice at its
fiscal total, and the amount retained (IVA cativo / retencao) as a movement of
its own. That is what the ledger stores.

The General Ledger report's default view, **Group by Voucher (Consolidated)**,
then folds them back together: its key is
`(voucher_type, voucher_no, account, party_type, party)`, so both rows land on
one line carrying a debit of 11,019,240 *and* a credit of 1,352,168.57 —
precisely the presentation the two rows exist to avoid.

This wrapper runs the upstream function untouched and splits any consolidated
**party** line that ended up with both sides back into two lines, taking the
`against` and `remarks` of each side from the underlying entries. Totals are
untouched: the two halves add up to the line they replace. Non-party lines
(income, VAT, stock) keep consolidating exactly as before — one line per
account per voucher is the point of that view.

`Show Net Values in Party Account` is left alone: ticking it is an explicit
request for one netted party line.

Installed from the package `__init__`, the same runtime-wrapping pattern as
`withholding/outstanding.py`.
"""

import importlib

import frappe
from frappe.utils import flt

_MODULE = "erpnext.accounts.report.general_ledger.general_ledger"
CONSOLIDATED = "Group by Voucher (Consolidated)"

_upstream = None
_installed = False


def get_accountwise_gle(filters, accounting_dimensions, gl_entries, gle_map):
	totals, entries = _upstream(filters, accounting_dimensions, gl_entries, gle_map)

	try:
		if filters.get("group_by") != CONSOLIDATED or filters.get("show_net_values_in_party_account"):
			return totals, entries
		return totals, _split_party_lines(entries, gl_entries)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "atc: general ledger party lines")
		return totals, entries


def _split_party_lines(entries, gl_entries):
	sources = {}
	for gle in gl_entries:
		if not gle.get("party"):
			continue
		key = (gle.get("voucher_type"), gle.get("voucher_no"), gle.get("account"), gle.get("party"))
		sources.setdefault(key, []).append(gle)

	out = []
	for entry in entries:
		if not (entry.get("party") and flt(entry.get("debit")) and flt(entry.get("credit"))):
			out.append(entry)
			continue

		key = (
			entry.get("voucher_type"),
			entry.get("voucher_no"),
			entry.get("account"),
			entry.get("party"),
		)
		rows = sources.get(key) or []
		out.append(_side(entry, rows, "debit"))
		out.append(_side(entry, rows, "credit"))

	return out


def _side(entry, rows, side):
	"""One half of a consolidated party line, described by its own entries."""
	other = "credit" if side == "debit" else "debit"

	line = entry.copy()
	line[other] = 0.0
	line[other + "_in_account_currency"] = 0.0

	own = [r for r in rows if flt(r.get(side)) and not flt(r.get(other))]
	if own:
		against = ", ".join(
			dict.fromkeys(a for r in own for a in (r.get("against") or "").split(", ") if a)
		)
		if against:
			line["against"] = against

		remarks = ", ".join(
			dict.fromkeys(r.get("remarks") for r in own if r.get("remarks") and r.get("remarks") != "No Remarks")
		)
		if remarks:
			line["remarks"] = remarks

	return line


def install():
	"""Wrap the upstream function. Idempotent; no-op if the report is not importable."""
	global _upstream, _installed
	if _installed:
		return True

	try:
		module = importlib.import_module(_MODULE)
	except Exception:
		return False

	if module.get_accountwise_gle is get_accountwise_gle:
		_installed = True
		return True

	_upstream = module.get_accountwise_gle
	module.get_accountwise_gle = get_accountwise_gle
	_installed = True
	return True
