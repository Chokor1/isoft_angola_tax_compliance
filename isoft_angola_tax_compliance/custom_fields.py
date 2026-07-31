# Copyright (c) 2026, ISOFT and contributors
# For license information, please see license.txt
"""Custom Field definitions owned by this app.

All fieldnames are prefixed `atc_` so they can never collide with the existing
core fields on this ERPNext fork. That matters for the cleanup phase: the
legacy DocFields (`total_tax_withholding_amount`, `apply_tax_withholding_on_service`,
`enable_vat_exemption`, ...) can be removed from core later without this app
needing to claim their names, and Frappe rejects a Custom Field whose fieldname
already exists as a DocField.
"""

import frappe

WITHHOLDING_TYPES = "\nII\nIVA\nIRT\nOther"
BASE_TYPES = "Item Net Amount\nTax Amount\nGrand Total"
SCOPES = "\nItem Based\nParty Based"


def get_custom_fields():
	return {
		"Tax Withholding Category": [
			{
				"fieldname": "atc_section",
				"fieldtype": "Section Break",
				"label": "Angola Tax Compliance",
				"insert_after": _anchor("Tax Withholding Category", ["round_off_tax_amount"]),
			},
			{
				"fieldname": "atc_withholding_type",
				"fieldtype": "Select",
				"label": "Withholding Type",
				"options": WITHHOLDING_TYPES,
				"insert_after": "atc_section",
				"description": "Emitted as SAF-T WithholdingTaxType and in the AGT withholdingTaxList.",
			},
			{
				"fieldname": "atc_base_type",
				"fieldtype": "Select",
				"label": "Withholding Base",
				"options": BASE_TYPES,
				"default": "Item Net Amount",
				"insert_after": "atc_withholding_type",
				"description": (
					"Item Net Amount: retencao na fonte on service lines. "
					"Tax Amount: IVA cativo, a percentage of the VAT charged. "
					"Grand Total: percentage of the whole document."
				),
			},
			{
				"fieldname": "atc_column_break",
				"fieldtype": "Column Break",
				"insert_after": "atc_base_type",
			},
			{
				"fieldname": "atc_applies_to",
				"fieldtype": "Select",
				"label": "Scope",
				"options": SCOPES,
				"insert_after": "atc_column_break",
				"description": (
					"Item Based: resolved per invoice line via item / item group. "
					"Party Based: driven by the customer, regardless of what is sold."
				),
			},
			{
				"fieldname": "atc_base_accounts_section",
				"fieldtype": "Section Break",
				"label": "Base Tax Accounts",
				"insert_after": "atc_applies_to",
				"depends_on": "eval:doc.atc_base_type=='Tax Amount'",
				"description": (
					"Which tax account heads the withholding is calculated on. Scoping this "
					"to the VAT accounts is what stops freight or imposto de selo rows from "
					"being withheld too."
				),
			},
			{
				"fieldname": "atc_base_tax_accounts",
				"fieldtype": "Table",
				"label": "Base Tax Accounts",
				"options": "Angola Withholding Base Account",
				"insert_after": "atc_base_accounts_section",
				"depends_on": "eval:doc.atc_base_type=='Tax Amount'",
			},
			{
				"fieldname": "atc_auto_assign_section",
				"fieldtype": "Section Break",
				"label": "Automatic Item Assignment",
				"insert_after": "atc_base_tax_accounts",
				"depends_on": "eval:doc.atc_applies_to=='Item Based'",
				"collapsible": 1,
				"description": (
					"Stamps this category onto new items that match. Existing items are "
					"left alone -- backfill them once with "
					"<code>isoft_angola_tax_compliance.auto_assign.backfill</code>."
				),
			},
			{
				"fieldname": "atc_auto_assign",
				"fieldtype": "Check",
				"label": "Auto-assign to New Items",
				"default": "0",
				"insert_after": "atc_auto_assign_section",
				"depends_on": "eval:doc.atc_applies_to=='Item Based'",
			},
			{
				"fieldname": "atc_auto_assign_non_stock",
				"fieldtype": "Check",
				"label": "All Non-Stock Items",
				"default": "0",
				"insert_after": "atc_auto_assign",
				"depends_on": "eval:doc.atc_auto_assign",
				"description": (
					"Matches every item with <code>is_stock_item = 0</code>. Broad: a "
					"non-stock item is not necessarily a service. An item group rule "
					"below is more precise and takes precedence."
				),
			},
			{
				"fieldname": "atc_auto_assign_item_groups",
				"fieldtype": "Table",
				"label": "Item Groups",
				"options": "Angola Withholding Item Group",
				"insert_after": "atc_auto_assign_non_stock",
				"depends_on": "eval:doc.atc_auto_assign",
			},
		],
		"Customer": [
			# Unlabelled section break, not a titled section: a Table rendered
			# inside the second column of the basic-info block comes out at half
			# width. This gives the grid the full row, directly under the party
			# and tax details, with no heading of its own.
			{
				"fieldname": "atc_withholding_section",
				"fieldtype": "Section Break",
				"insert_after": _anchor(
					"Customer", ["represents_company", "tax_category", "tax_id"]
				),
				# Set explicitly, not merely omitted: create_custom_fields updates an
				# existing record with `doc.update(df)`, which only writes the keys
				# present. Leaving these out would keep the old heading and the
				# collapsed state on sites that already have this field.
				"label": "",
				"collapsible": 0,
			},
			{
				"fieldname": "atc_withholdings",
				"fieldtype": "Table",
				"label": "Withholdings (Retenção / IVA Cativo)",
				"options": "Party Tax Withholding",
				"insert_after": "atc_withholding_section",
				"description": "Several regimes can apply at once. Close Valid To rather than deleting a row.",
			},
		],
		"Item": [
			{
				"fieldname": "atc_tax_withholding_category",
				"fieldtype": "Link",
				"label": "Withholding Category (Angola)",
				"options": "Tax Withholding Category",
				"insert_after": _anchor("Item", ["tax_withholding_category", "is_stock_item"]),
			}
		],
		"Item Group": [
			{
				"fieldname": "atc_tax_withholding_category",
				"fieldtype": "Link",
				"label": "Withholding Category (Angola)",
				"options": "Tax Withholding Category",
				"insert_after": _anchor("Item Group", ["is_group"]),
				"description": "Inherited by items in this group and its children.",
			}
		],
		"POS Profile": [
			{
				"fieldname": "atc_withholding_section",
				"fieldtype": "Section Break",
				"label": "Angolan Withholding",
				"insert_after": _anchor("POS Profile", ["account_for_change_amount", "company"]),
				"collapsible": 1,
			},
			{
				"fieldname": "atc_enable_withholding",
				"fieldtype": "Check",
				"label": "Enable Withholding (Retenção / IVA Cativo)",
				"default": "0",
				"insert_after": "atc_withholding_section",
				"description": (
					"When off, POS invoices from this profile never withhold, and customers "
					"subject to a withholding regime are hidden from the POS customer list. "
					"When on, the engine computes retenção / IVA cativo on POS invoices and "
					"the amount due is reduced accordingly."
				),
			},
		],
		"Sales Invoice": [
			{
				"fieldname": "atc_withholding_section",
				"fieldtype": "Section Break",
				"label": "Withholding (Retencao / IVA Cativo)",
				"insert_after": _anchor(
					"Sales Invoice",
					["total_tax_withholding_amount", "base_grand_total", "grand_total"],
				),
				"collapsible": 1,
				"collapsible_depends_on": "atc_withholdings",
			},
			{
				"fieldname": "atc_withholdings",
				"fieldtype": "Table",
				"label": "Withholdings",
				"options": "Sales Invoice Withholding",
				"insert_after": "atc_withholding_section",
				"read_only": 1,
				"no_copy": 1,
			},
			{
				"fieldname": "atc_total_withholding_amount",
				"fieldtype": "Currency",
				"label": "Total Withholding",
				"options": "currency",
				"insert_after": "atc_withholdings",
				"read_only": 1,
				"no_copy": 1,
			},
			{
				"fieldname": "atc_column_break_withholding",
				"fieldtype": "Column Break",
				"insert_after": "atc_total_withholding_amount",
			},
			{
				"fieldname": "atc_net_amount_receivable",
				"fieldtype": "Currency",
				"label": "Net Amount Receivable",
				"options": "currency",
				"insert_after": "atc_column_break_withholding",
				"read_only": 1,
				"no_copy": 1,
				"description": "Grand Total less withholding. The document total itself is unchanged.",
			},
			{
				"fieldname": "atc_base_total_withholding_amount",
				"fieldtype": "Currency",
				"label": "Total Withholding (Company Currency)",
				"insert_after": "atc_net_amount_receivable",
				"read_only": 1,
				"no_copy": 1,
			},
			{
				"fieldname": "atc_engine",
				"fieldtype": "Data",
				"label": "Withholding Engine",
				"insert_after": "atc_base_total_withholding_amount",
				"read_only": 1,
				"hidden": 1,
				"no_copy": 1,
				"print_hide": 1,
			},
			# --- legacy references, kept for print formats only ---------------
			# These were DocFields on the ERPNext fork. The legacy code that read
			# and wrote them is gone; the engine now keeps them in sync purely so
			# existing print formats keep rendering. Nothing calculates from them.
			{
				"fieldname": "apply_tax_withholding_on_service",
				"fieldtype": "Check",
				"label": "Apply Tax Withholding On Service (legacy)",
				"insert_after": "atc_engine",
				"read_only": 1,
				"hidden": 1,
				"no_copy": 1,
				"description": "Reference only. Set by the withholding engine; never read by it.",
			},
			{
				"fieldname": "total_tax_withholding_amount",
				"fieldtype": "Currency",
				"label": "Total Tax Withholding Amount (legacy)",
				"options": "currency",
				"insert_after": "apply_tax_withholding_on_service",
				"read_only": 1,
				"hidden": 1,
				"no_copy": 1,
				"description": "Reference only, retenção II portion. Mirrors atc_total_withholding_amount.",
			},
		],
		# Quotation carries the same information for quoting purposes only. It
		# posts no GL, so these are purely what-the-customer-will-pay figures.
		"Quotation": [
			{
				"fieldname": "atc_withholding_section",
				"fieldtype": "Section Break",
				"label": "Withholding (Retenção / IVA Cativo)",
				"insert_after": _anchor("Quotation", ["base_grand_total", "grand_total"]),
				"collapsible": 1,
				"collapsible_depends_on": "atc_withholdings",
			},
			{
				"fieldname": "atc_withholdings",
				"fieldtype": "Table",
				"label": "Expected Withholdings",
				"options": "Sales Invoice Withholding",
				"insert_after": "atc_withholding_section",
				"read_only": 1,
				"no_copy": 1,
				"description": "Indicative only. Nothing is booked until the invoice is raised.",
			},
			{
				"fieldname": "atc_total_withholding_amount",
				"fieldtype": "Currency",
				"label": "Total Withholding",
				"options": "currency",
				"insert_after": "atc_withholdings",
				"read_only": 1,
				"no_copy": 1,
			},
			{
				"fieldname": "atc_column_break_withholding",
				"fieldtype": "Column Break",
				"insert_after": "atc_total_withholding_amount",
			},
			{
				"fieldname": "atc_net_amount_receivable",
				"fieldtype": "Currency",
				"label": "Net Amount Payable by Customer",
				"options": "currency",
				"insert_after": "atc_column_break_withholding",
				"read_only": 1,
				"no_copy": 1,
				"description": "Grand Total less the amount the customer withholds at source.",
			},
			{
				"fieldname": "atc_base_total_withholding_amount",
				"fieldtype": "Currency",
				"label": "Total Withholding (Company Currency)",
				"insert_after": "atc_net_amount_receivable",
				"read_only": 1,
				"no_copy": 1,
			},
			{
				"fieldname": "atc_engine",
				"fieldtype": "Data",
				"label": "Withholding Engine",
				"insert_after": "atc_base_total_withholding_amount",
				"read_only": 1,
				"hidden": 1,
				"no_copy": 1,
				"print_hide": 1,
			},
		],
		"Quotation Item": [
			{
				"fieldname": "atc_withholding_category",
				"fieldtype": "Link",
				"label": "Withholding Category (Angola)",
				"options": "Tax Withholding Category",
				"insert_after": _anchor("Quotation Item", ["item_tax_rate", "item_code"]),
			},
			{
				"fieldname": "atc_withholding_amount",
				"fieldtype": "Currency",
				"label": "Withholding Amount",
				"insert_after": "atc_withholding_category",
				"read_only": 1,
				"no_copy": 1,
				"print_hide": 1,
			},
		],
		"Sales Invoice Item": [
			{
				"fieldname": "atc_withholding_category",
				"fieldtype": "Link",
				"label": "Withholding Category (Angola)",
				"options": "Tax Withholding Category",
				"insert_after": _anchor(
					"Sales Invoice Item", ["tax_withholding_category", "item_tax_rate", "item_code"]
				),
			},
			{
				"fieldname": "atc_withholding_amount",
				"fieldtype": "Currency",
				"label": "Withholding Amount",
				"insert_after": "atc_withholding_category",
				"read_only": 1,
				"no_copy": 1,
				"print_hide": 1,
			},
		],
	}


def _anchor(doctype, candidates):
	"""First candidate that actually exists, so install works on any fork state."""
	meta = frappe.get_meta(doctype)
	for fieldname in candidates:
		if meta.get_field(fieldname):
			return fieldname

	return candidates[-1]
