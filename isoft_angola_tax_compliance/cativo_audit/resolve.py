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


SOURCES = ("engine", "legacy_je", "manual_je", "manual_pe")
SYSTEM = ("engine", "legacy_je")
TAGS = {"engine": "invoice", "legacy_je": "automatic JE", "manual_je": "manual JE", "manual_pe": "receipt"}


def _in_period(date: Optional[str], from_date: Optional[str], to_date: Optional[str]) -> bool:
	date = (date or "")[:10]
	return (not from_date or date >= str(from_date)[:10]) and (not to_date or date <= str(to_date)[:10])


def _rate(pct: Optional[float]) -> str:
	if pct is None:
		return ""
	if abs(pct - 1) <= 0.01:
		return "100%"
	if abs(pct - 0.5) <= 0.01:
		return "50%"
	return "Other"


def build_rows(moves: List[dict], invoices: dict, from_date: Optional[str], to_date: Optional[str]) -> List[dict]:
	"""One row per invoice dated in the period, with every cativo movement it has.

	An invoice in the period brings all its cativo, whatever the movements' own dates.
	"""
	by_invoice = {}
	for m in moves:
		if m.get("invoice"):
			by_invoice.setdefault(m["invoice"], []).append(m)

	rows = []
	for name, ms in by_invoice.items():
		invoice = invoices.get(name)
		if not invoice or not _in_period(invoice["posting_date"], from_date, to_date):
			continue

		sums = {s: round(sum(m["amount"] for m in ms if m["source"] == s), 2) for s in SOURCES}
		system = sum(sums[s] for s in SYSTEM)
		manual = sums["manual_je"] + sums["manual_pe"]
		cativo = round(system + manual, 2)
		iva = round(invoice.get("iva") or 0, 2)
		pct = cativo / iva if iva else None
		has_system, has_manual = abs(system) > ZERO, abs(manual) > ZERO
		manual_seen = any(m["source"] in ("manual_je", "manual_pe") for m in ms)

		if has_system and has_manual:
			creation = "System + Manual"
		elif has_system:
			creation = "System"
		elif has_manual:
			creation = "Manual"
		else:
			creation = "Reversed"

		alerts = []
		if has_system and has_manual:
			alerts.append("System and manual cativo on the same invoice")
		if manual_seen and not has_system and not has_manual:
			alerts.append("Manual cativo reversed (net zero)")
		if int(invoice.get("docstatus") or 0) == 2:
			alerts.append("Cancelled invoice with active cativo")
		if pct is not None and abs(cativo) > ZERO and _rate(pct) == "Other":
			alerts.append("Cativo = %.1f%% of IVA" % (pct * 100))
		if not iva and abs(cativo) > ZERO:
			alerts.append("Invoice without IVA")
		if any("ERP link differs" in (m.get("link") or "") for m in ms):
			alerts.append("ERP link differs from the note")

		documents, seen = [], set()
		for m in sorted(ms, key=lambda m: (m.get("posting_date") or "", m["voucher"])):
			if (m["voucher_type"], m["voucher"]) in seen:
				continue
			seen.add((m["voucher_type"], m["voucher"]))
			documents.append({"voucher": m["voucher"], "doctype": m["voucher_type"], "tag": TAGS[m["source"]]})

		rows.append(
			{
				"invoice": name,
				"date": invoice["posting_date"],
				"customer": invoice.get("customer_name") or invoice.get("customer"),
				"nif": invoice.get("tax_id") or "",
				"status": "Cancelled" if int(invoice.get("docstatus") or 0) == 2 else "Submitted",
				"iva": iva,
				"engine": sums["engine"],
				"legacy_je": sums["legacy_je"],
				"manual_je": sums["manual_je"],
				"manual_pe": sums["manual_pe"],
				"cativo": cativo,
				"pct": pct,
				"rate": _rate(pct),
				"creation": creation,
				"documents": documents,
				"category": invoice.get("category") or "No IVA category",
				"alerts": alerts,
			}
		)
	return sorted(rows, key=lambda r: (r["date"], r["invoice"]))


def unassigned(moves: List[dict], from_date: Optional[str], to_date: Optional[str]) -> dict:
	"""Cativo no invoice could be found for, posted in the period."""
	entries = [
		{
			"voucher": m["voucher"],
			"voucher_type": m["voucher_type"],
			"posting_date": m["posting_date"],
			"customer": m.get("customer") or "",
			"amount": round(m["amount"], 2),
			"reason": m.get("link") or "",
		}
		for m in moves
		if not m.get("invoice") and _in_period(m.get("posting_date"), from_date, to_date)
	]
	entries.sort(key=lambda e: (e["posting_date"], e["voucher"]))
	return {"amount": round(sum(e["amount"] for e in entries), 2), "count": len(entries), "entries": entries}
