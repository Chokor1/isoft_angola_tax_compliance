// Copyright (c) 2026, ISOFT and contributors
// For license information, please see license.txt
//
// "Assign to Existing Items" on a category that claims items.
//
// New items are stamped on insert; existing ones are deliberately left alone so
// enabling a rule cannot quietly re-tag a whole catalogue. This button is the
// deliberate act, and it always shows what it will do before doing it.

frappe.ui.form.on("Tax Withholding Category", {
	refresh(frm) {
		if (frm.is_new()) return;
		if (!frm.doc.atc_auto_assign) return;

		frm.add_custom_button(__("Assign to Existing Items"), () =>
			isoft_atc_backfill_preview(frm)
		);
	},
});

function isoft_atc_backfill_preview(frm) {
	frappe.call({
		method: "isoft_angola_tax_compliance.auto_assign.get_backfill_preview",
		args: { category: frm.doc.name },
		freeze: true,
		freeze_message: __("Checking which items match…"),
		callback(r) {
			if (!r || !r.message) return;
			isoft_atc_show_dialog(frm, r.message);
		},
	});
}

function isoft_atc_show_dialog(frm, preview) {
	const rule = (preview.rules || [])[0];

	if (!rule) {
		frappe.msgprint({
			title: __("No rule to apply"),
			indicator: "orange",
			message: __(
				"This category is not set to auto-assign, or its Scope is not 'Item Based'."
			),
		});
		return;
	}

	if (!preview.matched) {
		frappe.msgprint({
			title: __("Nothing to assign"),
			indicator: "blue",
			message: __(
				"{0} item(s) have no withholding category, but none of them match this rule.<br><br>" +
					"Rule: non-stock = <b>{1}</b>, item groups = <b>{2}</b>",
				[
					preview.untagged_items,
					rule.non_stock ? __("yes") : __("no"),
					frappe.utils.escape_html((rule.groups || []).join(", ") || "—"),
				]
			),
		});
		return;
	}

	const rows = (preview.sample || [])
		.map(
			(row) => `<tr>
				<td>${frappe.utils.escape_html(row.item)}</td>
				<td>${frappe.utils.escape_html(row.item_name || "")}</td>
				<td>${frappe.utils.escape_html(row.item_group || "")}</td>
				<td class="text-center">${row.is_stock_item ? __("Yes") : __("No")}</td>
			</tr>`
		)
		.join("");

	const remaining = preview.matched - (preview.sample || []).length;

	const dialog = new frappe.ui.Dialog({
		title: __("Assign to Existing Items"),
		size: "large",
		fields: [
			{
				fieldtype: "HTML",
				fieldname: "preview",
				options: `
					<p>${__("This will set <b>{0}</b> on <b>{1}</b> item(s).", [
						frappe.utils.escape_html(frm.doc.name),
						preview.matched,
					])}</p>
					<p class="text-muted small">
						${__("Rule: all non-stock = <b>{0}</b> &middot; item groups = <b>{1}</b>", [
							rule.non_stock ? __("yes") : __("no"),
							frappe.utils.escape_html((rule.groups || []).join(", ") || "—"),
						])}<br>
						${__(
							"Only items with an empty category are changed — a value set by hand is never overwritten."
						)}
					</p>
					<div style="max-height: 320px; overflow: auto;">
					<table class="table table-bordered table-condensed small">
						<thead><tr>
							<th>${__("Item")}</th><th>${__("Name")}</th>
							<th>${__("Item Group")}</th><th class="text-center">${__("Stock")}</th>
						</tr></thead>
						<tbody>${rows}</tbody>
					</table>
					</div>
					${
						remaining > 0
							? `<p class="text-muted small">${__("… and {0} more.", [remaining])}</p>`
							: ""
					}
				`,
			},
		],
		primary_action_label: __("Assign {0} Item(s)", [preview.matched]),
		primary_action() {
			dialog.hide();
			frappe.call({
				method: "isoft_angola_tax_compliance.auto_assign.run_backfill",
				args: { category: frm.doc.name },
				freeze: true,
				freeze_message: __("Assigning…"),
				callback(r) {
					const updated = (r && r.message && r.message.updated) || 0;
					frappe.show_alert(
						{ message: __("{0} item(s) updated.", [updated]), indicator: "green" },
						7
					);
				},
			});
		},
	});

	dialog.show();
}
