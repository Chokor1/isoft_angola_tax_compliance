# Copyright (c) 2026, ISOFT and contributors
# For license information, please see license.txt
"""Seed Tax Withholding Categories and customer rows from the legacy setup.

Runs once on `bench migrate`. Strictly additive and idempotent: it creates no
duplicates, edits no existing rate row, and changes no company's mode -- so it
alters no accounting on its own.

It deliberately raises rather than swallowing errors: a data migration that
fails quietly is worse than a blocked deploy, and this one is safe to re-run.
Run `isoft_angola_tax_compliance.migrate_legacy.plan` on a copy of production
before deploying to see exactly what it will do.
"""

import frappe

from isoft_angola_tax_compliance.install import setup_custom_fields
from isoft_angola_tax_compliance.migrate_legacy import execute as migrate_legacy


def execute():
	if not frappe.db.exists("DocType", "Party Tax Withholding"):
		# App doctypes not synced yet (fresh install path); after_install runs it.
		return

	# The categories carry this app's custom fields, so make sure they exist:
	# patches run before the after_migrate hook.
	setup_custom_fields()
	migrate_legacy()
