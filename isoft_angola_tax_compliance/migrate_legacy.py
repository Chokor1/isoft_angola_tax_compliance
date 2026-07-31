# Copyright (c) 2026, ISOFT and contributors
# For license information, please see license.txt
"""Migrate the legacy withholding configuration into Tax Withholding Categories.

The old setup was:

    Company.default_vat_exempt_account          -> where IVA cativo was booked
    Company.default_tax_withholding_account     -> where retencao II was booked
    Customer.enable_vat_exemption + percent     -> which customers are cativo (50 / 100)
    Sales Invoice.apply_tax_withholding_on_service -> per-invoice retencao switch

This builds the equivalent configuration for the engine:

    Tax Withholding Category per regime, with one `accounts` row per company
    taken from the legacy company default, and one `Party Tax Withholding` row
    per customer taken from `enable_vat_exemption` / `vat_exemption_percent`.

Strictly additive and idempotent: it never edits an existing rate row, never
removes a customer row, never touches a company already configured by hand, and
never changes any company's mode. Running it changes no accounting on its own.

    bench --site <site> execute isoft_angola_tax_compliance.migrate_legacy.plan
    bench --site <site> execute isoft_angola_tax_compliance.migrate_legacy.execute

Item tagging is deliberately NOT migrated. The legacy rule was
`is_stock_item = 0`, which is not the same thing as "service" -- reproducing it
would carry the defect forward. Tag the service Item Groups by hand instead.
"""

import frappe
from frappe.utils import flt

CAT_II = "Retencao na Fonte II - Servicos 6,5%"
CAT_IVA = {
	"50": "IVA Cativo 50% - Grandes Contribuintes",
	"100": "IVA Cativo 100% - Estado",
}

II_RATE = 6.5
FROM_DATE = "2024-01-01"
TO_DATE = "2099-12-31"

LEGACY_IVA_ACCOUNT_FIELD = "default_vat_exempt_account"
LEGACY_II_ACCOUNT_FIELD = "default_tax_withholding_account"

# Angolan PGC: 3453x is IVA Liquidado. That is the correct cativo base -- unlike
# the legacy code, which withheld on the whole taxes table.
VAT_LIQUIDADO_PREFIX = "3453"


# ---------------------------------------------------------------- inspection


def _legacy_column(doctype, column):
	"""Is the legacy value still readable?

	Deliberately checks the DATABASE COLUMN, not the meta field. By the time
	this runs the legacy DocFields are usually gone: `bench migrate` syncs
	DocTypes before it runs patches, so the very deploy that carries this
	migration is also the one that removes `enable_vat_exemption` and friends
	from ERPNext. Frappe never drops columns, so the data survives -- but a
	`meta.get_field()` guard would see nothing and silently migrate nothing.
	"""
	return frappe.db.has_column(doctype, column)


def get_company_config():
	"""What each company's legacy defaults map to, plus anything suspicious."""
	has_iva = _legacy_column("Company", LEGACY_IVA_ACCOUNT_FIELD)
	has_ii = _legacy_column("Company", LEGACY_II_ACCOUNT_FIELD)

	config = {}
	for company in frappe.get_all("Company", pluck="name"):
		iva_account = _read_column("Company", company, LEGACY_IVA_ACCOUNT_FIELD) if has_iva else None
		ii_account = _read_column("Company", company, LEGACY_II_ACCOUNT_FIELD) if has_ii else None

		vat_accounts, vat_source = detect_vat_accounts(company)

		warnings = []
		if iva_account and ii_account and iva_account == ii_account:
			warnings.append(
				"Legacy retencao II and IVA cativo point at the SAME account ({0}). "
				"II is an Imposto Industrial credit and cativo an IVA credit -- sharing one "
				"account leaves both unreconcilable. Set a separate II account before "
				"relying on the migrated category.".format(ii_account)
			)
		if not vat_accounts:
			warnings.append(
				"No IVA Liquidado account found; the IVA cativo base cannot be set "
				"automatically for this company."
			)
		elif vat_source != "account_number":
			warnings.append(
				"IVA Liquidado accounts were guessed from the sales tax templates "
				"({0}). Review them -- any non-VAT row here (freight, imposto de selo) "
				"would be withheld on.".format(", ".join(vat_accounts))
			)

		config[company] = {
			"iva_account": iva_account,
			"ii_account": ii_account,
			"vat_accounts": vat_accounts,
			"vat_source": vat_source,
			"warnings": warnings,
		}

	return config


def detect_vat_accounts(company):
	"""Find the IVA Liquidado account(s) that the cativo base is a share of."""
	accounts = frappe.get_all(
		"Account",
		filters={
			"company": company,
			"is_group": 0,
			"account_number": ["like", VAT_LIQUIDADO_PREFIX + "%"],
		},
		pluck="name",
	)
	if accounts:
		return sorted(accounts), "account_number"

	# Fallback: whatever the sales tax templates actually charge.
	rows = frappe.db.sql(
		"""
		SELECT DISTINCT stc.account_head
		FROM `tabSales Taxes and Charges` stc
		INNER JOIN `tabSales Taxes and Charges Template` t ON t.name = stc.parent
		WHERE stc.parenttype = 'Sales Taxes and Charges Template'
			AND t.company = %s
			AND stc.account_head IS NOT NULL
		""",
		company,
		as_dict=True,
	)
	guessed = sorted({r.account_head for r in rows})
	return guessed, "tax_template" if guessed else "none"


def _read_column(doctype, name, column):
	"""Read one legacy column by raw SQL, bypassing meta entirely."""
	rows = frappe.db.sql(
		"SELECT `{column}` AS value FROM `tab{doctype}` WHERE name = %s".format(
			column=column, doctype=doctype
		),
		name,
		as_dict=True,
	)
	return rows[0].value if rows else None


def get_legacy_customers():
	"""Customers flagged cativo under the old scheme, grouped by percentage.

	Raw SQL, not `frappe.get_all`: the query builder validates fieldnames
	against meta, and these fields are no longer in meta once the ERPNext
	customization is removed. The columns and their data remain.
	"""
	if not (_legacy_column("Customer", "enable_vat_exemption")
			and _legacy_column("Customer", "vat_exemption_percent")):
		return {}

	rows = frappe.db.sql(
		"""
		SELECT name, customer_name, vat_exemption_percent, disabled
		FROM `tabCustomer`
		WHERE IFNULL(enable_vat_exemption, 0) = 1
		ORDER BY name
		""",
		as_dict=True,
	)

	grouped = {}
	for row in rows:
		percent = str(int(flt(row.vat_exemption_percent) or 0))
		grouped.setdefault(percent, []).append(row)

	return grouped


# ------------------------------------------------------------------ actions


def plan():
	"""Dry run: print exactly what execute() would do. Changes nothing."""
	return _run(dry_run=True)


def execute(dry_run=False):
	"""Apply the migration. Idempotent -- safe to re-run."""
	return _run(dry_run=dry_run)


def autorun():
	"""Entry point for the install / migrate hooks.

	Runs the seeding on every `bench install-app` and every `bench migrate`, so
	a `bench update` picks up newly-flagged customers or a newly-configured
	company without anyone remembering to run anything.

	Two behaviours differ from the manual entry points:

	* Quiet when there is nothing to do -- one line instead of the full report,
	  so it does not bury the rest of a `bench update`. The moment it actually
	  creates something, or has a warning, the whole report is printed.
	* Never aborts. A configuration-seeding step must not be able to break every
	  future update, so failures are logged and reported loudly but swallowed.
	  Run `migrate_legacy.plan` to see the traceback in context.
	"""
	import contextlib
	import io

	buffer = io.StringIO()

	try:
		with contextlib.redirect_stdout(buffer):
			actions = _run(dry_run=False)
	except Exception:
		print(buffer.getvalue())
		frappe.log_error(frappe.get_traceback(), "Angola withholding legacy seeding")
		print("  !! Angola withholding: legacy seeding FAILED. Nothing was changed by it.")
		print("     Diagnose with: bench --site <site> execute "
			"isoft_angola_tax_compliance.migrate_legacy.plan")
		return None

	created = any(actions[key] for key in ("categories", "company_rows", "customer_rows"))
	pending = len(actions["warnings"]) + len(actions["skipped"])

	if created:
		# Something changed -- show the whole report, this is worth reading.
		print(buffer.getvalue())
	elif pending:
		# Nothing changed but a config issue stands. Keep it to one line: these
		# conditions persist, and a full report on every `bench update` would
		# train people to scroll past it.
		print(f"  Angola withholding: nothing to seed, {pending} item(s) need attention. "
			"Detail: bench --site <site> execute "
			"isoft_angola_tax_compliance.migrate_legacy.plan")
	else:
		print("  Angola withholding: legacy configuration already seeded, nothing to do.")

	return actions


def _run(dry_run):
	config = get_company_config()
	customers = get_legacy_customers()

	print("=" * 78)
	print("  LEGACY WITHHOLDING MIGRATION" + ("  [DRY RUN -- nothing written]" if dry_run else ""))
	print("=" * 78)

	actions = {"categories": [], "company_rows": [], "customer_rows": [], "warnings": [], "skipped": []}

	# ---- companies -----------------------------------------------------
	print("\n  Companies")
	for company, cfg in config.items():
		print(f"    {company}")
		print(f"        legacy IVA cativo account : {cfg['iva_account'] or '-'}")
		print(f"        legacy retencao II account: {cfg['ii_account'] or '-'}")
		print(f"        IVA Liquidado ({cfg['vat_source']}): {', '.join(cfg['vat_accounts']) or '-'}")
		for warning in cfg["warnings"]:
			print(f"        !! {warning}")
			actions["warnings"].append(f"{company}: {warning}")

	# ---- categories ----------------------------------------------------
	# The full Angolan set is seeded whenever the company has an account for the
	# regime -- retencao II 6,5% plus IVA cativo at both 50% and 100%. They are
	# NOT conditioned on which percentages happen to appear on customers today:
	# a company that has no 100% customer this month will still have one next
	# month, and a missing category is a silent under-withholding.
	needed = {}
	if any(c["ii_account"] for c in config.values()):
		needed[CAT_II] = {"type": "II", "base": "Item Net Amount", "scope": "Item Based", "rate": II_RATE}

	if any(c["iva_account"] for c in config.values()):
		for percent, category in sorted(CAT_IVA.items(), key=lambda kv: int(kv[0])):
			needed[category] = {
				"type": "IVA",
				"base": "Tax Amount",
				"scope": "Party Based",
				"rate": flt(percent),
			}

	for percent in sorted(customers, key=lambda p: int(p)):
		if percent not in CAT_IVA:
			actions["skipped"].append(
				f"{len(customers[percent])} customer(s) with an unsupported "
				f"vat_exemption_percent of {percent}"
			)

	# Work out the per-company rows first: `accounts` is mandatory on Tax
	# Withholding Category, so a new category has to be created *with* them.
	rows_by_category = {}
	for name, spec in needed.items():
		rows = []
		for company, cfg in config.items():
			legacy_account = cfg["ii_account"] if spec["type"] == "II" else cfg["iva_account"]
			if not legacy_account:
				continue

			vat_accounts = cfg["vat_accounts"] if spec["type"] == "IVA" else None

			# Report what is actually configured, never the legacy default, so
			# this can't imply an overwrite that will not happen.
			current = _get_company_account(name, company)
			if current:
				rows.append(
					{
						"company": company,
						"account": current,
						"existing": True,
						"legacy": legacy_account,
						"vat_accounts": vat_accounts,
					}
				)
				continue

			# Refuse to book retencao II into the cativo account.
			if spec["type"] == "II" and cfg["ii_account"] == cfg["iva_account"]:
				reason = (
					"{0} / {1}: BLOCKED -- legacy II account is the same as the cativo "
					"account ({2}). Create a separate Imposto Industrial account and add "
					"the row by hand.".format(name, company, legacy_account)
				)
				actions["warnings"].append(reason)
				actions["skipped"].append(reason)
				rows.append({"company": company, "blocked": reason})
				continue

			rows.append(
				{
					"company": company,
					"account": legacy_account,
					"existing": False,
					"vat_accounts": vat_accounts,
				}
			)
		rows_by_category[name] = rows

	print("\n  Categories")
	for name, spec in needed.items():
		exists = frappe.db.exists("Tax Withholding Category", name)
		usable = [r for r in rows_by_category[name] if not r.get("blocked")]

		if not exists and not usable:
			reason = (
				"{0}: cannot create -- no company has a legacy account for this regime, "
				"and at least one account row is mandatory.".format(name)
			)
			print(f"    BLOCKED {reason}")
			actions["skipped"].append(reason)
			continue

		print(f"    {'exists  ' if exists else 'CREATE  '}{name}   ({spec['type']}, {spec['rate']}%, {spec['base']})")
		if not exists:
			actions["categories"].append(name)
			if not dry_run:
				_ensure_category(name, spec, usable)

	# ---- per-company account rows --------------------------------------
	print("\n  Company rows on categories")
	for name in needed:
		for row in rows_by_category[name]:
			if row.get("blocked"):
				print(f"    BLOCKED {row['blocked']}")
				continue

			if row["existing"]:
				drift = (
					""
					if row["account"] == row["legacy"]
					else "   (legacy default was {0})".format(row["legacy"])
				)
				print(f"    exists  {name} / {row['company']} -> {row['account']}{drift}")
			else:
				print(f"    ADD     {name} / {row['company']} -> {row['account']}")
				actions["company_rows"].append(f"{name} / {row['company']}")

			if not dry_run:
				_ensure_company_row(name, row["company"], row["account"], row.get("vat_accounts"))

	# ---- customers ------------------------------------------------------
	print("\n  Customers")
	total = 0
	for percent in sorted(customers, key=lambda p: int(p)):
		category = CAT_IVA.get(percent)
		if not category:
			continue
		for row in customers[percent]:
			present = _has_party_row(row.name, category)
			total += 1
			flag = "exists  " if present else "ASSIGN  "
			suffix = "  [disabled customer]" if row.disabled else ""
			print(f"    {flag}{row.name[:44]:46} {percent}%{suffix}")
			if not present:
				actions["customer_rows"].append(f"{row.name} -> {category}")
			if not dry_run:
				_ensure_party_row(row.name, category)

	# ---- summary --------------------------------------------------------
	print("\n" + "-" * 78)
	print(f"  categories to create : {len(actions['categories'])}")
	print(f"  company rows to add  : {len(actions['company_rows'])}")
	print(f"  customers to assign  : {len(actions['customer_rows'])} (of {total} legacy cativo customers)")
	print(f"  warnings             : {len(actions['warnings'])}")
	for note in actions["skipped"]:
		print(f"  SKIPPED              : {note}")
	print("\n  Items are NOT migrated: the legacy `is_stock_item = 0` rule is not")
	print("  the same as 'service'. Tag the service Item Groups by hand.")
	if dry_run:
		print("\n  DRY RUN -- nothing was written. Re-run with `execute` to apply.")
	else:
		frappe.db.commit()
		frappe.clear_cache()
		print("\n  Applied. No company mode was changed; accounting is unaffected.")

	return actions


# ------------------------------------------------------------------ helpers


def _ensure_category(name, spec, rows):
	"""Create the category together with its company rows.

	`accounts` is mandatory on Tax Withholding Category, so the rows cannot be
	added in a second pass -- the insert would fail with a MandatoryError.
	"""
	if frappe.db.exists("Tax Withholding Category", name):
		return name

	base_accounts = []
	for row in rows:
		for vat_account in row.get("vat_accounts") or []:
			base_accounts.append({"company": row["company"], "account": vat_account})

	doc = frappe.get_doc(
		{
			"doctype": "Tax Withholding Category",
			"__newname": name,
			"category_name": name,
			"atc_withholding_type": spec["type"],
			"atc_base_type": spec["base"],
			"atc_applies_to": spec["scope"],
			"rates": [
				{
					"tax_withholding_rate": spec["rate"],
					"from_date": FROM_DATE,
					"to_date": TO_DATE,
					"single_threshold": 0,
					"cumulative_threshold": 0,
				}
			],
			"accounts": [{"company": r["company"], "account": r["account"]} for r in rows],
			"atc_base_tax_accounts": base_accounts,
		}
	)
	doc.insert(ignore_permissions=True)
	return doc.name


def _get_company_account(category, company):
	"""The account actually configured on the category for this company."""
	return frappe.db.get_value(
		"Tax Withholding Account",
		{
			"parent": category,
			"parenttype": "Tax Withholding Category",
			"parentfield": "accounts",
			"company": company,
		},
		"account",
	)


def _has_company_row(category, company, fieldname):
	doctype = "Tax Withholding Account" if fieldname == "accounts" else "Angola Withholding Base Account"
	return bool(
		frappe.db.exists(
			doctype,
			{"parent": category, "parenttype": "Tax Withholding Category", "parentfield": fieldname, "company": company},
		)
	)


def _ensure_company_row(category, company, account, vat_accounts=None):
	doc = frappe.get_doc("Tax Withholding Category", category)
	changed = False

	if not any(r.company == company for r in doc.accounts):
		doc.append("accounts", {"company": company, "account": account})
		changed = True

	if vat_accounts:
		existing = {r.account for r in doc.get("atc_base_tax_accounts") or [] if r.company == company}
		for vat_account in vat_accounts:
			if vat_account not in existing:
				doc.append("atc_base_tax_accounts", {"company": company, "account": vat_account})
				changed = True

	if changed:
		doc.save(ignore_permissions=True)


def _has_party_row(customer, category):
	return bool(
		frappe.db.exists(
			"Party Tax Withholding",
			{
				"parent": customer,
				"parenttype": "Customer",
				"parentfield": "atc_withholdings",
				"tax_withholding_category": category,
			},
		)
	)


def _ensure_party_row(customer, category):
	if _has_party_row(customer, category):
		return

	doc = frappe.get_doc("Customer", customer)
	doc.append(
		"atc_withholdings",
		{
			"tax_withholding_category": category,
			# No valid_from: the legacy flag had no start date, so the regime is
			# treated as having always applied. Set one by hand if you need to
			# stop withholding for older postings.
			"reference": "Migrated from enable_vat_exemption",
		},
	)
	doc.flags.ignore_mandatory = True
	doc.flags.ignore_validate_update_after_submit = True
	doc.save(ignore_permissions=True)
