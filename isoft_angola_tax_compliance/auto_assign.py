# Copyright (c) 2026, ISOFT and contributors
# For license information, please see license.txt
"""Stamps a withholding category onto items automatically.

The rule lives on the Tax Withholding Category itself rather than in a global
setting, so each category declares which items it claims. Two kinds of rule, and
they can be combined:

    All Non-Stock Items   every item with is_stock_item = 0
    Item Groups           listed groups, optionally including nested ones

Precedence, most specific first:

    1. an item group rule, nearest ancestor of the item's group wins
    2. the non-stock rule

That ordering matters: "non-stock" is not the same as "service" -- a re-billed
expense or a non-stock good is also non-stock, and getting that wrong is exactly
the defect the removed legacy code had. The group rule is the precise one; the
non-stock rule is the convenient blanket.

New items are stamped on insert. Existing items are deliberately untouched, so
turning a rule on cannot silently reprice history -- backfill them once, on
purpose:

    bench --site <site> execute isoft_angola_tax_compliance.auto_assign.backfill
    bench --site <site> execute isoft_angola_tax_compliance.auto_assign.backfill \\
        --kwargs '{"confirm": true}'
"""

import frappe

CATEGORY_FIELD = "atc_tax_withholding_category"
SCOPE_ITEM = "Item Based"

AUTO_FIELD = "atc_auto_assign"
NON_STOCK_FIELD = "atc_auto_assign_non_stock"
GROUPS_FIELD = "atc_auto_assign_item_groups"
APPLIES_TO_FIELD = "atc_applies_to"

MAX_GROUP_DEPTH = 20


def get_rules():
	"""Every category that claims items, with its rule.

	Not memoised: the per-category read goes through `get_cached_doc`, which is
	the expensive part, and a stale rule list would be a nasty class of bug for
	the sake of one small query per item insert.
	"""
	if not frappe.db.has_column("Tax Withholding Category", AUTO_FIELD):
		return []

	names = frappe.get_all(
		"Tax Withholding Category",
		filters={AUTO_FIELD: 1},
		pluck="name",
		order_by="name asc",
	)

	rules = []
	for name in names:
		doc = frappe.get_cached_doc("Tax Withholding Category", name)
		if (doc.get(APPLIES_TO_FIELD) or SCOPE_ITEM) != SCOPE_ITEM:
			# A party-based category cannot be resolved from an item.
			continue

		rules.append(
			{
				"category": name,
				"non_stock": bool(doc.get(NON_STOCK_FIELD)),
				"groups": [
					{"item_group": r.item_group, "include_children": bool(r.include_child_groups)}
					for r in (doc.get(GROUPS_FIELD) or [])
					if r.item_group
				],
			}
		)

	return rules


def _ancestors(item_group):
	"""The group itself, then each parent up to the root."""
	chain = []
	current = item_group
	seen = set()

	for _ in range(MAX_GROUP_DEPTH):
		if not current or current in seen:
			break
		seen.add(current)
		chain.append(current)
		current = frappe.get_cached_value("Item Group", current, "parent_item_group")

	return chain


def resolve(item_group=None, is_stock_item=None, rules=None):
	"""Which category claims an item with this group / stock flag?

	Returns None when nothing matches. An item group rule beats the non-stock
	rule, and among group rules the nearest ancestor wins.
	"""
	rules = get_rules() if rules is None else rules
	if not rules:
		return None

	chain = _ancestors(item_group) if item_group else []

	best = None  # (distance from the item's own group, category)
	for rule in rules:
		for entry in rule["groups"]:
			group = entry["item_group"]
			if group not in chain:
				continue

			distance = chain.index(group)
			if distance > 0 and not entry["include_children"]:
				# Listed as an exact group only; the item sits below it.
				continue

			if best is None or distance < best[0]:
				best = (distance, rule["category"])

	if best:
		return best[1]

	if not is_stock_item:
		for rule in rules:
			if rule["non_stock"]:
				return rule["category"]

	return None


def apply_to_item(doc, method=None):
	"""`doc_events` handler: stamp the category on a newly created item.

	Wired to `before_insert`, so "only on new items" is guaranteed by the hook
	point -- no `is_new()` check here. (`is_new()` is just `__islocal`, which is
	unset on server-constructed documents, so testing it would silently skip
	every item created from code.)

	Only fills an empty field, so a deliberate choice is never overwritten.
	"""
	if not frappe.get_meta("Item").get_field(CATEGORY_FIELD):
		return

	if doc.get(CATEGORY_FIELD):
		return

	category = resolve(doc.get("item_group"), doc.get("is_stock_item"))
	if category:
		doc.set(CATEGORY_FIELD, category)


def diagnose(item=None, item_group=None):
	"""Why did an item not get a category? Reports every link in the chain.

		bench --site <site> execute isoft_angola_tax_compliance.auto_assign.diagnose \\
			--kwargs '{"item": "SRV-004"}'
	"""
	print("=" * 74)
	print("  AUTO-ASSIGN DIAGNOSTIC")
	print("=" * 74)

	# 1. is the code even deployed?
	meta = frappe.get_meta("Tax Withholding Category", cached=False)
	fields_present = all(meta.get_field(f) for f in (AUTO_FIELD, NON_STOCK_FIELD, GROUPS_FIELD))
	print(f"\n  1. rule fields on Tax Withholding Category : {'yes' if fields_present else 'NO'}")
	if not fields_present:
		print("       -> the app is not migrated on this site. Run `bench migrate`, then")
		print("          `bench restart` (workers preload, so code changes need it).")
		return {"deployed": False}

	print(f"     Item.{CATEGORY_FIELD} exists              : "
		f"{'yes' if frappe.get_meta('Item').get_field(CATEGORY_FIELD) else 'NO'}")

	# 2. which categories actually claim items?
	enabled = frappe.get_all(
		"Tax Withholding Category",
		filters={AUTO_FIELD: 1},
		fields=["name", APPLIES_TO_FIELD, NON_STOCK_FIELD],
	)
	print(f"\n  2. categories with 'Auto-assign to New Items' ticked : {len(enabled)}")
	for row in enabled:
		scope = row.get(APPLIES_TO_FIELD)
		note = "" if (scope or SCOPE_ITEM) == SCOPE_ITEM else f"   !! scope is '{scope}', not Item Based -- IGNORED"
		print(f"       {row.name}{note}")
	if not enabled:
		print("       -> nothing will ever be stamped. Tick it on the category.")
		print("          The section only shows when Scope is 'Item Based'.")

	rules = get_rules()
	print(f"\n  3. usable rules : {len(rules)}")
	for rule in rules:
		groups = ", ".join(
			g["item_group"] + ("*" if g["include_children"] else "") for g in rule["groups"]
		)
		print(f"       {rule['category']}")
		print(f"           all non-stock : {rule['non_stock']}")
		print(f"           item groups   : {groups or '-'}      (* = includes child groups)")

	# 4. what would happen for this item / group?
	if item:
		row = frappe.db.get_value(
			"Item", item, ["name", "item_group", "is_stock_item", CATEGORY_FIELD], as_dict=True
		)
		if not row:
			print(f"\n  4. item {item} does not exist")
			return {"deployed": True, "rules": len(rules)}

		print(f"\n  4. item {row.name}")
		print(f"       item_group            : {row.item_group}")
		print(f"       is_stock_item         : {row.is_stock_item}")
		print(f"       group ancestry        : {' -> '.join(_ancestors(row.item_group)) or '-'}")
		print(f"       category set today    : {row.get(CATEGORY_FIELD) or '(empty)'}")
		would = resolve(row.item_group, row.is_stock_item, rules)
		print(f"       a rule would assign   : {would or '(nothing matches)'}")
		if row.get(CATEGORY_FIELD):
			print("       -> already set; auto-assign never overwrites an existing value.")
		elif would:
			print("       -> this item pre-dates the rule. Auto-assign only stamps items at")
			print("          creation; run auto_assign.backfill to fill existing ones.")
		return {"deployed": True, "rules": len(rules), "would_assign": would}

	if item_group:
		print(f"\n  4. group {item_group}")
		print(f"       ancestry : {' -> '.join(_ancestors(item_group)) or '-'}")
		for stock in (0, 1):
			print(f"       is_stock_item={stock} -> {resolve(item_group, stock, rules) or '(nothing)'}")

	print("\n  Item groups that exist (check for near-duplicates like Services / SERVICOS):")
	for group in frappe.get_all("Item Group", filters={"is_group": 0}, pluck="name", limit=40):
		print(f"       {group}")

	return {"deployed": True, "rules": len(rules)}


def backfill(confirm=False, limit=None):
	"""Apply the rules to existing items. Dry run unless confirm=True.

	Only fills items whose category is empty; a value set by hand is never
	touched.
	"""
	rules = get_rules()

	print("=" * 74)
	print("  AUTO-ASSIGN BACKFILL" + ("" if confirm else "   [DRY RUN -- nothing written]"))
	print("=" * 74)

	if not rules:
		print("\n  No category has 'Auto-assign to New Items' enabled. Nothing to do.")
		return {"rules": 0, "matched": 0}

	print("\n  Rules")
	for rule in rules:
		groups = ", ".join(
			g["item_group"] + ("*" if g["include_children"] else "") for g in rule["groups"]
		)
		print(f"    {rule['category']}")
		print(f"        non-stock : {rule['non_stock']}")
		print(f"        groups    : {groups or '-'}   (* = includes child groups)")

	items = frappe.get_all(
		"Item",
		filters={CATEGORY_FIELD: ["in", ["", None]]},
		fields=["name", "item_group", "is_stock_item"],
		limit_page_length=limit or 0,
		order_by="name asc",
	)

	matched = {}
	for item in items:
		category = resolve(item.item_group, item.is_stock_item, rules)
		if category:
			matched.setdefault(category, []).append(item)

	print(f"\n  Items with no category : {len(items)}")
	print(f"  Matched by a rule      : {sum(len(v) for v in matched.values())}")
	for category, rows in matched.items():
		print(f"      {category}: {len(rows)}")
		for row in rows[:5]:
			print(f"          {row.name[:28]:30} group={str(row.item_group)[:20]:22} stock={row.is_stock_item}")
		if len(rows) > 5:
			print(f"          ... and {len(rows) - 5} more")

	if not confirm:
		print("\n  DRY RUN -- nothing written. Re-run with --kwargs '{\"confirm\": true}'.")
		return {"rules": len(rules), "matched": sum(len(v) for v in matched.values())}

	updated = 0
	for category, rows in matched.items():
		for row in rows:
			frappe.db.set_value("Item", row.name, CATEGORY_FIELD, category, update_modified=False)
			updated += 1

	frappe.db.commit()
	frappe.clear_cache()
	print(f"\n  Updated {updated} item(s).")
	return {"rules": len(rules), "matched": updated, "applied": True}
