/* Ledger dashboard. No frameworks, no third-party requests. All DOM text is set via textContent. */
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const SVGNS = "http://www.w3.org/2000/svg";

function h(tag, props, ...kids) {
  const el = tag === "svg" || props?.svg ? document.createElementNS(SVGNS, tag) : document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (k === "svg" || v == null || v === false) continue;
    if (k === "class") el.setAttribute("class", v);
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else if (k === "value") el.value = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat(Infinity)) if (kid != null && kid !== false) el.append(kid.nodeType ? kid : String(kid));
  return el;
}
const sv = (tag, props, ...kids) => h(tag, { ...props, svg: true }, ...kids);

const usd0 = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
const usd2 = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2 });
const money = (n, d = 0) => (n == null ? "—" : (d ? usd2 : usd0).format(n));
const pct = (n, d = 1) => (n == null ? "—" : `${n.toFixed(d)}%`);
const monthLabel = (m) => new Date(m + "-15").toLocaleDateString("en-US", { month: "short", year: "2-digit" });
const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);

async function api(path, { method = "GET", json, body, raw } = {}) {
  const headers = method === "GET" ? {} : { "X-Fin-Client": "1" };
  if (json !== undefined) headers["Content-Type"] = "application/json";
  const res = await fetch(path, { method, headers, body: json !== undefined ? JSON.stringify(json) : body });
  if (raw) return res;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

function toast(msg, isErr = false) {
  const t = h("div", { class: "toast" + (isErr ? " err" : ""), role: "status" }, msg);
  document.body.append(t);
  setTimeout(() => t.remove(), isErr ? 6000 : 2800);
}

/* ---------- tooltip ---------- */
const tip = $("#tip");
function showTip(ev, lines) {
  tip.replaceChildren(...lines.map((l, i) => h("div", i ? {} : { style: { fontWeight: 600 } }, l)));
  tip.hidden = false;
  const pad = 14, w = tip.offsetWidth, hgt = tip.offsetHeight;
  let x = ev.clientX + pad, y = ev.clientY + pad;
  if (x + w > innerWidth - 8) x = ev.clientX - w - pad;
  if (y + hgt > innerHeight - 8) y = ev.clientY - hgt - pad;
  tip.style.left = x + "px"; tip.style.top = y + "px";
}
const hideTip = () => (tip.hidden = true);
const hover = (el, linesFn) => {
  el.addEventListener("pointermove", (e) => showTip(e, linesFn()));
  el.addEventListener("pointerleave", hideTip);
  return el;
};

/* ---------- state ---------- */
let OV = null; // /api/overview
const loadOverview = async () => (OV = await api("/api/overview"));

/* ---------- charts ---------- */
function niceMax(v) {
  if (v <= 0) return 1;
  const p = 10 ** Math.floor(Math.log10(v)), f = v / p;
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10) * p;
}

function monthlyBars(months) {
  const W = 720, H = 270, L = 52, R = 10, T = 14, B = 30;
  const max = niceMax(Math.max(...months.flatMap((m) => [m.income, m.spending]), 1));
  const iw = W - L - R, ih = H - T - B, step = iw / months.length, bw = Math.min(28, step * 0.32);
  const y = (v) => T + ih - (v / max) * ih;
  const svg = sv("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Income and spending by month", style: { width: "100%", height: "auto" } });
  const g = sv("g", { class: "grid" });
  for (let i = 0; i <= 4; i++) {
    const v = (max / 4) * i;
    g.append(sv("line", { x1: L, x2: W - R, y1: y(v), y2: y(v) }), sv("text", { x: L - 8, y: y(v) + 4, "text-anchor": "end" }, v >= 1000 ? `$${v / 1000}k` : `$${v}`));
  }
  svg.append(g);
  months.forEach((m, i) => {
    const cx = L + step * i + step / 2;
    const mk = (val, x, color, label) => sv("path", {
      d: `M${x},${y(0)} V${y(val) + 4} a4,4 0 0 1 4,-4 h${bw - 8} a4,4 0 0 1 4,4 V${y(0)} Z`,
      fill: val > 0 ? color : "none", style: { cursor: "default" }, "data-l": label,
    });
    const inc = mk(m.income, cx - bw - 1, "var(--s1)", "Income"), sp = mk(m.spending, cx + 1, "var(--s2)", "Spending");
    const hit = sv("rect", { x: cx - step / 2, y: T, width: step, height: ih, fill: "transparent" });
    hover(hit, () => m.missing ? [monthLabel(m.month), "No imported transactions for this month"] : [monthLabel(m.month), `Income ${money(m.income)}`, `Spending ${money(m.spending)}`, `Net ${money(m.net)}`, `Saved ${pct(m.savings_rate)}`]);
    svg.append(inc, sp, hit, sv("text", { x: cx, y: H - 10, "text-anchor": "middle" }, m.missing ? "·" : monthLabel(m.month)));
  });
  svg.append(sv("line", { class: "axis", x1: L, x2: W - R, y1: y(0), y2: y(0) }));
  return svg;
}

function catBars(entries, onPick) {
  const max = Math.max(...entries.map((e) => e[1]), 1);
  return h("div", {}, entries.map(([name, v]) =>
    hover(h("div", { class: "bar-row", role: "link", tabindex: 0, onclick: () => onPick(name), onkeydown: (e) => e.key === "Enter" && onPick(name) },
      h("span", { class: "lbl", title: name }, name),
      h("span", { class: "track" }, h("i", { style: { width: `${(v / max) * 100}%` } })),
      h("span", { class: "num", style: { textAlign: "right" } }, money(v))), () => [name, money(v) + " / month", "Click to see transactions"])));
}

const SEQ = ["--seq-1", "--seq-2", "--seq-3", "--seq-4", "--seq-5", "--seq-6", "--seq-7"];
function heat(months, onPick) {
  const totals = {};
  months.forEach((m) => Object.entries(m.by_category).forEach(([c, v]) => (totals[c] = (totals[c] || 0) + v)));
  const cats = Object.entries(totals).sort((a, b) => b[1] - a[1]).slice(0, 14).map((e) => e[0]);
  const max = Math.max(...months.flatMap((m) => cats.map((c) => m.by_category[c] || 0)), 1);
  return h("div", { class: "scroll" }, h("table", { class: "heat" },
    h("thead", {}, h("tr", {}, h("th", {}, "Category"), months.map((m) => h("th", { class: "r" }, monthLabel(m.month))), h("th", { class: "r" }, "Total"))),
    h("tbody", {}, cats.map((c) => h("tr", {},
      h("td", {}, h("a", { href: `#/transactions?category=${encodeURIComponent(c)}` }, c)),
      months.map((m) => {
        const v = m.by_category[c] || 0, step = v ? Math.min(6, Math.floor((v / max) * 6.99)) : -1;
        return hover(h("td", { class: "c num", style: v ? { background: `var(${SEQ[step]})`, color: step >= 3 ? "#fff" : "#0b0b0b" } : {}, onclick: () => onPick(c, m.month) }, v ? money(v) : ""),
          () => [c, monthLabel(m.month), money(v)]);
      }),
      h("td", { class: "r num" }, money(totals[c])))))));
}

/* ---------- shared bits ---------- */
const head = (title, sub, ...actions) =>
  h("div", { class: "head" }, h("div", {}, h("div", { class: "eyebrow" }, "Ledger"), h("h1", {}, title), sub && h("p", {}, sub)), h("div", { class: "form-row", style: { flex: "0 0 auto" } }, actions));
const card = (title, ...body) => h("section", { class: "card" }, title && h("header", {}, h("h3", {}, title)), body);
const emptyState = (msg) => h("div", { class: "note" }, msg);
function stat(label, value, sub, cls = "") {
  return h("div", { class: "card stat" }, h("div", { class: "eyebrow" }, label), h("div", { class: "v num " + cls }, value), h("div", { class: "s" }, sub));
}

/* ---------- views ---------- */
function viewOverview() {
  const o = OV, s = o.snapshot, nw = o.net_worth;
  const root = h("div", {});
  if (!o.has_data) {
    root.append(head("Welcome", "Start by importing a bank or credit-card export."), card("Nothing here yet", emptyState("Open Accounts, choose your CSV/OFX/QFX exports, name the account and import."), h("p", {}, h("a", { class: "btn accent", href: "#/accounts" }, "Go to Accounts"))));
    return root;
  }
  const totalAssets = nw.assets, totalLiab = nw.liabilities, denom = totalAssets + totalLiab || 1;
  root.append(head("Overview", `${s ? `${monthLabel(s.period.first_month)} – ${monthLabel(s.period.last_month)} · ` : ""}${o.accounts.length} accounts`));

  const hero = h("section", { class: "card hero" },
    h("div", { class: "eyebrow" }, "Net worth"),
    h("div", { class: "big num " + (nw.net_worth < 0 ? "bad" : "") }, nw.missing_balances.length === o.accounts.length ? "—" : money(nw.net_worth)),
    h("div", { class: "split", "aria-hidden": "true" }, h("i", { style: { width: `${(totalAssets / denom) * 100}%`, background: "var(--s1)" } }), h("i", { style: { width: `${(totalLiab / denom) * 100}%`, background: "var(--s2)" } })),
    h("div", { class: "legend" }, h("span", { style: { "--c": "var(--s1)" } }, `Assets ${money(totalAssets)}`), h("span", { style: { "--c": "var(--s2)" } }, `Liabilities ${money(totalLiab)}`)),
    nw.missing_balances.length ? h("p", { class: "fine" }, `Balances missing for: ${nw.missing_balances.join(", ")}. `, h("a", { href: "#/accounts" }, "Add them")) : null);

  const tiles = [];
  if (s) {
    const a = s.monthly_averages, rec = s.paycheck_reconciliation;
    tiles.push(stat("Avg monthly income", money(a.income), "bank deposits, transfers excluded"),
      stat("Avg monthly spending", money(a.spending), `${money(s.essential_spending_per_month)} essentials`),
      stat("Savings rate", pct(a.savings_rate_pct), `${money(a.net)} / month left over`, a.net < 0 ? "bad" : "good"),
      stat("Fixed commitments", money(s.recurring_outflows_per_month), pct(s.recurring_outflows_pct_of_income, 0) + " of income"));
    if (rec) tiles.push(stat("Paycheck check", money(rec.difference), `deposits vs ${money(rec.estimated_monthly_take_home)} estimated take-home`));
  }
  root.append(h("div", { class: "grid cols-2" }, hero, h("div", { class: "grid cols-2", style: { gridTemplateColumns: "repeat(2,1fr)" } }, tiles)));

  const attention = [];
  if (o.uncategorized.merchants) attention.push(h("li", {}, h("span", {}, `${o.uncategorized.merchants} merchants (${money(o.uncategorized.spending)}) are uncategorized`), h("a", { href: "#/transactions?category=Uncategorized" }, "Fix")));
  if (nw.missing_balances.length) attention.push(h("li", {}, h("span", {}, `${nw.missing_balances.length} accounts have no balance yet`), h("a", { href: "#/accounts" }, "Add")));
  if (s?.planning.missing_inputs.length) attention.push(h("li", {}, h("span", {}, `${s.planning.missing_inputs.length} profile inputs missing for tax and retirement plans`), h("a", { href: "#/plan" }, "Add")));

  root.append(h("div", { class: "grid cols-2", style: { marginTop: "1rem" } },
    card("Income vs spending, last 12 months", o.months.length ? monthlyBars(windowMonths(o.months)) : emptyState("No months yet"),
      h("div", { class: "legend", style: { marginTop: ".4rem" } }, h("span", { style: { "--c": "var(--s1)" } }, "Income"), h("span", { style: { "--c": "var(--s2)" } }, "Spending"))),
    card("Where it goes", s ? catBars(Object.entries(s.average_spending_by_category).slice(0, 9), (c) => (location.hash = `#/transactions?category=${encodeURIComponent(c)}`)) : null)));

  root.append(h("div", { class: "grid cols-2", style: { marginTop: "1rem" } },
    card("Needs attention", attention.length ? h("ul", { class: "list" }, attention) : h("p", { class: "muted" }, "All caught up.")),
    card("Coming up", o.recurring.length ? h("ul", { class: "list" }, [...o.recurring].sort((a, b) => a.next.localeCompare(b.next)).slice(0, 6).map((r) =>
      h("li", {}, h("span", {}, cap(r.merchant.toLowerCase()), " ", h("span", { class: "muted" }, `· ~${r.next}`)), h("span", { class: "num " + (r.amount > 0 ? "good" : "") }, money(r.amount, 2))))) : h("p", { class: "muted" }, "No recurring items detected yet."))));
  return root;
}

function accountRow(a) {
  const bal = h("input", { type: "text", inputmode: "decimal", value: a.balance ?? "", placeholder: "balance", "aria-label": `${a.name} balance` });
  const apr = h("input", { type: "text", inputmode: "decimal", value: a.apr_pct ?? "", placeholder: "APR %", "aria-label": `${a.name} APR` });
  const min = h("input", { type: "text", inputmode: "decimal", value: a.min_payment ?? "", placeholder: "min pmt", "aria-label": `${a.name} minimum payment` });
  const type = h("select", { "aria-label": `${a.name} type` }, OV.types.map((t) => h("option", { value: t.value, selected: t.value === a.type }, t.label)));
  const ear = h("input", { type: "text", inputmode: "decimal", value: a.earmarked ?? "", placeholder: "set aside $", "aria-label": `${a.name} money set aside` });
  const earFor = h("select", { "aria-label": `${a.name} set aside for` }, h("option", { value: "" }, "for…"), OV.accounts.filter((x) => x.kind === "liability").map((x) => h("option", { value: x.name, selected: x.name === a.earmark_for }, x.name)));
  const save = h("button", { class: "btn small", type: "button", onclick: async () => {
    try {
      await api("/api/accounts", { method: "POST", json: { name: a.name, type: type.value, balance: bal.value, apr_pct: apr.value, min_payment: min.value, notes: a.notes, earmarked: ear.value, earmark_for: earFor.value } });
      await loadOverview(); toast("Saved " + a.name); render();
    } catch (e) { toast(e.message, true); }
  } }, "Save");
  const debt = a.kind === "liability";
  return h("tr", {},
    h("td", {}, h("div", { style: { fontWeight: 600 } }, a.name), h("div", { class: "muted", style: { fontSize: ".78rem" } }, a.transactions ? `${a.transactions} txns · ${a.first_date} → ${a.last_date}` : "no transactions")),
    h("td", {}, type), h("td", {}, bal), h("td", {}, debt ? apr : ear), h("td", {}, debt ? min : earFor), h("td", { class: "r" }, save));
}

function viewAccounts() {
  const o = OV, root = h("div", {}), nw = o.net_worth;
  root.append(head("Accounts", "Checking, savings, credit cards, loans and investments in one place."));

  const name = h("input", { type: "text", list: "acct-names", placeholder: "e.g. Visa ending in…", "aria-label": "Account name" });
  const type = h("select", { "aria-label": "Account type" }, o.types.map((t) => h("option", { value: t.value }, t.label)));
  const files = h("input", { type: "file", multiple: true, accept: ".csv,.ofx,.qfx", "aria-label": "Bank export files" });
  const out = h("div", { class: "fine" });
  const go = h("button", { class: "btn accent", type: "button" }, "Import");
  go.onclick = async () => {
    if (!files.files.length) return toast("Choose one or more files first", true);
    go.disabled = true; out.textContent = "";
    for (const f of files.files) {
      try {
        const q = new URLSearchParams({ filename: f.name, account: name.value.trim(), type: type.value });
        const r = await api("/api/import?" + q, { method: "POST", body: await f.arrayBuffer() });
        out.append(h("div", {}, `${f.name}: ${r.parsed} rows, ${r.added} new`));
      } catch (e) { out.append(h("div", { class: "bad" }, `${f.name}: ${e.message}`)); }
    }
    go.disabled = false; files.value = "";
    await loadOverview(); toast("Import finished"); render(out);
  };
  const drop = h("div", { class: "drop" },
    h("div", { class: "eyebrow", style: { marginBottom: ".6rem" } }, "Import statements (CSV, OFX, QFX)"),
    h("div", { class: "form-row" },
      h("label", { class: "f" }, "Account name", name, h("datalist", { id: "acct-names" }, o.accounts.map((a) => h("option", { value: a.name })))),
      h("label", { class: "f" }, "Type (for new accounts)", type), h("label", { class: "f", style: { flex: "2 1 220px" } }, "Files", files), go),
    h("p", { class: "fine" }, "Leave the name empty if your file has an Account column. Credit-card payments you make from checking are matched to the card payment so they are not counted twice."), out);
  const drop2 = drop;
  ["dragover", "dragenter"].forEach((ev) => drop2.addEventListener(ev, (e) => { e.preventDefault(); drop2.classList.add("over"); }));
  ["dragleave", "drop"].forEach((ev) => drop2.addEventListener(ev, () => drop2.classList.remove("over")));
  drop2.addEventListener("drop", (e) => { e.preventDefault(); files.files = e.dataTransfer.files; });
  root.append(card(null, drop2));

  const groups = [["Assets", o.accounts.filter((a) => a.kind === "asset")], ["Liabilities", o.accounts.filter((a) => a.kind === "liability")]];
  for (const [title, list] of groups) {
    if (!list.length) continue;
    const total = list.reduce((t, a) => t + (a.balance || 0), 0);
    root.append(h("div", { style: { marginTop: "1rem" } }, card(`${title} · ${money(total)}`, h("div", { class: "scroll" }, h("table", {},
      h("thead", {}, h("tr", {}, ["Account", "Type", title === "Assets" ? "Balance" : "Owed", title === "Liabilities" ? "APR %" : "Set aside $", title === "Liabilities" ? "Min payment" : "To repay", ""].map((t, i) => h("th", { class: i === 5 ? "r" : "" }, t)))),
      h("tbody", {}, list.map(accountRow)))))));
  }

  const add = h("div", { class: "form-row" });
  const n2 = h("input", { type: "text", placeholder: "e.g. Fidelity 401(k), Car loan", "aria-label": "New account name" });
  const t2 = h("select", { "aria-label": "New account type" }, o.types.map((t) => h("option", { value: t.value, selected: t.value === "retirement" }, t.label)));
  const b2 = h("input", { type: "text", inputmode: "decimal", placeholder: "balance", "aria-label": "New account balance" });
  add.append(h("label", { class: "f" }, "Name", n2), h("label", { class: "f" }, "Type", t2), h("label", { class: "f" }, "Balance", b2),
    h("button", { class: "btn", type: "button", onclick: async () => {
      try { await api("/api/accounts", { method: "POST", json: { name: n2.value, type: t2.value, balance: b2.value } }); await loadOverview(); toast("Account added"); render(); } catch (e) { toast(e.message, true); }
    } }, "Add account"));
  root.append(h("div", { style: { marginTop: "1rem" } }, card("Add an account without transactions", add, h("p", { class: "fine" }, "For things with no export: a 401(k), a mortgage, a car loan. Liability balances are the amount you owe. Debts with an APR feed the payoff plan; cash and retirement balances feed the emergency-fund and retirement plans."))));
  return root;
}

async function viewTransactions(params) {
  const root = h("div", {});
  const q = h("input", { type: "search", placeholder: "Search merchant…", value: params.get("q") || "", "aria-label": "Search" });
  const mon = h("select", { "aria-label": "Month" }, h("option", { value: "" }, "All months"), OV.months.map((m) => h("option", { value: m.month, selected: m.month === params.get("month") }, monthLabel(m.month))));
  const acc = h("select", { "aria-label": "Account" }, h("option", { value: "" }, "All accounts"), OV.accounts.map((a) => h("option", { value: a.name, selected: a.name === params.get("account") }, a.name)));
  const catSel = h("select", { "aria-label": "Category" }, h("option", { value: "" }, "All categories"));
  const body = h("tbody", {}), count = h("span", { class: "muted" });

  async function load() {
    const qs = new URLSearchParams({ q: q.value, month: mon.value, account: acc.value, category: catSel.value || "" });
    const d = await api("/api/transactions?" + qs);
    if (!catSel.dataset.filled) {
      d.categories.forEach((c) => catSel.append(h("option", { value: c, selected: c === params.get("category") }, c)));
      catSel.dataset.filled = "1";
      if (params.get("category") && !catSel.value) catSel.value = params.get("category");
      if (params.get("category")) return load();
    }
    count.textContent = `${d.transactions.length} shown`;
    body.replaceChildren(...d.transactions.map((t) => h("tr", {},
      h("td", { class: "num" }, t.date), h("td", {}, t.description, t.internal ? h("span", { class: "pill", style: { marginLeft: ".4rem" } }, "matched transfer") : null),
      h("td", { class: "muted" }, t.account),
      h("td", {}, h("button", { class: "pill" + (t.category === "Uncategorized" ? " warn alert" : ""), style: { cursor: "pointer", background: "none", font: "inherit", fontSize: ".72rem" }, title: "Click to categorize this merchant", onclick: () => categorizeDialog(t, load) }, t.category)),
      h("td", { class: "r num " + (t.amount > 0 ? "good" : "") }, money(t.amount, 2)))));
  }
  let timer; q.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(load, 200); });
  [mon, acc, catSel].forEach((el) => el.addEventListener("change", load));
  root.append(head("Transactions", "Click a category to teach the app a merchant. Rules are saved on this PC."),
    card(null, h("div", { class: "form-row" }, h("label", { class: "f" }, "Search", q), h("label", { class: "f" }, "Month", mon), h("label", { class: "f" }, "Account", acc), h("label", { class: "f" }, "Category", catSel))),
    h("div", { style: { marginTop: "1rem" } }, card(null, h("div", { class: "scroll" }, h("table", {}, h("thead", {}, h("tr", {}, ["Date", "Merchant", "Account", "Category", "Amount"].map((t, i) => h("th", { class: i === 4 ? "r" : "" }, t)))), body)), h("p", { class: "fine" }, count))));
  await load();
  return root;
}

function categorizeDialog(t, reload) {
  const kw = h("input", { type: "text", value: t.description.split(/[\d#*]/)[0].trim().toUpperCase() || t.description.toUpperCase(), "aria-label": "Keyword" });
  const cat = h("input", { type: "text", list: "cat-list", placeholder: "category", value: t.category === "Uncategorized" ? "" : t.category, "aria-label": "Category" });
  const dl = h("datalist", { id: "cat-list" }, ["Housing", "Utilities", "Groceries", "Dining", "Transportation", "Subscriptions", "Health", "Insurance", "Shopping", "Entertainment", "Cash & ATM", "Debt Payments", "Savings", "Transfers", "Income", "Other"].map((c) => h("option", { value: c })));
  const dlg = h("dialog", { style: { border: "1px solid var(--rule-2)", borderRadius: "10px", background: "var(--card)", color: "var(--ink)", padding: "1.2rem", width: "min(420px, 92vw)" } },
    h("h3", {}, "Categorize"), h("p", { class: "muted" }, t.description),
    h("div", { class: "grid" }, h("label", { class: "f" }, "Match any merchant containing", kw), h("label", { class: "f" }, "Category", cat, dl)),
    h("p", { class: "fine" }, "Your rules beat the bank's own labels. Use “Transfers” for card payments and moves between your accounts."),
    h("div", { class: "form-row", style: { marginTop: ".8rem", justifyContent: "flex-end" } },
      h("button", { class: "btn ghost", type: "button", onclick: () => dlg.close() }, "Cancel"),
      h("button", { class: "btn accent", type: "button", onclick: async () => {
        try { await api("/api/rules", { method: "POST", json: { keyword: kw.value, category: cat.value } }); dlg.close(); await loadOverview(); toast("Rule saved"); reload(); }
        catch (e) { toast(e.message, true); }
      } }, "Save rule")));
  dlg.addEventListener("close", () => dlg.remove());
  document.body.append(dlg); dlg.showModal(); cat.focus();
}

function viewSpending() {
  const root = h("div", {});
  root.append(head("Spending", "Average by category, and how each month compares. Darker means more."));
  if (!OV.months.length) return root.append(emptyState("Import some transactions first.")), root;
  const s = OV.snapshot, go = (c, m) => (location.hash = `#/transactions?category=${encodeURIComponent(c)}${m ? "&month=" + m : ""}`);
  root.append(card("By month", heat(OV.months, go)),
    h("div", { style: { marginTop: "1rem" } }, card("Monthly average by category", catBars(Object.entries(s.average_spending_by_category), go))));
  return root;
}

function viewRecurring() {
  const root = h("div", {}), sec = OV.recurring_sections || { active: OV.recurring, yearly: [], stopped: [] };
  const since = OV.snapshot?.period?.baseline_chosen_by_you ? ymLabel(OV.snapshot.period.averages_from) : null;
  const sum = (a, neg) => a.filter((r) => (neg ? r.amount < 0 : r.amount > 0)).reduce((t, r) => t + r.monthly_cost, 0);
  append(root, head("Recurring", since ? `Only what is still happening since ${since}. Stopped ones are flagged so you can confirm the account is really closed.` : "Charges and deposits that repeat on a steady schedule."));
  append(root, h("div", { class: "grid cols-3" },
    stat("Active charges", money(-sum(sec.active, true)), "per month, still charging"), stat("Active deposits", money(sum(sec.active, false)), "per month"),
    stat("Follow up", String(sec.stopped.length), "stopped: confirm closed", sec.stopped.length ? "bad" : "good")));
  const table = (cols, rows) => h("div", { class: "scroll" }, h("table", {}, h("thead", {}, h("tr", {}, cols.map((t, i) => h("th", { class: i >= cols.length - 2 ? "r" : "" }, t)))), h("tbody", {}, rows)));
  const rowOf = (r, extra) => h("tr", {}, h("td", {}, cap(r.merchant.toLowerCase())), h("td", {}, h("span", { class: "pill" }, r.cadence)), h("td", { class: "num" }, r.last), h("td", { class: "num" }, extra),
    h("td", { class: "r num " + (r.amount > 0 ? "good" : "") }, money(r.amount, 2)), h("td", { class: "r num" }, money(r.monthly_cost, 2)));
  const a = [...sec.active].sort((x, y) => x.amount - y.amount);
  append(root, h("div", { style: { marginTop: "1rem" } }, card("Active", a.length ? table(["Merchant", "Cadence", "Last paid", "Next", "Amount", "Per month"], a.map((r) => rowOf(r, "~" + r.next))) : emptyState("Nothing active detected yet. A pattern needs 3 similar payments."))));
  if (sec.yearly.length) append(root, h("div", { style: { marginTop: "1rem" } }, card("Yearly", table(["Merchant", "Cadence", "Last paid", "Renews", "Amount", "Per month"], sec.yearly.map((r) => rowOf(r, "~" + r.next))),
    h("p", { class: "fine" }, "Found by two payments about a year apart. Yearly charges need at least a year of history to show up."))));
  const stopped = h("div", {}, sec.stopped.length ? sec.stopped.map((r) => h("div", { class: "note", style: { margin: ".5rem 0" } },
    h("b", {}, cap(r.merchant.toLowerCase())), ` · ${r.cadence} · ${money(r.amount, 2)} · last ${r.last}`, h("br"), h("span", { class: "fine" }, r.follow_up))) : emptyState("No stopped patterns."));
  append(root, h("div", { style: { marginTop: "1rem" } }, card("Stopped: follow up", h("p", { class: "fine", style: { marginTop: 0 } }, "These used to repeat and no longer do. A payment that just stops is not proof the account is gone."), stopped)));
  return root;
}

const PROFILE_FIELDS = [
  ["age", "Age", "number"], ["retire_age", "Retire at age", "number"],
  ["filing_status", "Filing status", ["single", "mfj", "hoh", "mfs"]], ["state", "State tax", ["AR", "none"]],
  ["annual_gross_income", "Gross pay / year", "money"], ["pre_tax_benefits_annual", "Health premiums etc. / year", "money"],
  ["traditional_401k_annual", "Traditional 401(k) / year", "money"], ["roth_401k_annual", "Roth 401(k) / year", "money"],
  ["traditional_ira_annual", "Traditional IRA / year", "money"], ["roth_ira_annual", "Roth IRA / year", "money"],
  ["cash_savings", "Cash savings (else from accounts)", "money"], ["retirement_balance", "Retirement balance (else from accounts)", "money"],
  ["monthly_retirement_contribution", "Retirement saved / month", "money"], ["target_retirement_spending_monthly", "Target retirement spend / month", "money"],
  ["expected_return_pct", "Expected return %", "number"], ["inflation_pct", "Inflation %", "number"],
];

const ymLabel = (ym) => (ym ? monthLabel(ym) : "—");

function timelineChart(tl) {
  const pts = tl.slice(0, 72), W = 720, H = 240, L = 56, R = 12, T = 14, B = 30;
  const series = [
    ["Debt remaining", "var(--s2)", (t) => t.debt],
    ["Saved (emergency fund + goals)", "var(--s1)", (t) => t.emergency_fund + t.savings],
  ];
  const max = niceMax(Math.max(...pts.flatMap((t) => series.map((s) => s[2](t))), 1));
  const iw = W - L - R, ih = H - T - B, x = (i) => L + (pts.length > 1 ? (i / (pts.length - 1)) * iw : 0), y = (v) => T + ih - (v / max) * ih;
  const svg = sv("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Projected debt and savings by month", style: { width: "100%", height: "auto" } });
  const g = sv("g", { class: "grid" });
  for (let i = 0; i <= 4; i++) { const v = (max / 4) * i; g.append(sv("line", { x1: L, x2: W - R, y1: y(v), y2: y(v) }), sv("text", { x: L - 8, y: y(v) + 4, "text-anchor": "end" }, v >= 1000 ? `$${+(v / 1000).toFixed(1)}k` : `$${v}`)); }
  svg.append(g);
  pts.forEach((t, i) => { if (i % Math.ceil(pts.length / 8) === 0) svg.append(sv("text", { x: x(i), y: H - 10, "text-anchor": "middle" }, monthLabel(t.month))); });
  series.forEach(([, color, f]) => svg.append(sv("polyline", { points: pts.map((t, i) => `${x(i)},${y(f(t))}`).join(" "), fill: "none", stroke: color, "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" })));
  const hit = sv("rect", { x: L, y: T, width: iw, height: ih, fill: "transparent" });
  hit.addEventListener("pointerleave", hideTip);
  hit.addEventListener("pointermove", (e) => {
    const r = svg.getBoundingClientRect(), px = ((e.clientX - r.left) / r.width) * W, i = Math.max(0, Math.min(pts.length - 1, Math.round(((px - L) / iw) * (pts.length - 1))));
    const t = pts[i]; showTip(e, [monthLabel(t.month), `Debt ${money(t.debt)}`, `Saved ${money(t.emergency_fund + t.savings)}`, t.invested ? `Invested so far ${money(t.invested)}` : ""].filter(Boolean));
  });
  svg.append(hit, sv("line", { class: "axis", x1: L, x2: W - R, y1: y(0), y2: y(0) }));
  return svg;
}

const append = (el, ...kids) => { for (const k of kids.flat(Infinity)) if (k != null && k !== false) el.append(k.nodeType ? k : String(k)); };

function windowMonths(months, n = 12) {
  if (!months.length) return [];
  const by = Object.fromEntries(months.map((m) => [m.month, m]));
  const [y, mo] = months[months.length - 1].month.split("-").map(Number), out = [];
  for (let i = n - 1; i >= 0; i--) {
    const t = y * 12 + (mo - 1) - i, k = `${Math.floor(t / 12)}-${String((t % 12) + 1).padStart(2, "0")}`;
    out.push(by[k] || { month: k, missing: true, income: 0, spending: 0, net: 0, savings_rate: null });
  }
  return out;
}

function recoverySection(rc) {
  const root = h("div", { style: { margin: "1.4rem 0" } });
  if (!rc) return root;
  const p = rc.position;
  append(root, h("div", { class: "eyebrow" }, "Getting back on track"), h("h2", {}, "Where you stand and what moves the needle"),
    h("p", { class: "fine" }, "Computed scenarios from your own numbers, not advice. The choices stay yours."));
  const stand = card("Where you stand", h("ul", { class: "list" },
    h("li", {}, "Net worth", h("b", { class: "num " + (p.net_worth < 0 ? "bad" : "") }, money(p.net_worth))),
    h("li", {}, "Debt not yet funded", h("b", { class: "num" }, money(p.debt_not_yet_funded))),
    p.set_aside_for_debts ? h("li", {}, "Set aside for debts already funded", h("b", { class: "num" }, money(p.set_aside_for_debts))) : null,
    h("li", {}, "Usable cash", h("b", { class: "num" }, money(p.usable_cash))),
    h("li", {}, "Must-pay costs per month", h("b", { class: "num" }, money(p.must_pay_per_month))),
    h("li", {}, "Months your cash covers", h("b", { class: "num" }, p.months_covered_today ?? "—")),
    h("li", {}, "Monthly surplus", h("b", { class: "num " + (p.monthly_surplus < 0 ? "bad" : "good") }, money(p.monthly_surplus)))),
    p.pending_deposits ? h("p", { class: "fine" }, `Not counted yet: ${money(p.pending_deposits)} waiting to be deposited (would cover ${p.months_covered_after_deposits} months).`) : null);
  const stress = card("If income stops", h("div", { class: "scroll" }, h("table", {},
    h("thead", {}, h("tr", {}, ["Months", "Cash needed", "Short today", "After deposits"].map((t, i) => h("th", { class: i ? "r" : "" }, t)))),
    h("tbody", {}, rc.income_stops.map((r) => h("tr", {}, h("td", {}, r.months_without_income), h("td", { class: "r num" }, money(r.cash_needed)),
      h("td", { class: "r num " + (r.shortfall_today ? "bad" : "good") }, money(r.shortfall_today)), h("td", { class: "r num " + (r.shortfall_after_deposits ? "bad" : "good") }, money(r.shortfall_after_deposits))))))),
    h("p", { class: "fine" }, "No income and no unemployment benefits assumed. Useful if a contract does not convert."));
  append(root, h("div", { class: "grid cols-2" }, stand, stress));
  const d = rc.discretionary;
  if (d.total_per_month) {
    append(root, h("div", { style: { marginTop: "1rem" } }, card("What trimming discretionary spending changes",
      h("div", { class: "grid cols-2" },
        h("div", {}, h("p", { class: "fine", style: { marginTop: 0 } }, "Your average per month today"), h("ul", { class: "list" }, Object.entries(d.per_month).map(([c, v]) => h("li", {}, c, h("b", { class: "num" }, money(v)))), h("li", {}, h("b", {}, "Total"), h("b", { class: "num" }, money(d.total_per_month))))),
        h("div", { class: "scroll" }, h("table", {}, h("thead", {}, h("tr", {}, ["Trim", "Freed / mo", "Debt free", "Sooner"].map((t, i) => h("th", { class: i ? "r" : "" }, t)))),
          h("tbody", {}, d.scenarios.map((t) => h("tr", {}, h("td", {}, `${t.trim_pct}%`), h("td", { class: "r num" }, money(t.monthly_freed)), h("td", { class: "r" }, t.debt_free ? ymLabel(t.debt_free) : "—"), h("td", { class: "r num" }, t.months_sooner != null ? `${t.months_sooner} mo` : "—"))))),
          h("p", { class: "fine" }, `Debt free around ${d.debt_free_today ? ymLabel(d.debt_free_today) : "not within 10 years"} at today's pace.`))))));
  }
  rc.notes.forEach((n) => append(root, h("p", { class: "fine" }, n)));
  return root;
}

function goalsSection(gp) {
  const root = h("div", { style: { marginBottom: "1.4rem" } }), plan = gp.plan;
  const reload = async () => { await loadOverview(); render(); };

  // add form
  const kind = h("select", { "aria-label": "Goal type" }, gp.kinds.map((k) => h("option", { value: k.value, selected: k.value === "savings" }, k.label)));
  const name = h("input", { type: "text", placeholder: "e.g. Car, Down payment", "aria-label": "Goal name" });
  const target = h("input", { type: "text", inputmode: "decimal", placeholder: "target $", "aria-label": "Target amount" });
  const date = h("input", { type: "date", "aria-label": "Target date" });
  const cur = h("input", { type: "text", inputmode: "decimal", placeholder: "saved so far $", "aria-label": "Saved so far" });
  const months = h("input", { type: "text", inputmode: "decimal", placeholder: "months of essentials (3)", "aria-label": "Months of essentials" });
  const monthly = h("input", { type: "text", inputmode: "decimal", placeholder: "max per month $", "aria-label": "Monthly cap" });
  const fieldsFor = { savings: [target, date, cur], emergency_fund: [months, target, date], debt_free: [date], invest: [monthly] };
  const dyn = h("div", { class: "form-row", style: { flex: "3 1 380px" } });
  const labels = new Map([[target, "Target $"], [date, "By when"], [cur, "Saved so far $"], [months, "Months of essentials"], [monthly, "Max per month $"]]);
  const refreshFields = () => dyn.replaceChildren(...fieldsFor[kind.value].map((el) => h("label", { class: "f" }, labels.get(el), el)));
  kind.addEventListener("change", refreshFields); refreshFields();
  const add = h("button", { class: "btn accent", type: "button", onclick: async () => {
    try {
      await api("/api/goals", { method: "POST", json: { kind: kind.value, name: name.value, target: target.value, target_date: date.value, current: cur.value, months: months.value, monthly: monthly.value } });
      toast("Goal added"); await reload();
    } catch (e) { toast(e.message, true); }
  } }, "Add goal");

  append(root, h("div", { class: "eyebrow" }, "Planning"), h("h2", {}, "Goals & action plan"));
  if (!plan) { append(root, card(null, emptyState("Import transactions to build a plan."))); return root; }

  const total = plan.this_month.reduce((t, a) => t + a.amount, 0);
  const thisMonth = card("Next month: where your surplus goes",
    plan.this_month.length ? h("ul", { class: "list" }, plan.this_month.map((a) => h("li", {}, a.bucket, h("b", { class: "num" }, money(a.amount)))), h("li", {}, h("span", { class: "muted" }, `of ${money(plan.monthly_surplus)} average monthly surplus`), h("b", { class: "num" }, money(total)))) : emptyState(plan.monthly_surplus > 0 ? "Everything is funded. Consider adding a goal." : "No surplus to allocate yet."),
    h("p", { class: "fine" }, "Order: " + plan.order.join(" → ")));

  const goalCards = gp.goals.map((g) => {
    const pg = plan.goals.find((x) => x.id === g.id) || {};
    const pctDone = pg.target && pg.current != null ? Math.min(100, (pg.current / pg.target) * 100) : null;
    const pill = pg.status === "on track" ? h("span", { class: "pill" }, "On track") : pg.status === "behind" ? h("span", { class: "pill warn alert" }, `Behind${pg.months_late ? ` by ${pg.months_late} mo` : ""}`) : pg.status ? h("span", { class: "pill" }, cap(pg.status)) : null;
    return h("div", { class: "card" },
      h("header", {}, h("div", {}, h("div", { class: "eyebrow" }, g.kind_label), h("h3", {}, g.name)), h("button", { class: "btn ghost small", type: "button", "aria-label": `Remove ${g.name}`, onclick: async () => { await api(`/api/goals?id=${g.id}`, { method: "DELETE" }); await reload(); } }, "Remove")),
      pctDone != null ? h("div", { class: "split", "aria-hidden": "true" }, h("i", { style: { width: `${pctDone}%`, background: "var(--s1)" } })) : null,
      h("ul", { class: "list" },
        pg.target ? h("li", {}, "Target", h("b", { class: "num" }, money(pg.target))) : null,
        pg.current != null && pg.kind !== "debt_free" ? h("li", {}, "Have now", h("b", { class: "num" }, money(pg.current))) : null,
        g.target_date ? h("li", {}, "Wanted by", h("b", {}, ymLabel(g.target_date.slice(0, 7)))) : null,
        h("li", {}, "Projected", h("b", {}, pg.eta ? ymLabel(pg.eta) : pg.note || "not within 10 years")),
        pg.required_monthly != null ? h("li", {}, "Needed per month for your date", h("b", { class: "num" }, money(pg.required_monthly))) : null),
      pill);
  });

  const miles = plan.milestones.length ? h("ul", { class: "list" }, plan.milestones.slice(0, 14).map((m) => h("li", {}, m.label, h("b", {}, ymLabel(m.date))))) : h("p", { class: "muted" }, "No milestones yet.");
  append(root, 
    h("div", { class: "grid cols-2" }, thisMonth, card("Milestones", miles, plan.debt_free ? h("p", { class: "fine" }, `Debt free around ${ymLabel(plan.debt_free)} at this pace.`) : null)),
    h("div", { style: { marginTop: "1rem" } }, card("Projection", timelineChart(plan.timeline), h("div", { class: "legend", style: { marginTop: ".4rem" } }, h("span", { style: { "--c": "var(--s2)" } }, "Debt remaining"), h("span", { style: { "--c": "var(--s1)" } }, "Saved (emergency fund + goals)")))),
    gp.goals.length ? h("div", { class: "grid cols-3", style: { marginTop: "1rem" } }, goalCards) : null,
    h("div", { style: { marginTop: "1rem" } }, card("Add a goal", h("div", { class: "form-row" }, h("label", { class: "f" }, "Type", kind), h("label", { class: "f" }, "Name", name), dyn, add))),
    [...plan.warnings.map((w) => h("div", { class: "note bad", style: { marginTop: ".8rem" } }, w)),
      plan.assumptions.length ? h("div", { style: { marginTop: ".8rem" } }, card("Assumptions", h("ul", { class: "list" }, plan.assumptions.map((a) => h("li", {}, a))))) : null]);
  return root;
}

async function viewPlan() {
  const root = h("div", {}), [pr, gp] = await Promise.all([api("/api/profile"), api("/api/goals")]), s = OV.snapshot, p = s?.planning;
  root.append(head("Plan", "Debt, emergency fund, retirement and taxes. Every number is computed in code, not guessed by the model."));

  const inputs = {};
  const fields = PROFILE_FIELDS.map(([key, label, kind]) => {
    const cur = pr.profile[key], eff = pr.effective[key];
    const el = Array.isArray(kind)
      ? h("select", { "aria-label": label }, h("option", { value: "" }, "—"), kind.map((o) => h("option", { value: o, selected: String(cur) === o }, o)))
      : h("input", { type: "text", inputmode: "decimal", value: cur ?? "", placeholder: eff != null && cur == null ? String(eff) : "", "aria-label": label });
    inputs[key] = { el, kind };
    return h("label", { class: "f" }, label, el);
  });
  const saveBtn = h("button", { class: "btn accent", type: "button", onclick: async () => {
    const next = { ...pr.profile };
    for (const [k, { el, kind }] of Object.entries(inputs)) {
      const v = el.value.trim();
      if (v === "") delete next[k]; else next[k] = Array.isArray(kind) ? v : Number(v.replace(/[$,]/g, ""));
    }
    try { await api("/api/profile", { method: "PUT", json: next }); await loadOverview(); toast("Profile saved"); render(); } catch (e) { toast(e.message, true); }
  } }, "Save profile");

  if (!s) return root.append(emptyState("Import transactions to see your plan.")), root;
  root.append(goalsSection(gp));
  append(root, recoverySection(s.recovery));
  const sections = [];
  const ef = p.emergency_fund;
  sections.push(card("Emergency fund", h("ul", { class: "list" },
    h("li", {}, "3 months of essentials", h("b", { class: "num" }, money(ef.target_3_months))), h("li", {}, "6 months of essentials", h("b", { class: "num" }, money(ef.target_6_months))),
    ef.cash_savings != null ? [h("li", {}, "Cash on hand", h("b", { class: "num" }, money(ef.cash_savings))), h("li", {}, "Months covered", h("b", { class: "num" }, String(ef.months_covered))), h("li", {}, "Shortfall to 3 / 6 months", h("b", { class: "num" }, `${money(ef.shortfall_to_3_months)} / ${money(ef.shortfall_to_6_months)}`))] : h("li", { class: "muted" }, "Add cash balances to see your cushion."))));
  if (p.debt_payoff) sections.push(card(`Debt payoff · ${money(p.debt_payoff.total_debt)} owed`, h("div", { class: "scroll" }, h("table", {}, h("thead", {}, h("tr", {}, ["Scenario", "Pays / mo", "Months", "Interest"].map((t, i) => h("th", { class: i ? "r" : "" }, t)))),
    h("tbody", {}, p.debt_payoff.scenarios.map((d) => h("tr", {}, h("td", {}, d.scenario), h("td", { class: "r num" }, money(d.monthly_payment_total)), h("td", { class: "r num" }, d.months_to_debt_free ?? "never"), h("td", { class: "r num" }, d.total_interest != null ? money(d.total_interest) : "—")))))), p.debt_payoff.assumptions.map((a) => h("p", { class: "fine" }, a))));
  if (p.retirement?.scenarios) sections.push(card(`Retirement · ${p.retirement.years_to_retirement} years, today's dollars`, h("div", { class: "scroll" }, h("table", {}, h("thead", {}, h("tr", {}, ["", "Return", "Balance", "Income / mo", p.retirement.scenarios[0].balance_needed ? "Needed / mo" : ""].map((t, i) => h("th", { class: i ? "r" : "" }, t)))),
    h("tbody", {}, p.retirement.scenarios.map((r) => h("tr", {}, h("td", {}, cap(r.scenario)), h("td", { class: "r num" }, pct(r.nominal_return_pct)), h("td", { class: "r num" }, money(r.projected_balance)), h("td", { class: "r num" }, money(r.sustainable_monthly_income)), h("td", { class: "r num" }, r.monthly_contribution_needed != null ? money(r.monthly_contribution_needed) : "")))))), h("p", { class: "fine" }, "4% withdrawal rule; contributions assumed to rise with inflation.")));
  const t = p.tax;
  if (t && !t.error) sections.push(card(`Taxes · ${t.tax_year}`, h("ul", { class: "list" },
    h("li", {}, "Federal income tax", h("b", { class: "num" }, money(t.federal_income_tax))), h("li", {}, "FICA", h("b", { class: "num" }, money(t.fica.total))), t.state ? h("li", {}, `${t.state.state} income tax`, h("b", { class: "num" }, money(t.state.income_tax))) : null,
    h("li", {}, "Total", h("b", { class: "num" }, `${money(t.total_taxes)} (${pct(t.total_effective_rate_pct)})`)), h("li", {}, "Next dollar is taxed at", h("b", { class: "num" }, pct(t.combined_marginal_rate_on_next_dollar_pct))), h("li", {}, "Estimated take-home / month", h("b", { class: "num" }, money(t.estimated_monthly_take_home)))),
    t.what_if.length ? h("div", { class: "scroll" }, h("table", {}, h("thead", {}, h("tr", {}, ["Contribute more pre-tax", "Extra", "Tax saved", "Real cost"].map((x, i) => h("th", { class: i ? "r" : "" }, x)))), h("tbody", {}, t.what_if.map((w) => h("tr", {}, h("td", {}, w.scenario), h("td", { class: "r num" }, money(w.extra_pre_tax_contribution)), h("td", { class: "r num good" }, money(w.total_tax_saved)), h("td", { class: "r num" }, money(w.net_take_home_cost))))))) : null,
    t.warnings.map((w) => h("p", { class: "fine bad" }, w))));
  else if (t?.error) sections.push(card("Taxes", emptyState(t.error)));
  if (p.missing_inputs.length) sections.push(card("Add these to unlock more", h("ul", { class: "list" }, p.missing_inputs.map((m) => h("li", {}, m)))));

  root.append(h("div", { class: "grid cols-2" }, h("div", { class: "grid" }, sections), card("Your profile", h("p", { class: "fine", style: { marginTop: 0 } }, "Blank fields use your accounts where possible. Saved to profile.json on this PC."), h("div", { class: "fields" }, fields), h("div", { style: { marginTop: "1rem" } }, saveBtn))));
  return root;
}

async function viewAdvisor() {
  const root = h("div", {}), [st, cl] = await Promise.all([api("/api/ollama"), api("/api/claude")]), history = [];
  root.append(head("Victor Sterling", "Your analytical wealth advisor. He sees your totals and plan results, never your individual transactions."));
  if (!OV.has_data) return root.append(emptyState("Import transactions first.")), root;

  let engine = "local"; // always starts local: Claude is opt-in each visit
  const models = st.models.length ? st.models : ["llama3:8b"];
  const model = h("select", { "aria-label": "Local model" }, models.map((m) => h("option", { value: m, selected: m === "llama3:8b" }, m)));
  const msgs = h("div", { class: "msgs", "aria-live": "polite" });
  msgs.append(h("div", { class: "msg bot" }, "I'm Victor. I've read your accounts, cash flow, debts and goals, so I won't ask what I can already see. Ask me anything, or say \"build my plan\" and I'll tell you the one thing I still need to know."));
  const input = h("input", { type: "text", placeholder: "Ask about budgeting, debt, investing, taxes…", "aria-label": "Your question" });
  const send = h("button", { class: "btn accent", type: "submit" }, "Ask");
  const banner = h("div", { class: "note" });
  const modelBox = h("label", { class: "f", style: { flex: "0 1 260px" } }, "Local model", model);

  const engineBtns = ["local", "claude"].map((e) => h("button", { type: "button", class: "chip", "aria-pressed": e === engine ? "true" : "false", onclick: () => setEngine(e) }, e === "local" ? "Local (Ollama): stays on this PC" : "Claude: cloud, you approve each send"));
  function setEngine(e) {
    engine = e;
    engineBtns.forEach((b, i) => b.setAttribute("aria-pressed", ["local", "claude"][i] === e ? "true" : "false"));
    modelBox.hidden = e !== "local";
    banner.replaceChildren(e === "local"
      ? "Local mode: nothing leaves this computer."
      : h("span", {}, "Claude mode: before every message you will see exactly what is sent to Anthropic (your totals, plan results and the conversation, with account and merchant names hidden) and must approve it. ",
        cl.via === "cli" ? h("b", {}, "It uses your Claude Code login: no API key needed. ") : null,
        !cl.ready ? h("b", { class: "bad" }, "Claude isn't available: log in to Claude Code (run claude once in a terminal) or set ANTHROPIC_API_KEY, then restart the dashboard. ") : null,
        cl.via === "api" && !cl.sdk ? h("b", { class: "bad" }, 'Install the SDK: pip install "fin-client[claude]". ') : null));
    send.textContent = e === "local" ? "Ask" : "Review & send…";
  }

  function stream(res, bot, q, label) {
    return (async () => {
      const reader = res.body.getReader(), dec = new TextDecoder(); let text = "";
      for (;;) { const { done, value } = await reader.read(); if (done) break; text += dec.decode(value, { stream: true }); bot.textContent = text; msgs.scrollTop = msgs.scrollHeight; }
      history.push({ role: "user", content: q }, { role: "assistant", content: text });
    })();
  }

  async function askLocal(q) {
    msgs.append(h("div", { class: "msg user" }, q)); const bot = h("div", { class: "msg bot" }, "…"); msgs.append(bot);
    input.value = ""; send.disabled = true; msgs.scrollTop = msgs.scrollHeight;
    try {
      const res = await api("/api/advise", { method: "POST", json: { question: q, model: model.value, history }, raw: true });
      if (!res.ok) throw new Error((await res.json()).error || "Request failed");
      await stream(res, bot, q);
    } catch (e) { bot.textContent = e.message; bot.classList.add("bad"); }
    send.disabled = false; input.focus();
  }

  async function askClaude(q) {
    let include = false, prev;
    const load = async () => (prev = await api("/api/advise/preview", { method: "POST", json: { question: q, history, include_merchants: include } }));
    try { await load(); } catch (e) { return toast(e.message, true); }
    const view = h("textarea", { readonly: true, rows: 16, "aria-label": "Exact text that will be sent", style: { fontFamily: "ui-monospace, Consolas, monospace", fontSize: ".78rem", resize: "vertical" } });
    const meta = h("p", { class: "fine" });
    const fill = () => { view.value = prev.text; meta.textContent = `Model ${prev.payload.model} · about ${prev.approx_input_tokens.toLocaleString()} input tokens · fingerprint ${prev.digest}`; };
    fill();
    const merch = h("input", { type: "checkbox", id: "inc-m", onchange: async () => { include = merch.checked; try { await load(); fill(); } catch (e) { toast(e.message, true); } } });
    const okBtn = h("button", { class: "btn accent", type: "button" }, "Send exactly this to Claude");
    const dlg = h("dialog", { style: { border: "1px solid var(--rule-2)", borderRadius: "10px", background: "var(--card)", color: "var(--ink)", padding: "1.2rem", width: "min(760px, 94vw)" } },
      h("h3", {}, "Review what leaves this computer"),
      h("p", { class: "muted" }, "This is the complete request. Account and debt names are replaced with generic labels; individual transactions are never included."),
      view, meta,
      h("label", { class: "f", style: { display: "flex", gap: ".5rem", alignItems: "center", margin: ".4rem 0" } }, merch, "Also include recurring merchant names (e.g. rent, subscriptions)"),
      h("div", { class: "form-row", style: { justifyContent: "flex-end", marginTop: ".6rem" } }, h("button", { class: "btn ghost", type: "button", onclick: () => dlg.close() }, "Cancel"), okBtn));
    dlg.addEventListener("close", () => dlg.remove());
    okBtn.onclick = async () => {
      dlg.close();
      msgs.append(h("div", { class: "msg user" }, q)); const bot = h("div", { class: "msg bot" }, "…"); msgs.append(bot);
      input.value = ""; send.disabled = true;
      try {
        const res = await api("/api/advise", { method: "POST", json: { question: q, history, include_merchants: include, engine: "claude", digest: prev.digest }, raw: true });
        if (!res.ok) throw new Error((await res.json()).error || "Request failed");
        bot.prepend(h("div", { class: "eyebrow", style: { marginBottom: ".3rem" } }, "Claude · cloud"));
        const body = h("div", {}); bot.append(body);
        await stream(res, body, q);
      } catch (e) { bot.textContent = e.message; bot.classList.add("bad"); }
      send.disabled = false; input.focus();
    };
    document.body.append(dlg); dlg.showModal();
  }

  const ask = (q) => (q.trim() ? (engine === "claude" ? askClaude(q.trim()) : askLocal(q)) : null);
  const form = h("form", { class: "form-row", onsubmit: (e) => { e.preventDefault(); ask(input.value); } }, input, send);
  const chips = ["Build my plan", "Should I pay off debt or invest first?", "What should I do with my monthly surplus?", "Am I on track for retirement?", "Where am I overspending?"];
  root.append(card(null,
    !st.running ? h("div", { class: "note", style: { marginBottom: ".8rem" } }, "Local Ollama isn't running. Start it with ", h("code", {}, "ollama serve"), " and reload.") : null,
    h("div", { class: "chips", style: { marginBottom: ".6rem" } }, engineBtns), banner,
    h("div", { class: "form-row", style: { margin: ".8rem 0" } }, modelBox, h("div", { class: "chips", style: { flex: "3 1 300px", alignSelf: "end" } }, chips.map((c) => h("button", { class: "chip", type: "button", onclick: () => ask(c) }, c)))),
    h("div", { class: "chat" }, msgs, form), h("p", { class: "fine" }, "Educational guidance, not a licensed financial advisor. Check the numbers against the Plan page.")));
  setEngine("local");
  return root;
}

async function viewCategorize() {
  const root = h("div", {});
  const [st, cl] = await Promise.all([api("/api/categorize/status"), api("/api/claude")]);
  append(root, head("Categorize", "An AI decides what each merchant is. Your corrections always win and teach the next run."));

  append(root, h("div", { class: "grid cols-3" },
    stat("Merchants", String(st.merchants), `${st.unlabeled} still unlabeled`),
    stat("Labeled by you", String(st.yours), "always final"),
    stat("AI, applied", String(st.ai_applied), `confidence ≥ ${Math.round(st.ai_min * 100)}%`),
    stat("Needs your review", String(st.needs_review), "AI was unsure", st.needs_review ? "bad" : ""),
    stat("Uncategorized spend", pct(st.uncategorized_pct), "of all spending dollars", st.uncategorized_pct > 10 ? "bad" : "good")));

  // ---- run panel
  let engine = "local";
  const engineBtns = ["local", "claude"].map((e) => h("button", { type: "button", class: "chip", "aria-pressed": e === engine ? "true" : "false", onclick: () => pick(e) }, e === "local" ? "Local (Ollama): stays on this PC" : "Claude: stronger, you approve what is sent"));
  const redo = h("input", { type: "checkbox", id: "redo" }), limit = h("input", { type: "text", inputmode: "numeric", placeholder: "all", "aria-label": "Only the biggest N merchants" });
  const model = h("input", { type: "text", placeholder: "llama3:8b", "aria-label": "Model" });
  const bar = h("div", { class: "split", hidden: true }, h("i", { style: { width: "0%", background: "var(--s1)" } }));
  const msg = h("p", { class: "fine" });
  const go = h("button", { class: "btn accent", type: "button" }, `Categorize ${st.unlabeled} merchants`);
  const note = h("div", { class: "note", style: { margin: ".6rem 0" } });
  function pick(e) {
    engine = e;
    engineBtns.forEach((b, i) => b.setAttribute("aria-pressed", ["local", "claude"][i] === e ? "true" : "false"));
    model.placeholder = e === "local" ? "llama3:8b" : cl.model;
    note.replaceChildren(e === "local" ? "Local mode: merchant names never leave this computer. Small local models are decent, not great, on obscure merchants: check the review queue."
      : h("span", {}, "Claude mode: only merchant text, counts, typical amounts and your own labels are sent, after you review the exact requests. No account names or numbers. ",
        cl.via === "cli" ? h("b", {}, "It uses your Claude Code login: no API key needed. ") : null,
        !cl.ready ? h("b", { class: "bad" }, "Claude isn't available: log in to Claude Code (run claude once in a terminal) or set ANTHROPIC_API_KEY, then restart the dashboard. ") : null,
        cl.via === "api" && !cl.sdk ? h("b", { class: "bad" }, 'Install the SDK: pip install "fin-client[claude]". ') : null));
  }
  pick("local");

  async function runNow(extra) {
    go.disabled = true; bar.hidden = false; msg.textContent = "Working… labels are saved as each batch finishes.";
    try {
      const res = await api("/api/categorize/run", { method: "POST", raw: true, json: { engine, model: model.value.trim() || undefined, redo: redo.checked, limit: limit.value ? Number(limit.value) : undefined, ...extra } });
      if (!res.ok) throw new Error((await res.json()).error || "Request failed");
      const reader = res.body.getReader(), dec = new TextDecoder(); let buf = "", last = null;
      for (;;) {
        const { done, value } = await reader.read(); if (done) break;
        buf += dec.decode(value, { stream: true });
        let nl; while ((nl = buf.indexOf("\n")) >= 0) {
          const line = buf.slice(0, nl).trim(); buf = buf.slice(nl + 1); if (!line) continue;
          const o = JSON.parse(line); last = o;
          if (o.total) bar.firstChild.style.width = `${(o.done / o.total) * 100}%`;
          if (o.done) msg.textContent = `${o.done} of ${o.total} merchants…`;
        }
      }
      if (last?.error) throw new Error(last.error);
      const r = last.result;
      toast(`${r.saved} labeled · ${r.needs_review} need review`); await loadOverview(); render();
    } catch (e) { msg.textContent = e.message; toast(e.message, true); go.disabled = false; }
  }

  go.onclick = async () => {
    if (engine === "local") return runNow({});
    try {
      const prev = await api("/api/categorize/preview", { method: "POST", json: { engine, model: model.value.trim() || undefined, redo: redo.checked, limit: limit.value ? Number(limit.value) : undefined } });
      if (!prev.merchants) return toast("Nothing to categorize", true);
      const view = h("textarea", { readonly: true, rows: 16, "aria-label": "Exact text that will be sent", style: { fontFamily: "ui-monospace, Consolas, monospace", fontSize: ".78rem" } }); view.value = prev.text;
      const okBtn = h("button", { class: "btn accent", type: "button" }, "Send exactly this to Claude");
      const dlg = h("dialog", { style: { border: "1px solid var(--rule-2)", borderRadius: "10px", background: "var(--card)", color: "var(--ink)", padding: "1.2rem", width: "min(780px, 94vw)" } },
        h("h3", {}, "Review what leaves this computer"),
        h("p", { class: "muted" }, `${prev.merchants} merchants in ${prev.requests} request(s), model ${prev.model}. Merchant text, counts and typical amounts only. No account names or numbers.`),
        view, h("p", { class: "fine" }, `Fingerprint ${prev.digest}`),
        h("div", { class: "form-row", style: { justifyContent: "flex-end" } }, h("button", { class: "btn ghost", type: "button", onclick: () => dlg.close() }, "Cancel"), okBtn));
      dlg.addEventListener("close", () => dlg.remove());
      okBtn.onclick = () => { dlg.close(); runNow({ digest: prev.digest }); };
      document.body.append(dlg); dlg.showModal();
    } catch (e) { toast(e.message, true); }
  };

  append(root, h("div", { style: { marginTop: "1rem" } }, card("Run the categorizer",
    h("div", { class: "chips", style: { marginBottom: ".6rem" } }, engineBtns), note,
    h("div", { class: "form-row" }, h("label", { class: "f" }, "Model (optional)", model), h("label", { class: "f" }, "Only the biggest N merchants", limit),
      h("label", { class: "f", style: { display: "flex", gap: ".5rem", alignItems: "center", flex: "1 1 220px" } }, redo, "Re-label AI guesses (never yours)"), go), bar, msg)));

  // ---- review
  const rv = await api("/api/labels/review");
  const tab = { name: "review" };
  const holder = h("div", {});
  const tabs = h("div", { class: "chips", style: { marginBottom: ".6rem" } });
  const sel = (value) => h("select", { "aria-label": "Category" }, rv.taxonomy.map((c) => h("option", { value: c, selected: c === value }, c)));

  function rows(list, mode) {
    if (!list.length) return emptyState(mode === "review" ? "Nothing waiting. Run the categorizer, or everything is already decided." : "Nothing here yet.");
    const acceptAll = mode === "review" ? h("button", { class: "btn ghost small", type: "button", onclick: async () => {
      if (!confirm(`Accept the AI's guess for all ${list.length} merchants as your own labels?`)) return;
      for (const r of list) await api("/api/labels", { method: "POST", json: { key: r.key, category: r.category, merchant: r.merchant } });
      toast("Accepted"); await loadOverview(); render();
    } }, `Accept all ${list.length}`) : null;
    return h("div", {}, acceptAll && h("div", { style: { marginBottom: ".5rem" } }, acceptAll),
      h("div", { class: "scroll" }, h("table", {}, h("thead", {}, h("tr", {}, ["Merchant", "Seen", "Total", mode === "yours" ? "Yours" : "AI guess", "Change to", ""].map((t, i) => h("th", { class: i === 1 || i === 2 ? "r" : "" }, t)))),
        h("tbody", {}, list.slice(0, 120).map((r) => {
          const c = sel(r.category);
          const save = async (cat) => { await api("/api/labels", { method: "POST", json: { key: r.key, category: cat, merchant: r.merchant } }); toast("Saved"); await loadOverview(); render(); };
          return h("tr", {}, h("td", {}, r.merchant), h("td", { class: "r num" }, r.n), h("td", { class: "r num" }, money(r.total)),
            h("td", {}, mode === "yours" ? r.category : h("span", {}, r.category, " ", h("span", { class: "pill" + (r.confidence < st.ai_min ? " warn" : "") }, `${Math.round(r.confidence * 100)}%`))),
            h("td", {}, c), h("td", { class: "r" }, h("button", { class: "btn small", type: "button", onclick: () => save(c.value) }, mode === "review" && c.value === r.category ? "Accept" : "Save")));
        })))));
  }
  const lists = { review: rv.review, applied: rv.applied, yours: rv.yours };
  const names = { review: `Needs review (${rv.review.length})`, applied: `AI applied (${rv.applied.length})`, yours: `Yours (${rv.yours.length})` };
  function show(n) { tab.name = n; holder.replaceChildren(rows(lists[n], n)); [...tabs.children].forEach((b) => b.setAttribute("aria-pressed", b.dataset.n === n ? "true" : "false")); }
  Object.keys(lists).forEach((n) => tabs.append(h("button", { type: "button", class: "chip", "data-n": n, onclick: () => show(n) }, names[n])));
  show(rv.review.length ? "review" : "applied");
  append(root, h("div", { style: { marginTop: "1rem" } }, card("Review", h("p", { class: "fine", style: { marginTop: 0 } }, "Biggest spend first. Accepting or changing a merchant makes it your label, which beats every AI guess and is shown to the AI as an example next time."), tabs, holder)));

  // ---- accuracy
  const out = h("div", {}), evalBtn = h("button", { class: "btn", type: "button" }, "Measure accuracy (local model)");
  evalBtn.onclick = async () => {
    evalBtn.disabled = true; out.replaceChildren(h("p", { class: "muted" }, "Scoring the AI against merchants you labeled… this can take a minute."));
    try {
      const r = await api("/api/categorize/eval", { method: "POST", json: {} });
      if (r.error) { out.replaceChildren(emptyState(r.error)); }
      else out.replaceChildren(h("div", { class: "grid cols-3" }, stat("AI accuracy", pct(r.ai_accuracy), `${r.n} merchants you labeled`), stat("Rules + bank labels", pct(r.rules_only_accuracy), "the old method"), stat("AI when confident", r.confident_accuracy == null ? "—" : pct(r.confident_accuracy), `${r.confident_n} merchants`)),
        r.mistakes.length ? h("div", { class: "scroll", style: { marginTop: ".8rem" } }, h("table", {}, h("thead", {}, h("tr", {}, ["Merchant", "You said", "AI said", "Conf."].map((t) => h("th", {}, t)))), h("tbody", {}, r.mistakes.map((m) => h("tr", {}, h("td", {}, m.merchant), h("td", {}, m.expected), h("td", {}, m.ai), h("td", { class: "num" }, `${Math.round(m.confidence * 100)}%`)))))) : null);
    } catch (e) { out.replaceChildren(emptyState(e.message)); }
    evalBtn.disabled = false;
  };
  append(root, h("div", { style: { marginTop: "1rem" } }, card("How accurate is it?", h("p", { class: "fine", style: { marginTop: 0 } }, "Each merchant you labeled is hidden from the AI and scored. Label at least 10 first. For a Claude score, run fin-client categorize --eval --engine claude."), evalBtn, out)));
  return root;
}

/* ---------- router ---------- */
const VIEWS = [["overview", "Overview", viewOverview], ["accounts", "Accounts", viewAccounts], ["transactions", "Transactions", viewTransactions], ["categorize", "Categorize", viewCategorize],
  ["spending", "Spending", viewSpending], ["recurring", "Recurring", viewRecurring], ["plan", "Plan", viewPlan], ["advisor", "Advisor", viewAdvisor]];

function parseHash() {
  const [path, qs] = location.hash.replace(/^#\/?/, "").split("?");
  return [path || "overview", new URLSearchParams(qs || "")];
}

let renderSeq = 0, keepNote = null;
async function render(note) {
  keepNote = note || null;
  const seq = ++renderSeq, [name, params] = parseHash();
  const view = VIEWS.find((v) => v[0] === name) || VIEWS[0];
  $("#nav").replaceChildren(...VIEWS.map(([id, label], i) => h("a", { href: `#/${id}`, "aria-current": id === view[0] ? "page" : null }, h("span", { class: "n" }, String(i + 1)), label)));
  try {
    if (!OV) await loadOverview();
    const node = await view[2](params);
    if (seq !== renderSeq) return;
    $("#app").replaceChildren(node);
    document.title = `${view[1]} · Ledger`;
  } catch (e) {
    $("#app").replaceChildren(h("div", { class: "note" }, "Something went wrong: " + e.message));
  }
}

addEventListener("hashchange", () => { hideTip(); render(); window.scrollTo(0, 0); });
$("#theme").addEventListener("click", () => {
  const dark = matchMedia("(prefers-color-scheme: dark)").matches;
  const cur = document.documentElement.dataset.theme || (dark ? "dark" : "light");
  const next = cur === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  try { localStorage.setItem("ledger-theme", next); } catch { /* storage may be blocked */ }
});
try { const t = localStorage.getItem("ledger-theme"); if (t) document.documentElement.dataset.theme = t; } catch { /* ignore */ }
render();
