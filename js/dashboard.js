const MONEY = new Intl.NumberFormat("ru-RU", { style: "currency", currency: "RUB", maximumFractionDigits: 0 });

const STATUS_LABEL = {
  pending: "Pending",
  paid: "Paid",
  shipped: "Shipped",
  completed: "Completed",
  cancelled: "Cancelled",
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

const REL_TIME = new Intl.RelativeTimeFormat("en", { numeric: "auto" });
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
  return MONEY.format((cents || 0) / 100);
}

function relativeTime(iso) {
  const diff = new Date(iso).getTime() - Date.now();
  const absSec = Math.round(diff / 1000);
  if (Math.abs(absSec) < 60) return REL_TIME.format(absSec, "second");
  const absMin = Math.round(absSec / 60);
  if (Math.abs(absMin) < 60) return REL_TIME.format(absMin, "minute");
  const absHr = Math.round(absMin / 60);
  if (Math.abs(absHr) < 24) return REL_TIME.format(absHr, "hour");
  const absDay = Math.round(absHr / 24);
  if (Math.abs(absDay) < 30) return REL_TIME.format(absDay, "day");
  const absMo = Math.round(absDay / 30);
  if (Math.abs(absMo) < 12) return REL_TIME.format(absMo, "month");
  return REL_TIME.format(Math.round(absMo / 12), "year");
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
  const url = q ? `${location.pathname}?${q}` : location.pathname;
  history.replaceState({}, "", url);
}

function syncUIFromState() {
  $$("#statusChips .chip").forEach((c) => {
    c.classList.toggle("active", (c.dataset.status || "") === state.status);
  });
  $("#searchInput").value = state.q || "";
  $("#dateFrom").value = state.date_from || "";
  $("#dateTo").value = state.date_to || "";
  $("#minAmount").value = state.min_amount || "";
  $("#maxAmount").value = state.max_amount || "";
  $$("table thead th").forEach((th) => {
    const col = th.dataset.sort;
    const ind = th.querySelector(".sort-indicator");
    if (!col) return;
    th.classList.toggle("active-sort", col === state.sort);
    if (ind) {
      if (col === state.sort) ind.textContent = state.dir === "asc" ? "▲" : "▼";
      else ind.textContent = "";
    }
  });
  $$(".period-toggle button").forEach((b) => {
    b.classList.toggle("active", Number(b.dataset.days) === Number(state.days));
  });
}

async function refreshAll() {
  await Promise.all([refreshStats(), refreshActivity(), refreshTable(true)]);
}

async function refreshStats() {
  const days = Number(state.days) || 30;
  try {
    const data = await OrdersApp.api(`/api/stats?days=${days}`);
    renderKpis(data);
    renderRevenueChart(data);
    renderStatusChart(data);
    renderTopProducts(data);
    lastStatsDays = days;
  } catch (err) {
    if (err instanceof OrdersApp.ApiError) {
      OrdersApp.toast("Failed to load stats: " + err.message, "error");
    }
  }
}

function renderKpis(data) {
  const { kpi, kpi_prev } = data || { kpi: {}, kpi_prev: {} };
  const cards = [
    ["revenue", formatMoney(kpi.revenue_cents), kpi.revenue_cents, kpi_prev && kpi_prev.revenue_cents],
    ["orders", String(kpi.orders ?? 0), kpi.orders, kpi_prev && kpi_prev.orders],
    ["avg", formatMoney(kpi.avg_order_cents), kpi.avg_order_cents, kpi_prev && kpi_prev.avg_order_cents],
    ["pending", String(kpi.pending ?? 0), kpi.pending, kpi_prev && kpi_prev.pending],
  ];
  cards.forEach(([key, text, curr, prev]) => {
    const wrap = document.querySelector(`[data-kpi="${key}"]`);
    if (!wrap) return;
    const b = wrap.querySelector("b");
    if (b) b.textContent = text;
    const row = wrap.querySelector(".row");
    if (row) {
      const delta = computeDelta(
        typeof curr === "number" ? curr : 0,
        typeof prev === "number" ? prev : 0,
      );
      row.innerHTML = `<span class="${delta.cls}">${delta.html}</span>`;
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
        label: "Revenue (RUB)",
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
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: {
          ticks: { color: c.muted, maxRotation: 0, autoSkip: true, maxTicksLimit: 8 },
          grid: { color: c.border },
        },
        y: {
          ticks: { color: c.muted, callback: (v) => MONEY.format(v) },
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
      el("div", {}, ["No product data"]),
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
          `${p.orders} orders · ${formatMoney(p.revenue_cents)}`,
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
      el("div", {}, ["No activity yet"]),
    ]));
    return;
  }
  items.forEach((ev) => {
    const fromLbl = ev.from_status ? STATUS_LABEL[ev.from_status] : "Created";
    const toLbl = STATUS_LABEL[ev.to_status] || ev.to_status;
    const text = ev.from_status
      ? `${fromLbl} → ${toLbl}`
      : `Created (${toLbl})`;
    const dot = el("span", { class: `dot ${ev.to_status === "cancelled" ? "off" : "on"}` });
    const body = el("div", {}, [
      el("div", { style: "font-weight:600;font-size:0.88rem" }, [
        `#${ev.order_display_id} · ${ev.customer_name || "Customer"}`,
      ]),
      el("div", { class: "lead", style: "font-size:0.82rem" }, [text]),
      el("small", {}, [relativeTime(ev.created_at)]),
    ]);
    root.appendChild(el("div", { class: "activity-item" }, [dot, body]));
  });
}

async function refreshTable(refreshCountsToo) {
  if (tableAbort) tableAbort.abort();
  const ab = new AbortController();
  tableAbort = ab;
  const seq = ++requestSeq;
  const params = new URLSearchParams();
  ["status", "q", "date_from", "date_to", "min_amount", "max_amount", "sort", "dir", "page", "per_page"]
    .forEach((k) => {
      if (state[k] !== "" && state[k] != null) params.set(k, state[k]);
    });
  const qs = params.toString();
  try {
    setTableLoading();
    const data = await OrdersApp.api(`/api/orders?${qs}`, { signal: ab.signal });
    if (seq !== requestSeq) return;
    renderTable(data);
    renderPagination(data);
    if (refreshCountsToo) refreshChipCounts();
  } catch (err) {
    if (err && err.name === "AbortError") return;
    if (seq !== requestSeq) return;
    renderTableError(err);
  }
}

function setTableLoading() {
  const body = $("#ordersBody");
  body.innerHTML = "";
  for (let i = 0; i < 8; i++) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 7;
    td.innerHTML = `<div class="skeleton" style="height:24px;margin:6px 0"></div>`;
    tr.appendChild(td);
    body.appendChild(tr);
  }
}

function renderTableError(err) {
  const body = $("#ordersBody");
  body.innerHTML = "";
  const tr = document.createElement("tr");
  const td = document.createElement("td");
  td.colSpan = 7;
  const root = el("div", { class: "error-state" }, [
    el("i", { class: "fa-solid fa-triangle-exclamation" }),
    el("div", { style: "font-weight:700;margin-bottom:4px" }, ["Could not load orders"]),
    el("div", { class: "lead", style: "font-size:0.85rem;margin-bottom:12px" }, [
      (err && err.message) || "Unexpected error",
    ]),
    el("button", { class: "btn btn-primary", type: "button", onclick: () => refreshTable(false) }, [
      "<i class='fa-solid fa-rotate'></i> Retry",
    ].map((s) => (typeof s === "string" && s.startsWith("<") ? el("span", { html: s.replace(/^<[^>]+>|<[^>]+>$/g, "") }) : s))),
  ]);
  td.appendChild(root);
  tr.appendChild(td);
  body.appendChild(tr);
}

function renderTable(data) {
  const body = $("#ordersBody");
  body.innerHTML = "";
  if (!data.items || !data.items.length) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 7;
    td.appendChild(el("div", { class: "empty-state" }, [
      el("i", { class: "fa-solid fa-inbox" }),
      el("div", {}, ["No orders match your filters"]),
    ]));
    tr.appendChild(td);
    body.appendChild(tr);
    return;
  }
  data.items.forEach((o) => {
    const tr = document.createElement("tr");
    tr.dataset.id = o.id;
    tr.addEventListener("click", (e) => {
      if (e.target.closest(".actions") || e.target.closest("button")) return;
      openOrderDrawer(o.id);
    });
    const idCell = document.createElement("td");
    idCell.style.fontVariantNumeric = "tabular-nums";
    idCell.style.fontWeight = "700";
    idCell.textContent = `#${o.id + 1000}`;

    const custCell = document.createElement("td");
    custCell.appendChild(el("div", { style: "font-weight:600" }, [o.customer_name || "—"]));
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
      const menu = el("div", { class: "status-menu" });
      nextStatuses.forEach((s) => {
        const isCancel = s === "cancelled";
        const btn = el("button", { type: "button" }, [
          isCancel ? "<i class='fa-solid fa-ban' style='color:var(--danger);margin-right:6px'></i>" :
          s === "paid" ? "<i class='fa-solid fa-credit-card' style='color:var(--ok);margin-right:6px'></i>" :
          s === "shipped" ? "<i class='fa-solid fa-truck-fast' style='color:var(--info);margin-right:6px'></i>" :
          s === "completed" ? "<i class='fa-solid fa-check' style='color:var(--primary);margin-right:6px'></i>" : "",
          STATUS_LABEL[s],
        ].map((x) => typeof x === "string" && x.startsWith("<") ? el("span", { html: x }) : document.createTextNode(x)));
        btn.addEventListener("click", (e) => {
          e.stopPropagation();
          menu.classList.remove("open");
          if (isCancel) {
            askConfirm("Cancel order", `Cancel #${o.id + 1000}? This cannot be undone.`, () =>
              changeStatus(o.id, "cancelled"),
            );
          } else {
            changeStatus(o.id, s);
          }
        });
        menu.appendChild(btn);
      });
      const toggle = el("button", {
        class: "icon-btn",
        type: "button",
        title: "Change status",
        "aria-haspopup": "true",
        onclick: (e) => {
          e.stopPropagation();
          $$(".status-menu.open").forEach((m) => { if (m !== menu) m.classList.remove("open"); });
          menu.classList.toggle("open");
        },
      }, [el("i", { class: "fa-solid fa-ellipsis-vertical" })]);
      actions.appendChild(toggle);
      actions.appendChild(menu);
    }
    actionsCell.appendChild(actions);

    tr.append(idCell, custCell, prodCell, amtCell, statusCell, dateCell, actionsCell);
    body.appendChild(tr);
  });
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
    total ? `${from}–${to} of ${total}` : "0 orders",
  ]);
  pag.appendChild(info);
  const prev = el("button", { type: "button", disabled: curr <= 1 ? "disabled" : null, "aria-label": "Previous page" }, [
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

  const nxt = el("button", { type: "button", disabled: curr >= pages ? "disabled" : null, "aria-label": "Next page" }, [
    el("i", { class: "fa-solid fa-chevron-right" }),
  ]);
  nxt.addEventListener("click", () => { state.page = Math.min(pages, curr + 1); pushStateToUrl(); refreshTable(false); });
  pag.appendChild(nxt);
}

async function refreshChipCounts() {
  try {
    const data = await OrdersApp.api("/api/orders?per_page=1");
    const all = data.total || 0;
    document.querySelectorAll("[data-count-all]").forEach((e) => (e.textContent = all));
  } catch {
  }
  try {
    const raw = await OrdersApp.api("/api/stats?days=9999");
    const by = raw.by_status || {};
    Object.keys(STATUS_LABEL).forEach((s) => {
      const el_count = document.querySelector(`[data-count-${s}]`);
      if (el_count) el_count.textContent = String(by[s] || 0);
    });
  } catch {
  }
}

async function changeStatus(id, status) {
  try {
    await OrdersApp.api(`/api/orders/${id}/status`, {
      method: "PATCH",
      body: JSON.stringify({ status }),
    });
    OrdersApp.toast(`Status changed to ${STATUS_LABEL[status]}`, "success");
    closeAllStatusMenus();
    refreshStats();
    refreshActivity();
    refreshTable(false);
    refreshChipCounts();
    const drawer = $("#orderDrawer");
    if (drawer.classList.contains("open") && Number(drawer.dataset.id) === id) {
      openOrderDrawer(id);
    }
  } catch (err) {
    if (err instanceof OrdersApp.ApiError && err.status === 409) {
      OrdersApp.toast(err.message || "Status change not allowed", "error");
    } else if (err instanceof OrdersApp.ApiError) {
      OrdersApp.toast(err.message, "error");
    } else {
      OrdersApp.toast("Could not change status", "error");
    }
  }
}

function closeAllStatusMenus() {
  $$(".status-menu.open").forEach((m) => m.classList.remove("open"));
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

async function openOrderDrawer(id) {
  const drawer = $("#orderDrawer");
  const backdrop = $("#drawerBackdrop");
  drawer.dataset.id = id;
  backdrop.style.display = "grid";
  drawer.classList.add("open");
  const body = $("#drawerBody");
  body.innerHTML = '<div class="skeleton" style="height:200px"></div>';
  $("#drawerTitle").textContent = `Order #${id + 1000}`;
  try {
    const order = await OrdersApp.api(`/api/orders/${id}`);
    renderOrderDrawer(order);
  } catch (err) {
    body.innerHTML = "";
    body.appendChild(el("div", { class: "error-state" }, [
      el("i", { class: "fa-solid fa-triangle-exclamation" }),
      el("div", {}, ["Could not load order"]),
      el("button", {
        class: "btn btn-primary",
        type: "button",
        style: "margin-top:10px",
        onclick: () => openOrderDrawer(id),
      }, ["Retry"]),
    ]));
  }
}

function closeOrderDrawer() {
  $("#orderDrawer").classList.remove("open");
  $("#drawerBackdrop").style.display = "none";
}

function renderOrderDrawer(order) {
  const body = $("#drawerBody");
  body.innerHTML = "";
  const grid = el("div", { style: "display:grid;gap:14px;margin-bottom:18px" });
  grid.appendChild(el("div", {}, [
    el("div", { class: "lead", style: "font-size:0.75rem;margin-bottom:4px" }, ["Status"]),
    badge(order.status),
  ]));
  grid.appendChild(el("div", {}, [
    el("div", { class: "lead", style: "font-size:0.75rem;margin-bottom:4px" }, ["Customer"]),
    el("div", { style: "font-weight:700" }, [order.customer_name || "—"]),
    order.customer_email ? el("small", { class: "lead" }, [order.customer_email]) : null,
  ]));
  grid.appendChild(el("div", {}, [
    el("div", { class: "lead", style: "font-size:0.75rem;margin-bottom:4px" }, ["Product"]),
    el("div", { style: "font-weight:600" }, [order.product || "—"]),
  ]));
  grid.appendChild(el("div", { style: "display:grid;grid-template-columns:1fr 1fr;gap:14px" }, [
    el("div", {}, [
      el("div", { class: "lead", style: "font-size:0.75rem;margin-bottom:4px" }, ["Amount"]),
      el("div", { style: "font-weight:800" }, [formatMoney(order.amount_cents)]),
    ]),
    el("div", {}, [
      el("div", { class: "lead", style: "font-size:0.75rem;margin-bottom:4px" }, ["Created"]),
      el("div", { style: "font-variant-numeric:tabular-nums" }, [(order.created_at || "").replace("T", " ").slice(0, 19) + " UTC"]),
    ]),
  ]));
  if (order.note) {
    grid.appendChild(el("div", {}, [
      el("div", { class: "lead", style: "font-size:0.75rem;margin-bottom:4px" }, ["Note"]),
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
      const label = s === "cancelled" ? "Cancel order" : `Mark ${STATUS_LABEL[s]}`;
      const btn = el("button", { class: `btn ${cls}`, type: "button" }, [label]);
      btn.addEventListener("click", () => {
        if (s === "cancelled") {
          askConfirm("Cancel order", `Cancel #${order.id + 1000}? This cannot be undone.`, () => {
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

  const h3 = el("h3", { style: "margin-bottom:4px" }, ["History"]);
  body.appendChild(h3);
  const timeline = el("div", { class: "timeline" });
  (order.events || []).forEach((ev) => {
    const fromLbl = ev.from_status ? STATUS_LABEL[ev.from_status] : "Created";
    const toLbl = STATUS_LABEL[ev.to_status] || ev.to_status;
    const title = ev.from_status ? `${fromLbl} → ${toLbl}` : `Order created (${toLbl})`;
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
    OrdersApp.toast("Order created", "success");
    state.page = 1;
    pushStateToUrl();
    refreshAll();
  } catch (err) {
    if (err instanceof OrdersApp.ApiError && err.status === 422) {
      OrdersApp.renderFieldErrors(form, err.fields);
    } else if (err instanceof OrdersApp.ApiError) {
      OrdersApp.toast(err.message, "error");
    } else {
      OrdersApp.toast("Could not create order", "error");
    }
  }
}

function exportCsv() {
  const params = new URLSearchParams();
  ["status", "q", "date_from", "date_to", "min_amount", "max_amount", "sort", "dir"].forEach((k) => {
    if (state[k] !== "" && state[k] != null) params.set(k, state[k]);
  });
  const token = OrdersApp.getCsrfToken();
  const url = `/api/orders/export.csv?${params.toString()}`;
  if (token) {
    const form = document.createElement("form");
    form.method = "GET";
    form.action = url;
    document.body.appendChild(form);
    form.submit();
    form.remove();
  } else {
    window.location.href = url;
  }
}

async function resetDemoData() {
  askConfirm(
    "Reset demo data",
    "This will restore the original 60 demo orders. Your experiments will be lost. Continue?",
    async () => {
      try {
        await OrdersApp.api("/api/demo/reset", { method: "POST" });
        OrdersApp.toast("Demo data restored", "success");
        state.page = 1;
        pushStateToUrl();
        refreshAll();
      } catch (err) {
        if (err instanceof OrdersApp.ApiError) OrdersApp.toast(err.message, "error");
        else OrdersApp.toast("Could not reset data", "error");
      }
    },
  );
}

function initDashboardUI() {
  applyStateFromUrl();

  $("#collapseBtn")?.addEventListener("click", () => {
    $("#dashShell").classList.toggle("collapsed");
  });

  $("#sidebarLogout").addEventListener("click", async () => {
    try { await OrdersApp.api("/api/logout", { method: "POST" }); } catch {}
    window.location.href = "/";
  });

  $$(".sidebar a[data-nav]").forEach((a) => {
    a.addEventListener("click", () => {
      $$(".sidebar a[data-nav]").forEach((x) => x.classList.remove("active"));
      a.classList.add("active");
    });
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
    ["status", "q", "date_from", "date_to", "min_amount", "max_amount"].forEach((k) => (state[k] = DEFAULT_STATE[k]));
    state.sort = DEFAULT_STATE.sort;
    state.dir = DEFAULT_STATE.dir;
    state.page = 1;
    pushStateToUrl();
    syncUIFromState();
    refreshTable(false);
  });

  $$("table thead th[data-sort]").forEach((th) => {
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
    if (!e.target.closest(".actions")) closeAllStatusMenus();
  });

  window.addEventListener("resize", () => {
    if (charts.revenue) charts.revenue.resize();
    if (charts.status) charts.status.resize();
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
  refreshAll();
});
