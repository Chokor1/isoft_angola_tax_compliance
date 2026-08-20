"""Purchase Receipt that carries party details onto valuation-tax GL entries.

A valuation tax owed to a specific party — the classic Angolan case being a charge
withheld on behalf of a third party rather than the supplier — should post against
that party rather than sitting unattributed on the expense account. ERPNext's own
GL entry for a valuation tax carries no party, so this used to be an edit inside
erpnext/stock/doctype/purchase_receipt/purchase_receipt.py, which threaded
party_type/party from the tax row through make_tax_gl_entries() into add_gl_entry().

None of that is needed to achieve it. ERPNext already hands add_gl_entry() the tax
row itself as `item`, so the party can be read there and applied to the entry that
was just appended — leaving make_tax_gl_entries() and add_gl_entry() untouched
upstream, and this override about ten lines long.

The two fields it reads, party_type and party, are Custom Fields on Purchase Taxes
and Charges. When they are blank — which is every Purchase Receipt on this site so
far — this is a pass-through and the GL entries are exactly ERPNext's.
"""
from erpnext.stock.doctype.purchase_receipt.purchase_receipt import (
	PurchaseReceipt as _ERPNextPurchaseReceipt,
)


class AngolaPurchaseReceipt(_ERPNextPurchaseReceipt):
	def add_gl_entry(
		self,
		gl_entries,
		account,
		cost_center,
		debit,
		credit,
		remarks,
		against_account,
		party_type=None,
		party=None,
		**kwargs
	):
		"""Attribute the entry to a party when its source row names one.

		party_type/party are accepted explicitly so an existing caller that passes them
		keeps working; otherwise they come off `item`, which is the Purchase Taxes and
		Charges row for valuation-tax entries and a Purchase Receipt Item elsewhere —
		the latter has no such fields, so item rows are unaffected.
		"""
		before = len(gl_entries)
		super().add_gl_entry(
			gl_entries=gl_entries,
			account=account,
			cost_center=cost_center,
			debit=debit,
			credit=credit,
			remarks=remarks,
			against_account=against_account,
			**kwargs
		)

		item = kwargs.get("item")
		if item is not None:
			party_type = party_type or item.get("party_type")
			party = party or item.get("party")

		if party and len(gl_entries) > before:
			gl_entries[-1]["party_type"] = party_type
			gl_entries[-1]["party"] = party
