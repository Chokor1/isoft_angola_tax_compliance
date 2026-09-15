// Copyright (c) 2026, ISOFT and contributors
// For license information, please see license.txt
//
// Debit notes: swap the expense account of each line for the one configured
// in Return Account Settings, as soon as the new return opens. The server
// applies the same mapping on validate, so this is presentation only.

frappe.ui.form.on("Purchase Invoice", {
	onload_post_render(frm) {
		isoft_atc_apply_return_accounts(frm, "expense_account");
	},
});

function isoft_atc_apply_return_accounts(frm, field) {
	if (!frm.is_new() || !cint(frm.doc.is_return) || !frm.doc.company) return;
	frappe.call({
		method: "isoft_angola_tax_compliance.returns.get_return_account_map",
		args: { doctype: frm.doc.doctype, company: frm.doc.company },
		callback(r) {
			const map = r.message || {};
			let changed = false;
			(frm.doc.items || []).forEach((item) => {
				const target = map[item[field]];
				if (target && item[field] !== target) {
					frappe.model.set_value(item.doctype, item.name, field, target);
					changed = true;
				}
			});
			if (changed) frm.refresh_field("items");
		},
	});
}
