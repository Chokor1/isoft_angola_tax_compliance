// Copyright (c) 2026, ISOFT and contributors
// For license information, please see license.txt
//
// Accounts are configured per company, so every Account link on a row is
// restricted to that row's company. The note account must be a ledger.

function account_query(frm, cdt, cdn, ledger_only) {
	const row = locals[cdt][cdn];
	const filters = { company: row.company || "" };
	if (ledger_only) filters.is_group = 0;
	return { filters: filters };
}

frappe.ui.form.on("Return Account Settings", {
	setup(frm) {
		for (const table of ["sales_mappings", "purchase_mappings"]) {
			frm.set_query("invoice_account", table, (doc, cdt, cdn) => account_query(frm, cdt, cdn, false));
			frm.set_query("return_account", table, (doc, cdt, cdn) => account_query(frm, cdt, cdn, true));
		}
	},
});
