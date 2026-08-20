# Copyright (c) 2024, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class ClosingConfigurationProfile(Document):
	def validate(self):
		self.validate_accounts_company()
		self.validate_destination_not_in_sources()

	def validate_accounts_company(self):
		"""Every account on the profile must belong to the profile's company.

		The profile drives real GL postings, so mixing companies here would post
		one company's balances onto another company's account.
		"""
		self._check_account("destination_account", self.destination_account, _("Destination Account"))

		for row in self.source_accounts or []:
			self._check_account("account", row.account, _("Row #{0}: Source Account").format(row.idx))

	def _check_account(self, fieldname, account, label):
		if not account:
			return

		account_company = frappe.db.get_value("Account", account, "company")
		if account_company and account_company != self.company:
			frappe.throw(
				_("{0} {1} belongs to company {2}, not {3}.").format(
					label, frappe.bold(account), frappe.bold(account_company), frappe.bold(self.company)
				),
				title=_("Invalid Account"),
			)

	def validate_destination_not_in_sources(self):
		"""Closing an account into itself would post both legs to the same account."""
		sources = [row.account for row in self.source_accounts or [] if row.account]
		if self.destination_account and self.destination_account in sources:
			frappe.throw(
				_("Destination Account {0} cannot also be a Source Account.").format(
					frappe.bold(self.destination_account)
				)
			)
