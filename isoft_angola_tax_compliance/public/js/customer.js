// "Generate Account" on Customer, moved here from ERPNext's customer.js.
//
// Offers the button only when Automation Settings has customer generation enabled and
// the party has no account row yet, then creates a numbered child account under the
// configured parent and links it. The numbering matters on this site: account numbers
// are mandatory, so a party account cannot simply be auto-created unnumbered.
//
// Layered on with doctype_js, which Frappe merges across apps, so this stacks with
// ERPNext's own customer.js rather than replacing it.

frappe.ui.form.on('Customer', {
	refresh: function (frm) {
		if (frm.doc.__islocal || frm.is_new()) return;

		frappe.call({
			method: 'isoft_angola_tax_compliance.isoft_angola_tax_compliance.doctype.automation_settings.automation_settings.enable_auto_generate_customer_account',
			callback: function (r) {
				if (r.message != 1) return;
				if ((frm.doc.accounts || []).length !== 0) return;
				if (frm.doc.__unsaved) return;

				frm.add_custom_button(__('Generate Account'), function () {
					frappe.call({
						method: 'isoft_angola_tax_compliance.isoft_angola_tax_compliance.doctype.automation_settings.automation_settings.generate_party_account',
						args: {
							party_type: 'Customer',
							party_name: frm.doc.name,
							currency: frm.doc.default_currency,
						},
						callback: function (res) {
							if (!res.message) return;
							const account_doc = res.message;
							const newRow = frappe.model.get_new_doc('Party Account', frm.doc, 'accounts');
							newRow.account = account_doc.name;
							frm.refresh_field('accounts');
							frappe.show_alert({
								message: __('New Account ({0}) generated successfully', [account_doc.name]),
								indicator: 'green'
							}, 5);
							frm.page.remove_inner_button(__('Generate Account'));
							frm.dirty();
							frm.save();
						},
						error: function () {
							frappe.msgprint({
								message: __('Generating the account failed. Please try again.'),
								title: __('Generating Account Failed'),
								indicator: 'red'
							});
						}
					});
				}).addClass('generate_account_button');
			}
		});
	}
});
