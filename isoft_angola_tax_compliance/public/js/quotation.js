// Copyright (c) 2026, ISOFT and contributors
// For license information, please see license.txt
//
// Live withholding preview on Quotation -- indicative only, a Quotation posts
// no GL. Shared logic lives in withholding_preview.js.

isoft_atc.bind("Quotation", "Quotation Item", [
	"party_name",
	"quotation_to",
	"company",
	"transaction_date",
	"currency",
	"conversion_rate",
	"taxes_and_charges",
]);
