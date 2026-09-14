# Copyright (c) 2026, ISOFT and contributors
# For license information, please see license.txt
"""Keep a party's debit and its credit on separate lines of the General Ledger.

`withholding/gl.py` books the customer account as the invoice at its fiscal
total, plus one credit per amount retained -- retencao na fonte and IVA cativo
each against its own account. That is what the ledger stores.

The General Ledger report's default view, **Group by Voucher (Consolidated)**,
then folds them back together: its key is
`(voucher_type, voucher_no, account, party_type, party)`, so all of them land on
one line carrying a debit of 11,019,240 *and* a credit of 1,352,168.57 --
precisely the presentation the separate rows exist to avoid.

This wrapper runs the upstream function untouched and re-expands any
consolidated **party** line that ended up with both sides into one line per side
and counter-account (`against`), with amounts taken from a snapshot of the
entries made before upstream consolidates them. Totals are untouched: the lines
add up to the one they replace, or the original line is kept. Non-party lines
(income, VAT, stock) keep consolidating exactly as before -- one line per
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


AMOUNT_FIELDS = ("debit", "credit", "debit_in_account_currency", "credit_in_account_currency")


def get_accountwise_gle(filters, accounting_dimensions, gl_entries, gle_map):
	sources = None
	if filters.get("group_by") == CONSOLIDATED and not filters.get("show_net_values_in_party_account"):
		try:
			# Taken before upstream runs: its consolidation adds every row's amounts
			# into the first row object of the group, so afterwards the entries
			# themselves no longer hold their own amounts.
			sources = _snapshot_party_rows(gl_entries)
		except Exception:
			frappe.log_error(frappe.get_traceback(), "atc: general ledger party lines")

	totals, entries = _upstream(filters, accounting_dimensions, gl_entries, gle_map)
	if not sources:
		return totals, entries

	try:
		return totals, _split_party_lines(entries, sources)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "atc: general ledger party lines")
		return totals, entries


def _key(gle):
	return (gle.get("voucher_type"), gle.get("voucher_no"), gle.get("account"), gle.get("party"))


def _snapshot_party_rows(gl_entries):
	sources = {}
	for gle in gl_entries:
		if not gle.get("party"):
			continue
		snap = frappe._dict({f: flt(gle.get(f)) for f in AMOUNT_FIELDS})
		snap.against = gle.get("against") or ""
		snap.remarks = gle.get("remarks")
		sources.setdefault(_key(gle), []).append(snap)
	return sources


def _split_party_lines(entries, sources):
	out = []
	for entry in entries:
		if not (entry.get("party") and flt(entry.get("debit")) and flt(entry.get("credit"))):
			out.append(entry)
			continue
		out.extend(_lines(entry, sources.get(_key(entry)) or []) or [entry])
	return out


def _lines(entry, rows):
	"""A consolidated party line re-expanded to one line per side and counter-account.

	Rows on the same side against the same accounts still add up into one line;
	only another side or another counter-account opens a new one -- so the invoice
	debit, the retencao credit and the IVA cativo credit each get a line. Returns
	nothing (the entry stays as upstream built it) unless the lines add up to it.
	"""
	groups = {}
	for r in rows:
		if r.debit and r.credit:
			return []
		if not (r.debit or r.credit):
			continue
		side = "debit" if r.debit else "credit"
		groups.setdefault((side, r.against), []).append(r)

	lines = []
	for (side, against), members in groups.items():
		other = "credit" if side == "debit" else "debit"
		line = entry.copy()
		line[side] = sum(r[side] for r in members)
		line[side + "_in_account_currency"] = sum(r[side + "_in_account_currency"] for r in members)
		line[other] = 0.0
		line[other + "_in_account_currency"] = 0.0
		if against:
			line["against"] = against
		remarks = ", ".join(
			dict.fromkeys(
				r.remarks for r in members if r.remarks and r.remarks != "No Remarks"
			)
		)
		if remarks:
			line["remarks"] = remarks
		lines.append(line)

	for side in ("debit", "credit"):
		if abs(sum(flt(l[side]) for l in lines) - flt(entry.get(side))) > 0.01:
			return []
	return lines


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
