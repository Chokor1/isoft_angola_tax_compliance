# Copyright (c) 2026, ISOFT and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

# Fields added after this Single already existed. A Single keeps one row per
# field in tabSingles, so a field nobody has saved yet reads as None rather
# than as its default -- which would quietly switch a rule off while its
# checkbox looks ticked. Filling them on save is what makes the form honest;
# customer_name.py applies the same defaults when reading.
CUSTOMER_NAME_DEFAULTS = {
	"customer_name_enforcement": "Warn",
	"name_applies_to": "Individuals Only",
	"name_reject_digits": 1,
	"name_reject_email": 1,
	"name_reject_symbols": 0,
	"name_min_length": 3,
}


RULE_FIELDS = ("name_reject_digits", "name_reject_email", "name_reject_symbols", "name_min_length")


class AngolaNIFValidationSettings(Document):
	def validate(self):
		self.default_customer_name_rules()

	def default_customer_name_rules(self):
		"""Never leave the customer-name check enabled with nothing to check.

		A Check field on a Single reads as 0 whether it was never saved or
		deliberately cleared, so the switches cannot be told apart. Turning the
		check on while every rule is off would validate nothing at all, so that
		combination seeds the recommended rules; any rule the user has chosen is
		left exactly as it is.
		"""
		if not self.get("validate_customer_name"):
			return

		for fieldname in ("customer_name_enforcement", "name_applies_to"):
			if not self.get(fieldname):
				self.set(fieldname, CUSTOMER_NAME_DEFAULTS[fieldname])

		if any(self.get(f) for f in RULE_FIELDS):
			return

		for fieldname in RULE_FIELDS:
			self.set(fieldname, CUSTOMER_NAME_DEFAULTS[fieldname])
		frappe.msgprint(
			frappe._("Customer name validation was switched on with no rule selected, so digits, e-mail addresses and a minimum length of {0} are now checked. Untick any of them to change that.").format(
				CUSTOMER_NAME_DEFAULTS["name_min_length"]
			),
			indicator="blue",
			alert=True,
		)
