// Copyright (c) 2024, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

frappe.ui.form.on('Closing Configuration Profile', {
	setup: function (frm) {
		// Accounts are company-specific, so both ends of the profile are filtered
		// to the profile's own company.
		frm.set_query("destination_account", function () {
			return {
				filters: [
					['Account', 'company', '=', frm.doc.company],
					['Account', 'is_group', '=', '0'],
					['Account', 'freeze_account', '=', 'No'],
				]
			};
		});

		frm.set_query("account", "source_accounts", function () {
			return {
				filters: [
					['Account', 'company', '=', frm.doc.company],
					['Account', 'freeze_account', '=', 'No'],
				]
			};
		});
	},

	company: function (frm) {
		// The accounts belong to the previous company.
		frm.set_value("destination_account", null);
		frm.clear_table("source_accounts");
		frm.refresh_field("source_accounts");
	}
});
