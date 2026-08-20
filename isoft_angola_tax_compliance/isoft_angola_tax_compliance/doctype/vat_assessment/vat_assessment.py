# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_months, flt, get_first_day, get_last_day, getdate

from isoft_angola_tax_compliance.isoft_angola_tax_compliance.doctype.vat_assessment_settings.vat_assessment_settings import (
	get_vat_assessment_settings,
)

MONTHS = [
	"January",
	"February",
	"March",
	"April",
	"May",
	"June",
	"July",
	"August",
	"September",
	"October",
	"November",
	"December",
]


def get_period(month, year):
	"""Return (from_date, to_date) covering the whole *month* of *year*.

	The assessment is always a full calendar month, so the period is derived
	rather than entered.
	"""
	if month not in MONTHS:
		frappe.throw(_("Please select a valid Month."))
	try:
		year = int(year)
	except (TypeError, ValueError):
		frappe.throw(_("Please enter a valid Year."))
	if year < 1900 or year > 9999:
		frappe.throw(_("Please enter a valid Year."))

	from_date = getdate(f"{year}-{MONTHS.index(month) + 1:02d}-01")
	return from_date, get_last_day(from_date)


class VATAssessment(Document):
	def validate(self):
		self.set_period()
		if not self.posting_date:
			self.posting_date = self.to_date
		self.validate_posting_date()
		self.compute()

	def set_period(self):
		"""Derive from_date / to_date from the selected month and year."""
		self.from_date, self.to_date = get_period(self.month, self.year)

	def validate_posting_date(self):
		"""When enabled in VAT Assessment Settings, the Posting Date must fall
		within the month after the assessed month."""
		settings = get_vat_assessment_settings(self.company)
		if not settings.get("restrict_posting_to_next_month"):
			return
		next_month = add_months(getdate(self.to_date), 1)
		earliest = get_first_day(next_month)
		latest = get_last_day(next_month)
		posting = getdate(self.posting_date)
		if posting < earliest or posting > latest:
			frappe.throw(
				_("Posting Date must fall within the month after the assessed month ({0} to {1}).").format(
					frappe.format(earliest, {"fieldtype": "Date"}),
					frappe.format(latest, {"fieldtype": "Date"}),
				)
			)

	def before_submit(self):
		self.check_duplicate()
		self.compute()
		if not self.account_summary:
			frappe.throw(_("Nothing to assess for the selected period."))
		# Journal Entries are NOT created on submit. The accountant explicitly
		# clicks "Create Draft" / "Submit Journal Entries" from the form. Submit
		# only locks the computed assessment figures.

	def on_cancel(self):
		self.cancel_journal_entries()

	# ------------------------------------------------------------------ #
	# Duplicate guard
	# ------------------------------------------------------------------ #
	def check_duplicate(self):
		existing = frappe.db.sql(
			"""
			SELECT name FROM `tabVAT Assessment`
			WHERE company = %(company)s
			  AND docstatus = 1
			  AND name != %(name)s
			  AND from_date <= %(to_date)s
			  AND to_date >= %(from_date)s
			LIMIT 1
			""",
			{
				"company": self.company,
				"name": self.name or "",
				"from_date": self.from_date,
				"to_date": self.to_date,
			},
		)
		if existing:
			frappe.throw(
				_("A submitted VAT Assessment ({0}) already covers {1} {2} for {3}.").format(
					existing[0][0], _(self.month), self.year, self.company
				)
			)

	# ------------------------------------------------------------------ #
	# Computation
	# ------------------------------------------------------------------ #
	def compute(self):
		settings = get_vat_assessment_settings(self.company)
		self._require_settings(settings)

		self.set("account_summary", [])

		# Deductible VAT — debit balance (debit - credit)
		ded_dr, ded_cr = self._account_movement(settings.deductible_vat_account)
		deductible = flt(ded_dr) - flt(ded_cr)
		self.deductible_vat = deductible
		self._add_summary(settings.deductible_vat_account, "Deductible VAT", ded_dr, ded_cr, deductible)

		# Output VAT — credit balance (credit - debit)
		out_dr, out_cr = self._account_movement(settings.output_vat_account)
		output = flt(out_cr) - flt(out_dr)
		self.output_vat = output
		self._add_summary(settings.output_vat_account, "Output VAT", out_dr, out_cr, output)

		# Withheld VAT (cativo) — debit balance. VAT the customer withheld at
		# source and paid to the State on our behalf, so it reduces the payable
		# exactly like deductible VAT. Optional: companies without withholding
		# leave the account unset.
		withheld = 0.0
		if settings.get("withheld_vat_recoverable_account"):
			wh_dr, wh_cr = self._account_movement(settings.withheld_vat_recoverable_account)
			withheld = flt(wh_dr) - flt(wh_cr)
			self._add_summary(
				settings.withheld_vat_recoverable_account,
				"Withheld VAT Recoverable",
				wh_dr,
				wh_cr,
				withheld,
			)
		self.withheld_vat_recoverable = withheld

		# Adjustments (Regulações)
		adj_state = 0.0
		adj_company = 0.0
		for row in settings.adjustment_accounts or []:
			if not row.account:
				continue
			a_dr, a_cr = self._account_movement(row.account)
			if row.direction == "Favor do Estado":
				# increases payable — natural credit balance
				bal = flt(a_cr) - flt(a_dr)
				adj_state += bal
				self._add_summary(
					row.account, "Adjustment in Favour of the State", a_dr, a_cr, bal
				)
			else:
				# Favor da Empresa — decreases payable — natural debit balance
				bal = flt(a_dr) - flt(a_cr)
				adj_company += bal
				self._add_summary(
					row.account, "Adjustment in Favour of the Company", a_dr, a_cr, bal
				)

		self.adjustment_state = adj_state
		self.adjustment_company = adj_company

		# Net VAT Position = Output - Deductible - Withheld + Favor do Estado - Favor da Empresa
		net = flt(output) - flt(deductible) - flt(withheld) + flt(adj_state) - flt(adj_company)
		self.net_vat_position = net
		if net > 0:
			self.vat_position = "Payable"
		elif net < 0:
			self.vat_position = "Recoverable"
		else:
			self.vat_position = "Nil"

		# Build the planned journal entries (preview only — nothing is posted).
		self._build_plan(settings)

	def _require_settings(self, settings):
		required = [
			("deductible_vat_account", _("Deductible VAT Account")),
			("output_vat_account", _("Output VAT Account")),
			("vat_assessment_account", _("VAT Assessment Account")),
			("vat_payable_account", _("VAT Payable Account")),
			("vat_recoverable_account", _("VAT Recoverable Account")),
		]
		missing = [label for field, label in required if not settings.get(field)]
		if missing:
			frappe.throw(
				_("Please configure the following in VAT Assessment Settings: {0}").format(
					", ".join(missing)
				)
			)

	def _ledger_accounts(self, account):
		"""Resolve *account* into a list of posting (ledger) accounts.

		For a ledger account this is just ``[account]``; for a group account it
		is every non-group descendant under it (by nested-set lft/rgt), so the
		whole branch is aggregated/cleared together.
		"""
		acc = frappe.db.get_value("Account", account, ["lft", "rgt", "is_group"], as_dict=True)
		if not acc:
			return []
		if not acc.is_group:
			return [account]
		return frappe.db.sql_list(
			"""
			SELECT name FROM `tabAccount`
			WHERE company = %(company)s AND is_group = 0
			  AND lft > %(lft)s AND rgt < %(rgt)s
			ORDER BY lft
			""",
			{"company": self.company, "lft": acc.lft, "rgt": acc.rgt},
		)

	def _account_movement(self, account):
		"""Return (debit, credit) totals for *account* over the period.

		When *account* is a group, all ledger accounts beneath it are summed.
		Only non-cancelled GL entries are counted.
		"""
		ledgers = self._ledger_accounts(account)
		if not ledgers:
			return (0.0, 0.0)
		row = frappe.db.sql(
			"""
			SELECT COALESCE(SUM(debit), 0) AS debit, COALESCE(SUM(credit), 0) AS credit
			FROM `tabGL Entry`
			WHERE account IN %(accounts)s
			  AND company = %(company)s
			  AND posting_date BETWEEN %(from_date)s AND %(to_date)s
			  AND IFNULL(is_cancelled, 0) = 0
			""",
			{
				"accounts": tuple(ledgers),
				"company": self.company,
				"from_date": self.from_date,
				"to_date": self.to_date,
			},
			as_dict=True,
		)
		return (flt(row[0].debit), flt(row[0].credit)) if row else (0.0, 0.0)

	def _ledger_movement(self, account):
		"""Return {ledger: (debit, credit)} per posting account under *account*
		(itself if a ledger). Used to clear each child ledger individually in the
		journal entries — you cannot post to a group account."""
		ledgers = self._ledger_accounts(account)
		result = {}
		if not ledgers:
			return result
		rows = frappe.db.sql(
			"""
			SELECT account, COALESCE(SUM(debit), 0) AS debit, COALESCE(SUM(credit), 0) AS credit
			FROM `tabGL Entry`
			WHERE account IN %(accounts)s
			  AND company = %(company)s
			  AND posting_date BETWEEN %(from_date)s AND %(to_date)s
			  AND IFNULL(is_cancelled, 0) = 0
			GROUP BY account
			""",
			{
				"accounts": tuple(ledgers),
				"company": self.company,
				"from_date": self.from_date,
				"to_date": self.to_date,
			},
			as_dict=True,
		)
		for r in rows:
			result[r.account] = (flt(r.debit), flt(r.credit))
		return result

	def _add_summary(self, account, role, debit, credit, balance):
		side = "Debit" if flt(balance) > 0 else ("Credit" if flt(balance) < 0 else "Nil")
		# For output/adjustment-estado the "balance" is naturally credit-positive;
		# present the side from the raw debit/credit movement for clarity.
		raw = flt(debit) - flt(credit)
		side = "Debit" if raw > 0 else ("Credit" if raw < 0 else "Nil")
		self.append(
			"account_summary",
			{
				"account": account,
				"role": role,
				"debit": flt(debit),
				"credit": flt(credit),
				"balance": abs(flt(balance)),
				"balance_side": side,
			},
		)

	# ------------------------------------------------------------------ #
	# Planning (preview) — no posting
	# ------------------------------------------------------------------ #
	def _transfer_lines(self, source_account, assessment_acc):
		"""Build the JE lines to move *source_account* (and its ledger children,
		if a group) into the assessment account. Returns a list of line dicts, or
		[] when there is nothing to move. Cannot post to a group, hence the
		per-ledger expansion."""
		movements = self._ledger_movement(source_account)
		lines = []
		net = 0.0  # debit-positive net across the branch
		for ledger, (dr, cr) in movements.items():
			bal = flt(dr) - flt(cr)
			if bal == 0:
				continue
			net += bal
			if bal > 0:
				lines.append({"account": ledger, "debit": 0.0, "credit": abs(bal)})
			else:
				lines.append({"account": ledger, "debit": abs(bal), "credit": 0.0})

		if not lines or net == 0:
			return []

		if net > 0:
			lines.append({"account": assessment_acc, "debit": abs(net), "credit": 0.0})
		else:
			lines.append({"account": assessment_acc, "debit": 0.0, "credit": abs(net)})
		return lines

	def build_plan(self):
		"""Return the planned journal entries as a list of
		{title, lines:[{account, debit, credit}]} — no documents are created."""
		settings = get_vat_assessment_settings(self.company)
		assessment_acc = settings.vat_assessment_account
		plan = []

		lines = self._transfer_lines(settings.deductible_vat_account, assessment_acc)
		if lines:
			plan.append({"title": _("Transfer Deductible VAT"), "lines": lines})

		lines = self._transfer_lines(settings.output_vat_account, assessment_acc)
		if lines:
			plan.append({"title": _("Transfer Output VAT"), "lines": lines})

		# Always transferred, whatever the resulting position — the withheld VAT
		# account is left at zero even when the assessment ends up recoverable and
		# the assessment account itself is carried forward.
		if settings.get("withheld_vat_recoverable_account"):
			lines = self._transfer_lines(settings.withheld_vat_recoverable_account, assessment_acc)
			if lines:
				plan.append({"title": _("Transfer Withheld VAT"), "lines": lines})

		for row in settings.adjustment_accounts or []:
			if not row.account:
				continue
			lines = self._transfer_lines(row.account, assessment_acc)
			if lines:
				plan.append({"title": _("Transfer Adjustment {0}").format(row.account), "lines": lines})

		# Final assessment — ONLY for a payable (credit) position. A recoverable
		# (debit) balance is left in the assessment account; the Dr Recoverable /
		# Cr Assessment move is done next month after AGT portal verification.
		net = flt(self.net_vat_position)
		if net > 0:
			amt = abs(net)
			plan.append({
				"title": _("VAT Payable"),
				"lines": [
					{"account": assessment_acc, "debit": amt, "credit": 0.0},
					{"account": settings.vat_payable_account, "debit": 0.0, "credit": amt},
				],
			})
		return plan

	def _build_plan(self, settings=None):
		"""Populate the read-only planned_entries preview table from build_plan()."""
		self.set("planned_entries", [])
		for idx, je in enumerate(self.build_plan(), start=1):
			for line in je["lines"]:
				self.append("planned_entries", {
					"entry_no": idx,
					"description": je["title"],
					"account": line["account"],
					"debit": flt(line["debit"]),
					"credit": flt(line["credit"]),
				})

	# ------------------------------------------------------------------ #
	# Journal Entries — explicit create / submit
	# ------------------------------------------------------------------ #
	def generate_journal_entries(self, submit=False):
		"""Create a Journal Entry for every planned entry. If *submit*, also submit
		them. Skips creation when JEs already exist (use Submit / Refresh instead).
		Records each created JE in the created_journal_entries table."""
		if self.docstatus != 1:
			frappe.throw(_("Submit the VAT Assessment before creating Journal Entries."))

		existing = [r.journal_entry for r in (self.created_journal_entries or []) if r.journal_entry]
		live = [n for n in existing if frappe.db.exists("Journal Entry", n)]
		if live:
			frappe.throw(
				_("Journal Entries already exist for this assessment. Use Submit or Refresh Status instead.")
			)

		plan = self.build_plan()
		if not plan:
			frappe.throw(_("Nothing to post for this assessment."))

		self.set("created_journal_entries", [])
		for je in plan:
			name = self._make_je(je["title"], je["lines"], submit)
			self.append("created_journal_entries", {
				"journal_entry": name,
				"description": je["title"],
			})
		self.refresh_journal_entry_status(save=False)
		self.save(ignore_permissions=True)

		if flt(self.net_vat_position) < 0:
			frappe.msgprint(
				_(
					"Recoverable position: the balance is left in the VAT Assessment account. "
					"Transfer it to VAT Recoverable next month after verifying in the AGT portal."
				),
				indicator="orange",
				alert=True,
			)

	def submit_journal_entries(self):
		"""Submit the ticked draft Journal Entries created for this assessment.
		Creates them first if none exist yet."""
		if self.docstatus != 1:
			frappe.throw(_("Submit the VAT Assessment before submitting Journal Entries."))

		rows = [r for r in (self.created_journal_entries or []) if r.journal_entry]
		live = [r for r in rows if frappe.db.exists("Journal Entry", r.journal_entry)]
		if not live:
			# Nothing created yet — create and submit all entries in one go.
			self.generate_journal_entries(submit=True)
			return

		for r in live:
			je = frappe.get_doc("Journal Entry", r.journal_entry)
			if je.docstatus == 0:
				je.submit()
		self.refresh_journal_entry_status(save=True)

	def refresh_journal_entry_status(self, save=True):
		"""Sync the status / amount columns of created_journal_entries with the
		live Journal Entry documents."""
		status_map = {0: "Draft", 1: "Submitted", 2: "Cancelled"}
		for r in self.created_journal_entries or []:
			if not r.journal_entry or not frappe.db.exists("Journal Entry", r.journal_entry):
				r.status = "Missing"
				continue
			je = frappe.db.get_value(
				"Journal Entry", r.journal_entry,
				["docstatus", "total_debit", "posting_date"], as_dict=True,
			)
			r.status = status_map.get(je.docstatus, "Draft")
			r.total_debit = flt(je.total_debit)
			r.posting_date = je.posting_date
		if save:
			self.save(ignore_permissions=True)

	def _make_je(self, title, lines, submit):
		je = frappe.new_doc("Journal Entry")
		je.voucher_type = "Journal Entry"
		je.company = self.company
		je.posting_date = self.posting_date
		je.user_remark = _("Generated by VAT Assessment {0} - {1}").format(self.name, title)
		je.title = title
		for line in lines:
			je.append(
				"accounts",
				{
					"account": line["account"],
					"debit_in_account_currency": flt(line.get("debit", 0)),
					"credit_in_account_currency": flt(line.get("credit", 0)),
				},
			)
		je.insert(ignore_permissions=True)
		if submit:
			je.submit()
		return je.name

	def cancel_journal_entries(self):
		for r in self.created_journal_entries or []:
			name = r.journal_entry
			if not name or not frappe.db.exists("Journal Entry", name):
				continue
			je = frappe.get_doc("Journal Entry", name)
			if je.docstatus == 1:
				# This assessment owns these JEs and is cancelling them as a cascade —
				# skip the link-integrity check that would otherwise block it because
				# this VAT Assessment (still docstatus 1 mid-cancel) links to them.
				je.flags.ignore_links = True
				je.cancel()
			elif je.docstatus == 0:
				# Same reason for drafts, but the link check on delete is bypassed
				# with force=True (Document.delete has no ignore_links path).
				frappe.delete_doc("Journal Entry", name, force=True, ignore_permissions=True)


@frappe.whitelist()
def preview_assessment(company, month, year, posting_date=None):
	"""Compute the assessment without saving — used by the Preview button."""
	from_date, to_date = get_period(month, year)
	doc = frappe.new_doc("VAT Assessment")
	doc.company = company
	doc.month = month
	doc.year = year
	doc.from_date = from_date
	doc.to_date = to_date
	doc.posting_date = posting_date or to_date
	doc.compute()
	return {
		"from_date": from_date,
		"to_date": to_date,
		"deductible_vat": doc.deductible_vat,
		"output_vat": doc.output_vat,
		"withheld_vat_recoverable": doc.withheld_vat_recoverable,
		"adjustment_state": doc.adjustment_state,
		"adjustment_company": doc.adjustment_company,
		"net_vat_position": doc.net_vat_position,
		"vat_position": doc.vat_position,
		"account_summary": [
			{
				"account": r.account,
				"role": r.role,
				"debit": r.debit,
				"credit": r.credit,
				"balance": r.balance,
				"balance_side": r.balance_side,
			}
			for r in doc.account_summary
		],
		"planned_entries": [
			{
				"entry_no": r.entry_no,
				"description": r.description,
				"account": r.account,
				"debit": r.debit,
				"credit": r.credit,
			}
			for r in doc.planned_entries
		],
	}


@frappe.whitelist()
def create_draft_entries(assessment):
	"""Create the planned Journal Entries as drafts (not submitted)."""
	doc = frappe.get_doc("VAT Assessment", assessment)
	doc.generate_journal_entries(submit=False)
	return {"ok": True, "count": len(doc.created_journal_entries or [])}


@frappe.whitelist()
def submit_journal_entries(assessment):
	"""Submit the Journal Entries for the assessment (creating them first if
	none exist)."""
	doc = frappe.get_doc("VAT Assessment", assessment)
	doc.submit_journal_entries()
	return {"ok": True, "count": len(doc.created_journal_entries or [])}


@frappe.whitelist()
def refresh_status(assessment):
	"""Re-sync the status of the linked Journal Entries."""
	doc = frappe.get_doc("VAT Assessment", assessment)
	doc.refresh_journal_entry_status(save=True)
	return {"ok": True}
