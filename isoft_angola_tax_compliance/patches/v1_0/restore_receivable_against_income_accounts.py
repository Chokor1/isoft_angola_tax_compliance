# Copyright (c) 2026, ISOFT and contributors
# For license information, please see license.txt
"""Give the invoice's own customer row back its native `against`: the income accounts.

ERPNext books the customer row of a Sales Invoice against
`Sales Invoice.against_income_account`, the income accounts of its items:

    Dr Clientes     4,145,382   against 6131 Vendas de Mercadorias
    Cr Clientes       236,360   against 3412 Imposto Industrial - Retencao
    Cr Clientes       509,082   against 34543 IVA Regularizacoes - Cativo

Until 2026-09-14 `withholding/gl.py` also appended every withholding account to
that first row, so it read "6131 Vendas, 3412 Retencao, 34543 IVA Cativo". With
each withholding on a customer row of its own against its own account, naming
them on the invoice row as well is wrong: the invoice row faces the sale, the
withholding rows face the retentions. The engine no longer appends them; this
patch removes them from the rows already posted (the 2026-09-09 merge patch
appended them the same way).

Only `against` changes -- a descriptive column. No amount, balance, outstanding
or report total moves. The rows touched are ERPNext's own customer rows (the
debit of an invoice, the credit of a credit note, and their cancellation
reversals). A row against withholding accounts only is an engine row and is
left alone, and so is anything that names no withholding account (POS payment
rows, write-offs).

Old values are written first to private/files/atc_gl_against_backup_<timestamp>.json
on the site. `find_affected()` is a dry run that changes nothing.
"""

import json
import os

import frappe
from frappe.utils import now_datetime


def execute():
	plan = find_affected()
	if not plan["rows"]:
		print("atc: no customer rows naming withholding accounts in against")
		return

	_write_backup(plan)

	for row in plan["rows"]:
		frappe.db.set_value("GL Entry", row["name"], "against", row["new"], update_modified=False)

	print(
		f"atc: restored the income-account against on {len(plan['rows'])} customer rows "
		f"({len(plan['invoices'])} invoices)"
	)


def _accounts(value):
	return [a.strip() for a in (value or "").split(",") if a.strip()]


def find_affected():
	"""Dry run. Returns {'rows': [...], 'invoices': set}."""
	invoices = frappe.db.sql(
		"""SELECT name, debit_to, customer, against_income_account
		   FROM `tabSales Invoice`
		   WHERE docstatus IN (1, 2) AND IFNULL(atc_total_withholding_amount, 0) <> 0
		   ORDER BY creation""",
		as_dict=True,
	)

	rows, touched = [], set()
	for si in invoices:
		withholding_accounts = {
			a
			for a in frappe.get_all(
				"Sales Invoice Withholding",
				filters={"parent": si.name, "parenttype": "Sales Invoice"},
				pluck="account_head",
			)
			if a
		}
		if not withholding_accounts:
			continue

		native = _accounts(si.against_income_account)
		gles = frappe.db.sql(
			"""SELECT name, against FROM `tabGL Entry`
			   WHERE voucher_type = 'Sales Invoice' AND voucher_no = %s
			     AND account = %s AND party_type = 'Customer' AND party = %s""",
			(si.name, si.debit_to, si.customer),
			as_dict=True,
		)
		for g in gles:
			parts = _accounts(g.against)
			kept = [a for a in parts if a not in withholding_accounts]
			if len(kept) == len(parts) or not kept:
				# names no withholding account, or is one of the engine's own rows
				continue

			# Put back exactly what ERPNext wrote when the remainder is its
			# income-account list; otherwise just drop the withholding accounts.
			new = si.against_income_account if native and sorted(native) == sorted(kept) else ", ".join(kept)
			rows.append({"invoice": si.name, "name": g.name, "old": g.against, "new": new})
			touched.add(si.name)

	return {"rows": rows, "invoices": touched}


def _write_backup(plan):
	folder = frappe.get_site_path("private", "files")
	os.makedirs(folder, exist_ok=True)
	path = os.path.join(
		folder, "atc_gl_against_backup_" + now_datetime().strftime("%Y%m%d_%H%M%S") + ".json"
	)
	with open(path, "w") as f:
		json.dump({"rows": plan["rows"]}, f, indent=1, default=str)
	print("atc: backup written to", path)
