// Copyright (c) 2025, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

frappe.ui.form.on('Automation Settings', {
	onload: function (frm) {
		frm.fields_dict['customer_account_settings'].grid.get_field('parent_account').get_query = function (doc, cdt, cdn) {
			return {
				filters: {
					"is_group": 1,
				}
			};
		};
		frm.fields_dict['supplier_account_settings'].grid.get_field('parent_account').get_query = function (doc, cdt, cdn) {
			return {
				filters: {
					"is_group": 1,
				}
			};
		};
	}
});
