# Copyright (c) 2026, ISOFT and contributors
# For license information, please see license.txt

"""Make ERPNext's financial reports treat Angola Closing Voucher like Period Closing Voucher.

ERPNext keeps the year's Profit and Loss readable after a closing by leaving
closing entries out of the reports that ask for it (``ignore_closing_entries``).
The test for that is a hard-coded ``voucher_type != 'Period Closing Voucher'``,
so the GL written by Angola Closing Voucher was counted as ordinary activity: once
a year was closed, its Profit and Loss report came out empty.

Both condition builders that carry that test are wrapped here, so every report
that ignores closing entries now ignores both voucher types:

- financial_statements.get_additional_conditions: Profit and Loss Statement,
  Trial Balance (period movements, unless "With Period Closing Entry" is ticked),
  the net-profit line of Cash Flow, and every other report built on
  financial_statements.get_data / set_gl_entries_by_account.
- consolidated_financial_statement.get_additional_conditions.

Reports that ask for closing entries (Balance Sheet) are unchanged and still
show the result in class 8 once a year is closed.

The wrap is applied by the before_request and before_job hooks, so it is active
in every web worker and background job before a report runs. It is idempotent.
"""

import functools

CLOSING_VOUCHER_TYPES = ("Period Closing Voucher", "Angola Closing Voucher")

_REPLACEMENTS = (
	(
		"ifnull(voucher_type, '')!='Period Closing Voucher'",
		"ifnull(voucher_type, '') not in ('Period Closing Voucher', 'Angola Closing Voucher')",
	),
	(
		"ifnull(gl.voucher_type, '')!='Period Closing Voucher'",
		"ifnull(gl.voucher_type, '') not in ('Period Closing Voucher', 'Angola Closing Voucher')",
	),
)

_PATCHED_FLAG = "_isoft_angola_closing_patched"


def _widen(conditions):
	for old, new in _REPLACEMENTS:
		conditions = conditions.replace(old, new)
	return conditions


def _wrap(module):
	original = module.get_additional_conditions
	if getattr(original, _PATCHED_FLAG, False):
		return False

	@functools.wraps(original)
	def get_additional_conditions(*args, **kwargs):
		return _widen(original(*args, **kwargs))

	setattr(get_additional_conditions, _PATCHED_FLAG, True)
	module.get_additional_conditions = get_additional_conditions
	return True


def apply(*args, **kwargs):
	"""Hook entry point (before_request / before_job). Safe to call repeatedly."""
	from erpnext.accounts.report import financial_statements
	from erpnext.accounts.report.consolidated_financial_statement import (
		consolidated_financial_statement,
	)

	_wrap(financial_statements)
	_wrap(consolidated_financial_statement)
