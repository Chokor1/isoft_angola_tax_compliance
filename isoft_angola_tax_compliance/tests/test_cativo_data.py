# Copyright (c) 2026, ISOFT and contributors
# For license information, please see license.txt
"""Cativo Auditing data loading, against canned ledger rows (no site needed).

    ../../env/bin/python -m unittest isoft_angola_tax_compliance.tests.test_cativo_data -v
"""

import unittest
from unittest import mock

from isoft_angola_tax_compliance.cativo_audit import data

ACCOUNTS = dict(
	cativo=["34543 - Cativo"],
	iva=["34531 - IVA Liquidado"],
	closing=["345211 - IVA Dedutivel", "34531 - IVA Liquidado"],
	keywords=["apuramento", "abertura"],
	derived=False,
)

GL = [
	dict(voucher_type="Payment Entry", voucher_no="REC 2025/13149", account="34543 - Cativo", posting_date="2025-11-01", amount=151032.0),
	dict(voucher_type="Journal Entry", voucher_no="PR2025/10027", account="34543 - Cativo", posting_date="2025-03-31", amount=-676006.93),
	dict(voucher_type="Journal Entry", voucher_no="CBL2026/3289", account="34543 - Cativo", posting_date="2026-05-31", amount=107100.0),
]
JE = [
	dict(name="PR2025/10027", is_vat_exemption=0, user_remark="Apuramento IVA", remark=""),
	dict(name="CBL2026/3289", is_vat_exemption=0, user_remark="Iva Cativo FT2026/41", remark=""),
]
JE_ACCOUNTS = [
	dict(parent="PR2025/10027", account="34543 - Cativo", party_type=None, party=None, reference_type=None, reference_name=None),
	dict(parent="PR2025/10027", account="345211 - IVA Dedutivel", party_type=None, party=None, reference_type=None, reference_name=None),
	dict(parent="CBL2026/3289", account="31121783 - CFE", party_type="Customer", party="CFE",
		reference_type="Sales Invoice", reference_name="FT FT3526S17184N/2019"),
	dict(parent="CBL2026/3289", account="34543 - Cativo", party_type=None, party=None, reference_type=None, reference_name=None),
]
PE = [dict(name="REC 2025/13149", party="INIS", remarks="Montante AKZ 151032 Recebido de INIS")]
PE_REFS = [dict(parent="REC 2025/13149", reference_doctype="Sales Invoice", reference_name="FT 2025/865", allocated_amount=58965.79)]
CANDIDATES = [dict(name="FT FT3526S17184N/41", customer="CFE")]
INVOICES = [
	dict(name="FT 1", posting_date="2025-01-02", customer="C", customer_name="Customer C", tax_id="5000000000",
		docstatus=1, is_return=0, net_total=700.0, grand_total=800.0, iva=100.0),
]


class TestData(unittest.TestCase):
	def setUp(self):
		self.queries = []

		def fake_sql(query, values=None):
			self.queries.append(query)
			if "tabSales Invoice Withholding" in query:
				return []
			if "tabGL Entry" in query:
				return GL
			if "tabJournal Entry Account" in query:
				return JE_ACCOUNTS
			if "tabJournal Entry" in query:
				return JE
			if "tabPayment Entry Reference" in query:
				return PE_REFS
			if "tabPayment Entry" in query:
				return PE
			if "tabParty Tax Withholding" in query:
				return []
			if "tabSales Taxes and Charges" in query:
				return INVOICES
			if "tabSales Invoice" in query:
				return CANDIDATES
			raise AssertionError("unexpected query: " + query[:80])

		patcher = mock.patch.object(data, "_sql", side_effect=fake_sql)
		patcher.start()
		self.addCleanup(patcher.stop)

	def test_load_uses_account_currency(self):
		data.load_movements("ITEC LDA", ACCOUNTS)
		gl = next(q for q in self.queries if "tabGL Entry" in q)
		self.assertIn("debit_in_account_currency", gl)
		self.assertNotIn("base_", gl)
		data.load_invoices(["FT 1"], ACCOUNTS)
		si = next(q for q in self.queries if "tabSales Taxes and Charges" in q)
		self.assertIn("tax_amount_after_discount_amount", si)
		self.assertNotIn("base_tax", si)

	def test_pe_split_by_allocation(self):
		moves = [m for m in data.load_movements("ITEC LDA", ACCOUNTS) if m["voucher"] == "REC 2025/13149"]
		self.assertEqual(
			[(m["invoice"], round(m["amount"], 2)) for m in moves], [("FT 2025/865", 58965.79), (None, 92066.21)]
		)

	def test_closing_je_excluded(self):
		self.assertNotIn("PR2025/10027", [m["voucher"] for m in data.load_movements("ITEC LDA", ACCOUNTS)])

	def test_manual_je_resolved_from_note(self):
		m = next(m for m in data.load_movements("ITEC LDA", ACCOUNTS) if m["voucher"] == "CBL2026/3289")
		self.assertEqual((m["source"], m["invoice"]), ("manual_je", "FT FT3526S17184N/41"))
		self.assertIn("ERP link differs", m["link"])


if __name__ == "__main__":
	unittest.main()
