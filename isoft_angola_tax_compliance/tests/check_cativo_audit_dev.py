# Copyright (c) 2026, ISOFT and contributors
# For license information, please see license.txt
"""Read-only smoke check of Cativo Auditing on a real site.

Run from frappe-bench/sites:
    ../env/bin/python ../apps/isoft_angola_tax_compliance/isoft_angola_tax_compliance/tests/check_cativo_audit_dev.py [site] [from] [to]
"""

import sys
import time

import frappe

SITE = sys.argv[1] if len(sys.argv) > 1 else "dev.isoft.ao"
FROM = sys.argv[2] if len(sys.argv) > 2 else "2026-01-01"
TO = sys.argv[3] if len(sys.argv) > 3 else "2026-12-31"


def main():
	frappe.init(site=SITE)
	frappe.connect()
	frappe.set_user("Administrator")
	try:
		from isoft_angola_tax_compliance.cativo_audit import api, data, resolve

		for company in frappe.get_all("Company", filters={"country": "Angola"}, pluck="name"):
			started = time.time()
			result = api.get_cativo_audit(company, FROM, TO)
			elapsed = time.time() - started

			rows, unassigned = result["rows"], result["unassigned"]
			moves = data.load_movements(company, result["accounts"])
			invoices = {r["invoice"] for r in rows}
			expected = sum(m["amount"] for m in moves if m["invoice"] in invoices)
			expected += sum(m["amount"] for m in moves if not m["invoice"] and resolve._in_period(m["posting_date"], FROM, TO))
			got = sum(r["cativo"] for r in rows) + unassigned["amount"]

			print(
				"%-40s rows %4d  cativo %16.2f  unassigned %14.2f (%d)  derived %-5s  %.2fs"
				% (company[:40], len(rows), sum(r["cativo"] for r in rows), unassigned["amount"], unassigned["count"],
				result["accounts"]["derived"], elapsed)
			)
			assert elapsed < 5, "too slow: %.1fs" % elapsed
			assert abs(got - expected) < 0.05, "totals do not tie: %.2f vs %.2f" % (got, expected)
	finally:
		frappe.db.rollback()
		frappe.destroy()


if __name__ == "__main__":
	main()
