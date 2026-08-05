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
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "problem" && data) {
			const colour = data.problem === __("Wrong Customer Type") ? "orange" : "red";
			value = `<span style="color: var(--text-on-${colour}, ${colour})">${value}</span>`;
		}
		return value;
	},
};
