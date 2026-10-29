# Copyright (c) 2026, ISOFT and contributors
# For license information, please see license.txt
"""The rules behind Cativo Auditing, kept free of frappe so they can be tested alone.

Cativo reaches the cativo accounts four ways: the withholding engine on the invoice
itself, the old tool's automatic journal entry, a manual journal entry, and a receipt
paid into the cativo account. The first two always know their invoice. The last two
often do not: a manual journal entry's ERP link is usually where a reconciliation put
the customer credit, while the invoice the cativo is for is only written in its note
("Iva Cativo FT2026/41"). These functions decide which invoice each movement belongs to.
"""

import re
import unicodedata
from typing import List, Optional, Set, Tuple

# "FT2026/38", "FR VIA2025/5372", "FT FT3526S17184N/1660", "FT398", "NC106"
TOKEN_RE = re.compile(
	r"\b(FT|FR|NC)\s*([A-Z]{2,3}\d{4}|\d{4}|FT\d+S\d+N|FR\d+S\d+N|NC\d+S\d+N)?\s*/?\s*(\d+)\b", re.I
)
# "Iva Cativo do recebimento RG RG9726S2895N/8"
RECEIPT_RE = re.compile(r"\b(?:RG|REC|RC) [A-Z0-9]+/\d+")
# "Valor estava cativo no CBL2026/3278"
VOUCHER_RE = re.compile(r"\b(?:CBL|REC|RC|RG|RE)\s?[\w/-]+")

ZERO = 0.005


def normalise(text: str) -> str:
	"""Lower case without accents, so "Apuramento" and "APURAMENTO" match alike."""
	decomposed = unicodedata.normalize("NFD", text or "")
	return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


def note_tokens(text: str) -> List[Tuple[str, str, str]]:
	"""Invoice references written in a note, as (kind, series, number)."""
	return [(kind.upper(), (series or "").upper(), number) for kind, series, number in TOKEN_RE.findall(text or "")]


def candidate_patterns(token: Tuple[str, str, str]) -> List[Tuple[str, str]]:
	"""Invoice names a token may mean: ("exact", name) or ("like", SQL pattern).

	The customer match made by the caller picks between them.
	"""
	kind, series, number = token
	if series and not series.isdigit():
		return [("exact", "%s %s/%s" % (kind, series, number))]
	if series:
		out = [("exact", "%s %s/%s" % (kind, series, number))]
		# From 2026 the same number may be the AGT series ("FT FT3526S17184N/38")
		if series >= "2026":
			out.append(("like", "%s %s%%N/%s" % (kind, kind, number)))
		return out
	return [("like", "%s %%/%s" % (kind, number))]


def pick_invoice(candidates: List[str], erp_refs: List[str]) -> Tuple[Optional[str], str]:
	"""Choose a manual journal entry's invoice from its note candidates and ERP links.

	The note wins over the ERP link; an ambiguous note is only settled by an ERP link
	that is one of its candidates.
	"""
	candidates = list(dict.fromkeys(candidates))
	ref = erp_refs[0] if erp_refs else None

	if len(candidates) == 1:
		if ref and ref != candidates[0]:
			return candidates[0], "From the note (ERP link differs: %s)" % ref
		return candidates[0], "From the note"

	if len(candidates) > 1:
		if ref in candidates:
			return ref, "ERP link (ambiguous note)"
		return None, "Ambiguous: %s" % ", ".join(candidates)

	if ref:
		return ref, "ERP link"
	return None, "Not identified"


def named_receipts(text: str) -> List[str]:
	"""Receipts a note names, e.g. "RG RG9726S2895N/8"."""
	return RECEIPT_RE.findall(text or "")


def named_vouchers(text: str) -> List[str]:
	"""Vouchers a note names, e.g. "CBL2026/3278" in "Valor estava cativo no CBL2026/3278"."""
	return VOUCHER_RE.findall(text or "")


def match_reversal(move: dict, moves: List[dict]) -> Tuple[Optional[str], str]:
	"""The invoice of the movement a negative, unassigned entry reverses.

	"Cliente pagou a totalidade da ft" names no invoice; it undoes an earlier cativo of
	the same customer and amount. A note that names the voucher it corrects wins.
	"""
	if move.get("invoice") or move["amount"] >= 0:
		return None, ""

	with_invoice = [m for m in moves if m is not move and m.get("invoice") and m["amount"] > 0]
	by_voucher = {}
	for m in with_invoice:
		by_voucher.setdefault(m["voucher"], m)

	for voucher in named_vouchers(move.get("note")):
		if voucher in by_voucher:
			return by_voucher[voucher]["invoice"], "Reverses %s (named in the note)" % voucher

	same = [
		m
		for m in with_invoice
		if m.get("customer") == move.get("customer")
		and abs(m["amount"] + move["amount"]) < ZERO
		and (m.get("posting_date") or "") <= (move.get("posting_date") or "")
	]
	if same and len({m["invoice"] for m in same}) == 1:
		src = max(same, key=lambda m: m.get("posting_date") or "")
		return src["invoice"], "Reverses %s (same customer and amount)" % src["voucher"]
	return None, ""


def is_vat_closing(
	has_customer_row: bool, other_accounts: List[str], closing_accounts: Set[str], note: str, keywords: List[str]
) -> bool:
	"""A monthly VAT closing that moves the cativo account but is no invoice's cativo."""
	if has_customer_row:
		return False
	if any(account in closing_accounts for account in other_accounts):
		return True
	text = normalise(note)
	return any(normalise(k).strip() and normalise(k).strip() in text for k in keywords)
