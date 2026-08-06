// Copyright (c) 2026, ISOFT and contributors
// For license information, please see license.txt

frappe.query_reports["Invalid NIF"] = {
	filters: [
		{
			fieldname: "customer_type",
			label: __("Customer Type"),
			fieldtype: "Select",
			options: ["", "Individual", "Company"],
		},
		{
			fieldname: "include_disabled",
			label: __("Include Disabled Customers"),
			fieldtype: "Check",
			default: 0,
		},
	],
	onload(report) {
		report.page.add_inner_button(__("Fix Customer Types"), () => atc_nif_fix_types(report));
	},
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "problem" && data) {
			const colour = data.problem === __("Wrong Customer Type") ? "orange" : "red";
			value = `<span style="color: var(--text-on-${colour}, ${colour})">${value}</span>`;
		}
		return value;
	},
};


// Bulk-correct Customer Type where the NIF is unambiguously valid for the other
// type. Malformed numbers are never touched -- they stay in the report.
function atc_nif_fix_types(report) {
	const include_disabled = report.get_filter_value("include_disabled") ? 1 : 0;

	frappe.call({
		method: "isoft_angola_tax_compliance.nif.get_type_fix_preview",
		args: { include_disabled },
		freeze: true,
		freeze_message: __("Checking which customers can be corrected…"),
		callback(r) {
			const preview = r && r.message;
			if (!preview) return;

			if (!preview.total) {
				frappe.msgprint({
					title: __("Nothing to correct"),
					indicator: "blue",
					message: __(
						"No customer has a NIF that is valid for the other type. " +
							"Anything left in this report needs the number itself fixed."
					),
				});
				return;
			}

			const directions = Object.keys(preview.by_direction)
				.map((k) => `<li><b>${frappe.utils.escape_html(k)}</b>: ${preview.by_direction[k]}</li>`)
				.join("");

			const rows = (preview.sample || [])
				.map(
					(f) => `<tr>
						<td>${frappe.utils.escape_html(f.customer)}</td>
						<td>${frappe.utils.escape_html(f.tax_id)}</td>
						<td>${frappe.utils.escape_html(f.from_type)}</td>
						<td><b>${frappe.utils.escape_html(f.to_type)}</b></td>
					</tr>`
				)
				.join("");

			const remaining = preview.total - (preview.sample || []).length;

			const dialog = new frappe.ui.Dialog({
				title: __("Fix Customer Types"),
				size: "large",
				fields: [
					{
						fieldtype: "HTML",
						fieldname: "preview",
						options: `
							<p>${__("<b>{0}</b> customer(s) have a NIF that is valid for the other type.", [
								preview.total,
							])}</p>
							<ul class="small">${directions}</ul>
							<p class="text-muted small">
								${__(
									"Only the Customer Type is changed, and only where the NIF format leaves no doubt. Customers whose NIF is malformed are not touched."
								)}
							</p>
							<div style="max-height: 320px; overflow: auto;">
							<table class="table table-bordered table-condensed small">
								<thead><tr>
									<th>${__("Customer")}</th><th>${__("NIF")}</th>
									<th>${__("From")}</th><th>${__("To")}</th>
								</tr></thead>
								<tbody>${rows}</tbody>
							</table>
							</div>
							${remaining > 0 ? `<p class="text-muted small">${__("… and {0} more.", [remaining])}</p>` : ""}
						`,
					},
				],
				primary_action_label: __("Correct {0} Customer(s)", [preview.total]),
				primary_action() {
					dialog.hide();
					frappe.call({
						method: "isoft_angola_tax_compliance.nif.run_type_fix",
						args: { include_disabled },
						freeze: true,
						freeze_message: __("Correcting…"),
						callback(res) {
							const updated = (res && res.message && res.message.updated) || 0;
							frappe.show_alert(
								{ message: __("{0} customer(s) corrected.", [updated]), indicator: "green" },
								7
							);
							report.refresh();
						},
					});
				},
			});

			dialog.show();
		},
	});
}
