from . import __version__ as app_version

app_name = "isoft_angola_tax_compliance"
app_title = "Isoft Angola Tax Compliance"
app_publisher = "ISOFT"
app_description = "Angolan tax compliance - retencao na fonte (Imposto Industrial) and IVA cativo withholding"
app_icon = "octicon octicon-file-directory"
app_color = "grey"
app_email = "abbasschokor225@gmail.com"
app_license = "MIT"

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/isoft_angola_tax_compliance/css/isoft_angola_tax_compliance.css"
# app_include_js = "/assets/isoft_angola_tax_compliance/js/isoft_angola_tax_compliance.js"

# include js, css files in header of web template
# web_include_css = "/assets/isoft_angola_tax_compliance/css/isoft_angola_tax_compliance.css"
# web_include_js = "/assets/isoft_angola_tax_compliance/js/isoft_angola_tax_compliance.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "isoft_angola_tax_compliance/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# doctype_js = {"doctype" : "public/js/doctype.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
#	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# Installation
# ------------

# before_install = "isoft_angola_tax_compliance.install.before_install"
# # Live preview of the withholding rows before save (server validate stays
# authoritative). Frappe merges doctype_js across apps, so this stacks with the
# Sales Invoice JS already contributed by isoft_picking / AGT / intelize.
app_include_js = "/assets/isoft_angola_tax_compliance/js/withholding_preview.js"

doctype_js = {
	"Sales Invoice": "public/js/sales_invoice.js",
	"Quotation": "public/js/quotation.js",
	"Tax Withholding Category": "public/js/tax_withholding_category.js",
	# "Generate Account" for parties without an account row, moved out of ERPNext's
	# own customer.js / supplier.js. Frappe merges doctype_js across apps, so these
	# stack with ERPNext's rather than replacing them.
	"Customer": "public/js/customer.js",
	"Supplier": "public/js/supplier.js",
}

# Quotation posts no GL, so it needs no controller override -- only the
# computation, so the quoted net-of-withholding figure is visible and printable.
doc_events = {
	"Quotation": {
		"validate": [
			"isoft_angola_tax_compliance.withholding.apply.set_withholdings",
			"isoft_angola_tax_compliance.nif.validate_transaction",
		],
	},
	"Sales Invoice": {
		"validate": "isoft_angola_tax_compliance.nif.validate_transaction",
	},
	# Stamp the withholding category on newly created items, per the rules on
	# the categories themselves. Insert only, and only when the field is empty.
	"Item": {
		"before_insert": "isoft_angola_tax_compliance.auto_assign.apply_to_item",
	},
	# Angolan NIF format check. Off until enabled in Angola NIF Validation
	# Settings, and warns rather than blocks unless configured otherwise.
	"Customer": {
		"validate": "isoft_angola_tax_compliance.nif.validate_customer",
	},
}

after_install = "isoft_angola_tax_compliance.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "isoft_angola_tax_compliance.uninstall.before_uninstall"
# after_uninstall = "isoft_angola_tax_compliance.uninstall.after_uninstall"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "isoft_angola_tax_compliance.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
#	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
#	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# DocType Class
# ---------------
# Override standard doctype classes

# override_doctype_class = {
#	"ToDo": "custom_app.overrides.CustomToDo"
# }

# Document Events
# ---------------
# Hook on document methods and events

# doc_events = {
#	"*": {
#		"on_update": "method",
#		"on_cancel": "method",
#		"on_trash": "method"
#	}
# }

# Scheduled Tasks
# ---------------

# scheduler_events = {
#	"all": [
#		"isoft_angola_tax_compliance.tasks.all"
#	],
#	"daily": [
#		"isoft_angola_tax_compliance.tasks.daily"
#	],
#	"hourly": [
#		"isoft_angola_tax_compliance.tasks.hourly"
#	],
#	"weekly": [
#		"isoft_angola_tax_compliance.tasks.weekly"
#	]
#	"monthly": [
#		"isoft_angola_tax_compliance.tasks.monthly"
#	]
# }

# Testing
# -------

# before_tests = "isoft_angola_tax_compliance.install.before_tests"

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
#	"frappe.desk.doctype.event.event.get_events": "isoft_angola_tax_compliance.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
#	"Task": "isoft_angola_tax_compliance.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Request Events
# ----------------
# before_request = ["isoft_angola_tax_compliance.utils.before_request"]
# after_request = ["isoft_angola_tax_compliance.utils.after_request"]

# Job Events
# ----------
# before_job = ["isoft_angola_tax_compliance.utils.before_job"]
# after_job = ["isoft_angola_tax_compliance.utils.after_job"]

# User Data Protection
# --------------------

user_data_fields = [
	{
		"doctype": "{doctype_1}",
		"filter_by": "{filter_by}",
		"redact_fields": ["{field_1}", "{field_2}"],
		"partial": 1,
	},
	{
		"doctype": "{doctype_2}",
		"filter_by": "{filter_by}",
		"partial": 1,
	},
	{
		"doctype": "{doctype_3}",
		"strict": False,
	},
	{
		"doctype": "{doctype_4}"
	}
]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
#	"isoft_angola_tax_compliance.auth.validate"
# ]



# ---------------------------------------------------------------------------
# Isoft Angola Tax Compliance
# ---------------------------------------------------------------------------
# Adds the withholding computation + GL entries to Sales Invoice, and lets the
# engine neutralise the legacy in-core path at runtime, without editing any
# ERPNext file. See overrides/sales_invoice.py.
override_doctype_class = {
	"Sales Invoice": "isoft_angola_tax_compliance.overrides.sales_invoice.AngolaSalesInvoice",
	# Valuation taxes owed to a party post against that party. Pass-through when the
	# tax row names none, which is every Purchase Receipt on this site so far.
	"Purchase Receipt": "isoft_angola_tax_compliance.overrides.purchase_receipt.AngolaPurchaseReceipt",
}

# Live preview of the withholding rows before save (server validate stays
# authoritative). Frappe merges doctype_js across apps, so this stacks with the
# Sales Invoice JS already contributed by isoft_picking / AGT / intelize.
app_include_js = "/assets/isoft_angola_tax_compliance/js/withholding_preview.js"

doctype_js = {
	"Sales Invoice": "public/js/sales_invoice.js",
	"Quotation": "public/js/quotation.js",
	"Tax Withholding Category": "public/js/tax_withholding_category.js",
	# "Generate Account" for parties without an account row, moved out of ERPNext's
	# own customer.js / supplier.js. Frappe merges doctype_js across apps, so these
	# stack with ERPNext's rather than replacing them.
	"Customer": "public/js/customer.js",
	"Supplier": "public/js/supplier.js",
}

# Quotation posts no GL, so it needs no controller override -- only the
# computation, so the quoted net-of-withholding figure is visible and printable.
doc_events = {
	"Quotation": {
		"validate": [
			"isoft_angola_tax_compliance.withholding.apply.set_withholdings",
			"isoft_angola_tax_compliance.nif.validate_transaction",
		],
	},
	"Sales Invoice": {
		"validate": "isoft_angola_tax_compliance.nif.validate_transaction",
	},
	# Stamp the withholding category on newly created items, per the rules on
	# the categories themselves. Insert only, and only when the field is empty.
	"Item": {
		"before_insert": "isoft_angola_tax_compliance.auto_assign.apply_to_item",
	},
	# Angolan NIF format check. Off until enabled in Angola NIF Validation
	# Settings, and warns rather than blocks unless configured otherwise.
	"Customer": {
		"validate": "isoft_angola_tax_compliance.nif.validate_customer",
	},
}

after_install = "isoft_angola_tax_compliance.install.after_install"
after_migrate = "isoft_angola_tax_compliance.install.after_migrate"
