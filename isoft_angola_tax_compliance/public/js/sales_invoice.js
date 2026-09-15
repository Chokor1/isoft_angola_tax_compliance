// Copyright (c) 2026, ISOFT and contributors
// For license information, please see license.txt
//
// Live withholding preview on Sales Invoice. Shared logic lives in
// withholding_preview.js (loaded desk-wide via app_include_js).

isoft_atc.bind("Sales Invoice", "Sales Invoice Item", [
	"customer",
	"company",
	"posting_date",
	"currency",
	"conversion_rate",
	"taxes_and_charges",
	"is_pos",
	"is_return",
]);

// Credit notes: swap the income account of each line for the one configured in
// Return Account Settings, as soon as the new return opens. The server applies
// the same mapping on validate, so this is presentation only.
frappe.ui.form.on("Sales Invoice", {
	onload_post_render(frm) {
		if (!frm.is_new() || !cint(frm.doc.is_return) || !frm.doc.company) return;
		frappe.call({
			method: "isoft_angola_tax_compliance.returns.get_return_account_map",
			args: { doctype: frm.doc.doctype, company: frm.doc.company },
			callback(r) {
				const map = r.message || {};
				let changed = false;
				(frm.doc.items || []).forEach((item) => {
					const target = map[item.income_account];
					if (target && item.income_account !== target) {
						frappe.model.set_value(item.doctype, item.name, "income_account", target);
						changed = true;
					}
				});
				if (changed) frm.refresh_field("items");
			},
		});
	},
});
