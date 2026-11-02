function money(cents) {
  return new Intl.NumberFormat(I18N.getLang(), { style: "currency", currency: "RUB", maximumFractionDigits: 0 }).format((cents || 0) / 100);
}

const { t, tn } = I18N;

// Подписи статусов берутся из словаря в момент обращения, поэтому следуют за языком.
const STATUS_LABEL = {
  get pending() { return t("status.pending"); },
  get paid() { return t("status.paid"); },
  get shipped() { return t("status.shipped"); },
  get completed() { return t("status.completed"); },
  get cancelled() { return t("status.cancelled"); },
};

const STATUS_NEXT = {
  pending: ["paid", "cancelled"],
  paid: ["shipped", "cancelled"],
  shipped: ["completed"],
  completed: [],
  cancelled: [],
};

const STATUS_COLORS = {
  pending: "#ca8a04",
  paid: "#16a34a",
  shipped: "#2563eb",
  completed: "#7c3aed",
  cancelled: "#dc2626",
};

const relFmt = () => new Intl.RelativeTimeFormat(I18N.getLang(), { numeric: "auto" });
const DAY_MS = 24 * 60 * 60 * 1000;

function el(tag, props, children) {
  const e = document.createElement(tag);
  if (props) {
    for (const [k, v] of Object.entries(props)) {
      if (k === "class") e.className = v;
      else if (k === "dataset") Object.assign(e.dataset, v);
      else if (k.startsWith("on") && typeof v === "function") e.addEventListener(k.slice(2), v);
      else if (k === "html") e.innerHTML = v;
      else if (v !== null && v !== undefined && v !== false) e.setAttribute(k, v);
    }
  }
  (children || []).forEach((c) => {
    if (c == null || c === false) return;
    if (typeof c === "string" || typeof c === "number") e.appendChild(document.createTextNode(String(c)));
    else e.appendChild(c);
  });
  return e;
}

function formatMoney(cents) {
  return money(cents);
}

function relativeTime(iso) {
  const diff = new Date(iso).getTime() - Date.now();
  const absSec = Math.round(diff / 1000);
  if (Math.abs(absSec) < 60) return relFmt().format(absSec, "second");
  const absMin = Math.round(absSec / 60);
  if (Math.abs(absMin) < 60) return relFmt().format(absMin, "minute");
  const absHr = Math.round(absMin / 60);
  if (Math.abs(absHr) < 24) return relFmt().format(absHr, "hour");
  const absDay = Math.round(absHr / 24);
  if (Math.abs(absDay) < 30) return relFmt().format(absDay, "day");
  const absMo = Math.round(absDay / 30);
  if (Math.abs(absMo) < 12) return relFmt().format(absMo, "month");
  return relFmt().format(Math.round(absMo / 12), "year");
}

function sourceBadge(src) {
  if (src !== "telegram" && src !== "panel") return null;
  return el("span", { class: "badge", style: "margin-left:6px;font-size:0.68rem" }, [t(`source.${src}`)]);
}

function tgLink(o) {
  const u = o.telegram_url;
  if (!u || !u.startsWith("https://t.me/")) return null;
  return el("a", { href: u, target: "_blank", rel: "noopener noreferrer" }, ["@" + (o.telegram_username || "")]);
}

function badge(status) {
  const s = status || "pending";
  return el("span", { class: `badge ${s}` }, [STATUS_LABEL[s] || s]);
}

function computeDelta(curr, prev) {
  if (prev === 0 || prev == null) {
    if (curr === 0 || curr == null) return { cls: "kpi-delta-flat", html: "—" };
    return { cls: "kpi-delta-flat", html: "—" };
  }
  const pct = ((curr - prev) / prev) * 100;
  const rounded = Math.abs(pct) >= 1 ? pct.toFixed(0) + "%" : pct.toFixed(1) + "%";
  if (pct > 0.05) return { cls: "kpi-delta-up", html: `▲ ${rounded}` };
  if (pct < -0.05) return { cls: "kpi-delta-down", html: `▼ ${rounded}` };
  return { cls: "kpi-delta-flat", html: `— ${rounded}` };
}

function debounce(fn, wait) {
  let t;
  return function (...args) {
    clearTimeout(t);
    t = setTimeout(() => fn.apply(this, args), wait);
  };
}

const DEFAULT_STATE = {
  status: "",
  source: "",
  q: "",
  date_from: "",
  date_to: "",
  min_amount: "",
  max_amount: "",
  sort: "created_at",
  dir: "desc",
  page: 1,
  per_page: 10,
  days: 30,
};

let state = { ...DEFAULT_STATE };
let requestSeq = 0;
let tableAbort = null;
let charts = { revenue: null, status: null };
let lastStatsDays = null;
let autoRefreshTimer = null;
let autoRefreshBusy = false;
const AUTO_REFRESH_MS = 5000;

function autoRefreshPaused() {
  return pendingStatuses.size > 0 || document.hidden || document.querySelector(
    '.modal-backdrop.open, .modal-backdrop.closing, .drawer.open, .status-menu.open, input:focus, textarea:focus, select:focus, button:focus, tr[data-id]:focus'
  );
}

async function autoRefresh() {
  if (autoRefreshBusy || autoRefreshPaused()) return;
  autoRefreshBusy = true;
  try {
    const view = currentView();
    if (view === "analytics") await Promise.all([refreshStats(true), refreshActivity()]);
    else if (view === "orders") await refreshTable(true, true);
    else if (view === "overview") await refreshOverview();
  } finally {
    autoRefreshBusy = false;
  }
}

function startAutoRefresh() {
  clearTimeout(autoRefreshTimer);
  autoRefreshTimer = setTimeout(async () => {
    try {
      await autoRefresh();
    } finally {
      startAutoRefresh();
    }
  }, AUTO_REFRESH_MS);
}

function stateToQuery() {
  const params = new URLSearchParams();
  Object.entries(state).forEach(([k, v]) => {
    if (v === "" || v == null) return;
    if (DEFAULT_STATE[k] === v && ["per_page"].includes(k)) return;
    params.set(k, v);
  });
  return params.toString();
}

function applyStateFromUrl() {
  const p = new URLSearchParams(location.search);
  const next = { ...DEFAULT_STATE };
  Object.keys(DEFAULT_STATE).forEach((k) => {
    if (p.has(k)) next[k] = p.get(k);
  });
  next.page = Number(next.page) || 1;
  next.per_page = Number(next.per_page) || 10;
  next.days = Number(next.days) || 30;
  state = next;
  syncUIFromState();
}

function pushStateToUrl() {
  const q = stateToQuery();
  const url = (q ? `${location.pathname}?${q}` : location.pathname) + location.hash;
  history.replaceState({}, "", url);
}

function syncUIFromState() {
  $$("#statusChips .chip").forEach((c) => {
    c.classList.toggle("active", (c.dataset.status || "") === state.status);
    c.setAttribute("aria-pressed", String((c.dataset.status || "") === state.status));
  });
  $("#sourceFilter").value = state.source || "";
  $("#searchInput").value = state.q || "";
  $("#searchClear").hidden = !state.q;
  $("#dateFrom").value = state.date_from || "";
  $("#dateTo").value = state.date_to || "";
  $("#minAmount").value = state.min_amount || "";
  $("#maxAmount").value = state.max_amount || "";
  $$("table thead th").forEach((th) => {
    const col = th.dataset.sort;
    const ind = th.querySelector(".sort-indicator");
    if (!col) return;
    th.classList.toggle("active-sort", col === state.sort);
    th.setAttribute("aria-sort", col === state.sort ? (state.dir === "asc" ? "ascending" : "descending") : "none");
    if (ind) {
      if (col === state.sort) ind.textContent = state.dir === "asc" ? "▲" : "▼";
      else ind.textContent = "";
    }
  });
  $$(".period-toggle button").forEach((b) => {
    b.classList.toggle("active", Number(b.dataset.days) === Number(state.days));
    b.setAttribute("aria-pressed", String(Number(b.dataset.days) === Number(state.days)));
  });
}

function currentView() {
  const name = location.hash.slice(1);
  return ["analytics", "orders", "products", "competitors"].includes(name) ? name : "overview";
}
async function refreshAll() {
  if (currentView() === "analytics") await refreshAnalytics();
  else if (currentView() === "orders") await refreshTable(true);
  else if (currentView() === "overview") await refreshOverview();
}
async function refreshOverview() {
  await Promise.all([refreshAttention(), refreshChipCounts()]);
}
async function refreshAnalytics() {
  await Promise.all([refreshStats(), refreshActivity()]);
}
function localTodayWindow(now = new Date()) {
  const start = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  return {
    start: start.getTime(),
    end: now.getTime(),
    dateFrom: start.toISOString().slice(0, 10),
    dateTo: now.toISOString().slice(0, 10),
  };
}

async function refreshAttention() {
  const list = $("#attentionList");
  try {
    const data = await OrdersApp.api("/api/orders?status=pending&sort=created_at&dir=asc&per_page=100");
    const items = data.total < 5 ? data.items : data.items.filter(order => Date.now() - new Date(order.created_at).getTime() > DAY_MS);
    list.replaceChildren(...items.slice(0, 5).map(order => el("div", { class: "attention-item" }, [
      el("button", { type: "button", class: "btn btn-ghost", onclick: () => openOrderDrawer(order.id) }, [`#${order.id + 1000} · ${order.customer_name}`]),
      el("span", {}, [formatMoney(order.amount_cents)]), el("small", { class: "lead" }, [relativeTime(order.created_at)]),
    ])));
    if (!items.length) list.appendChild(el("p", { class: "lead" }, [t("overview.empty")]));
    const window = localTodayWindow();
    const params = new URLSearchParams({ date_from: window.dateFrom, date_to: window.dateTo, per_page: "100" });
    const todayOrders = await OrdersApp.api(`/api/orders?${params}`);
    let orders = [...todayOrders.items];
    for (let page = 2; page <= todayOrders.pages; page++) {
      params.set("page", page);
      const next = await OrdersApp.api(`/api/orders?${params}`);
      orders.push(...next.items);
    }
    // API date filters use UTC days; trim the result to the user's local day.
    orders = orders.filter(order => {
      const created = new Date(order.created_at).getTime();
      return created >= window.start && created <= window.end;
    });
    const paid = orders.filter(order => ["paid", "shipped", "completed"].includes(order.status));
    const revenue = paid.reduce((sum, order) => sum + order.amount_cents, 0);
    const kpi = { revenue_cents: revenue, orders: orders.length, avg_order_cents: paid.length ? Math.floor(revenue / paid.length) : 0, pending: data.total };
    if (currentView() === "overview") {
      renderKpis({ kpi, kpi_prev: {} });
      document.querySelectorAll("#overview .row").forEach(node => { node.textContent = t("overview.today"); });
      document.querySelector('#overview [data-kpi="pending"] .row').textContent = t("overview.now");
    }
  } catch {
    list.replaceChildren(el("button", { type: "button", class: "btn btn-ghost", onclick: refreshAttention }, [t("btn.retry")]));
  }
}

async function refreshStats(silent = false) {
  const days = Number(state.days) || 30;
  try {
    const data = await OrdersApp.api(`/api/stats?days=${days}`);
    if (days !== (Number(state.days) || 30)) return;
    if (currentView() !== "analytics") return;
    renderKpis(data);
    const revenueData = $("#revenueData");
    if (revenueData) revenueData.replaceChildren(...data.series.map((entry) => el("tr", {}, [
      el("td", {}, [entry.date]), el("td", {}, [formatMoney(entry.revenue_cents)]),
    ])));
    renderRevenueChart(data);
    renderStatusChart(data);
    renderTopProducts(data);
    lastStatsDays = days;
  } catch (err) {
    if (!silent && err instanceof OrdersApp.ApiError) {
      OrdersApp.toast(t("toast.stats_failed", { error: I18N.apiMessage(err.message) }), "error");
    }
  }
}

function renderKpis(data) {
  const { kpi, kpi_prev } = data || { kpi: {}, kpi_prev: {} };
  const cards = [
    ["revenue", formatMoney(kpi.revenue_cents), kpi.revenue_cents, kpi_prev && kpi_prev.revenue_cents],
    ["orders", String(kpi.orders ?? 0), kpi.orders, kpi_prev && kpi_prev.orders],
    ["avg", formatMoney(kpi.avg_order_cents), kpi.avg_order_cents, kpi_prev && kpi_prev.avg_order_cents],
    ["pending", String((currentView() === "analytics" ? data.by_status?.pending : kpi.pending) ?? 0), kpi.pending, kpi_prev && kpi_prev.pending],
  ];
  cards.forEach(([key, text, curr, prev]) => {
    const wrap = document.querySelector(`${currentView() === "analytics" ? "#analyticsKpis" : "#overview"} [data-kpi="${key}"]`);
    if (!wrap) return;
    const b = wrap.querySelector("b");
    if (b) b.textContent = text;
    const row = wrap.querySelector(".row");
    if (row) {
      const delta = computeDelta(
        typeof curr === "number" ? curr : 0,
        typeof prev === "number" ? prev : 0,
      );
      row.replaceChildren();
      if (currentView() !== "analytics" || key !== "pending") row.appendChild(el("span", { class: delta.cls }, [delta.html]));
      if (currentView() === "analytics") row.appendChild(el("span", { class: "lead" }, [(key === "pending" ? "" : " · ") + t("analytics.period", { days: state.days })]));
    }
  });
}

function themeColors() {
  const styles = getComputedStyle(document.documentElement);
  return {
    primary: styles.getPropertyValue("--primary").trim() || "#4f46e5",
    primary2: styles.getPropertyValue("--primary-2").trim() || "#06b6d4",
    text: styles.getPropertyValue("--text").trim() || "#0f172a",
    muted: styles.getPropertyValue("--muted").trim() || "#64748b",
    border: styles.getPropertyValue("--border").trim() || "#e2e8f0",
  };
}

function renderRevenueChart(data) {
  const c = themeColors();
  const labels = data.series.map((d) => d.date.slice(5));
  const values = data.series.map((d) => (d.revenue_cents || 0) / 100);
  const ctx = $("#revenueChart");
  if (charts.revenue) {
    charts.revenue.data.labels = labels;
    charts.revenue.data.datasets[0].data = values;
    charts.revenue.options.scales.x.ticks.color = c.muted;
    charts.revenue.options.scales.y.ticks.color = c.muted;
    charts.revenue.options.scales.x.grid.color = c.border;
    charts.revenue.options.scales.y.grid.color = c.border;
    charts.revenue.update();
    return;
  }
  const gradient = ctx.getContext("2d").createLinearGradient(0, 0, 0, 240);
  gradient.addColorStop(0, c.primary + "40");
  gradient.addColorStop(1, c.primary + "00");
  charts.revenue = new Chart(ctx, {
    type: "line",
    data: {
      labels,
      datasets: [{
        label: t("chart.revenue_label"),
        data: values,
        fill: true,
        tension: 0.35,
        borderColor: c.primary,
        backgroundColor: gradient,
        pointRadius: 0,
        pointHoverRadius: 4,
        borderWidth: 2,
      }],
    },
    options: {
      animation: false,
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: {
          ticks: { color: c.muted, maxRotation: 0, autoSkip: true, maxTicksLimit: 8 },
          grid: { color: c.border },
        },
        y: {
          ticks: { color: c.muted, callback: (v) => money(v * 100) },
          grid: { color: c.border },
          beginAtZero: true,
        },
      },
    },
  });
}

function renderStatusChart(data) {
  const c = themeColors();
  const all = ["pending", "paid", "shipped", "completed", "cancelled"];
  const counts = data.by_status || {};
  const labels = all.map((s) => STATUS_LABEL[s]);
  const values = all.map((s) => counts[s] || 0);
  const colors = all.map((s) => STATUS_COLORS[s]);
  const legend = $("#statusLegend");
  legend.innerHTML = "";
  all.forEach((s) => {
    const count = counts[s] || 0;
    const row = el("div", { class: "legend-item" }, [
      el("span", { class: "swatch", style: `background:${STATUS_COLORS[s]}` }),
      document.createTextNode(STATUS_LABEL[s]),
      el("b", {}, [String(count)]),
    ]);
    legend.appendChild(row);
  });
  const ctx = $("#statusChart");
  const total = values.reduce((a, b) => a + b, 0);
  if (charts.status) {
    charts.status.data.labels = labels;
    charts.status.data.datasets[0].data = values;
    charts.status.data.datasets[0].backgroundColor = colors;
    charts.status.options.plugins.tooltip.callbacks.label = (ctx) => `${ctx.label}: ${ctx.parsed} (${total ? Math.round(ctx.parsed / total * 100) : 0}%)`;
    charts.status.update();
    return;
  }
  charts.status = new Chart(ctx, {
    type: "doughnut",
    data: {
      labels,
      datasets: [{
        data: values,
        backgroundColor: colors,
        borderColor: "transparent",
        borderWidth: 2,
        hoverOffset: 6,
      }],
    },
    options: {
      animation: false,
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: (ctx) => `${ctx.label}: ${ctx.parsed} (${total ? Math.round(ctx.parsed / total * 100) : 0}%)`,
          },
        },
      },
      cutout: "68%",
    },
  });
}

function renderTopProducts(data) {
  const list = data.top_products || [];
  const root = $("#topProducts");
  root.innerHTML = "";
  if (!list.length) {
    root.appendChild(el("div", { class: "empty-state" }, [
      el("i", { class: "fa-solid fa-box-open" }),
      el("div", {}, [t("top.empty")]),
    ]));
    return;
  }
  const max = Math.max(1, ...list.map((p) => p.revenue_cents || 0));
  list.forEach((p) => {
    const pct = Math.round((p.revenue_cents || 0) / max * 100);
    const bar = el("div", { class: "tp-bar" }, [el("span", { style: `width:${pct}%` })]);
    const row = el("div", { class: "tp-row" }, [
      el("div", { style: "min-width:140px;flex:1" }, [
        el("div", { style: "font-weight:700;font-size:0.88rem" }, [p.product]),
        el("small", { class: "lead", style: "display:block;font-size:0.75rem" }, [
          `${tn("top.orders", p.orders)} · ${formatMoney(p.revenue_cents)}`,
        ]),
      ]),
      bar,
    ]);
    root.appendChild(row);
  });
}

async function refreshActivity() {
  try {
    const data = await OrdersApp.api("/api/activity?limit=10");
    renderActivity(data);
  } catch {
  }
}

function renderActivity(items) {
  const root = $("#activityList");
  root.innerHTML = "";
  if (!items || !items.length) {
    root.appendChild(el("div", { class: "empty-state" }, [
      el("i", { class: "fa-solid fa-clock" }),
      el("div", {}, [t("activity.empty")]),
    ]));
    return;
  }
  items.forEach((ev) => {
    const toLbl = STATUS_LABEL[ev.to_status] || ev.to_status;
    const text = ev.from_status
      ? `${STATUS_LABEL[ev.from_status] || ev.from_status} → ${toLbl}`
      : t("activity.created", { status: toLbl });
    const dot = el("span", { class: `dot ${ev.to_status === "cancelled" ? "off" : "on"}` });
    const body = el("div", {}, [
      el("div", { style: "font-weight:600;font-size:0.88rem" }, [
        `#${ev.order_display_id} · ${ev.customer_name || t("activity.customer")}`,
      ]),
      el("div", { class: "lead", style: "font-size:0.82rem" }, [text]),
      el("small", {}, [relativeTime(ev.created_at)]),
    ]);
    root.appendChild(el("div", { class: "activity-item" }, [dot, body]));
  });
}

async function refreshTable(refreshCountsToo, silent = false) {
  if (silent && tableAbort) return;
  if (tableAbort) tableAbort.abort();
  const ab = new AbortController();
  tableAbort = ab;
  const seq = ++requestSeq;
  const params = new URLSearchParams();
  ["status", "source", "q", "date_from", "date_to", "min_amount", "max_amount", "sort", "dir", "page", "per_page"]
    .forEach((k) => {
      if (state[k] !== "" && state[k] != null) params.set(k, state[k]);
    });
  const qs = params.toString();
  try {
    if (!silent) setTableLoading();
    const data = await OrdersApp.api(`/api/orders?${qs}`, { signal: ab.signal });
    if (seq !== requestSeq || (silent && autoRefreshPaused())) return;
    renderTable(data, silent);
    renderPagination(data);
    await refreshChipCounts();
  } catch (err) {
    if (err && err.name === "AbortError") return;
    if (seq !== requestSeq) return;
    if (!silent) renderTableError(err);
  } finally {
    if (tableAbort === ab) tableAbort = null;
  }
}

function setTableLoading() {
  const body = $("#ordersBody");
  $("#tableWrap").setAttribute("aria-busy", "true");
  if (body.querySelector("tr[data-id]")) return;
  body.innerHTML = "";
  for (let i = 0; i < 8; i++) {
    const tr = document.createElement("tr");
    tr.setAttribute("aria-hidden", "true");
    for (let column = 0; column < 7; column++) {
      const td = document.createElement("td");
      const placeholder = document.createElement("div");
      placeholder.className = "skeleton";
      placeholder.style.width = column === 1 ? "85%" : "65%";
      td.appendChild(placeholder);
      tr.appendChild(td);
    }
    body.appendChild(tr);
  }
}

function renderTableError(err) {
  $("#tableWrap").setAttribute("aria-busy", "false");
  const body = $("#ordersBody");
  if (body.querySelector("tr[data-id]")) {
    $("#tableNotice").replaceChildren(
      el("span", {}, [t("table.error_title") + " "]),
      el("button", { type: "button", class: "btn btn-ghost", onclick: () => refreshTable(false) }, [t("btn.retry")]),
    );
    return;
  }
  body.innerHTML = "";
  const tr = document.createElement("tr");
  const td = document.createElement("td");
  td.colSpan = 7;
  const root = el("div", { class: "error-state" }, [
    el("i", { class: "fa-solid fa-triangle-exclamation" }),
    el("div", { style: "font-weight:700;margin-bottom:4px" }, [t("table.error_title")]),
    el("div", { class: "lead", style: "font-size:0.85rem;margin-bottom:12px" }, [
      (err && I18N.apiMessage(err.message)) || t("table.unexpected"),
    ]),
    el("button", { class: "btn btn-primary", type: "button", onclick: () => refreshTable(false) }, [
      el("i", { class: "fa-solid fa-rotate" }),
      " " + t("btn.retry"),
    ]),
  ]);
  td.appendChild(root);
  tr.appendChild(td);
  body.appendChild(tr);
}

function renderTable(data, silent = false) {
  closeAllStatusMenus();
  $("#tableNotice").textContent = "";
  $("#tableWrap").setAttribute("aria-busy", "false");
  const body = $("#ordersBody");
  body.innerHTML = "";
  if (!data.items || !data.items.length) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 7;
    td.appendChild(el("div", { class: "empty-state" }, [
      el("i", { class: "fa-solid fa-inbox" }),
      el("div", {}, [t("table.empty")]),
    ]));
    tr.appendChild(td);
    body.appendChild(tr);
    return;
  }
  data.items.forEach((o) => {
    const tr = document.createElement("tr");
    tr.dataset.id = o.id;
    tr.tabIndex = 0;
    tr.addEventListener("keydown", (event) => {
      if (event.target === tr && (event.key === "Enter" || event.key === " ")) {
        event.preventDefault(); openOrderDrawer(o.id);
      }
    });
    tr.addEventListener("click", (e) => {
      if (e.target.closest(".actions") || e.target.closest("button")) return;
      openOrderDrawer(o.id);
    });
    const idCell = document.createElement("td");
    idCell.style.fontVariantNumeric = "tabular-nums";
    idCell.style.fontWeight = "700";
    idCell.textContent = `#${o.id + 1000}`;

    const custCell = document.createElement("td");
    custCell.appendChild(el("div", { style: "font-weight:600" }, [o.customer_name || "—", sourceBadge(o.source)]));
    if (o.customer_email) {
      const em = document.createElement("small");
      em.className = "lead";
      em.style.display = "block";
      em.style.fontSize = "0.75rem";
      em.textContent = o.customer_email;
      custCell.appendChild(em);
    }

    const prodCell = document.createElement("td");
    prodCell.textContent = o.product || "";

    const amtCell = document.createElement("td");
    amtCell.style.fontVariantNumeric = "tabular-nums";
    amtCell.style.fontWeight = "700";
    amtCell.textContent = formatMoney(o.amount_cents);

    const statusCell = document.createElement("td");
    statusCell.appendChild(badge(o.status));

    const dateCell = document.createElement("td");
    dateCell.style.fontVariantNumeric = "tabular-nums";
    dateCell.style.fontSize = "0.82rem";
    dateCell.textContent = (o.created_at || "").slice(0, 10);

    const actionsCell = document.createElement("td");
    const actions = el("div", { class: "actions", onclick: (e) => e.stopPropagation() });
    const nextStatuses = STATUS_NEXT[o.status] || [];
    if (nextStatuses.length) {
      let menu = null;
      const ensureMenu = () => {
        if (menu) return;
        menu = el("div", { class: "status-menu", role: "menu", id: `status-menu-${o.id}-${++statusMenuSequence}`, "aria-label": t("table.status_menu") });
        nextStatuses.forEach((s) => {
          const isCancel = s === "cancelled";
          const btn = el("button", { type: "button", role: "menuitem", tabindex: "-1" }, [
            isCancel ? "<i class='fa-solid fa-ban' style='color:var(--danger);margin-right:6px'></i>" :
            s === "paid" ? "<i class='fa-solid fa-credit-card' style='color:var(--ok);margin-right:6px'></i>" :
            s === "shipped" ? "<i class='fa-solid fa-truck-fast' style='color:var(--info);margin-right:6px'></i>" :
            s === "completed" ? "<i class='fa-solid fa-check' style='color:var(--primary);margin-right:6px'></i>" : "",
            t(`action.${s}`),
          ].map((x) => typeof x === "string" && x.startsWith("<") ? el("span", { html: x }) : document.createTextNode(x)));
          btn.addEventListener("click", (e) => {
            e.stopPropagation();
            closeAllStatusMenus(true);
            if (isCancel) {
              askConfirm(t("confirm.cancel_title"), t("confirm.cancel_body", { id: o.id + 1000 }), () =>
                changeStatus(o.id, "cancelled"),
              );
            } else {
              changeStatus(o.id, s);
            }
          });
          menu.appendChild(btn);
        });
        toggle.setAttribute("aria-controls", menu.id);
      };
      const toggle = el("button", {
        class: "icon-btn",
        type: "button",
        title: t("table.status_menu"),
        "aria-label": t("table.status_menu"),
        "aria-haspopup": "menu",
        "aria-expanded": "false",
        onclick: (e) => {
          e.stopPropagation();
          ensureMenu();
          if (menu.classList.contains("open")) closeAllStatusMenus(true);
          else openStatusMenu(menu, toggle);
        },
      }, [el("i", { class: "fa-solid fa-ellipsis-vertical", "aria-hidden": "true" })]);
      toggle.addEventListener("keydown", (event) => {
        if (event.key === "ArrowDown" || event.key === "ArrowUp") {
          event.preventDefault();
          ensureMenu();
          openStatusMenu(menu, toggle);
          if (event.key === "ArrowUp") menu.lastElementChild.focus();
        }
      });
      actions.appendChild(toggle);
    }
    actionsCell.appendChild(actions);

    tr.append(idCell, custCell, prodCell, amtCell, statusCell, dateCell, actionsCell);
    body.appendChild(tr);
  });
  if (!silent && !window.matchMedia("(prefers-reduced-motion: reduce)").matches) body.animate([{ opacity: .7 }, { opacity: 1 }], { duration: 160, easing: "cubic-bezier(.2,.8,.2,1)" });
}

function renderPagination(data) {
  const pag = $("#pagination");
  pag.innerHTML = "";
  const curr = Number(state.page) || 1;
  const pages = Number(data.pages) || 1;
  const total = Number(data.total) || 0;
  const per = Number(state.per_page) || 10;
  const from = total ? (curr - 1) * per + 1 : 0;
  const to = Math.min(curr * per, total);
  const info = el("span", { class: "info" }, [
    total ? t("pager.info", { from, to, total }) : t("pager.none"),
  ]);
  pag.appendChild(info);
  const prev = el("button", { type: "button", disabled: curr <= 1 ? "disabled" : null, "aria-label": t("pager.prev") }, [
    el("i", { class: "fa-solid fa-chevron-left" }),
  ]);
  prev.addEventListener("click", () => { state.page = Math.max(1, curr - 1); pushStateToUrl(); refreshTable(false); });
  pag.appendChild(prev);

  const pagesShown = new Set([1, pages, curr - 1, curr, curr + 1].filter((x) => x >= 1 && x <= pages));
  let last = 0;
  [...pagesShown].sort((a, b) => a - b).forEach((p) => {
    if (p - last > 1) {
      const g = document.createElement("button");
      g.type = "button";
      g.disabled = true;
      g.textContent = "…";
      g.style.border = "0";
      g.style.background = "transparent";
      g.style.boxShadow = "none";
      g.style.cursor = "default";
      pag.appendChild(g);
    }
    const b = el("button", { type: "button", class: p === curr ? "active" : null }, [String(p)]);
    b.addEventListener("click", () => { state.page = p; pushStateToUrl(); refreshTable(false); });
    pag.appendChild(b);
    last = p;
  });

  const nxt = el("button", { type: "button", disabled: curr >= pages ? "disabled" : null, "aria-label": t("pager.next") }, [
    el("i", { class: "fa-solid fa-chevron-right" }),
  ]);
  nxt.addEventListener("click", () => { state.page = Math.min(pages, curr + 1); pushStateToUrl(); refreshTable(false); });
  pag.appendChild(nxt);
}

async function refreshChipCounts() {
  const params = new URLSearchParams();
  ["source", "q", "date_from", "date_to", "min_amount", "max_amount"].forEach(key => { if (state[key]) params.set(key, state[key]); });
  try {
    const data = await OrdersApp.api(`/api/orders/counts?${params}`);
    document.querySelectorAll("[data-count-all]").forEach(node => { node.textContent = data.total; });
    Object.keys(STATUS_LABEL).forEach(status => {
      const node = document.querySelector(`[data-count-${status}]`);
      if (node) node.textContent = String(data.by_status[status] || 0);
    });
  } catch { /* Keep previous counts on network errors. */ }
}

const pendingStatuses = new Set();
async function changeStatus(id, status) {
  if (pendingStatuses.has(id)) return;
  pendingStatuses.add(id);
  const row = document.querySelector(`#ordersBody tr[data-id="${id}"]`);
  const cell = row?.children[4];
  const previous = cell?.firstElementChild;
  if (cell) cell.replaceChildren(badge(status));
  row?.setAttribute("aria-busy", "true");
  row?.querySelectorAll("button").forEach((button) => { button.disabled = true; });
  try {
    await OrdersApp.api(`/api/orders/${id}/status`, {
      method: "PATCH",
      body: JSON.stringify({ status }),
    });
    OrdersApp.toast(t("toast.status_changed", { status: STATUS_LABEL[status] }), "success");
    closeAllStatusMenus();
    refreshOverview();
    refreshTable(false);
    const drawer = $("#orderDrawer");
    if (drawer.classList.contains("open") && Number(drawer.dataset.id) === id) {
      openOrderDrawer(id);
    }
  } catch (err) {
    if (cell && previous) cell.replaceChildren(previous);
    if (err instanceof OrdersApp.ApiError && err.status === 409) {
      OrdersApp.toast(I18N.apiMessage(err.message) || t("toast.status_denied"), "error");
    } else if (err instanceof OrdersApp.ApiError) {
      OrdersApp.toast(I18N.apiMessage(err.message), "error");
    } else {
      OrdersApp.toast(t("toast.status_failed"), "error");
    }
  } finally {
    pendingStatuses.delete(id);
    row?.removeAttribute("aria-busy");
    row?.querySelectorAll("button").forEach((button) => { button.disabled = false; });
  }
}

let activeStatusMenu = null;
let statusMenuTrigger = null;
let statusMenuParent = null;
let statusMenuSequence = 0;

function statusMenuPosition(anchor, width, height, viewportWidth, viewportHeight) {
  const left = Math.max(8, Math.min(anchor.right - width, viewportWidth - width - 8));
  const below = anchor.bottom + 6;
  const top = below + height <= viewportHeight - 8 ? below : Math.max(8, anchor.top - height - 6);
  return { left, top };
}

function openStatusMenu(menu, trigger) {
  closeAllStatusMenus();
  activeStatusMenu = menu;
  statusMenuTrigger = trigger;
  statusMenuParent = trigger.parentNode;
  document.body.appendChild(menu);
  menu.classList.add("open");
  trigger.setAttribute("aria-expanded", "true");
  const box = menu.getBoundingClientRect();
  const position = statusMenuPosition(trigger.getBoundingClientRect(), box.width, box.height, window.innerWidth, window.innerHeight);
  menu.style.left = `${position.left}px`;
  menu.style.top = `${position.top}px`;
  menu.firstElementChild?.focus();
  menu.onkeydown = (event) => {
    const buttons = [...menu.querySelectorAll("button")];
    const index = buttons.indexOf(document.activeElement);
    if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
      event.preventDefault();
      const next = event.key === "Home" ? 0 : event.key === "End" ? buttons.length - 1 :
        (index + (event.key === "ArrowDown" ? 1 : -1) + buttons.length) % buttons.length;
      buttons[next]?.focus();
    } else if (event.key === "Escape") {
      event.preventDefault();
      closeAllStatusMenus(true);
    } else if (event.key === "Tab") closeAllStatusMenus(true);
  };
}

function closeAllStatusMenus(restoreFocus = false) {
  activeStatusMenu?.classList.remove("open");
  if (activeStatusMenu && statusMenuParent?.isConnected) statusMenuParent.appendChild(activeStatusMenu);
  else activeStatusMenu?.remove();
  statusMenuTrigger?.setAttribute("aria-expanded", "false");
  if (restoreFocus && statusMenuTrigger?.isConnected) statusMenuTrigger.focus();
  activeStatusMenu = null;
  statusMenuTrigger = null;
  statusMenuParent = null;
}

function askConfirm(title, body, onOk) {
  $("#confirmTitle").textContent = title;
  $("#confirmBody").textContent = body;
  const ok = $("#confirmOk");
  const newOk = ok.cloneNode(true);
  ok.parentNode.replaceChild(newOk, ok);
  newOk.addEventListener("click", () => {
    OrdersApp.closeModal("confirmModal");
    onOk();
  });
  OrdersApp.openModal("confirmModal");
}

let drawerReturnFocus;
async function openOrderDrawer(id) {
  const drawer = $("#orderDrawer");
  if (!drawer.classList.contains("open")) drawerReturnFocus = document.activeElement;
  drawer.inert = false;
  drawer.setAttribute("aria-hidden", "false");
  drawer.dataset.id = id;
  OrdersApp.openModal("drawerBackdrop");
  OrdersApp.openModal("orderDrawer");
  $("#drawerClose").focus();
  const body = $("#drawerBody");
  body.innerHTML = '<div class="skeleton" style="height:200px"></div>';
  $("#drawerTitle").textContent = t("drawer.title", { id: id + 1000 });
  try {
    const order = await OrdersApp.api(`/api/orders/${id}`);
    if (drawer.classList.contains("open") && Number(drawer.dataset.id) === id) renderOrderDrawer(order);
  } catch (err) {
    body.innerHTML = "";
    body.appendChild(el("div", { class: "error-state" }, [
      el("i", { class: "fa-solid fa-triangle-exclamation" }),
      el("div", {}, [t("drawer.error")]),
      el("button", {
        class: "btn btn-primary",
        type: "button",
        style: "margin-top:10px",
        onclick: () => openOrderDrawer(id),
      }, [t("btn.retry")]),
    ]));
  }
}

function closeOrderDrawer() {
  OrdersApp.closeDrawer("orderDrawer");
  OrdersApp.closeModal("drawerBackdrop");
  if (drawerReturnFocus?.isConnected) drawerReturnFocus.focus();
}

function renderOrderDrawer(order) {
  const body = $("#drawerBody");
  body.innerHTML = "";
  const grid = el("div", { style: "display:grid;gap:14px;margin-bottom:18px" });
  grid.appendChild(el("div", {}, [
    el("div", { class: "lead", style: "font-size:0.75rem;margin-bottom:4px" }, [t("field.status")]),
    badge(order.status),
  ]));
  grid.appendChild(el("div", {}, [
    el("div", { class: "lead", style: "font-size:0.75rem;margin-bottom:4px" }, [t("field.customer")]),
    el("div", { style: "font-weight:700" }, [order.customer_name || "—", sourceBadge(order.source)]),
    order.customer_email ? el("small", { class: "lead", style: "display:block" }, [order.customer_email]) : null,
    order.customer_phone ? el("small", { class: "lead", style: "display:block" }, [order.customer_phone]) : null,
    tgLink(order) ? el("small", { style: "display:block" }, [tgLink(order)]) : null,
  ]));
  grid.appendChild(el("div", {}, [
    el("div", { class: "lead", style: "font-size:0.75rem;margin-bottom:4px" }, [t("field.product")]),
    el("div", { style: "font-weight:600" }, [order.product || "—"]),
  ]));
  if ((order.items || []).length) {
    grid.appendChild(el("div", {}, [
      el("div", { class: "lead", style: "font-size:0.75rem;margin-bottom:4px" }, [t("field.items")]),
      ...order.items.map((i) => el("div", { style: "display:flex;justify-content:space-between;gap:10px" }, [
        el("span", {}, [`${i.product} × ${i.quantity}`]),
        el("span", { style: "font-variant-numeric:tabular-nums" }, [formatMoney(i.unit_price_cents * i.quantity)]),
      ])),
    ]));
  }
  if (order.delivery_date) {
    grid.appendChild(el("div", {}, [
      el("div", { class: "lead", style: "font-size:0.75rem;margin-bottom:4px" }, [t("field.delivery")]),
      el("div", { style: "font-weight:600" }, [order.delivery_date]),
    ]));
  }
  grid.appendChild(el("div", { style: "display:grid;grid-template-columns:1fr 1fr;gap:14px" }, [
    el("div", {}, [
      el("div", { class: "lead", style: "font-size:0.75rem;margin-bottom:4px" }, [t("field.amount")]),
      el("div", { style: "font-weight:800" }, [formatMoney(order.amount_cents)]),
    ]),
    el("div", {}, [
      el("div", { class: "lead", style: "font-size:0.75rem;margin-bottom:4px" }, [t("field.created")]),
      el("div", { style: "font-variant-numeric:tabular-nums" }, [(order.created_at || "").replace("T", " ").slice(0, 19) + " UTC"]),
    ]),
  ]));
  if (order.note) {
    grid.appendChild(el("div", {}, [
      el("div", { class: "lead", style: "font-size:0.75rem;margin-bottom:4px" }, [t("field.note")]),
      el("div", {
        style: "background:var(--bg);padding:10px 12px;border-radius:10px;border:1px solid var(--border);white-space:pre-wrap;word-break:break-word",
      }, [order.note]),
    ]));
  }
  body.appendChild(grid);

  const nextStatuses = STATUS_NEXT[order.status] || [];
  if (nextStatuses.length) {
    const actionRow = el("div", { style: "display:flex;gap:8px;flex-wrap:wrap;margin:6px 0 18px" });
    nextStatuses.forEach((s) => {
      const cls = s === "cancelled" ? "btn-ghost" : "btn-primary";
      const label = t(`action.${s}`);
      const btn = el("button", { class: `btn ${cls}`, type: "button" }, [label]);
      btn.addEventListener("click", () => {
        if (s === "cancelled") {
          askConfirm(t("confirm.cancel_title"), t("confirm.cancel_body", { id: order.id + 1000 }), () => {
            changeStatus(order.id, "cancelled");
          });
        } else {
          changeStatus(order.id, s);
        }
      });
      actionRow.appendChild(btn);
    });
    body.appendChild(actionRow);
  }

  const h3 = el("h3", { style: "margin-bottom:4px" }, [t("drawer.history")]);
  body.appendChild(h3);
  const timeline = el("div", { class: "timeline" });
  (order.events || []).forEach((ev) => {
    const toLbl = STATUS_LABEL[ev.to_status] || ev.to_status;
    const title = ev.from_status
      ? `${STATUS_LABEL[ev.from_status] || ev.from_status} → ${toLbl}`
      : t("history.created", { status: toLbl });
    timeline.appendChild(el("div", { class: "timeline-item" }, [
      el("div", { style: "font-weight:700" }, [title]),
      el("small", {}, [(ev.created_at || "").replace("T", " ").slice(0, 19) + " UTC · " + relativeTime(ev.created_at)]),
    ]));
  });
  body.appendChild(timeline);
}

async function submitNewOrder(e) {
  e.preventDefault();
  const form = e.target;
  if (form.getAttribute("aria-busy") === "true") return;
  form.setAttribute("aria-busy", "true");
  const submit = form.querySelector('[type="submit"]');
  submit.disabled = true;
  OrdersApp.clearFieldErrors(form);
  const data = Object.fromEntries(new FormData(form));
  const payload = {
    customer_name: (data.customer_name || "").trim(),
    product: (data.product || "").trim(),
    amount: data.amount,
  };
  if ((data.customer_email || "").trim()) payload.customer_email = data.customer_email.trim();
  if ((data.note || "").trim()) payload.note = data.note.trim();
  try {
    await OrdersApp.api("/api/orders", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    OrdersApp.closeModal("newOrderModal");
    form.reset();
    OrdersApp.toast(t("toast.order_created"), "success");
    state.page = 1;
    pushStateToUrl();
    refreshAll();
  } catch (err) {
    if (err instanceof OrdersApp.ApiError && err.status === 422) {
      OrdersApp.renderFieldErrors(form, err.fields);
    } else if (err instanceof OrdersApp.ApiError) {
      OrdersApp.toast(I18N.apiMessage(err.message), "error");
    } else {
      OrdersApp.toast(t("toast.create_failed"), "error");
    }
  } finally {
    form.setAttribute("aria-busy", "false");
    submit.disabled = false;
  }
}

function exportCsv() {
  const params = new URLSearchParams();
  ["status", "source", "q", "date_from", "date_to", "min_amount", "max_amount", "sort", "dir"].forEach((k) => {
    if (state[k] !== "" && state[k] != null) params.set(k, state[k]);
  });
  window.location.href = `/api/orders/export.csv?${params.toString()}`;
}

async function resetDemoData() {
  askConfirm(
    t("reset.title"),
    t("reset.body"),
    async () => {
      try {
        await OrdersApp.api("/api/demo/reset", { method: "POST" });
        OrdersApp.toast(t("toast.reset_done"), "success");
        state.page = 1;
        pushStateToUrl();
        refreshAll();
      } catch (err) {
        if (err instanceof OrdersApp.ApiError) OrdersApp.toast(I18N.apiMessage(err.message), "error");
        else OrdersApp.toast(t("toast.reset_failed"), "error");
      }
    },
  );
}

function initDashboardUI() {
  applyStateFromUrl();
  document.addEventListener("workspace:change", () => void refreshAll());
  document.querySelector("[data-quick-new]").addEventListener("click", () => $("#newOrderBtn").click());
  document.querySelector("[data-quick-export]").addEventListener("click", exportCsv);



  $("#sidebarLogout").addEventListener("click", async () => {
    try { await OrdersApp.api("/api/logout", { method: "POST" }); } catch {}
    window.location.href = "/";
  });

  $("#newOrderBtn").addEventListener("click", () => {
    OrdersApp.clearFieldErrors($("#newOrderForm"));
    $("#newOrderForm").reset();
    OrdersApp.openModal("newOrderModal");
  });

  $("#exportBtn").addEventListener("click", exportCsv);
  $("#resetDemoBtn")?.addEventListener("click", resetDemoData);
  $("#newOrderForm").addEventListener("submit", submitNewOrder);

  $("#drawerClose").addEventListener("click", closeOrderDrawer);
  $("#drawerBackdrop").addEventListener("click", closeOrderDrawer);

  const clockEl = $("#clock");
  if (clockEl) {
    const tick = () => {
      const d = new Date();
      const pad = (n) => String(n).padStart(2, "0");
      clockEl.textContent = `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
    };
    tick();
    setInterval(tick, 1000);
  }

  $$("#statusChips .chip").forEach((c) => {
    c.addEventListener("click", () => {
      state.status = c.dataset.status || "";
      state.page = 1;
      pushStateToUrl();
      syncUIFromState();
      refreshTable(false);
    });
  });

  $("#sourceFilter").addEventListener("change", (e) => {
    state.source = e.target.value;
    state.page = 1;
    pushStateToUrl();
    refreshTable(false);
  });

  const debouncedSearch = debounce(() => {
    state.q = $("#searchInput").value.trim();
    state.page = 1;
    pushStateToUrl();
    refreshTable(false);
  }, 300);
  $("#searchInput").addEventListener("input", debouncedSearch);

  ["dateFrom", "dateTo", "minAmount", "maxAmount"].forEach((id) => {
    const key = id.replace(/[A-Z]/g, (m) => "_" + m.toLowerCase());
    $(`#${id}`).addEventListener("change", (e) => {
      state[key] = e.target.value;
      state.page = 1;
      pushStateToUrl();
      refreshTable(false);
    });
  });

  $("#resetFiltersBtn").addEventListener("click", () => {
    ["status", "source", "q", "date_from", "date_to", "min_amount", "max_amount"].forEach((k) => (state[k] = DEFAULT_STATE[k]));
    state.sort = DEFAULT_STATE.sort;
    state.dir = DEFAULT_STATE.dir;
    state.page = 1;
    pushStateToUrl();
    syncUIFromState();
    refreshTable(false);
  });

  $$("table thead th[data-sort]").forEach((th) => {
    th.tabIndex = 0;
    th.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") { event.preventDefault(); th.click(); }
    });
    th.addEventListener("click", () => {
      const col = th.dataset.sort;
      if (state.sort === col) {
        state.dir = state.dir === "asc" ? "desc" : "asc";
      } else {
        state.sort = col;
        state.dir = "desc";
      }
      state.page = 1;
      pushStateToUrl();
      syncUIFromState();
      refreshTable(false);
    });
  });

  $$(".period-toggle button").forEach((b) => {
    b.addEventListener("click", () => {
      state.days = Number(b.dataset.days);
      pushStateToUrl();
      syncUIFromState();
      refreshStats();
    });
  });

  document.addEventListener("click", (e) => {
    if (!e.target.closest(".actions, .status-menu")) closeAllStatusMenus();
  });

  window.addEventListener("scroll", () => closeAllStatusMenus(), true);
  window.addEventListener("resize", () => {
    closeAllStatusMenus();
    if (charts.revenue) charts.revenue.resize();
    if (charts.status) charts.status.resize();
  });

  I18N.onChange(() => {
    if (charts.revenue) { charts.revenue.destroy(); charts.revenue = null; }
    if (charts.status) { charts.status.destroy(); charts.status = null; }
    syncUIFromState();
    refreshAll();
    const drawer = $("#orderDrawer");
    if (drawer.classList.contains("open")) openOrderDrawer(Number(drawer.dataset.id));
  });

  const mq = window.matchMedia("(prefers-color-scheme: dark)");
  const origSetTheme = window.setTheme;
  if (typeof origSetTheme === "function") {
    const observer = new MutationObserver(() => {
      if (charts.revenue || charts.status) {
        if (lastStatsDays != null) {
          if (charts.revenue) { charts.revenue.destroy(); charts.revenue = null; }
          if (charts.status) { charts.status.destroy(); charts.status = null; }
          refreshStats();
        }
      }
    });
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  }
}

document.addEventListener("DOMContentLoaded", async () => {
  try {
    await OrdersApp.api("/api/me");
  } catch {
  }
  initDashboardUI();
  await refreshAll();
  startAutoRefresh();
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) {
      void autoRefresh();
      startAutoRefresh();
    }
  });
  window.addEventListener("pagehide", () => clearTimeout(autoRefreshTimer));
  window.addEventListener("pageshow", startAutoRefresh);
});
