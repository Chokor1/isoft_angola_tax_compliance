# Copyright (c) 2026, ISOFT and contributors
# For license information, please see license.txt
"""Install / migrate hooks.

Deliberately does NOT create any Angola Tax Compliance Settings record. A
company with no settings row resolves to mode Off, so installing this app
changes nothing about how the site behaves until someone explicitly opts a
company into Shadow.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from isoft_angola_tax_compliance.custom_fields import get_custom_fields


def after_install():
	setup_custom_fields()


def after_migrate():
	setup_custom_fields()


def setup_custom_fields():
	frappe.clear_cache()
	create_custom_fields(get_custom_fields(), ignore_validate=True)
	frappe.db.commit()
