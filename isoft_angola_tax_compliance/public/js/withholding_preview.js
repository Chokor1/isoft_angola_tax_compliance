// Copyright (c) 2026, ISOFT and contributors
// For license information, please see license.txt
//
// Shared live preview of the withholding rows, used by both Sales Invoice and
// Quotation. The server-side validate remains authoritative -- this only mirrors
// what it would compute, so the user sees retencao / IVA cativo before saving.
//
// Values are assigned directly and refreshed rather than set with frm.set_value,
// so opening or editing a draft is never marked dirty by the preview itself.

frappe.provide("isoft_atc");

isoft_atc.FIELDS = {
	table: "atc_withholdings",
	total: "atc_total_withholding_amount",
	base_total: "atc_base_total_withholding_amount",
	net: "atc_net_amount_receivable",
	item_amount: "atc_withholding_amount",
};

isoft_atc.get_customer = function (frm) {
	// Sales Invoice has `customer`; Quotation has quotation_to + party_name and
	// may point at a Lead, which has no withholding configuration.
	if (frm.doc.customer) return frm.doc.customer;
	if (frm.doc.doctype === "Quotation") {
		if ((frm.doc.quotation_to || "Customer") !== "Customer") return null;
		return frm.doc.party_name;
	}
	return null;
};

isoft_atc.clear = function (frm) {
	const f = isoft_atc.FIELDS;
	frm.doc[f.table] = [];
	frm.doc[f.total] = 0;
	frm.doc[f.base_total] = 0;
	frm.doc[f.net] = flt(frm.doc.grand_total);
	(frm.doc.items || []).forEach((row) => {
		row[f.item_amount] = 0;
	});
	[f.table, f.total, f.base_total, f.net, "items"].forEach((name) => frm.refresh_field(name));
};

isoft_atc.apply = function (frm, message) {
	const f = isoft_atc.FIELDS;
	const child_doctype = "Sales Invoice Withholding";

	frm.doc[f.table] = [];
	(message.rows || []).forEach((row, index) => {
		const child = frappe.model.add_child(frm.doc, child_doctype, f.table);
		Object.assign(child, row);
		child.idx = index + 1;
	});

	frm.doc[f.total] = flt(message.total);
	frm.doc[f.base_total] = flt(message.base_total);
	frm.doc[f.net] = flt(message.net_receivable);

	const by_idx = {};
	(message.items || []).forEach((row) => {
		by_idx[row.idx] = flt(row.amount);
	});
	(frm.doc.items || []).forEach((row) => {
		row[f.item_amount] = by_idx[row.idx] || 0;
	});

	[f.table, f.total, f.base_total, f.net, "items"].forEach((name) => frm.refresh_field(name));

	(message.messages || []).forEach((text) => {
		frappe.show_alert({ message: text, indicator: "orange" }, 7);
	});
};

isoft_atc.refresh_withholding = frappe.utils.debounce(function (frm) {
	if (!frm || !frm.doc || frm.doc.docstatus !== 0) return;
	if (!frm.fields_dict[isoft_atc.FIELDS.table]) return;
	if (!frm.doc.company || !isoft_atc.get_customer(frm)) {
		isoft_atc.clear(frm);
		return;
	}

	frappe.call({
		method: "isoft_angola_tax_compliance.api.compute_withholdings",
		args: { doc: frm.doc },
		// no freeze: this fires while the user types
		callback: function (r) {
			if (!r || !r.message || frm.doc.docstatus !== 0) return;
			isoft_atc.apply(frm, r.message);
		},
	});
}, 500);

// Grid events shared by both parents: anything that moves a base.
isoft_atc.ITEM_TRIGGERS = [
	"item_code",
	"qty",
	"rate",
	"amount",
	"net_amount",
	"discount_percentage",
	"discount_amount",
	"atc_withholding_category",
	"items_remove",
];

isoft_atc.TAX_TRIGGERS = ["account_head", "rate", "tax_amount", "charge_type", "taxes_remove"];

isoft_atc.bind = function (parent_doctype, item_doctype, parent_triggers) {
	const handler = (frm) => isoft_atc.refresh_withholding(frm);

	const parent_map = {};
	(parent_triggers || []).forEach((name) => (parent_map[name] = handler));
	frappe.ui.form.on(parent_doctype, parent_map);

	const item_map = {};
	isoft_atc.ITEM_TRIGGERS.forEach((name) => (item_map[name] = handler));
	frappe.ui.form.on(item_doctype, item_map);

	const tax_map = {};
	isoft_atc.TAX_TRIGGERS.forEach((name) => (tax_map[name] = handler));
	frappe.ui.form.on("Sales Taxes and Charges", tax_map);
};
