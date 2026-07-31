# Copyright (c) 2026, ISOFT and contributors
# For license information, please see license.txt
"""Sales Invoice controller override.

Two jobs, and nothing else:

  validate()        compute the withholding rows and store them on the document
  get_gl_entries()  book them inside the invoice's own GL entries

The legacy in-core path (calculate_withholding_tax_amount,
create_withholding_tax_journal_entry_auto, apply_vat_exemption,
cancel_vat_exemption_journal_entry) has been deleted from ERPNext, along with
the runtime neutralisers that used to shadow those method names during the
cutover. There is now exactly one code path for retencao and IVA cativo.

Journal Entries created by the old path before the cutover are untouched and
still readable: `Journal Entry.is_tax_withholding` / `is_vat_exemption` are
deliberately kept, and api.get_withholdings() falls back to them so a re-export
of a pre-cutover period still declares what was actually booked.

Note: POS Invoice subclasses SalesInvoice by direct import, so this override
does not reach it. POS Awesome issues Sales Invoice with is_pos = 1, which is
covered.
"""

import frappe

from erpnext.accounts.doctype.sales_invoice.sales_invoice import SalesInvoice

from isoft_angola_tax_compliance.withholding.apply import set_withholdings
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
