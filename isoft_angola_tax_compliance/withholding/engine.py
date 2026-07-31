# Copyright (c) 2026, ISOFT and contributors
# For license information, please see license.txt
"""Computes the withholding rows for a Sales Invoice.

One engine covers both Angolan regimes, because they are the same mechanism
with a different base:

    Retencao na Fonte (II)   base_type = "Item Net Amount"   scope = Item Based
    IVA Cativo               base_type = "Tax Amount"        scope = Party Based

Everything that differs between them -- the rate, the validity window, the
account, the SAF-T type, the base -- is configuration on the Tax Withholding
Category, not code. A change in the law is a new dated rate row.

Neither regime changes `grand_total`: SAF-T and the AGT payload must carry the
full document value, with the withholding reported in its own block. What the
withholding changes is the receivable, and that is done in gl.py.
"""

import frappe
from frappe import _
from frappe.utils import flt, getdate

from isoft_angola_tax_compliance.withholding import resolver
from isoft_angola_tax_compliance.withholding.settings import applies_to_document

# Custom fields this app adds to Tax Withholding Category.
TYPE_FIELD = "atc_withholding_type"
BASE_TYPE_FIELD = "atc_base_type"
APPLIES_TO_FIELD = "atc_applies_to"
BASE_ACCOUNTS_FIELD = "atc_base_tax_accounts"

BASE_ITEM_NET = "Item Net Amount"
BASE_TAX_AMOUNT = "Tax Amount"
BASE_GRAND_TOTAL = "Grand Total"

SCOPE_ITEM = "Item Based"
SCOPE_PARTY = "Party Based"

# Bases are evaluated in this order so the sequencing is explicit rather than
# accidental. Today II (pre-VAT) and IVA cativo (the VAT) do not interact, but
# anything added on Grand Total would depend on ordering.
BASE_ORDER = {BASE_ITEM_NET: 0, BASE_TAX_AMOUNT: 1, BASE_GRAND_TOTAL: 2}


def evaluate(doc):
	"""Return {"rows": [...], "messages": [...]} for a Sales Invoice.

	Never raises: a misconfiguration is returned as a message rather than an
	exception, so the caller decides how to surface it. `apply.set_withholdings`
	throws, because booking a silently wrong amount on a fiscal document is
	worse than refusing to submit.
	"""
	result = {"rows": [], "messages": []}

	if not applies_to_document(doc):
		return result

	if not resolver.get_customer(doc):
		# No resolvable customer -- e.g. a Quotation addressed to a Lead. Every
		# claim this engine makes is about what a specific customer withholds,
		# so without one we compute nothing. Returning only the item-based half
		# would be worse than returning nothing: the party-based regimes (IVA
		# cativo) cannot be evaluated, so the total would be silently short.
		return result

	posting_date = getdate(resolver.get_posting_date(doc))
	precision = doc.precision("grand_total") or 2

	candidates = {}

	for category in resolver.get_party_categories(doc):
		candidates.setdefault(category, {"category": category, "items": [], "party": True})

	for category, items in resolver.get_item_categories(doc).items():
		entry = candidates.setdefault(category, {"category": category, "items": [], "party": False})
		entry["items"] = items

	rows = []
	for category in candidates:
		row, message = _evaluate_category(doc, candidates[category], posting_date, precision)
		if message:
			result["messages"].append(message)
		if row:
			rows.append(row)

	rows.sort(key=lambda r: (BASE_ORDER.get(r["_base_type"], 99), r["tax_withholding_category"]))
	for row in rows:
		row.pop("_base_type", None)

	result["rows"] = rows
	return result


def _evaluate_category(doc, candidate, posting_date, precision):
	category = candidate["category"]

	try:
		config = get_category_config(category)
	except frappe.DoesNotExistError:
		return None, _("Tax Withholding Category {0} does not exist.").format(category)

	scope = config.get("applies_to")
	if scope == SCOPE_PARTY and not candidate["party"]:
		# Party-scoped category reached us via an item; ignore it.
		return None, None
	if scope == SCOPE_ITEM and not candidate["items"]:
		return None, None

	rate_row = get_rate_row(config, posting_date)
	if not rate_row:
		return None, _("No rate in force on {0} for Tax Withholding Category {1}.").format(
			posting_date, category
		)

	account = get_account(config, doc.company)
	if not account:
		return None, _("No account set for Tax Withholding Category {0} against Company {1}.").format(
			category, doc.company
		)

	base_type = config.get("base_type") or BASE_ITEM_NET
	taxable, base_taxable, message = _get_base(doc, config, candidate, base_type)
	if message:
		return None, message
	if not base_taxable:
		return None, None

	skip = _threshold_blocks(doc, config, rate_row, base_taxable, posting_date)
	if skip:
		return None, None

	rate = flt(rate_row.get("tax_withholding_rate"))
	if not rate:
		return None, None

	# Rounded independently per currency; the receivable reduction in gl.py is
	# then the sum of these rounded rows, never a separately rounded total.
	base_withholding = _round(base_taxable * rate / 100.0, precision, config)
	withholding = _round(taxable * rate / 100.0, precision, config)

	if not base_withholding:
		return None, None

	return (
		{
			"tax_withholding_category": category,
			"withholding_type": config.get("withholding_type") or "Other",
			"description": config.get("description") or category,
			"rate": rate,
			"account_head": account,
			"cost_center": doc.get("cost_center"),
			"currency": doc.get("currency"),
			"taxable_amount": flt(taxable, precision),
			"base_taxable_amount": flt(base_taxable, precision),
			"withholding_amount": withholding,
			"base_withholding_amount": base_withholding,
			"_base_type": base_type,
		},
		None,
	)


def _get_base(doc, config, candidate, base_type):
	"""Return (amount in transaction currency, amount in company currency, message)."""
	if base_type == BASE_ITEM_NET:
		taxable = sum(flt(i.get("net_amount")) for i in candidate["items"])
		base_taxable = sum(flt(i.get("base_net_amount")) for i in candidate["items"])
		return taxable, base_taxable, None

	if base_type == BASE_TAX_AMOUNT:
		accounts = {
			r.get("account")
			for r in config.get("base_tax_accounts") or []
			if r.get("company") == doc.company and r.get("account")
		}
		if not accounts:
			return (
				0,
				0,
				_(
					"Tax Withholding Category {0} uses the 'Tax Amount' base but has no tax account "
					"head configured for Company {1}."
				).format(config["name"], doc.company),
			)

		taxable = sum(flt(t.get("tax_amount")) for t in doc.get("taxes") or [] if t.get("account_head") in accounts)
		base_taxable = sum(
			flt(t.get("base_tax_amount")) for t in doc.get("taxes") or [] if t.get("account_head") in accounts
		)
		return taxable, base_taxable, None

	if base_type == BASE_GRAND_TOTAL:
		return flt(doc.get("grand_total")), flt(doc.get("base_grand_total")), None

	return 0, 0, _("Unknown base type {0} on Tax Withholding Category {1}.").format(
		base_type, config["name"]
	)


def _threshold_blocks(doc, config, rate_row, base_taxable, posting_date):
	"""True when a configured threshold has not been reached yet."""
	single = flt(rate_row.get("single_threshold"))
	cumulative = flt(rate_row.get("cumulative_threshold"))

	if single and abs(base_taxable) < single:
		return True

	if cumulative:
		previous = _get_cumulative_base(
			doc, config["name"], rate_row.get("from_date"), rate_row.get("to_date")
		)
		if abs(previous) + abs(base_taxable) < cumulative:
			return True

	return False


def _get_cumulative_base(doc, category, from_date, to_date):
	"""Base already accumulated for this customer + category inside the rate window.

	Only counts invoices booked by this engine. Invoices predating the app are
	not included, so a cumulative threshold spanning the cutover will start
	from zero -- deliberate.
	"""
	customer = resolver.get_customer(doc)
	if not (from_date and to_date and customer):
		return 0

	rows = frappe.db.sql(
		"""
		SELECT COALESCE(SUM(w.base_taxable_amount), 0) AS total
		FROM `tabSales Invoice Withholding` w
		INNER JOIN `tabSales Invoice` si ON si.name = w.parent
		WHERE w.parenttype = 'Sales Invoice'
			AND w.tax_withholding_category = %(category)s
			AND si.customer = %(customer)s
			AND si.company = %(company)s
			AND si.docstatus = 1
			AND si.name != %(name)s
			AND si.posting_date BETWEEN %(from_date)s AND %(to_date)s
		""",
		{
			"category": category,
			"customer": customer,
			"company": doc.company,
			"name": doc.name or "",
			"from_date": from_date,
			"to_date": to_date,
		},
		as_dict=True,
	)

	return flt(rows[0].total) if rows else 0


def _round(amount, precision, config):
	if config.get("round_off_tax_amount"):
		return flt(round(flt(amount)), 0)

	return flt(amount, precision)


def get_category_config(category):
	"""Flatten a Tax Withholding Category (core fields + this app's custom fields)."""
	doc = frappe.get_cached_doc("Tax Withholding Category", category)

	base_type = doc.get(BASE_TYPE_FIELD) or BASE_ITEM_NET
	applies_to = doc.get(APPLIES_TO_FIELD)
	if not applies_to:
		# Sensible inference for categories configured before these fields existed.
		applies_to = SCOPE_ITEM if base_type == BASE_ITEM_NET else SCOPE_PARTY

	return {
		"name": doc.name,
		"description": doc.get("category_name") or doc.name,
		"withholding_type": doc.get(TYPE_FIELD),
		"base_type": base_type,
		"applies_to": applies_to,
		"base_tax_accounts": [r.as_dict() for r in doc.get(BASE_ACCOUNTS_FIELD) or []],
		"round_off_tax_amount": doc.get("round_off_tax_amount"),
		"rates": [r.as_dict() for r in doc.get("rates") or []],
		"accounts": [r.as_dict() for r in doc.get("accounts") or []],
	}


def get_rate_row(config, posting_date):
	posting_date = getdate(posting_date)
	for rate in config.get("rates") or []:
		if not (rate.get("from_date") and rate.get("to_date")):
			continue
		if getdate(rate["from_date"]) <= posting_date <= getdate(rate["to_date"]):
			return rate

	return None


def get_account(config, company):
	for row in config.get("accounts") or []:
		if row.get("company") == company:
			return row.get("account")

	return None
