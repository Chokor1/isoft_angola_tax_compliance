// Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

frappe.ui.form.on("VAT Assessment", {
	setup: function (frm) {
		// The assessment always covers a whole calendar month — the period is
		// derived from Month + Year, never entered by hand.
		frm.set_df_property("from_date", "read_only", 1);
		frm.set_df_property("to_date", "read_only", 1);
	},

	onload: function (frm) {
		if (frm.is_new() && !frm.doc.year) {
			frm.set_value("year", new Date().getFullYear());
		}
	},

	refresh: function (frm) {
		// Net VAT position indicator
		if (frm.doc.vat_position) {
			const colors = { Payable: "red", Recoverable: "green", Nil: "gray" };
			frm.dashboard.add_indicator(
				__("Net VAT: {0} ({1})", [
					format_currency(frm.doc.net_vat_position, frm.doc.company_currency),
					__(frm.doc.vat_position),
				]),
				colors[frm.doc.vat_position] || "blue"
			);
		}

		// Journal-entry actions are only available once the assessment is submitted.
		if (frm.doc.docstatus === 1) {
			frm.dashboard.set_headline(
				__("Verify the figures in the AGT partner portal before relying on this assessment.")
			);

			// Only count journal entries that still exist (ignore "Missing").
			const jes = (frm.doc.created_journal_entries || []).filter(
				(r) => r.journal_entry && r.status !== "Missing"
			);
			const has_jes = jes.length > 0;
			const has_draft = jes.some((r) => r.status === "Draft");

			// Create Draft — only when no journal entries exist yet.
			if (!has_jes) {
				frm.add_custom_button(
					__("Create Draft"),
					function () {
						je_action(frm, "create_draft_entries", __("Creating draft Journal Entries..."));
					},
					__("Journal Entries")
				);
			}

			// Submit — only when drafts exist that still need submitting.
			if (has_draft) {
				frm.add_custom_button(
					__("Submit Journal Entries"),
					function () {
						frappe.confirm(
							__("Submit the VAT closing Journal Entries to the ledger?"),
							function () {
								je_action(
									frm,
									"submit_journal_entries",
									__("Submitting Journal Entries...")
								);
							}
						);
					},
					__("Journal Entries")
				);
			}

			// View + Refresh — only when journal entries exist.
			if (has_jes) {
				frm.add_custom_button(
					__("View Journal Entries"),
					function () {
						frappe.set_route("List", "Journal Entry", {
							name: ["in", jes.map((r) => r.journal_entry)],
						});
					},
					__("Journal Entries")
				);
				frm.add_custom_button(
					__("Refresh Status"),
					function () {
						je_action(frm, "refresh_status", __("Refreshing status..."));
					},
					__("Journal Entries")
				);
			}
		}
	},

	company: function (frm) {
		recompute(frm);
	},

	month: function (frm) {
		recompute(frm);
	},

	year: function (frm) {
		recompute(frm);
	},

	posting_date: function (frm) {
		recompute(frm);
	},
});

// Auto-compute the assessment whenever Company / month / year change (draft
// only). Silent: no popup if a field is still missing — just clears the preview.
function recompute(frm) {
	if (frm.doc.docstatus !== 0) return;
	if (!frm.doc.company || !frm.doc.month || !frm.doc.year) {
		frm.clear_table("account_summary");
		frm.clear_table("planned_entries");
		frm.refresh_field("account_summary");
		frm.refresh_field("planned_entries");
		return;
	}
	// A Posting Date still sitting on the old month's last day was defaulted, not
	// chosen — let it follow the month instead of going stale.
	const auto_posting = !frm.doc.posting_date || frm.doc.posting_date === frm.doc.to_date;

	frappe.call({
		method: "isoft_angola_tax_compliance.isoft_angola_tax_compliance.doctype.vat_assessment.vat_assessment.preview_assessment",
		args: {
			company: frm.doc.company,
			month: frm.doc.month,
			year: frm.doc.year,
			posting_date: frm.doc.posting_date || null,
		},
		callback: function (r) {
			if (!r.message) return;
			const m = r.message;
			frm.set_value("from_date", m.from_date);
			frm.set_value("to_date", m.to_date);
			if (auto_posting) frm.set_value("posting_date", m.to_date);
			frm.set_value("deductible_vat", m.deductible_vat);
			frm.set_value("output_vat", m.output_vat);
			frm.set_value("withheld_vat_recoverable", m.withheld_vat_recoverable);
			frm.set_value("adjustment_state", m.adjustment_state);
			frm.set_value("adjustment_company", m.adjustment_company);
			frm.set_value("net_vat_position", m.net_vat_position);
			frm.set_value("vat_position", m.vat_position);

			// Account breakdown
			frm.clear_table("account_summary");
			(m.account_summary || []).forEach(function (row) {
				// Copy only real data fields — never the server doc's internal
				// keys (name, parentfield, doctype, idx ...), which would corrupt
				// the new child row's grid metadata.
				frm.add_child("account_summary", {
					account: row.account,
					role: row.role,
					debit: row.debit,
					credit: row.credit,
					balance: row.balance,
					balance_side: row.balance_side,
				});
			});
			frm.refresh_field("account_summary");

			// Planned journal entries (preview only)
			frm.clear_table("planned_entries");
			(m.planned_entries || []).forEach(function (row) {
				frm.add_child("planned_entries", {
					entry_no: row.entry_no,
					description: row.description,
					account: row.account,
					debit: row.debit,
					credit: row.credit,
				});
			});
			frm.refresh_field("planned_entries");
		},
	});
}

function je_action(frm, method, message) {
	const run = () =>
		frappe.call({
			method: "isoft_angola_tax_compliance.isoft_angola_tax_compliance.doctype.vat_assessment.vat_assessment." + method,
			args: { assessment: frm.doc.name },
			freeze: true,
			freeze_message: message,
			callback: function (r) {
				if (r.exc) return;
				frm.reload_doc();
				frappe.show_alert({ message: __("Done."), indicator: "green" });
			},
		});

	// Persist any checkbox edits (Create/Submit ticks) before acting on them.
	if (frm.is_dirty()) {
		frm.save("Update").then(run);
	} else {
		run();
	}
}
