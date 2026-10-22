"""Angola's chart of accounts and IVA tax templates, shipped by this app.

Two setup-time datasets used to sit in ERPNext core:

  * the verified chart `Angola - Plano Geral de Contabilidade (PGC)`, which is not an
    upstream chart at all — it was added to the fork;
  * the `Angola` entry in the setup wizard's country_wise_tax.json, which the fork
    rewrote. Upstream ships a single 10% "Angola VAT"; the fork replaced it with IVA
    at 14% booked to the PGC accounts (3453 IVA Liquidado, 34523 IVA Dedutível).

Both now live here. ERPNext v13 has no hook for either — despite what is often
assumed, `chart_of_accounts` as a hook arrives in later versions; in v13 both
get_chart() and get_charts_for_country() scan erpnext's own `verified/` folder by
absolute path, and setup_taxes_and_charges() reads its JSON the same way. So this
module layers itself on with three wrappers, installed from the app's __init__.

All three are genuine wrappers, not copies: each calls the ERPNext original and adds
to, or substitutes for, its result. Nothing of upstream's logic is duplicated, so an
ERPNext upgrade that rewrites those functions keeps working — the wrapper simply has
less to do.

Both datasets are inert until a Company is created, so nothing here touches an
existing site.
"""
import json
import os

import frappe

COUNTRY = "Angola"
_HERE = os.path.dirname(os.path.abspath(__file__))
CHART_DIR = os.path.join(_HERE, "chart_of_accounts")
TAX_DATA = os.path.join(_HERE, "data", "country_wise_tax.json")

_patched = False
_upstream_get_chart = None
_upstream_get_charts_for_country = None
_upstream_setup_taxes_and_charges = None


def _enabled():
	"""Whether this app should supply Angola's chart and tax templates.

	Reads the toggle if the settings record exists; defaults to on, so the behaviour
	matches what ERPNext core did before these datasets moved here.
	"""
	try:
		value = frappe.db.get_single_value(
			"Angola NIF Validation Settings", "provide_angola_chart_and_taxes"
		)
	except Exception:
		return True
	return True if value is None else bool(value)


# ---------- chart of accounts ----------

def _read_charts():
	"""[(chart name, parsed content)] for every chart JSON this app ships."""
	charts = []
	if not os.path.isdir(CHART_DIR):
		return charts
	for fname in sorted(os.listdir(CHART_DIR)):
		if not fname.endswith(".json"):
			continue
		try:
			with open(os.path.join(CHART_DIR, fname), "r", encoding="utf-8") as f:
				content = json.load(f)
		except Exception:
			continue
		if content.get("name"):
			charts.append((content["name"], content))
	return charts


@frappe.whitelist()
def get_charts_for_country(country, with_standard=False):
	"""ERPNext's chart list for a country, plus any this app ships for it.

	Whitelisted because it stands in for a whitelisted ERPNext method: the setup wizard
	and the Company form call it over HTTP, and frappe refuses a replacement function
	that is not itself whitelisted — the chart dropdown then comes back empty.
	"""
	install_patches()
	charts = list(_upstream_get_charts_for_country(country, with_standard) or [])
	if not _enabled():
		return charts

	country_code = frappe.db.get_value("Country", country, "code")
	for name, content in _read_charts():
		if name in charts:
			continue
		if content.get("country_code") == country_code or content.get("country") == country:
			charts.insert(0, name)

	# Upstream appends the two standard charts unless it found exactly one chart for
	# the country. Adding ours can change that count, so re-apply the same rule.
	standards = ["Standard", "Standard with Numbers"]
	real = [c for c in charts if c not in standards]
	if len(real) == 1 and not with_standard:
		return real
	for std in standards:
		if std not in charts:
			charts.append(std)
	return charts


def get_chart(chart_template, existing_company=None):
	"""Serve this app's chart when ERPNext does not recognise the template name."""
	chart = _upstream_get_chart(chart_template, existing_company)
	if chart or existing_company or not _enabled():
		return chart
	for name, content in _read_charts():
		if name == chart_template:
			return content.get("tree") or {}
	return chart


# ---------- company default accounts ----------

# Company field -> PGC number of the ledger it defaults to. ERPNext's own pass can't
# get these right on a Portuguese chart: it takes whichever Receivable / Payable / Bank
# ledger the database returns first, and looks the income account up by the English
# name "Sales", so it is left empty. Group structure and these choices follow ITEC LDA.
DEFAULT_ACCOUNTS = {
	"default_receivable_account": "31121000",  # Clientes Indiferenciados
	"default_payable_account": "32100000",  # Fornecedores Nacionais - Diversos
	"default_cash_account": "45101",  # Caixa - Sede
	"default_bank_account": "431011",  # Banco Principal - Conta Corrente
	"default_income_account": "61301",  # Vendas de Mercadorias
	"default_expense_account": "7161",  # Custo das Mercadorias Vendidas e Consumidas
	"round_off_account": "7581",  # Arredondamentos
	"write_off_account": "7581",
	"exchange_gain_loss_account": "78112",  # Diferenças de Câmbio
	"default_payroll_payable_account": "36110000",  # Pessoal - Remunerações a Pagar
	"default_inventory_account": "261101",  # Existências - Armazém Central
	"stock_adjustment_account": "7841",  # Quebras e Ajustes de Existências
	"stock_received_but_not_billed": "3281",
	"expenses_included_in_valuation": "21228",
	"accumulated_depreciation_account": "1819",
	"depreciation_expense_account": "7319",
	"capital_work_in_progress_account": "141",
	"asset_received_but_not_billed": "3711",
	"expenses_included_in_asset_valuation": "7583",
}


def flag_new_chart(doc, method=None):
	"""Company validate: remember whether this save is the one that creates the chart.

	Mirrors the condition in Company.on_update, which creates the accounts only for a
	company that has none yet.
	"""
	doc.flags.angola_new_chart = bool(
		doc.chart_of_accounts
		and not frappe.local.flags.ignore_chart_of_accounts
		and doc.chart_of_accounts in dict(_read_charts())
		and not frappe.db.exists("Account", {"company": doc.name})
	)


def set_company_default_accounts(doc, method=None):
	"""Company on_update: once, right after this app's chart was installed for it.

	Runs after ERPNext's own on_update, so it replaces the guesses made there. A field
	whose account the company does not have is left as ERPNext set it.
	"""
	if not doc.flags.angola_new_chart or not _enabled():
		return
	doc.flags.angola_new_chart = False
	erpnext_cash_account = doc.default_cash_account

	for fieldname, number in DEFAULT_ACCOUNTS.items():
		if not doc.meta.has_field(fieldname):
			continue
		account = frappe.db.get_value(
			"Account", {"company": doc.name, "account_number": number, "is_group": 0}
		)
		if account and doc.get(fieldname) != account:
			doc.db_set(fieldname, account)

	# ERPNext linked the Cash mode of payment to its own pick a moment ago.
	if erpnext_cash_account and doc.default_cash_account != erpnext_cash_account:
		frappe.db.set_value(
			"Mode of Payment Account",
			{"company": doc.name, "default_account": erpnext_cash_account},
			"default_account",
			doc.default_cash_account,
		)


# ---------- country tax templates ----------

def _app_tax_data():
	try:
		with open(TAX_DATA, "r", encoding="utf-8") as f:
			return json.load(f)
	except Exception:
		return {}


def setup_taxes_and_charges(company_name, country):
	"""Use this app's tax templates for the countries it covers; defer otherwise.

	Mirrors upstream's own dispatch — detect the simple format, expand it, apply it,
	then run the regional hook — by calling upstream's helpers rather than reimplementing
	them.
	"""
	data = _app_tax_data().get(country) if _enabled() else None
	if not data:
		return _upstream_setup_taxes_and_charges(company_name, country)

	from erpnext.setup.setup_wizard.operations.taxes_setup import (
		from_detailed_data,
		simple_to_detailed,
		update_regional_tax_settings,
	)

	if not frappe.db.exists("Company", company_name):
		frappe.throw(frappe._("Company {} does not exist yet. Taxes setup aborted.").format(company_name))

	if "chart_of_accounts" not in data:
		data = simple_to_detailed(data)

	from_detailed_data(company_name, data)
	update_regional_tax_settings(country, company_name)

	# Tax categories, the IVA item tax templates and the withholding categories.
	# After the templates above, which are what create IVA Liquidado on a company
	# whose chart does not have it. Behind a savepoint: these are conveniences, and
	# a failure here must not stop the company from being created.
	from isoft_angola_tax_compliance.angola_setup.defaults import setup_company

	frappe.db.savepoint("angola_tax_defaults")
	try:
		setup_company(company_name)
	except Exception:
		frappe.db.rollback(save_point="angola_tax_defaults")
		frappe.log_error(frappe.get_traceback(), "Angola tax defaults")


# ---------- installation ----------

def ensure_loaded(doc=None, method=None):
	"""Company doc event. Does nothing itself: resolving it imports this app, which
	installs the wrappers in the worker about to create the company's accounts and
	taxes. Without it a worker that never touched this app would use ERPNext's
	lookups and not find the Angola chart.
	"""
	install_patches()


_TARGET_MODULES = {
	"erpnext.accounts.doctype.account.chart_of_accounts.chart_of_accounts": (
		("get_chart", "get_chart"),
		("get_charts_for_country", "get_charts_for_country"),
	),
	"erpnext.accounts.doctype.company.company": (("get_charts_for_country", "get_charts_for_country"),),
	"erpnext.setup.setup_wizard.operations.taxes_setup": (
		("setup_taxes_and_charges", "setup_taxes_and_charges"),
	),
	"erpnext.setup.doctype.company.company": (
		("setup_taxes_and_charges", "setup_taxes_and_charges"),
	),
}


def install_patches():
	"""Point ERPNext's chart lookup and company tax setup at this app's data.

	Idempotent, and a no-op when ERPNext is not importable yet.
	"""
	global _patched, _upstream_get_chart, _upstream_get_charts_for_country
	global _upstream_setup_taxes_and_charges

	if _patched:
		return True
	try:
		import importlib

		coa = importlib.import_module(
			"erpnext.accounts.doctype.account.chart_of_accounts.chart_of_accounts"
		)
		taxes = importlib.import_module("erpnext.setup.setup_wizard.operations.taxes_setup")
	except Exception:
		return False

	if coa.get_chart is get_chart:
		_patched = True
		return True

	_upstream_get_chart = coa.get_chart
	_upstream_get_charts_for_country = coa.get_charts_for_country
	_upstream_setup_taxes_and_charges = taxes.setup_taxes_and_charges

	import importlib

	for module_name, names in _TARGET_MODULES.items():
		try:
			mod = importlib.import_module(module_name)
		except Exception:
			continue
		for attr, own in names:
			if hasattr(mod, attr):
				setattr(mod, attr, globals()[own])

	_patched = True
	return True
