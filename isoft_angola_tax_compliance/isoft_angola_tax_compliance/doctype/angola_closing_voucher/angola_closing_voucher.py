# Copyright (c) 2026, ISOFT and contributors
# For license information, please see license.txt

"""Profile-driven period closing for Angola.

This used to live inside ERPNext's Period Closing Voucher as a second
"closing method". It is a different operation from the standard one: instead of
closing every Profit and Loss account into a single closing account for a whole
fiscal year, it moves the balance of a named set of source accounts into a named
destination account over an arbitrary date range, once per Closing Configuration
Profile. Keeping it in core meant patching a stock doctype; it is its own
doctype here so Period Closing Voucher stays untouched.
"""

import frappe
from frappe import _
from frappe.utils import flt, getdate

from erpnext.accounts.doctype.accounting_dimension.accounting_dimension import (
	get_accounting_dimensions,
)
from erpnext.accounts.utils import get_children
from erpnext.controllers.accounts_controller import AccountsController

VOUCHER_TYPE = "Angola Closing Voucher"

# Above this many entries the posting is handed to a background job, mirroring
# what Period Closing Voucher does.
BACKGROUND_THRESHOLD = 5000


class AngolaClosingVoucher(AccountsController):
	def validate(self):
		self.validate_dates()
		self.validate_profiles()

	def on_submit(self):
		self.db_set("gle_processing_status", "In Progress")
		self.make_gl_entries()

	def on_cancel(self):
		self.db_set("gle_processing_status", "In Progress")
		self.ignore_linked_doctypes = ("GL Entry", "Stock Ledger Entry")

		gle_count = frappe.db.count(
			"GL Entry",
			{"voucher_type": VOUCHER_TYPE, "voucher_no": self.name, "is_cancelled": 0},
		)
		if gle_count > BACKGROUND_THRESHOLD:
			frappe.enqueue(
				make_reverse_gl_entries,
				voucher_type=VOUCHER_TYPE,
				voucher_no=self.name,
				in_background=True,
				queue="long",
			)
			frappe.msgprint(
				_("The GL Entries will be cancelled in the background, it can take a few minutes."),
				alert=True,
			)
		else:
			make_reverse_gl_entries(voucher_type=VOUCHER_TYPE, voucher_no=self.name)

	# ------------------------------------------------------------------ validation

	def validate_dates(self):
		from erpnext.accounts.utils import validate_fiscal_year

		if getdate(self.from_date) > getdate(self.to_date):
			frappe.throw(_("From Date cannot be after To Date."))

		if getdate(self.posting_date) < getdate(self.to_date):
			frappe.throw(
				_("Posting Date cannot be before the To Date of the period being closed.")
			)

		validate_fiscal_year(
			self.posting_date, None, self.company, label=_("Posting Date"), doc=self
		)

	def validate_profiles(self):
		"""Each profile must belong to this company, and none may repeat.

		The company check matters because a profile carries real account numbers;
		posting one company's profile from another company's voucher would move
		balances between two sets of books.
		"""
		seen = set()

		for row in self.profiles or []:
			if row.closing_configuration_profile in seen:
				frappe.throw(
					_("Row #{0}: Closing Configuration Profile {1} is listed more than once.").format(
						row.idx, frappe.bold(row.closing_configuration_profile)
					)
				)
			seen.add(row.closing_configuration_profile)

			profile_company = frappe.db.get_value(
				"Closing Configuration Profile", row.closing_configuration_profile, "company"
			)
			if profile_company and profile_company != self.company:
				frappe.throw(
					_("Row #{0}: Closing Configuration Profile {1} belongs to company {2}, not {3}.").format(
						row.idx,
						frappe.bold(row.closing_configuration_profile),
						frappe.bold(profile_company),
						frappe.bold(self.company),
					),
					title=_("Invalid Profile"),
				)

	# ------------------------------------------------------------------ posting

	def make_gl_entries(self):
		gl_entries = self.get_gl_entries()
		if not gl_entries:
			frappe.throw(
				_("No balances were found for the selected profiles between {0} and {1}.").format(
					self.from_date, self.to_date
				)
			)

		if len(gl_entries) > BACKGROUND_THRESHOLD:
			frappe.enqueue(
				process_gl_entries, gl_entries=gl_entries, in_background=True, queue="long"
			)
			frappe.msgprint(
				_("The GL Entries will be processed in the background, it can take a few minutes."),
				alert=True,
			)
		else:
			process_gl_entries(gl_entries)

	def get_gl_entries(self):
		gl_entries = []

		for row in self.profiles:
			profile = row.closing_configuration_profile
			destination_account = self.get_destination_account_from_profile(profile)
			source_accounts = self.get_source_accounts_from_profile(profile)

			if not source_accounts:
				frappe.throw(
					_("Row #{0}: Closing Configuration Profile {1} resolves to no ledger accounts.").format(
						row.idx, frappe.bold(profile)
					)
				)

			# One leg per source account, then the balancing leg on the destination.
			for acc in self.get_balances_based_on_dimensions(
				source_accounts, group_by_account=True
			):
				if flt(acc.bal_in_company_currency):
					gl_entries.append(self.get_gle_for_source_account(acc))

			for acc in self.get_balances_based_on_dimensions(
				source_accounts, group_by_account=False
			):
				if flt(acc.bal_in_company_currency):
					gl_entries.append(self.get_gle_for_destination_account(acc, destination_account))

		return gl_entries

	def get_destination_account_from_profile(self, profile):
		return frappe.db.get_value("Closing Configuration Profile", profile, "destination_account")

	def get_source_accounts_from_profile(self, profile):
		"""Flatten the profile's source accounts to ledger accounts.

		A source may be a group account, in which case every ledger beneath it is
		closed together.
		"""
		ledger_accounts = []

		rows = frappe.get_all(
			"Closing Configuration Profile Source Accounts",
			filters={"parent": profile, "parenttype": "Closing Configuration Profile"},
			pluck="account",
		)
		for account in rows:
			self.fetch_children_accounts(account, ledger_accounts)

		# A group may be reached twice through overlapping sources; posting the same
		# ledger account twice would double the closing entry.
		return list(dict.fromkeys(ledger_accounts))

	def fetch_children_accounts(self, account, children_array):
		if frappe.db.get_value("Account", account, "is_group"):
			for child_account in get_children("Account", account, self.company):
				self.fetch_children_accounts(child_account["value"], children_array)
		else:
			children_array.append(account)
		return children_array

	def get_gle_for_source_account(self, acc):
		"""Reverse the source account's balance so it ends the period at zero."""
		gl_entry = self.get_gl_dict(
			{
				"account": acc.account,
				"cost_center": acc.cost_center,
				"finance_book": acc.finance_book,
				"account_currency": acc.account_currency,
				"debit_in_account_currency": abs(flt(acc.bal_in_account_currency))
				if flt(acc.bal_in_account_currency) < 0
				else 0,
				"debit": abs(flt(acc.bal_in_company_currency))
				if flt(acc.bal_in_company_currency) < 0
				else 0,
				"credit_in_account_currency": abs(flt(acc.bal_in_account_currency))
				if flt(acc.bal_in_account_currency) > 0
				else 0,
				"credit": abs(flt(acc.bal_in_company_currency))
				if flt(acc.bal_in_company_currency) > 0
				else 0,
			},
			item=acc,
		)
		self.update_default_dimensions(gl_entry, acc)
		return gl_entry

	def get_gle_for_destination_account(self, acc, destination_account):
		"""The balancing leg, carrying the same total onto the destination."""
		gl_entry = self.get_gl_dict(
			{
				"account": destination_account,
				"cost_center": acc.cost_center,
				"finance_book": acc.finance_book,
				"account_currency": acc.account_currency,
				"debit_in_account_currency": abs(flt(acc.bal_in_account_currency))
				if flt(acc.bal_in_account_currency) > 0
				else 0,
				"debit": abs(flt(acc.bal_in_company_currency))
				if flt(acc.bal_in_company_currency) > 0
				else 0,
				"credit_in_account_currency": abs(flt(acc.bal_in_account_currency))
				if flt(acc.bal_in_account_currency) < 0
				else 0,
				"credit": abs(flt(acc.bal_in_company_currency))
				if flt(acc.bal_in_company_currency) < 0
				else 0,
			},
			item=acc,
		)
		self.update_default_dimensions(gl_entry, acc)
		return gl_entry

	def update_default_dimensions(self, gl_entry, acc):
		if not self.get("accounting_dimensions"):
			self.accounting_dimensions = get_accounting_dimensions()

		for dimension in self.accounting_dimensions:
			gl_entry.update({dimension: acc.get(dimension)})

	def get_balances_based_on_dimensions(self, source_accounts, group_by_account=False):
		"""Dimension-wise balance of `source_accounts` over the voucher's period."""
		if not source_accounts:
			return []

		dimension_fields = ["t1.cost_center", "t1.finance_book"]

		self.accounting_dimensions = get_accounting_dimensions()
		for dimension in self.accounting_dimensions:
			dimension_fields.append("t1.{0}".format(dimension))

		if group_by_account:
			dimension_fields.append("t1.account")

		placeholders = ", ".join(["%s"] * len(source_accounts))

		return frappe.db.sql(
			"""
			select
				t2.account_currency,
				{dimension_fields},
				sum(t1.debit_in_account_currency) - sum(t1.credit_in_account_currency) as bal_in_account_currency,
				sum(t1.debit) - sum(t1.credit) as bal_in_company_currency
			from `tabGL Entry` t1, `tabAccount` t2
			where
				t1.is_cancelled = 0
				and t1.account = t2.name
				and t2.name in ({placeholders})
				and t2.docstatus < 2
				and t2.company = %s
				and t1.company = %s
				and t1.posting_date between %s and %s
			group by {dimension_fields}
		""".format(
				dimension_fields=", ".join(dimension_fields),
				placeholders=placeholders,
			),
			tuple(source_accounts) + (self.company, self.company, self.from_date, self.to_date),
			as_dict=1,
		)


def process_gl_entries(gl_entries, in_background=False):
	"""Post the closing entries.

	When this runs inline as part of submit, an error must reach the user: the
	rollback that follows also undoes the freshly inserted voucher, so swallowing
	the exception would leave them with no document and no message. Recording the
	failure on the document only makes sense in the background, where the voucher
	is already committed.
	"""
	from erpnext.accounts.general_ledger import make_gl_entries

	if not in_background:
		make_gl_entries(gl_entries, merge_entries=False)
		frappe.db.set_value(
			VOUCHER_TYPE, gl_entries[0].get("voucher_no"), "gle_processing_status", "Completed"
		)
		return

	try:
		make_gl_entries(gl_entries, merge_entries=False)
		frappe.db.set_value(
			VOUCHER_TYPE, gl_entries[0].get("voucher_no"), "gle_processing_status", "Completed"
		)
	except Exception as e:
		frappe.db.rollback()
		frappe.log_error(frappe.get_traceback(), "Angola Closing Voucher posting failed")
		frappe.db.set_value(
			VOUCHER_TYPE,
			gl_entries[0].get("voucher_no"),
			{"gle_processing_status": "Failed", "error_message": str(e)},
		)


def make_reverse_gl_entries(voucher_type, voucher_no, in_background=False):
	from erpnext.accounts.general_ledger import make_reverse_gl_entries

	if not in_background:
		make_reverse_gl_entries(voucher_type=voucher_type, voucher_no=voucher_no)
		frappe.db.set_value(VOUCHER_TYPE, voucher_no, "gle_processing_status", "Completed")
		return

	try:
		make_reverse_gl_entries(voucher_type=voucher_type, voucher_no=voucher_no)
		frappe.db.set_value(VOUCHER_TYPE, voucher_no, "gle_processing_status", "Completed")
	except Exception as e:
		frappe.db.rollback()
		frappe.log_error(frappe.get_traceback(), "Angola Closing Voucher cancellation failed")
		frappe.db.set_value(
			VOUCHER_TYPE, voucher_no, {"gle_processing_status": "Failed", "error_message": str(e)}
		)
