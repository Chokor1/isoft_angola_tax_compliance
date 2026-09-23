// Copyright (c) 2026, ISOFT and contributors
// For license information, please see license.txt
//
// "Check Existing Customers" says what the customer-name rules would refuse
// before anybody is refused by them. Read-only: it changes nothing.

frappe.ui.form.on("Angola NIF Validation Settings", {
	refresh(frm) {
		frm.clear_custom_buttons();
		if (!frm.doc.validate_customer_name) return;

		frm.add_custom_button(__("Check Existing Customers"), () => {
			if (frm.is_dirty()) {
				frappe.msgprint(__("Save the settings first, so the check uses the rules on screen."));
				return;
			}
			frappe.call({
				method: "isoft_angola_tax_compliance.customer_name.scan_customer_names",
				freeze: true,
				freeze_message: __("Checking customer names..."),
				callback: (r) => show_result(r.message || {}),
			});
		});
	},

	// The button lives behind the switch, so the switch has to redraw it.
	validate_customer_name(frm) {
		frm.trigger("refresh");
	},
});

function show_result(result) {
	if (!result.enabled) {
		frappe.msgprint(__("Tick Validate Customer Name and save before running the check."));
		return;
	}
	const esc = frappe.utils.escape_html;
	const rows = result.rows || [];
	if (!result.total) {
		frappe.msgprint({
			title: __("Customer names"),
			indicator: "green",
			message: __("All {0} enabled customers pass these rules.", [result.checked]),
		});
		return;
	}
	const table = rows
		.map(
			(r) => `<tr>
				<td><a href="/app/customer/${encodeURIComponent(r.customer)}" target="_blank" rel="noopener">${esc(r.customer_name || r.customer)}</a></td>
				<td>${esc(r.customer_type || "")}</td>
				<td>${esc(r.message)}</td>
			</tr>`
		)
		.join("");
	frappe.msgprint({
		title: __("Customer names to fix"),
		indicator: "orange",
		message: `
			<p>${__("{0} of {1} enabled customers do not pass the current rules.", [result.total, result.checked])}
			${rows.length < result.total ? " " + __("The first {0} are listed.", [rows.length]) : ""}
			${__("With Enforcement set to Block, saving one of these customers would be refused until the name is corrected.")}</p>
			<div style="max-height:60vh;overflow:auto">
				<table class="table table-bordered small">
					<thead><tr><th>${__("Customer")}</th><th>${__("Type")}</th><th>${__("Problem")}</th></tr></thead>
					<tbody>${table}</tbody>
				</table>
			</div>`,
		wide: true,
	});
}
