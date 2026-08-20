// Copyright (c) 2026, ISOFT and contributors
// For license information, please see license.txt

frappe.ui.form.on('Angola Closing Voucher', {
	setup: function (frm) {
		// A profile carries real account numbers, so only this company's profiles
		// may be selected.
		frm.set_query("closing_configuration_profile", "profiles", function (doc) {
			return {
				filters: {
					company: doc.company
				}
			};
		});
	},

	onload: function (frm) {
		if (frm.is_new() && !frm.doc.posting_date) {
			frm.set_value("posting_date", frappe.datetime.get_today());
		}
	},

	company: function (frm) {
		// The selected profiles belong to the previous company.
		frm.clear_table("profiles");
		frm.refresh_field("profiles");
	},

	refresh: function (frm) {
		if (frm.doc.docstatus > 0) {
			frm.add_custom_button(__('Ledger'), function () {
				frappe.route_options = {
					"voucher_no": frm.doc.name,
					"from_date": frm.doc.posting_date,
					"to_date": moment(frm.doc.modified).format('YYYY-MM-DD'),
					"company": frm.doc.company,
					"group_by": "",
					"show_cancelled_entries": frm.doc.docstatus === 2
				};
				frappe.set_route("query-report", "General Ledger");
			}, "fa fa-table");
		}
	}
});
