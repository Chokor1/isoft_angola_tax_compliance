# Copyright (c) 2026, ISOFT and contributors
# For license information, please see license.txt
"""Cativo Auditing rules. Pure Python: run without a site.

    ../../env/bin/python -m unittest isoft_angola_tax_compliance.tests.test_cativo_resolve -v
"""

import unittest

from isoft_angola_tax_compliance.cativo_audit.resolve import (
	build_rows,
	candidate_patterns,
	is_vat_closing,
	match_reversal,
	named_receipts,
	note_tokens,
	pick_invoice,
	unassigned,
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


def mv(source, voucher, date, amount, invoice=None, link=""):
	types = {"engine": "Sales Invoice", "legacy_je": "Journal Entry", "manual_je": "Journal Entry", "manual_pe": "Payment Entry"}
	return dict(
		source=source, voucher_type=types[source], voucher=voucher, posting_date=date, account="34543 - Cativo",
		amount=amount, invoice=invoice, link=link, customer="C", note="", erp_refs=[],
	)


def inv(name, date, iva, docstatus=1):
	return dict(
		name=name, posting_date=date, customer="C", customer_name="Customer C", tax_id="5000000000",
		docstatus=docstatus, is_return=0, net_total=iva * 7, grand_total=iva * 8, iva=iva, category="IVA Cativo 100% - Estado",
	)


class TestRows(unittest.TestCase):
	def test_mixed_sources_and_other_rate(self):
		rows = build_rows(
			[mv("engine", "FT 1", "2025-03-01", 100, "FT 1"), mv("manual_pe", "REC 1", "2025-04-01", 50, "FT 1")],
			{"FT 1": inv("FT 1", "2025-03-01", 100)}, "2025-01-01", "2025-12-31",
		)
		r = rows[0]
		self.assertEqual((r["engine"], r["manual_pe"], r["cativo"]), (100, 50, 150))
		self.assertEqual(r["creation"], "System + Manual")
		self.assertEqual(r["rate"], "Other")
		self.assertIn("System and manual cativo on the same invoice", r["alerts"])
		self.assertIn("Cativo = 150.0% of IVA", r["alerts"])
		self.assertEqual([d["tag"] for d in r["documents"]], ["invoice", "receipt"])

	def test_rates(self):
		rows = build_rows(
			[mv("legacy_je", "RE 1", "2025-01-02", 50, "FT A"), mv("legacy_je", "RE 2", "2025-01-02", 100.4, "FT B")],
			{"FT A": inv("FT A", "2025-01-02", 100), "FT B": inv("FT B", "2025-01-02", 100)}, None, None,
		)
		self.assertEqual([r["rate"] for r in rows], ["50%", "100%"])
		self.assertEqual(rows[0]["alerts"], [])

	def test_reversed_manual(self):
		r = build_rows(
			[mv("manual_pe", "REC 1", "2025-04-24", 129124.8, "FT 535"), mv("manual_je", "CBL 1", "2026-01-28", -129124.8, "FT 535")],
			{"FT 535": inv("FT 535", "2025-04-20", 129124.8)}, "2025-01-01", "2025-12-31",
		)[0]
		self.assertEqual((r["cativo"], r["creation"]), (0, "Reversed"))
		self.assertIn("Manual cativo reversed (net zero)", r["alerts"])

	def test_cancelled_and_no_iva(self):
		r = build_rows([mv("legacy_je", "RE 9", "2025-06-01", 10, "FT 9")], {"FT 9": inv("FT 9", "2025-06-01", 0, docstatus=2)}, None, None)[0]
		self.assertEqual(r["status"], "Cancelled")
		self.assertIsNone(r["pct"])
		self.assertEqual(r["rate"], "")
		self.assertIn("Cancelled invoice with active cativo", r["alerts"])
		self.assertIn("Invoice without IVA", r["alerts"])

	def test_period_uses_invoice_date(self):
		moves = [mv("manual_je", "CBL 1", "2026-05-31", 24444, "FT 2025/1924")]
		invs = {"FT 2025/1924": inv("FT 2025/1924", "2025-12-15", 24444)}
		self.assertEqual(len(build_rows(moves, invs, "2025-01-01", "2025-12-31")), 1)
		self.assertEqual(build_rows(moves, invs, "2026-01-01", "2026-12-31"), [])
		self.assertEqual(unassigned(moves, "2026-01-01", "2026-12-31")["count"], 0)

	def test_erp_link_differs_alert(self):
		r = build_rows(
			[mv("manual_je", "CBL 2", "2026-05-31", 107100, "FT 41", link="From the note (ERP link differs: FT 2019)")],
			{"FT 41": inv("FT 41", "2026-02-06", 107100)}, None, None,
		)[0]
		self.assertIn("ERP link differs from the note", r["alerts"])

	def test_unassigned_by_own_date(self):
		moves = [
			mv("manual_pe", "RG 19", "2025-02-15", 7648136.16, None, link="Not allocated"),
			mv("manual_je", "RE 194", "2026-07-31", 1842105.26, None, link="Not identified"),
		]
		u = unassigned(moves, "2025-01-01", "2025-12-31")
		self.assertEqual((u["count"], u["amount"]), (1, 7648136.16))
		self.assertEqual(u["entries"][0]["reason"], "Not allocated")


if __name__ == "__main__":
	unittest.main()
