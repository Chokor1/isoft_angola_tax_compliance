# Copyright (c) 2026, ISOFT and contributors
# For license information, please see license.txt
"""Removes the abandoned `Tax Withholding` doctypes.

`Tax Withholding` and `Tax Withholding Item` were created for an earlier,
never-finished item-based experiment. The code that wrote them
(`evaluate_item_withholding_tax`) has been deleted from ERPNext, so they hold
nothing the system reads.

They are worse than dead weight: they were created as *standard* doctypes in the
`Accounts` module of ERPNext, so Frappe looks for a Python module that does not
exist and opening the list view raises

    ImportError: Module import failed for Tax Withholding
    (erpnext.accounts.doctype.tax_withholding.tax_withholding
     Error: No module named '...')

Deliberately NOT run from the install/migrate hooks. Dropping a table destroys
data, and that must be an explicit decision, not a side effect of `bench update`.

    bench --site <site> execute isoft_angola_tax_compliance.cleanup.report
    bench --site <site> execute isoft_angola_tax_compliance.cleanup.stop_the_crash
    bench --site <site> execute isoft_angola_tax_compliance.cleanup.remove --kwargs '{"confirm": true}'
"""

import frappe

ORPHANS = ["Tax Withholding", "Tax Withholding Item"]


def _table_count(doctype):
	table = "tab{0}".format(doctype)
	if not frappe.db.sql("SHOW TABLES LIKE %s", table):
		return None
	return frappe.db.sql("SELECT COUNT(*) FROM `{0}`".format(table))[0][0]


def report():
	"""What exists, how much data, and whether it will crash the desk."""
	print("=" * 74)
	print("  ABANDONED TAX WITHHOLDING DOCTYPES")
	print("=" * 74)

	found = {}
	for doctype in ORPHANS:
		row = frappe.db.get_value("DocType", doctype, ["name", "custom", "module"], as_dict=True)
		rows = _table_count(doctype)
		crashes = bool(row) and not row.get("custom")
		found[doctype] = {"doctype_record": bool(row), "custom": row.get("custom") if row else None,
						  "module": row.get("module") if row else None, "rows": rows,
						  "crashes_desk": crashes}
		print(f"\n  {doctype}")
		print(f"      DocType record : {'yes' if row else 'no'}"
			  f"{'  (custom={0}, module={1})'.format(row.custom, row.module) if row else ''}")
		print(f"      table rows     : {'no table' if rows is None else rows}")
		if crashes:
			print("      !! standard doctype with no Python module -- the list view raises ImportError")

	print("\n  stop_the_crash() makes them custom so the desk stops erroring, keeping the data.")
	print("  remove(confirm=True) deletes the doctypes and drops the tables.")
	return found


def stop_the_crash():
	"""Non-destructive: mark them custom so Frappe stops importing a missing module.

	Frappe only calls `load_doctype_module` for standard doctypes
	(`frappe/desk/form/meta.py`: `if not self.custom`). Flipping the flag ends
	the ImportError immediately without touching a single row, which is the
	right first move on a production site.
	"""
	changed = []
	for doctype in ORPHANS:
		row = frappe.db.get_value("DocType", doctype, ["name", "custom"], as_dict=True)
		if row and not row.custom:
			frappe.db.set_value("DocType", doctype, "custom", 1, update_modified=False)
			changed.append(doctype)

	frappe.db.commit()
	frappe.clear_cache()
	print(f"  marked custom: {changed or 'nothing to do'}")
	return changed


def remove(confirm=False):
	"""Delete the doctypes and drop their tables. Destructive."""
	state = {d: _table_count(d) for d in ORPHANS}

	if not confirm:
		print("  DRY RUN -- nothing deleted. Would remove:")
		for doctype, rows in state.items():
			exists = frappe.db.exists("DocType", doctype)
			print(f"      {doctype:26} doctype={'yes' if exists else 'no':3} rows={rows}")
		print("\n  Re-run with --kwargs '{\"confirm\": true}' to apply.")
		return state

	removed = []
	for doctype in ORPHANS:
		if frappe.db.exists("DocType", doctype):
			# delete_doc drops the table too. Any leftover table (a doctype record
			# already gone, as on some sites) is dropped with sql_ddl, which is
			# the sanctioned path for DDL inside a transaction.
			frappe.delete_doc("DocType", doctype, force=True, ignore_permissions=True)
			removed.append(doctype)
		frappe.db.sql_ddl("DROP TABLE IF EXISTS `tab{0}`".format(doctype))

	frappe.db.commit()
	frappe.clear_cache()
	print(f"  removed: {removed}")
	print(f"  tables dropped: {ORPHANS}")
	return {"removed": removed, "rows_before": state}
