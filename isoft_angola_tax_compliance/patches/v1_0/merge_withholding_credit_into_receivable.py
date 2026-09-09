# Copyright (c) 2026, ISOFT and contributors
# For license information, please see license.txt
"""Reshape the customer ledger of invoices posted before 2026-09-09.

Until then the engine booked the withholding as a *separate credit* on the
receivable:

    Dr Clientes            114,000     (ERPNext's own row)
    Cr Clientes             13,500     (engine row, against = withholding accounts)

which the General Ledger report consolidates into one customer line carrying
both a debit and a credit. The engine now nets the withholding into the
receivable debit (see withholding/gl.py). This patch brings the already-posted
invoices to the same shape:

    Dr Clientes            100,500

by subtracting the engine's credit from ERPNext's debit row and deleting the
credit row. Net movement per account, party balances, outstanding amounts and
every trial-balance figure are unchanged; only the presentation of the customer
line changes. Credit notes (sides flipped) and cancelled invoices (original +
reversal pairs, both is_cancelled = 1) are handled by the same rule.

Every row it modifies or deletes is written first to
private/files/atc_gl_merge_backup_<timestamp>.json on the site, so the change
can be reconstructed. `find_affected()` is a dry run that changes nothing.
"""

import json
import os

import frappe
from frappe.utils import flt, now_datetime

TOLERANCE = 0.011


def execute():
	plan = find_affected()
	if not plan["pairs"]:
		print("atc: no two-row withholding ledgers to merge")
		return

	_write_backup(plan)

	for pair in plan["pairs"]:
		_merge_pair(pair)

	print(
		f"atc: merged withholding credit into the receivable on "
		f"{len(plan['pairs'])} GL rows ({len(plan['invoices'])} invoices); "
		f"skipped {len(plan['skipped'])}"
	)
	for s in plan["skipped"]:
		print("   skipped:", s)


def find_affected():
	"""Dry run. Returns {'pairs': [...], 'skipped': [...], 'invoices': set}."""
	invoices = frappe.db.sql(
		"""SELECT name, debit_to, customer, docstatus, is_return, return_against,
		          base_grand_total, base_rounded_total, base_rounding_adjustment
		   FROM `tabSales Invoice`
		   WHERE docstatus IN (1, 2) AND IFNULL(atc_total_withholding_amount, 0) <> 0
		   ORDER BY creation""",
		as_dict=True,
	)

	pairs, skipped, touched = [], [], set()
	for si in invoices:
		rows = frappe.get_all(
			"Sales Invoice Withholding",
			filters={"parent": si.name, "parenttype": "Sales Invoice"},
			fields=["account_head", "base_withholding_amount", "withholding_amount"],
		)
		if not rows:
			continue
		wh_base = abs(flt(sum(flt(r.base_withholding_amount) for r in rows), 2))
		wh_accounts = ", ".join(sorted({r.account_head for r in rows if r.account_head}))
		if not wh_base:
			continue

		gt = abs(
			flt(
				si.base_rounded_total
				if (si.base_rounding_adjustment and si.base_rounded_total)
				else si.base_grand_total,
				2,
			)
		)

		gles = frappe.db.sql(
			"""SELECT name, debit, credit, debit_in_account_currency,
			          credit_in_account_currency, against, against_voucher, is_cancelled
			   FROM `tabGL Entry`
			   WHERE voucher_type = 'Sales Invoice' AND voucher_no = %s
			     AND account = %s AND party_type = 'Customer' AND party = %s
			   ORDER BY creation""",
			(si.name, si.debit_to, si.customer),
			as_dict=True,
		)

		wh_rows = [
			g
			for g in gles
			if (g.against or "") == wh_accounts
			and _side_amount(g)
			and abs(_side_amount(g) - wh_base) <= TOLERANCE
			and not (flt(g.debit) and flt(g.credit))
		]
		if not wh_rows:
			continue

		used = set()
		for w in wh_rows:
			w_is_credit = flt(w.credit) > 0
			candidates = [
				g
				for g in gles
				if g.name not in used
				and g.name != w.name
				and g.is_cancelled == w.is_cancelled
				and g.against_voucher == w.against_voucher
				and (flt(g.debit) > 0) == w_is_credit
				and abs(_side_amount(g) - gt) <= TOLERANCE
			]
			if len(candidates) != 1:
				skipped.append(
					f"{si.name}: withholding row {w.name} has {len(candidates)} matching receivable rows"
				)
				continue
			r = candidates[0]
			used.add(r.name)
			pairs.append(
				{
					"invoice": si.name,
					"receivable": r,
					"withholding": w,
					"receivable_side": "debit" if w_is_credit else "credit",
					"withholding_accounts": wh_accounts,
				}
			)
			touched.add(si.name)

	return {"pairs": pairs, "skipped": skipped, "invoices": touched}


def _side_amount(g):
	return flt(g.debit) or flt(g.credit)


def _merge_pair(pair):
	r, w, side = pair["receivable"], pair["withholding"], pair["receivable_side"]
	other = "credit" if side == "debit" else "debit"

	new_base = flt(r[side]) - flt(w[other])
	new_acc = flt(r[side + "_in_account_currency"]) - flt(w[other + "_in_account_currency"])

	existing_against = [a for a in (r.against or "").split(", ") if a]
	against = ", ".join(
		existing_against
		+ [a for a in pair["withholding_accounts"].split(", ") if a not in existing_against]
	)

	frappe.db.set_value(
		"GL Entry",
		r.name,
		{side: new_base, side + "_in_account_currency": new_acc, "against": against},
		update_modified=False,
	)
	# frappe.delete_doc refuses submitted rows even with force=True on v13;
	# ERPNext's own delete_gl_entries (accounts/utils.py) deletes GL rows with
	# plain SQL, and so do we.
	frappe.db.sql("DELETE FROM `tabGL Entry` WHERE name = %s", w.name)


def _write_backup(plan):
	folder = frappe.get_site_path("private", "files")
	os.makedirs(folder, exist_ok=True)
	path = os.path.join(
		folder, "atc_gl_merge_backup_" + now_datetime().strftime("%Y%m%d_%H%M%S") + ".json"
	)
	with open(path, "w") as f:
		json.dump(
			{
				"pairs": [
					{
						"invoice": p["invoice"],
						"receivable_before": dict(p["receivable"]),
						"withholding_row_deleted": dict(p["withholding"]),
					}
					for p in plan["pairs"]
				],
				"skipped": plan["skipped"],
			},
			f,
			indent=1,
			default=str,
		)
	print("atc: backup written to", path)
