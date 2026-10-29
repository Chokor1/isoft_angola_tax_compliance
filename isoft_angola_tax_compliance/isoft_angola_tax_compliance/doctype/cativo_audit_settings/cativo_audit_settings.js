// Copyright (c) 2026, ISOFT and contributors
// For license information, please see license.txt

frappe.ui.form.on("Cativo Audit Settings", {
	refresh(frm) {
		frm.add_custom_button(__("Fill from Company Setup"), () => {
			frappe.call({
				method: "isoft_angola_tax_compliance.isoft_angola_tax_compliance.doctype.cativo_audit_settings.cativo_audit_settings.fill_from_company_setup",
				freeze: true,
				callback(r) {
					const have = new Set((frm.doc.accounts || []).map((row) => [row.company, row.account, row.purpose].join("|")));
					let added = 0;
					(r.message || []).forEach((row) => {
						if (have.has([row.company, row.account, row.purpose].join("|"))) return;
						Object.assign(frm.add_child("accounts"), row);
						added += 1;
					});
					frm.refresh_field("accounts");
					frappe.show_alert({message: __("{0} rows added. Review them and save.", [added]), indicator: added ? "green" : "blue"});
					if (added) frm.dirty();
				},
			});
		});
		frm.add_custom_button(__("Open Cativo Auditing"), () => frappe.set_route("cativo-auditing"));
	},
});
