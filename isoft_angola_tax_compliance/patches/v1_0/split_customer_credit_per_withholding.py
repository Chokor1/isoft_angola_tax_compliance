# Copyright (c) 2026, ISOFT and contributors
# For license information, please see license.txt
"""One customer credit per withholding, not one for all of them.

When an invoice carries more than one withholding -- retencao na fonte II on one
account and IVA cativo on another -- the customer account must show each
retention as a movement of its own:

    Dr Clientes                               114,000
    Cr Clientes                                 6,500   against 3419  Imposto Industrial Retido
    Cr Clientes                                 7,000   against 34551 IVA Cativo Retido
    Dr 3419  Imposto Industrial Retido          6,500
    Dr 34551 IVA Cativo Retido                  7,000

Until 2026-09-14 `withholding/gl.py` posted a single credit of 13,500 against
both accounts, and `split_withholding_credit_from_receivable` re-created that
same single credit on the invoices it reshaped. This patch replaces each such
aggregate credit with one row per withholding, copied from it so posting date,
party, cost centre and dimensions stay identical. The replacement rows add up
exactly to the row they replace, so balances, outstanding amounts and the trial
balance are unchanged. Cancelled invoices (original + reversal) and credit notes
follow the same rule: the replacement rows take the side of the row they replace.

Invoices with a single withholding already have this shape and are left alone.
Listed after `split_withholding_credit_from_receivable`, so an invoice that was
still netted is first given its aggregate credit there and split here.

Every aggregate row it deletes is written first, in full, to
private/files/atc_gl_per_withholding_backup_<timestamp>.json on the site together
with the withholding rows it was split by. `find_affected()` is a dry run that
changes nothing.
"""

import json
import os

import frappe
from frappe.utils import flt, now, now_datetime

TOLERANCE = 0.011


def execute():
	plan = find_affected()
	if not plan["items"]:
		print("atc: no aggregate withholding credits to split")
		return

	_write_backup(plan)

	for item in plan["items"]:
		_replace(item)

	print(
		f"atc: split {len(plan['items'])} aggregate customer rows into one per withholding "
		f"({len(plan['invoices'])} invoices); skipped {len(plan['skipped'])}"
	)
	for s in plan["skipped"]:
		print("   skipped:", s)


def find_affected():
	"""Dry run. Returns {'items': [...], 'skipped': [...], 'invoices': set}."""
	invoices = frappe.db.sql(
		"""SELECT name, company, debit_to, customer
		   FROM `tabSales Invoice`
		   WHERE docstatus IN (1, 2) AND IFNULL(atc_total_withholding_amount, 0) <> 0
		   ORDER BY creation""",
		as_dict=True,
	)

	items, skipped, touched = [], [], set()
	for si in invoices:
		wh = [
			r
			for r in frappe.get_all(
				"Sales Invoice Withholding",
				filters={"parent": si.name, "parenttype": "Sales Invoice"},
				fields=["account_head", "base_withholding_amount", "withholding_amount", "description"],
				order_by="idx",
			)
			if flt(r.base_withholding_amount)
		]
		if len(wh) < 2:
			continue

		if len({flt(r.base_withholding_amount) > 0 for r in wh}) > 1:
			skipped.append(f"{si.name}: withholding rows have mixed signs")
			continue

		wh_base = abs(flt(sum(flt(r.base_withholding_amount) for r in wh), 2))
		wh_accounts = ", ".join(sorted({r.account_head for r in wh if r.account_head}))

		gles = frappe.db.sql(
			"""SELECT * FROM `tabGL Entry`
			   WHERE voucher_type = 'Sales Invoice' AND voucher_no = %s
			     AND account = %s AND party_type = 'Customer' AND party = %s
			   ORDER BY creation""",
			(si.name, si.debit_to, si.customer),
			as_dict=True,
		)
		for g in gles:
			if flt(g.debit) and flt(g.credit):
				continue
			# The aggregate row is the one against every withholding account at
			# once, for the whole amount withheld. ERPNext's own receivable row is
			# against the income accounts too, and rows already split are against
			# a single account for part of the amount, so neither matches.
			if (g.against or "") != wh_accounts:
				continue
			if abs((flt(g.debit) or flt(g.credit)) - wh_base) > TOLERANCE:
				continue

			items.append(
				{"invoice": si.name, "company": si.company, "aggregate": g, "withholdings": wh}
			)
			touched.add(si.name)

	return {"items": items, "skipped": skipped, "invoices": touched}


def _parts(withholdings, total, total_account_currency, in_company_currency):
	"""Split the aggregate amounts by withholding; the last part absorbs rounding
	so the parts always add up to the row being replaced, to the cent."""
	parts = []
	used, used_acc = 0.0, 0.0
	for i, wh in enumerate(withholdings):
		if i == len(withholdings) - 1:
			amount = flt(total - used, 2)
			amount_acc = flt(total_account_currency - used_acc, 2)
		else:
			amount = abs(flt(wh.base_withholding_amount, 2))
			amount_acc = amount if in_company_currency else abs(flt(wh.withholding_amount, 2))
			used += amount
			used_acc += amount_acc
		parts.append((wh, amount, amount_acc))
	return parts


def _replace(item):
	source = item["aggregate"]
	side = "credit" if flt(source.credit) > 0 else "debit"
	other = "debit" if side == "credit" else "credit"

	company_currency = frappe.get_cached_value("Company", item["company"], "default_currency")
	parts = _parts(
		item["withholdings"],
		flt(source[side]),
		flt(source[side + "_in_account_currency"]),
		source.account_currency == company_currency,
	)

	for wh, amount, amount_acc in parts:
		row = dict(source)
		row.update(
			{
				"doctype": "GL Entry",
				# ERPNext names GL rows with a throwaway hash and lets the daily
				# `rename_gle_sle_docs` job give them their ACC-GLE series name --
				# `to_rename` is what puts this row in that job's queue.
				"name": frappe.generate_hash(txt="", length=10),
				"to_rename": 1,
				side: amount,
				side + "_in_account_currency": amount_acc,
				other: 0,
				other + "_in_account_currency": 0,
				"against": wh.account_head,
				"remarks": wh.description or source.get("remarks"),
				"creation": now(),
				"modified": now(),
			}
		)
		for key in ("_user_tags", "_comments", "_assign", "_liked_by"):
			row.pop(key, None)
		frappe.get_doc(row).db_insert()

	# frappe.delete_doc refuses submitted GL rows on v13 even with force=True;
	# ERPNext's own delete_gl_entries uses plain SQL, and so do we.
	frappe.db.sql("DELETE FROM `tabGL Entry` WHERE name = %s", source.name)


def _write_backup(plan):
	folder = frappe.get_site_path("private", "files")
	os.makedirs(folder, exist_ok=True)
	path = os.path.join(
		folder, "atc_gl_per_withholding_backup_" + now_datetime().strftime("%Y%m%d_%H%M%S") + ".json"
	)
	with open(path, "w") as f:
		json.dump(
			{
				"items": [
					{
						"invoice": i["invoice"],
						"aggregate_row_deleted": dict(i["aggregate"]),
						"split_by": [dict(w) for w in i["withholdings"]],
					}
					for i in plan["items"]
				],
				"skipped": plan["skipped"],
			},
			f,
			indent=1,
			default=str,
		)
	print("atc: backup written to", path)
