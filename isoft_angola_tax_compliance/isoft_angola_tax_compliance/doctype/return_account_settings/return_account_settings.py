# Copyright (c) 2026, ISOFT and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

TABLES = [("sales_mappings", "Income Account Mapping"), ("purchase_mappings", "Expense Account Mapping")]


class ReturnAccountSettings(Document):
	def validate(self):
		for fieldname, label in TABLES:
			self._validate_table(self.get(fieldname) or [], _(label))

	def _validate_table(self, rows, label):
		seen = set()
		for row in rows:
			if row.invoice_account == row.return_account:
				frappe.throw(
					_("{0}, row #{1}: the invoice account and the note account are the same.").format(
						label, row.idx
					)
				)
			key = (row.company, row.invoice_account)
			if key in seen:
				frappe.throw(
					_("{0}, row #{1}: {2} is mapped more than once for {3}.").format(
						label, row.idx, row.invoice_account, row.company
					)
				)
			seen.add(key)

			for account in (row.invoice_account, row.return_account):
				company, is_group = frappe.db.get_value("Account", account, ["company", "is_group"]) or (None, None)
				if company != row.company:
					frappe.throw(
						_("{0}, row #{1}: account {2} does not belong to {3}.").format(
							label, row.idx, account, row.company
						)
					)
				if is_group and account == row.return_account:
					frappe.throw(
						_("{0}, row #{1}: {2} is a group account and cannot be posted to.").format(
							label, row.idx, account
						)
					)
