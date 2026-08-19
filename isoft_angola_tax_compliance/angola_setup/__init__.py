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


def get_charts_for_country(country, with_standard=False):
	"""ERPNext's chart list for a country, plus any this app ships for it."""
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


# ---------- installation ----------

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
