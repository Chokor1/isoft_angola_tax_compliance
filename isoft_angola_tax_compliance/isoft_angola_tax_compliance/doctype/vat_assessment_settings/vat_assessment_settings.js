// Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

/*	The accounts are configured per company, so every Account link on a row must
	be restricted to that row's company — otherwise it is easy to pick another
	company's account, which the server then rejects on save.
*/
const SOURCE_ACCOUNT_FIELDS = [
	"deductible_vat_account",
	"output_vat_account",
	"withheld_vat_recoverable_account",
];

const DESTINATION_ACCOUNT_FIELDS = [
	"vat_assessment_account",
	"vat_payable_account",
	"vat_recoverable_account",
];

function row_account_query(frm, cdt, cdn, allow_group) {
	const row = locals[cdt][cdn];
	const filters = { company: row.company || "" };
	if (!allow_group) {
		// Journal entries cannot post to a group account.
		filters.is_group = 0;
	}
	return { filters: filters };
}

frappe.ui.form.on("VAT Assessment Settings", {
	setup: function (frm) {
		SOURCE_ACCOUNT_FIELDS.forEach(function (fieldname) {
			frm.set_query(fieldname, "company_accounts", function (doc, cdt, cdn) {
				return row_account_query(frm, cdt, cdn, true);
			});
		});

		DESTINATION_ACCOUNT_FIELDS.forEach(function (fieldname) {
			frm.set_query(fieldname, "company_accounts", function (doc, cdt, cdn) {
				return row_account_query(frm, cdt, cdn, false);
			});
		});

		frm.set_query("account", "adjustment_accounts", function (doc, cdt, cdn) {
			return row_account_query(frm, cdt, cdn, true);
		});
	},
});

frappe.ui.form.on("VAT Assessment Company Account", {
	company: function (frm, cdt, cdn) {
		// The accounts belong to the previous company, so clear them.
		SOURCE_ACCOUNT_FIELDS.concat(DESTINATION_ACCOUNT_FIELDS).forEach(function (fieldname) {
			frappe.model.set_value(cdt, cdn, fieldname, null);
		});
	},
});

frappe.ui.form.on("VAT Assessment Adjustment Account", {
	company: function (frm, cdt, cdn) {
		frappe.model.set_value(cdt, cdn, "account", null);
	},
});
