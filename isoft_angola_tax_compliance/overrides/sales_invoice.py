# Copyright (c) 2026, ISOFT and contributors
# For license information, please see license.txt
"""Sales Invoice controller override.

Two jobs:

1. Add the withholding computation (validate) and the withholding GL entries
   (get_gl_entries) without editing any ERPNext file.

2. Neutralise the legacy in-core withholding path -- again without editing it.
   ERPNext calls that logic as methods on `self`:

       sales_invoice.py:216  self.calculate_withholding_tax_amount()
       sales_invoice.py:505  self.apply_vat_exemption()
       sales_invoice.py:509  self.create_withholding_tax_journal_entry_auto()

   Because this class shadows those names, overriding them here turns the old
   behaviour off at runtime, per company, while the core file stays untouched.
   Off/Shadow -> legacy runs exactly as before. Active -> legacy is skipped and
   this engine owns the accounting. Rolling back is one field on the settings.

   The parent methods are looked up with getattr so that deleting them from
   core later (the cleanup phase) cannot break this class.

Note: POS Invoice subclasses SalesInvoice by direct import, so this override
does not reach it. Both the legacy paths already skip `is_pos`.
"""

import frappe

from erpnext.accounts.doctype.sales_invoice.sales_invoice import SalesInvoice

from isoft_angola_tax_compliance.withholding.apply import set_withholdings
from isoft_angola_tax_compliance.withholding.comparison import log_comparison
from isoft_angola_tax_compliance.withholding.gl import add_withholding_gl_entries


class AngolaSalesInvoice(SalesInvoice):
	# ------------------------------------------------------------------ new
	def validate(self):
		super(AngolaSalesInvoice, self).validate()
		set_withholdings(self)

	def get_gl_entries(self, warehouse_account=None):
		gl_entries = super(AngolaSalesInvoice, self).get_gl_entries(warehouse_account)
		add_withholding_gl_entries(self, gl_entries)
		return gl_entries

	def on_submit(self):
		super(AngolaSalesInvoice, self).on_submit()
		log_comparison(self)

	# The legacy in-core path (calculate_withholding_tax_amount,
	# create_withholding_tax_journal_entry_auto, apply_vat_exemption,
	# cancel_vat_exemption_journal_entry) has been deleted from ERPNext, so the
	# runtime neutralisers that used to shadow those method names are gone too.
	# There is now exactly one code path for retencao and IVA cativo: this one.
	#
	# Journal Entries created by the old path before the cutover are untouched
	# and still readable -- `Journal Entry.is_tax_withholding` /
	# `is_vat_exemption` are deliberately kept, and api.get_withholdings() falls
	# back to them for invoices booked before the engine took over.
