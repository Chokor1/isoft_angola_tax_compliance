# Copyright (c) 2025, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

# import frappe
import frappe
from frappe.model.document import Document

class AutomationSettings(Document):
	def validate(self):
		if self.enable_generate_customer_account:
			default_count = 0
			for setting in self.customer_account_settings:
				if setting.default:
					default_count = default_count + 1

			if default_count == 0:
				frappe.throw("Please select one default customer account.")
			elif default_count > 1:
				frappe.throw("Only one default customer account is allowed.")

		if self.enable_generate_supplier_account:
			default_count = 0
			for setting in self.supplier_account_settings:
				if setting.default:
					default_count = default_count + 1

			if default_count == 0:
				frappe.throw("Please select one default supplier account.")
			elif default_count > 1:
				frappe.throw("Only one default supplier account is allowed.")
			
@frappe.whitelist()
def enable_auto_generate_customer_account():
    """Whether the Customer form should offer Generate Account."""
    return frappe.db.get_singles_value("Automation Settings", "enable_generate_customer_account")

@frappe.whitelist()
def enable_auto_generate_supplier_account():
    """Whether the Supplier form should offer Generate Account.

    Read the supplier flag, not the customer one -- this returned
    enable_generate_customer_account before the move, so the Supplier button
    followed the Customer toggle.
    """
    return frappe.db.get_singles_value("Automation Settings", "enable_generate_supplier_account")

@frappe.whitelist()
def generate_party_account(party_type, party_name, currency = None):
	if not party_type:
		frappe.throw("Party type is required.")
	if not party_name:
		frappe.throw("Party name is required.")

	automation_settings = frappe.get_single("Automation Settings")
	parent_account = None
	digits = None
	if party_type	== 'Customer':
		account_type = 'Receivable'
		for setting in automation_settings.customer_account_settings:			
			if (not currency and setting.default) or setting.currency == currency:
				parent_account = setting.parent_account	
				digits = setting.digits
				break
	elif party_type == 'Supplier':
		account_type = 'Payable'
		for setting in automation_settings.supplier_account_settings:
			if (not currency and setting.default) or setting.currency == currency:
				parent_account = setting.parent_account	
				digits = setting.digits
				break
	else:
		frappe.throw("Invalid party type '{}'. Expected 'Customer' or 'Supplier'.".format(party_type))

	if not parent_account or not digits:
		frappe.throw("No account settings found for currency '{}' under {} settings.".format(currency, party_type))
	else:
		parent_account_number = frappe.db.get_value("Account", parent_account, "account_number")
		parent_account_currency = frappe.db.get_value("Account", parent_account, "account_currency")
		parent_account_company = frappe.db.get_value("Account", parent_account, "company")
		current = frappe.db.get_value("Series", parent_account, "current", order_by="name")
		if current is None:
			current = 1
		while(True):
			account_number = format_account_number(parent_account_number, current, digits)
			if frappe.db.exists('Account', {'account_number': account_number}):
				current+=1
			else:
				break
		account = frappe.get_doc({
    	"doctype": "Account",
    	"account_name": party_name,
    	"account_number": account_number,
    	"parent_account": parent_account,  
    	"account_type": account_type,               
    	"company": parent_account_company,       
    	"account_currency": currency or parent_account_currency,
		})
		account.insert()
		frappe.db.sql("""
    INSERT INTO `tabSeries` (`name`, `current`)
    VALUES (%s, %s)
    ON DUPLICATE KEY UPDATE `current` = VALUES(`current`)
""", (parent_account, current))
		return account
				

def format_account_number(parent_code, current, total_length=8):
	combined = f"{parent_code}{current}"
	padding_needed = total_length - len(str(combined))
	padded_child = str(current).zfill(padding_needed + len(str(current)))
	return f"{parent_code}{padded_child[-(total_length - len(str(parent_code))):]}"
