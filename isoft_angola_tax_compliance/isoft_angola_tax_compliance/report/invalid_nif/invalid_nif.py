# Copyright (c) 2026, ISOFT and contributors
# For license information, please see license.txt
"""Customers whose NIF does not match the Angolan format.

Grouped so the two very different problems do not look the same:

  Wrong Customer Type   the NIF is valid, the type is not -- fix the type
  Invalid Format        the NIF itself is wrong -- fix the number

`Invoices` counts submitted Sales Invoices already issued against that customer,
so the ones that matter most sort to the top.
"""

import frappe
from frappe import _

from isoft_angola_tax_compliance.nif import PATTERNS, check, get_settings


def execute(filters=None):
	filters = frappe._dict(filters or {})
	return get_columns(), get_data(filters)


def get_columns():
	return [
		{"label": _("Customer"), "fieldname": "customer", "fieldtype": "Link",
		 "options": "Customer", "width": 240},
		{"label": _("Customer Name"), "fieldname": "customer_name", "fieldtype": "Data", "width": 220},
		{"label": _("Type"), "fieldname": "customer_type", "fieldtype": "Data", "width": 90},
		{"label": _("NIF"), "fieldname": "tax_id", "fieldtype": "Data", "width": 150},
		{"label": _("Problem"), "fieldname": "problem", "fieldtype": "Data", "width": 160},
		{"label": _("Suggested Type"), "fieldname": "suggested_type", "fieldtype": "Data", "width": 130},
		{"label": _("Invoices"), "fieldname": "invoices", "fieldtype": "Int", "width": 90},
		{"label": _("Detail"), "fieldname": "detail", "fieldtype": "Data", "width": 420},
	]


def get_data(filters):
	settings = get_settings()

	conditions = ["1=1"]
	values = {}
	if not filters.get("include_disabled"):
		conditions.append("IFNULL(c.disabled, 0) = 0")
	if filters.get("customer_type"):
		conditions.append("c.customer_type = %(customer_type)s")
		values["customer_type"] = filters.customer_type

	customers = frappe.db.sql(
		"""
		SELECT c.name, c.customer_name, c.customer_type, IFNULL(c.tax_id, '') AS tax_id
		FROM `tabCustomer` c
		WHERE {conditions}
		ORDER BY c.name
		""".format(conditions=" AND ".join(conditions)),
		values,
		as_dict=True,
	)

	bad = []
	for row in customers:
		ok, message = check(row.tax_id, row.customer_type, settings)
		if ok:
			continue

		suggested = ""
		for other, pattern in PATTERNS.items():
			if other != row.customer_type and pattern.match((row.tax_id or "").strip()):
				suggested = other
				break

		bad.append(
			{
				"customer": row.name,
				"customer_name": row.customer_name,
				"customer_type": row.customer_type,
				"tax_id": row.tax_id,
				"problem": _("Wrong Customer Type") if suggested else _("Invalid Format"),
				"suggested_type": suggested,
				"detail": frappe.utils.strip_html(message or ""),
			}
		)

	if not bad:
		return []

	# One query for the invoice counts rather than one per customer.
	names = [r["customer"] for r in bad]
	counts = dict(
		frappe.db.sql(
			"""SELECT customer, COUNT(*) FROM `tabSales Invoice`
			   WHERE docstatus = 1 AND customer IN %(names)s GROUP BY customer""",
			{"names": names},
		)
		or []
	)
	for row in bad:
		row["invoices"] = counts.get(row["customer"], 0)

	# Most-invoiced first: those are the ones already reaching AGT.
	bad.sort(key=lambda r: (-r["invoices"], r["customer"]))
	return bad
