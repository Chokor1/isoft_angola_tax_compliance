# Copyright (c) 2026, ISOFT and contributors
# For license information, please see license.txt
"""Re-run the legacy seeding after fixing how it reads the old configuration.

The first version guarded on `frappe.get_meta(...).get_field(...)`. That guard
is wrong here: `bench migrate` syncs DocTypes BEFORE it runs patches, so the
same deploy that carries this migration is also the one that removes
`enable_vat_exemption`, `vat_exemption_percent` and the two `Company` account
fields from ERPNext. By the time the patch ran, meta no longer knew those
fields, every guard returned False, and it migrated nothing at all -- silently,
because "nothing to migrate" and "cannot see anything to migrate" looked
identical.

Frappe never drops columns, so the data was still there the whole time. The
migration now reads the columns directly and this patch replays it.

Safe to run on a site where the first attempt DID work: the seeding is
idempotent and additive.
"""

import frappe

from isoft_angola_tax_compliance.install import setup_custom_fields
from isoft_angola_tax_compliance.migrate_legacy import execute as migrate_legacy


def execute():
	if not frappe.db.exists("DocType", "Party Tax Withholding"):
		return

	setup_custom_fields()
	migrate_legacy()
