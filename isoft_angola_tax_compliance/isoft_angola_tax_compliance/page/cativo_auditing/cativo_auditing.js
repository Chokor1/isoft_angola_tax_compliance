// Copyright (c) 2026, ISOFT and contributors
// For license information, please see license.txt
//
// Cativo Auditing: every sales invoice with IVA cativo in the ERP, by source (new
// engine, the old tool's automatic JE, manual JE, manual receipt), with the documents
// it was booked on. Read-only. The analysis runs on the server
// (isoft_angola_tax_compliance.cativo_audit.api); this page only filters, sorts and
// draws it. Wrapped in an IIFE so nothing leaks into the desk's globals.

(function () {
	"use strict";

	const API = "isoft_angola_tax_compliance.cativo_audit.api";
	const esc = (v) => frappe.utils.escape_html(v == null ? "" : String(v));

	const COLUMNS = [
		{ key: "invoice", label: __("Invoice"), kind: "text" },
		{ key: "date", label: __("Date"), kind: "date" },
		{ key: "customer", label: __("Customer"), kind: "text", wide: true },
		{ key: "nif", label: __("NIF"), kind: "text" },
		{ key: "status", label: __("Status"), kind: "text" },
		{ key: "iva", label: __("IVA"), kind: "money" },
		{ key: "engine", label: __("New engine"), kind: "money" },
		{ key: "legacy_je", label: __("Automatic JE"), kind: "money" },
		{ key: "manual_je", label: __("Manual JE"), kind: "money" },
		{ key: "manual_pe", label: __("Manual receipt"), kind: "money" },
		{ key: "cativo", label: __("Cativo total"), kind: "money" },
		{ key: "pct", label: __("% of IVA"), kind: "pct" },
		{ key: "rate", label: __("Rate"), kind: "text" },
		{ key: "creation", label: __("Creation"), kind: "text" },
		{ key: "documents", label: __("Cativo documents"), kind: "docs" },
		{ key: "category", label: __("Customer IVA category"), kind: "text" },
		{ key: "alerts", label: __("Alerts"), kind: "alerts" },
	];
	const MONEY = COLUMNS.filter((c) => c.kind === "money").map((c) => c.key);
	const CREATION_TONE = { System: "green", Manual: "amber", "System + Manual": "red", Reversed: "gray" };

	// Number format follows the system setting ("#.###,##", "# ###,##", "#,###.##" …).
	const LOCALE = { "#.###,##": "de-DE", "# ###,##": "fr-FR", "#,###.##": "en-US", "#'###.##": "de-CH" };
	const money_fmt = new Intl.NumberFormat(LOCALE[(frappe.boot.sysdefaults || {}).number_format] || "en-US", {
		minimumFractionDigits: 2,
		maximumFractionDigits: 2,
	});
	const money = (v) => (v == null || v === "" ? "" : money_fmt.format(v));
	const pct = (v) => (v == null || v === "" ? "" : money_fmt.format(v * 100).replace(/[.,]\d\d$/, (m) => m.slice(0, 2)) + "%");

	frappe.pages["cativo-auditing"].on_page_load = function (wrapper) {
		const page = frappe.ui.make_app_page({ parent: wrapper, title: __("Cativo Auditing"), single_column: true });
		// The page draws its own header (title + actions); drop the desk's title bar on this page only.
		$(wrapper).find(".page-head").addClass("hide");
		wrapper.cativo_auditing = new CativoAuditing(page);
	};

	class CativoAuditing {
		constructor(page) {
			this.page = page;
			this.data = { rows: [], unassigned: { amount: 0, count: 0, entries: [] }, warnings: [] };
			this.filters = { q: "", creation: "", rate: "", alerts: false };
			this.sort = { key: "date", dir: 1 };
			this.make();
			this.load_companies();
		}

		make() {
			const select = (role, options) =>
				`<select class="ca-input" data-role="${role}">${options
					.map(([v, l]) => `<option value="${esc(v)}">${esc(l)}</option>`)
					.join("")}</select>`;
			this.$root = $(`
				<div class="ca-root">
					<div class="ca-head">
						<div>
							<div class="ca-title">${__("Cativo Auditing")}</div>
							<div class="ca-sub">${__("Every sales invoice with IVA cativo in the ERP, by where the cativo was booked")}</div>
						</div>
						<div class="ca-actions">
							<button type="button" class="ca-btn" data-action="settings" title="${__("Cativo Audit Settings")}"><i class="fa fa-cog"></i></button>
							<button type="button" class="ca-btn" data-action="refresh"><i class="fa fa-refresh"></i> ${__("Refresh")}</button>
							<button type="button" class="ca-btn ca-btn-primary" data-action="export"><i class="fa fa-file-excel-o"></i> ${__("Export")}</button>
						</div>
					</div>

					<div class="ca-filters">
						<label class="ca-field"><span>${__("Company")}</span><select class="ca-input" data-role="company"></select></label>
						<label class="ca-field"><span>${__("From")}</span><input type="date" class="ca-input" data-role="from" value="${frappe.datetime.year_start()}"></label>
						<label class="ca-field"><span>${__("To")}</span><input type="date" class="ca-input" data-role="to" value="${frappe.datetime.year_end()}"></label>
						<label class="ca-field ca-grow"><span>${__("Search")}</span><input type="search" class="ca-input" data-role="q" placeholder="${__("Invoice, customer, NIF, document or alert")}"></label>
						<label class="ca-field"><span>${__("Creation")}</span>${select("creation", [
							["", __("All")], ["System", __("System")], ["Manual", __("Manual")],
							["System + Manual", __("System + Manual")], ["Reversed", __("Reversed")],
						])}</label>
						<label class="ca-field"><span>${__("Rate")}</span>${select("rate", [
							["", __("All")], ["100%", "100%"], ["50%", "50%"], ["Other", __("Other")],
						])}</label>
						<label class="ca-check"><input type="checkbox" data-role="alerts"> ${__("Only with alerts")}</label>
					</div>

					<div class="ca-notice" data-role="warnings" hidden></div>

					<div class="ca-card">
						<div class="ca-card-head">
							<div class="ca-card-title">${__("Invoices with cativo")}</div>
							<div class="ca-card-note">${__("Invoices are selected by invoice date; their cativo counts whatever its posting date.")}</div>
						</div>
						<div class="ca-scroll">
							<table class="ca-table">
								<thead><tr>${COLUMNS.map(
									(c) => `<th class="${c.kind === "money" || c.kind === "pct" ? "ca-num" : ""}" data-sort="${c.key}">${esc(c.label)}</th>`
								).join("")}</tr></thead>
								<tbody data-role="tbody"></tbody>
								<tfoot data-role="tfoot"></tfoot>
							</table>
						</div>
						<div class="ca-unassigned" data-role="unassigned"></div>
					</div>
				</div>`).appendTo(this.page.main);

			const $r = this.$root;
			$r.on("change", '[data-role="company"], [data-role="from"], [data-role="to"]', () => this.load());
			$r.on("input", '[data-role="q"]', (e) => {
				this.filters.q = e.currentTarget.value;
				this.paint();
			});
			$r.on("change", '[data-role="creation"], [data-role="rate"], [data-role="alerts"]', () => {
				this.filters.creation = $r.find('[data-role="creation"]').val();
				this.filters.rate = $r.find('[data-role="rate"]').val();
				this.filters.alerts = $r.find('[data-role="alerts"]').is(":checked");
				this.paint();
			});
			$r.on("click", "th[data-sort]", (e) => {
				const key = e.currentTarget.dataset.sort;
				const numeric = MONEY.includes(key) || key === "pct";
				this.sort = this.sort.key === key ? { key, dir: -this.sort.dir } : { key, dir: numeric ? -1 : 1 };
				this.paint();
			});
			// v13's router ignores target=_blank on /app links; open documents ourselves.
			$r.on("click", "a[data-doc]", (e) => {
				e.preventDefault();
				e.stopPropagation();
				const a = e.currentTarget;
				window.open(`/app/${frappe.router.slug(a.dataset.doctype)}/${encodeURIComponent(a.dataset.doc)}`, "_blank");
			});
			$r.on("click", '[data-action="refresh"]', () => this.load());
			$r.on("click", '[data-action="export"]', () => this.export());
			$r.on("click", '[data-action="settings"]', () => frappe.set_route("Form", "Cativo Audit Settings"));
		}

		load_companies() {
			frappe.db.get_list("Company", { fields: ["name"], order_by: "name asc", limit: 100 }).then((rows) => {
				const names = rows.map((r) => r.name);
				const $c = this.$root.find('[data-role="company"]');
				$c.html(names.map((n) => `<option value="${esc(n)}">${esc(n)}</option>`).join(""));
				const preferred = frappe.defaults.get_user_default("company") || frappe.defaults.get_default("company");
				if (names.includes(preferred)) $c.val(preferred);
				this.load();
			});
		}

		load() {
			const $r = this.$root;
			const company = $r.find('[data-role="company"]').val();
			if (!company) return;
			this.state = "loading";
			this.paint();
			let settled = false;
			frappe
				.call({
					method: `${API}.get_cativo_audit`,
					args: { company, from_date: $r.find('[data-role="from"]').val() || null, to_date: $r.find('[data-role="to"]').val() || null },
					callback: (r) => {
						settled = true;
						this.data = r.message || this.data;
						this.state = "ready";
					},
					error: (r) => {
						settled = true;
						this.state = "error";
						this.error = (r && r.message) || __("The audit could not be loaded.");
					},
				})
				.always(() => {
					// v13 may call neither callback (502 / 403 / timeouts): settle here.
					if (!settled) {
						this.state = "error";
						this.error = __("The audit could not be loaded. Try again.");
					}
					this.paint();
				});
		}

		shown() {
			const f = this.filters;
			const q = (f.q || "").toLowerCase().trim();
			const rows = (this.data.rows || []).filter((r) => {
				if (f.creation && r.creation !== f.creation) return false;
				if (f.rate && r.rate !== f.rate) return false;
				if (f.alerts && !(r.alerts || []).length) return false;
				if (!q) return true;
				const text = [r.invoice, r.customer, r.nif, r.category, (r.alerts || []).join(" "), (r.documents || []).map((d) => d.voucher).join(" ")]
					.join(" ")
					.toLowerCase();
				return text.includes(q);
			});
			const { key, dir } = this.sort;
			const val = (r) => (key === "documents" ? (r.documents || []).length : key === "alerts" ? (r.alerts || []).join("; ") : r[key]);
			return rows.sort((a, b) => {
				const x = val(a), y = val(b);
				if (typeof x === "number" || typeof y === "number") return ((x == null ? -Infinity : x) - (y == null ? -Infinity : y)) * dir;
				return String(x || "").localeCompare(String(y || ""), undefined, { numeric: true }) * dir;
			});
		}

		totals(rows) {
			const t = { count: rows.length };
			["iva", ...MONEY.filter((k) => k !== "iva")].forEach((k) => {
				t[k] = Math.round(rows.reduce((s, r) => s + (Number(r[k]) || 0), 0) * 100) / 100;
			});
			t.pct = t.iva ? t.cativo / t.iva : null;
			return t;
		}

		cell(c, r) {
			const v = r[c.key];
			if (c.kind === "money") return `<td class="ca-num">${money(v)}</td>`;
			if (c.kind === "pct") return `<td class="ca-num">${pct(v)}</td>`;
			if (c.kind === "date") return `<td class="ca-nowrap">${esc(frappe.datetime.str_to_user(v))}</td>`;
			if (c.kind === "docs")
				return `<td class="ca-docs">${(v || [])
					.map((d) => `<a href="#" data-doc="${esc(d.voucher)}" data-doctype="${esc(d.doctype)}">${esc(d.voucher)}</a> <span class="ca-tag">${esc(__(d.tag))}</span>`)
					.join("<br>")}</td>`;
			if (c.kind === "alerts") return `<td class="ca-alert">${(v || []).map(esc).join("<br>")}</td>`;
			if (c.key === "invoice")
				return `<td class="ca-nowrap"><a href="#" data-doc="${esc(v)}" data-doctype="Sales Invoice">${esc(v)}</a></td>`;
			if (c.key === "creation") return `<td><span class="ca-pill ca-${CREATION_TONE[v] || "gray"}">${esc(__(v))}</span></td>`;
			if (c.key === "status") return `<td class="${v === "Cancelled" ? "ca-alert" : ""}">${esc(__(v))}</td>`;
			return `<td class="${c.wide ? "ca-wide" : ""}">${esc(v)}</td>`;
		}

		paint() {
			const $r = this.$root;
			const $tbody = $r.find('[data-role="tbody"]');
			const $tfoot = $r.find('[data-role="tfoot"]');
			const $un = $r.find('[data-role="unassigned"]');
			const cols = COLUMNS.length;

			$r.find("th[data-sort]").each((_, th) => {
				th.classList.toggle("ca-sorted-asc", th.dataset.sort === this.sort.key && this.sort.dir === 1);
				th.classList.toggle("ca-sorted-desc", th.dataset.sort === this.sort.key && this.sort.dir === -1);
			});

			const warnings = this.data.warnings || [];
			$r.find('[data-role="warnings"]')
				.prop("hidden", !warnings.length || this.state !== "ready")
				.html(
					warnings.map(esc).join("<br>") +
						` <a href="/app/cativo-audit-settings" class="ca-link">${__("Open Cativo Audit Settings")}</a>`
				);

			if (this.state === "loading") {
				$tbody.html(
					Array.from({ length: 6 }, () => `<tr class="ca-skeleton"><td colspan="${cols}"><span></span></td></tr>`).join("")
				);
				$tfoot.empty();
				$un.empty();
				return;
			}
			if (this.state === "error") {
				$tbody.html(`<tr><td colspan="${cols}" class="ca-empty ca-alert">${esc(this.error)}</td></tr>`);
				$tfoot.empty();
				$un.empty();
				return;
			}

			const rows = this.shown();
			$tbody.html(
				rows.length
					? rows
							.map((r) => `<tr class="${r.status === "Cancelled" ? "ca-cancelled" : ""}">${COLUMNS.map((c) => this.cell(c, r)).join("")}</tr>`)
							.join("")
					: `<tr><td colspan="${cols}" class="ca-empty">${
							(this.data.rows || []).length ? __("No invoice matches these filters.") : __("No invoices with cativo in this period.")
					  }</td></tr>`
			);

			const t = this.totals(rows);
			$tfoot.html(
				`<tr>${COLUMNS.map((c, i) => {
					if (i === 0) return `<td colspan="5">${__("Total · {0} invoices shown", [t.count])}</td>`;
					if (i < 5) return "";
					if (c.kind === "money") return `<td class="ca-num">${money(t[c.key])}</td>`;
					if (c.kind === "pct") return `<td class="ca-num">${pct(t.pct)}</td>`;
					return "<td></td>";
				}).join("")}</tr>`
			);

			const un = this.data.unassigned || { amount: 0, count: 0, entries: [] };
			$un.html(`
				<details>
					<summary>
						<span class="ca-un-label">${__("Cativo with no invoice identified")}</span>
						<span class="ca-un-amount">${money(un.amount)}</span>
						<span class="ca-un-note">${__("{0} entries posted in the period; not affected by the filters above", [un.count])}</span>
					</summary>
					${
						un.count
							? `<table class="ca-table ca-un-table"><thead><tr>
								<th>${__("Document")}</th><th>${__("Date")}</th><th>${__("Type")}</th><th>${__("Customer")}</th>
								<th class="ca-num">${__("Amount")}</th><th>${__("Why")}</th></tr></thead><tbody>${un.entries
									.map(
										(e) => `<tr>
										<td class="ca-nowrap"><a href="#" data-doc="${esc(e.voucher)}" data-doctype="${esc(e.voucher_type)}">${esc(e.voucher)}</a></td>
										<td class="ca-nowrap">${esc(frappe.datetime.str_to_user(e.posting_date))}</td>
										<td>${esc(__(e.voucher_type))}</td><td>${esc(e.customer)}</td>
										<td class="ca-num">${money(e.amount)}</td><td class="ca-muted">${esc(e.reason)}</td></tr>`
									)
									.join("")}</tbody></table>`
							: ""
					}
				</details>`);
		}

		export() {
			if (this.state !== "ready") return;
			const $r = this.$root;
			const rows = this.shown().map((r) => {
				const out = {};
				COLUMNS.forEach((c) => {
					const v = r[c.key];
					out[c.key] =
						c.kind === "docs" ? (v || []).map((d) => `${d.voucher} (${__(d.tag)})`).join("\n")
						: c.kind === "alerts" ? (v || []).join("; ")
						: c.kind === "date" ? frappe.datetime.str_to_user(v)
						: v;
				});
				return out;
			});
			const t = this.totals(this.shown());
			const totals = { invoice: __("Total · {0} invoices shown", [t.count]), pct: t.pct };
			MONEY.forEach((k) => (totals[k] = t[k]));
			open_url_post(`/api/method/${API}.export_cativo_audit`, {
				payload: JSON.stringify({
					company: $r.find('[data-role="company"]').val(),
					from_date: $r.find('[data-role="from"]').val(),
					to_date: $r.find('[data-role="to"]').val(),
					columns: COLUMNS.map((c) => ({ key: c.key, label: c.label, kind: c.kind === "money" || c.kind === "pct" ? c.kind : "text" })),
					rows,
					totals,
					unassigned: this.data.unassigned,
				}),
			});
		}
	}
})();
