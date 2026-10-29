# Copyright (c) 2026, ISOFT and contributors
# For license information, please see license.txt
"""Cativo Auditing rules. Pure Python: run without a site.

    ../../env/bin/python -m unittest isoft_angola_tax_compliance.tests.test_cativo_resolve -v
"""

import unittest

from isoft_angola_tax_compliance.cativo_audit.resolve import (
	candidate_patterns,
	is_vat_closing,
	match_reversal,
	named_receipts,
	note_tokens,
	pick_invoice,
)


class TestResolve(unittest.TestCase):
	def test_note_tokens(self):
		self.assertEqual(note_tokens("Iva Cativo FT2026/38"), [("FT", "2026", "38")])
		self.assertEqual(note_tokens("fr VIA2025/7202 correcção"), [("FR", "VIA2025", "7202")])
		self.assertEqual(
			note_tokens("Regularização iva cativo FT FT3526S17184N/1660"), [("FT", "FT3526S17184N", "1660")]
		)
		self.assertEqual(note_tokens("iva não lançado da FT398"), [("FT", "", "398")])
		self.assertEqual(note_tokens("cliente pagou a totalidade"), [])

	def test_candidate_patterns(self):
		self.assertEqual(
			candidate_patterns(("FT", "2026", "38")), [("exact", "FT 2026/38"), ("like", "FT FT%N/38")]
		)
		self.assertEqual(candidate_patterns(("FT", "2025", "1924")), [("exact", "FT 2025/1924")])
		self.assertEqual(candidate_patterns(("FR", "VIA2025", "5372")), [("exact", "FR VIA2025/5372")])
		self.assertEqual(candidate_patterns(("FT", "", "398")), [("like", "FT %/398")])

	def test_note_beats_erp_link(self):
		inv, link = pick_invoice(["FT FT3526S17184N/41"], ["FT FT3526S17184N/2019"])
		self.assertEqual(inv, "FT FT3526S17184N/41")
		self.assertIn("FT FT3526S17184N/2019", link)

	def test_ambiguous_note_is_unassigned(self):
		inv, link = pick_invoice(["FT 2026/38", "FT FT3526S17184N/38"], ["FT FT3526S17184N/2019"])
		self.assertIsNone(inv)
		self.assertIn("FT 2026/38", link)
		self.assertIn("FT FT3526S17184N/38", link)

	def test_ambiguous_note_uses_erp_link_when_it_is_a_candidate(self):
		self.assertEqual(pick_invoice(["FT 2026/38", "FT FT3526S17184N/38"], ["FT 2026/38"])[0], "FT 2026/38")

	def test_no_candidates_falls_back_to_erp_link(self):
		self.assertEqual(pick_invoice([], ["FT 2025/413"])[0], "FT 2025/413")
		self.assertEqual(pick_invoice([], []), (None, "Not identified"))

	def test_named_receipts(self):
		self.assertEqual(
			named_receipts("Iva Cativo do recebimento RG RG9726S2895N/8\nDevido ao erp"), ["RG RG9726S2895N/8"]
		)

	def test_reversal_same_customer_and_amount(self):
		pe = dict(
			source="manual_pe", voucher="REC 2025/12628", posting_date="2025-08-08", amount=3766273.0,
			invoice="FT 2025/1309", customer="CABINDA OIL REFINERY, LDA", note="",
		)
		rev = dict(
			source="manual_je", voucher="CBL2026/85", posting_date="2025-12-23", amount=-3766273.0,
			invoice=None, customer="CABINDA OIL REFINERY, LDA", note="cliente pagou a totalidade da ft",
		)
		self.assertEqual(match_reversal(rev, [pe, rev])[0], "FT 2025/1309")

	def test_reversal_named_voucher(self):
		src = dict(
			source="manual_je", voucher="CBL2026/3278", posting_date="2026-05-31", amount=24444.0,
			invoice="FT 2025/1924", customer="CFE", note="",
		)
		rev = dict(
			source="manual_je", voucher="CBL2026/4605", posting_date="2026-08-01", amount=-24444.0,
			invoice=None, customer="CFE", note="Valor estava cativo no CBL2026/3278",
		)
		inv, link = match_reversal(rev, [src, rev])
		self.assertEqual(inv, "FT 2025/1924")
		self.assertIn("CBL2026/3278", link)

	def test_reversal_with_two_possible_invoices_stays_unassigned(self):
		a = dict(source="manual_pe", voucher="A", posting_date="2025-01-01", amount=100.0, invoice="FT 1", customer="C", note="")
		b = dict(source="manual_pe", voucher="B", posting_date="2025-01-02", amount=100.0, invoice="FT 2", customer="C", note="")
		rev = dict(source="manual_je", voucher="R", posting_date="2025-02-01", amount=-100.0, invoice=None, customer="C", note="x")
		self.assertIsNone(match_reversal(rev, [a, b, rev])[0])

	def test_vat_closing(self):
		closing = {"34531 - IVA Liquidado MN - ITEC"}
		kw = ["apuramento", "abertura"]
		self.assertTrue(is_vat_closing(False, ["34531 - IVA Liquidado MN - ITEC"], closing, "", kw))
		self.assertTrue(is_vat_closing(False, ["431021 - BAI"], closing, "IVA APURAMENTO MARÇO 24", kw))
		self.assertTrue(is_vat_closing(False, [], closing, "ABERTURA ANO 2024", kw))
		self.assertFalse(is_vat_closing(True, ["34531 - IVA Liquidado MN - ITEC"], closing, "apuramento", kw))
		self.assertFalse(
			is_vat_closing(False, ["45102 - Caixa"], closing, "Correcção ao lançamento da FR ALV2025/1229", kw)
		)


if __name__ == "__main__":
	unittest.main()
