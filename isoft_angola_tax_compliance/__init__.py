
__version__ = '0.0.1'


def _install_runtime_patches():
	"""Layer Angola's chart of accounts and IVA tax templates onto ERPNext.

	ERPNext v13 discovers charts and country tax data by reading its own folders, with
	no hook to register another app's, so the lookups are wrapped here — the one point
	that runs in every process. Defensive by design: before erpnext is importable (a
	fresh `bench new-site`) it does nothing, and must never break the import.

	See angola_setup/__init__.py.
	"""
	try:
		from isoft_angola_tax_compliance.angola_setup import install_patches

		install_patches()
	except Exception:
		pass


_install_runtime_patches()
