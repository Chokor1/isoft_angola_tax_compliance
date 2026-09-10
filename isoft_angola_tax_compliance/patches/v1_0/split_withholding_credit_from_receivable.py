# Copyright (c) 2026, ISOFT and contributors
# For license information, please see license.txt
"""Give back the two-row customer ledger to invoices that were netted.

Between 2026-09-09 and 2026-09-10 the engine subtracted the withholding from
the receivable debit, and `merge_withholding_credit_into_receivable` reshaped
the invoices posted before that to match:

    Dr Clientes            100,500     (fiscal total 114,000 less 13,500 retained)

That is not how the ledger should read. The customer's debit must carry the
invoice as it was issued, and the retention must be a movement of its own:

    Dr Clientes            114,000     (ERPNext's own row, restored here)
    Cr Clientes             13,500     (engine row, re-created here)

so the customer account, the statement and the aging show both the invoice and
what was retained from it. `withholding/gl.py` posts this shape again; this
patch brings the already-posted invoices back to it. Net movement per account,
party balances, `outstanding_amount` and every trial-balance figure are
unchanged -- only the presentation of the customer line changes. Credit notes
(sides flipped) and cancelled invoices (original + reversal, both
`is_cancelled = 1`) are handled by the same rule.

Every row it modifies or creates is written first to
private/files/atc_gl_split_backup_<timestamp>.json on the site, so the change
can be reconstructed. `find_affected()` is a dry run that changes nothing.
"""

import json
import os

import frappe
from frappe.utils import flt, now, now_datetime

TOLERANCE = 0.011


def execute():
	plan = find_affected()
	if not plan["rows"]:
		print("atc: no netted withholding ledgers to split")
		return

	_write_backup(plan)

	for row in plan["rows"]:
		_split_row(row)

	print(
		f"atc: split the withholding credit out of the receivable on "
		f"{len(plan['rows'])} GL rows ({len(plan['invoices'])} invoices); "
		f"skipped {len(plan['skipped'])}"
	)
	for s in plan["skipped"]:
		print("   skipped:", s)


def find_affected():
	"""Dry run. Returns {'rows': [...], 'skipped': [...], 'invoices': set}."""
	invoices = frappe.db.sql(
		"""SELECT name, company, debit_to, customer, docstatus, is_return, return_against,
		          base_grand_total, base_rounded_total, base_rounding_adjustment
		   FROM `tabSales Invoice`
		   WHERE docstatus IN (1, 2) AND IFNULL(atc_total_withholding_amount, 0) <> 0
		   ORDER BY creation""",
		as_dict=True,
	)

	rows, skipped, touched = [], [], set()
	for si in invoices:
		wh = frappe.get_all(
			"Sales Invoice Withholding",
			filters={"parent": si.name, "parenttype": "Sales Invoice"},
			fields=["account_head", "base_withholding_amount", "withholding_amount", "description"],
		)
		if not wh:
			continue

		wh_base = abs(flt(sum(flt(r.base_withholding_amount) for r in wh), 2))
		wh_acct = abs(flt(sum(flt(r.withholding_amount) for r in wh), 2))
		if not wh_base:
			continue
		wh_accounts = ", ".join(sorted({r.account_head for r in wh if r.account_head}))
		remarks = ", ".join(dict.fromkeys(r.description for r in wh if r.description))

		gt = abs(
			flt(
				si.base_rounded_total
				if (si.base_rounding_adjustment and si.base_rounded_total)
				else si.base_grand_total,
				2,
			)
		)
		net = flt(gt - wh_base, 2)
		if net <= 0:
			skipped.append(f"{si.name}: withholding is not smaller than the invoice total")
			continue

		gles = frappe.db.sql(
			"""SELECT name, debit, credit, debit_in_account_currency,
			          credit_in_account_currency, account_currency, is_cancelled
			   FROM `tabGL Entry`
			   WHERE voucher_type = 'Sales Invoice' AND voucher_no = %s
			     AND account = %s AND party_type = 'Customer' AND party = %s
			   ORDER BY creation""",
			(si.name, si.debit_to, si.customer),
			as_dict=True,
		)

		for g in gles:
			if flt(g.debit) and flt(g.credit):
				continue
			if abs(_side_amount(g) - net) > TOLERANCE:
				continue

			side = "debit" if flt(g.debit) > 0 else "credit"
			other = "credit" if side == "debit" else "debit"

			# Already two rows? Then the credit is there and nothing is netted.
			if any(
				o.name != g.name
				and o.is_cancelled == g.is_cancelled
				and flt(o[other]) > 0
				and abs(flt(o[other]) - wh_base) <= TOLERANCE
				for o in gles
			):
				continue

			rows.append(
				{
					"invoice": si.name,
					"company": si.company,
					"receivable": g,
					"side": side,
					"other": other,
					"wh_base": wh_base,
					"wh_account_currency": wh_acct,
					"withholding_accounts": wh_accounts,
					"remarks": remarks,
					"grand_total": gt,
				}
			)
			touched.add(si.name)

	return {"rows": rows, "skipped": skipped, "invoices": touched}


def _side_amount(g):
	return flt(g.debit) or flt(g.credit)


def _split_row(item):
	g, side, other = item["receivable"], item["side"], item["other"]
	wh_base = item["wh_base"]
	company_currency = frappe.get_cached_value("Company", item["company"], "default_currency")
	wh_acct = wh_base if g.account_currency == company_currency else item["wh_account_currency"]

	# 1. the receivable row goes back to the fiscal total
	frappe.db.set_value(
		"GL Entry",
		g.name,
		{
			side: flt(g[side]) + wh_base,
			side + "_in_account_currency": flt(g[side + "_in_account_currency"]) + wh_acct,
		},
		update_modified=False,
	)

	# 2. the withholding gets its own row on the same account, copied from the
	#    receivable row so posting date, dimensions and party stay identical
	source = frappe.db.sql(
		"SELECT * FROM `tabGL Entry` WHERE name = %s", g.name, as_dict=True
	)[0]
	source.update(
		{
			"doctype": "GL Entry",
			# ERPNext names GL rows with a throwaway hash and lets the daily
			# `rename_gle_sle_docs` job give them their ACC-GLE series name --
			# `to_rename` is what puts this row in that job's queue.
			"name": frappe.generate_hash(txt="", length=10),
			"to_rename": 1,
			side: 0,
			side + "_in_account_currency": 0,
			other: wh_base,
			other + "_in_account_currency": wh_acct,
			"against": item["withholding_accounts"],
			"remarks": item["remarks"] or source.get("remarks"),
			"creation": now(),
			"modified": now(),
		}
	)
	source.pop("_user_tags", None)
	source.pop("_comments", None)
	source.pop("_assign", None)
	source.pop("_liked_by", None)
	frappe.get_doc(source).db_insert()


def _write_backup(plan):
	folder = frappe.get_site_path("private", "files")
	os.makedirs(folder, exist_ok=True)
	path = os.path.join(
		folder, "atc_gl_split_backup_" + now_datetime().strftime("%Y%m%d_%H%M%S") + ".json"
	)
	with open(path, "w") as f:
		json.dump(
			{
				"rows": [
					{
						"invoice": r["invoice"],
						"receivable_before": dict(r["receivable"]),
						"withholding_row_added": {
							"side": r["other"],
							"amount": r["wh_base"],
							"against": r["withholding_accounts"],
						},
					}
					for r in plan["rows"]
				],
				"skipped": plan["skipped"],
			},
			f,
			indent=1,
			default=str,
		)
	print("atc: backup written to", path)
