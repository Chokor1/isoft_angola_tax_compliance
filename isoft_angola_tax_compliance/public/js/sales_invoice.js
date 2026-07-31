// Copyright (c) 2026, ISOFT and contributors
// For license information, please see license.txt
//
// Live withholding preview on Sales Invoice. Shared logic lives in
// withholding_preview.js (loaded desk-wide via app_include_js).

isoft_atc.bind("Sales Invoice", "Sales Invoice Item", [
	"customer",
	"company",
	"posting_date",
	"currency",
	"conversion_rate",
	"taxes_and_charges",
	"is_pos",
	"is_return",
]);
