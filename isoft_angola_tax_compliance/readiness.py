# Copyright (c) 2026, ISOFT and contributors
# For license information, please see license.txt
"""Pre-flight check: is this site safe to switch the engine on?

Run it BEFORE `bench update` on a site that already has the app, or straight
after installing it. It writes nothing.

The check that matters is the last one. Since enablement is now the company's
country, an Angolan company goes live the moment the code lands -- and if a
withholding category resolves for a customer but has no account for that
company, `set_withholdings` throws and **that customer cannot be invoiced at
all**. Better to learn that here than from a cashier.

    bench --site <site> execute isoft_angola_tax_compliance.readiness.check
"""

import frappe

from isoft_angola_tax_compliance.withholding.settings import ANGOLA

CATEGORY_FIELD = "atc_tax_withholding_category"


def check():
	problems = []
	warnings = []

	print("=" * 76)
	print("  ANGOLA WITHHOLDING -- READINESS")
	print("=" * 76)

	# ---- 1. which companies go live ------------------------------------
	companies = frappe.get_all("Company", fields=["name", "country"])
	angolan = [c.name for c in companies if c.country == ANGOLA]
	print(f"\n  1. Companies with country = Angola  ({len(angolan)} of {len(companies)})")
	for c in companies:
		flag = "LIVE " if c.country == ANGOLA else "     "
		print(f"       {flag} {c.name:28} {c.country}")
	if not angolan:
		print("       -> nothing will be enabled on this site.")
		return {"angolan_companies": 0, "problems": [], "warnings": []}

	# ---- 2. categories -------------------------------------------------
	cats = frappe.get_all(
		"Tax Withholding Category",
		fields=["name", "atc_withholding_type", "atc_base_type", "atc_applies_to"],
	)
	print(f"\n  2. Tax Withholding Categories  ({len(cats)})")
	for c in cats:
		bits = f"type={c.atc_withholding_type or '-':5} base={c.atc_base_type or '-':16} scope={c.atc_applies_to or '-'}"
		print(f"       {c.name[:44]:46} {bits}")
		if not c.atc_withholding_type:
			warnings.append(f"{c.name}: no Withholding Type -- SAF-T/AGT will declare it as 'Other'")

	# ---- 3. who is subject to withholding ------------------------------
	party_rows = frappe.db.sql(
		"""SELECT ptw.parent AS customer, ptw.tax_withholding_category AS category
		   FROM `tabParty Tax Withholding` ptw WHERE ptw.parenttype = 'Customer'""",
		as_dict=True,
	)
	tagged_items = frappe.db.sql(
		"SELECT COUNT(*) FROM `tabItem` WHERE IFNULL(`{0}`, '') <> ''".format(CATEGORY_FIELD)
	)[0][0]
	used = sorted({r.category for r in party_rows})
	print(f"\n  3. Customers with a withholding row : {len(party_rows)}")
	print(f"     Items tagged with a category      : {tagged_items}")
	for cat in used:
		print(f"       {cat}: {sum(1 for r in party_rows if r.category == cat)} customer(s)")
	if not party_rows and not tagged_items:
		warnings.append(
			"No customer or item is configured, so nothing will be withheld. "
			"Run migrate_legacy.plan to see whether the old configuration was picked up."
		)

	# ---- 4. THE blocker: resolved category with no account -------------
	print("\n  4. Account coverage  (a gap here BLOCKS invoicing)")
	item_cats = frappe.db.sql(
		"SELECT DISTINCT `{0}` FROM `tabItem` WHERE IFNULL(`{0}`, '') <> ''".format(CATEGORY_FIELD)
	)
	needed = sorted(used + [r[0] for r in item_cats])

	for company in angolan:
		for category in sorted(set(needed)):
			account = frappe.db.get_value(
				"Tax Withholding Account",
				{"parent": category, "parenttype": "Tax Withholding Category", "company": company},
				"account",
			)
			if account:
				print(f"       OK      {company:22} {category[:34]:36} -> {account[:34]}")
				continue

			print(f"       BLOCKED {company:22} {category[:34]:36} -> no account")
			problems.append(
				f"{company} / {category}: no account. Any invoice where this category "
				"resolves will fail to submit."
			)

		# cativo also needs a base tax account
		for category in sorted(set(needed)):
			base_type = frappe.db.get_value("Tax Withholding Category", category, "atc_base_type")
			if base_type != "Tax Amount":
				continue
			has_base = frappe.db.exists(
				"Angola Withholding Base Account",
				{"parent": category, "parenttype": "Tax Withholding Category", "company": company},
			)
			if not has_base:
				print(f"       BLOCKED {company:22} {category[:34]:36} -> no base tax account")
				problems.append(
					f"{company} / {category}: 'Tax Amount' base with no IVA Liquidado account. "
					"The cativo base would be zero."
				)

	# ---- 5. leftovers --------------------------------------------------
	orphan = frappe.db.get_value("DocType", "Tax Withholding", ["name", "custom"], as_dict=True)
	if orphan and not orphan.custom:
		warnings.append(
			"The abandoned `Tax Withholding` doctype is still standard -- its list view "
			"raises ImportError. Fix with cleanup.stop_the_crash."
		)

	pos = frappe.get_all(
		"POS Profile", filters={"atc_enable_withholding": 1}, pluck="name"
	) if frappe.db.has_column("POS Profile", "atc_enable_withholding") else []
	print(f"\n  5. POS Profiles with withholding enabled : {pos or 'none (POS unaffected)'}")

	# ---- verdict --------------------------------------------------------
	print("\n" + "-" * 76)
	if problems:
		print(f"  NOT READY -- {len(problems)} blocking problem(s):")
		for p in problems:
			print(f"      * {p}")
	else:
		print("  READY -- every category that can resolve has an account on every Angolan company.")

	for w in warnings:
		print(f"  warning: {w}")

	return {
		"angolan_companies": len(angolan),
		"problems": problems,
		"warnings": warnings,
		"ready": not problems,
	}
