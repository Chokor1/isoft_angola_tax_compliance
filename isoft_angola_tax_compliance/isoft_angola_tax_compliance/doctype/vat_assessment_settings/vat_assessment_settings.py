# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

# Accounts that may be group accounts (every ledger beneath them is aggregated).
SOURCE_ACCOUNT_FIELDS = [
	("deductible_vat_account", "Deductible VAT Account"),
	("output_vat_account", "Output VAT Account"),
	("withheld_vat_recoverable_account", "Withheld VAT Recoverable Account"),
]

# Journal Entries can only post to ledger accounts, so these must not be groups.
DESTINATION_ACCOUNT_FIELDS = [
	("vat_assessment_account", "VAT Assessment Account"),
	("vat_payable_account", "VAT Payable Account"),
	("vat_recoverable_account", "VAT Recoverable Account"),
]

# Core accounts that must be distinct from one another within a company.
DISTINCT_ACCOUNT_FIELDS = [
	"deductible_vat_account",
	"output_vat_account",
	"vat_assessment_account",
	"vat_payable_account",
	"vat_recoverable_account",
]


class VATAssessmentSettings(Document):
	def validate(self):
		self.validate_one_row_per_company()
		self.validate_company_accounts()
		self.validate_distinct_accounts()
		self.validate_adjustment_accounts()

	def validate_one_row_per_company(self):
		seen = set()
		for row in self.company_accounts or []:
			if row.company in seen:
				frappe.throw(
					_("Row #{0}: Company {1} is configured more than once.").format(row.idx, row.company)
				)
			seen.add(row.company)

	def validate_company_accounts(self):
		"""Every configured account must belong to the company on its own row.

		Source accounts (deductible, output, withheld) MAY be group accounts —
		all ledger accounts beneath them are aggregated. Destination accounts
		(assessment, payable, recoverable) must be ledgers, because Journal
		Entries can only post to ledger accounts.
		"""
		for row in self.company_accounts or []:
			for fieldname, label in SOURCE_ACCOUNT_FIELDS:
				if row.get(fieldname):
					self._check_account_company(row.get(fieldname), _(label), row.company, allow_group=True)

			for fieldname, label in DESTINATION_ACCOUNT_FIELDS:
				if row.get(fieldname):
					self._check_account_company(row.get(fieldname), _(label), row.company, allow_group=False)

	def validate_adjustment_accounts(self):
		"""Adjustment accounts are per company too, and may be group accounts."""
		configured = {row.company for row in self.company_accounts or []}

		for row in self.adjustment_accounts or []:
			if not row.account:
				continue

			if row.company not in configured:
				frappe.throw(
					_("Row #{0}: Company {1} has no VAT accounts configured, so it cannot have adjustment accounts.").format(
						row.idx, row.company
					)
				)

			self._check_account_company(
				row.account, _("VAT Adjustment Account"), row.company, allow_group=True
			)

	def _check_account_company(self, account, label, company, allow_group=False):
		acc = frappe.db.get_value("Account", account, ["company", "is_group"], as_dict=True)
		if not acc:
			return
		if acc.is_group and not allow_group:
			frappe.throw(
				_("{0} ({1}) is a group account. Please select a ledger account — journal entries cannot post to a group.").format(
					label, account
				)
			)
		if acc.company != company:
			frappe.throw(_("{0} ({1}) does not belong to company {2}.").format(label, account, company))

	def validate_distinct_accounts(self):
		"""Within a company, the five core accounts must be distinct."""
		for row in self.company_accounts or []:
			core = [row.get(field) for field in DISTINCT_ACCOUNT_FIELDS]
			core = [a for a in core if a]
			if len(core) != len(set(core)):
				frappe.throw(
					_("Row #{0} ({1}): the VAT accounts (deductible, output, assessment, payable, recoverable) must all be different.").format(
						row.idx, row.company
					)
				)


def get_vat_assessment_settings(company):
	"""Return the VAT Assessment configuration that applies to `company`.

	The accounts are held per company, so this flattens the matching row into a
	dict alongside that company's adjustment accounts and the global options.
	Callers read it exactly as they read the old single-company document.
	"""
	if not company:
		frappe.throw(_("Please select a Company before reading VAT Assessment Settings."))

	settings = frappe.get_single("VAT Assessment Settings")

	row = next((r for r in settings.company_accounts or [] if r.company == company), None)
	if not row:
		configured = [r.company for r in settings.company_accounts or []]
		frappe.throw(
			_("VAT Assessment Settings has no VAT accounts configured for company {0}. Configured companies: {1}").format(
				company, ", ".join(configured) or _("(none)")
			)
		)

	resolved = frappe._dict(
		{fieldname: row.get(fieldname) for fieldname, _label in SOURCE_ACCOUNT_FIELDS}
	)
	resolved.update({fieldname: row.get(fieldname) for fieldname, _label in DESTINATION_ACCOUNT_FIELDS})

	resolved.company = company
	resolved.restrict_posting_to_next_month = settings.restrict_posting_to_next_month
	resolved.adjustment_accounts = [
		r for r in settings.adjustment_accounts or [] if r.company == company
	]

	return resolved
