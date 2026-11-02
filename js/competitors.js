/* External names are always rendered as text, never HTML. */
(() => {
  const t = (key, params) => I18N.t(key, params);
  const node = (tag, text) => {
    const result = document.createElement(tag);
    if (text !== undefined) result.textContent = text;
    return result;
  };
  const money = (cents) => new Intl.NumberFormat(I18N.getLang(), {
    style: "currency", currency: "RUB", minimumFractionDigits: 2,
  }).format(cents / 100);
  const cursorKey = "competitor:lastNotifiedEvent";
  let cursor = null;
  try {
    const saved = localStorage.getItem(cursorKey);
    if (saved !== null && /^\d+$/.test(saved) && Number.isSafeInteger(Number(saved))) {
      cursor = Number(saved);
    }
  } catch { /* Polling still works when browser storage is unavailable. */ }
  let busy = false;
  let refreshAgain = false;
  let timer;
  let snapshot;
  let mappingWrites = 0;
  let renderPending = false;

  function renderWhenIdle() {
    if (!snapshot) return;
    const rows = document.getElementById("competitorRows");
    if (mappingWrites || rows.contains(document.activeElement)) {
      renderPending = true;
      return;
    }
    renderPending = false;
    render(snapshot.data, snapshot.history);
  }

  function historyLine(item) {
    const time = new Date(item.created_at).toLocaleString(I18N.getLang());
    const percent = new Intl.NumberFormat(I18N.getLang(), {
      maximumFractionDigits: 2, signDisplay: "always",
    }).format(item.change_percent);
    return `${time} — ${item.name}: ${money(item.old_price_cents)} → ${money(item.new_price_cents)} (${percent}%)`;
  }

  function render(data, history) {
    const rows = document.getElementById("competitorRows");
    rows.replaceChildren();
    document.getElementById("competitorSource").textContent = data.source;
    for (const item of data.items) {
      const row = node("tr");
      row.append(node("td", item.name), node("td", money(item.last_price_cents)));
      const cell = node("td");
      const select = node("select");
      select.setAttribute("aria-label", `${t("competitors.mine")}: ${item.name}`);
      const placeholder = node("option", t("competitors.choose"));
      placeholder.value = "";
      placeholder.disabled = true;
      select.append(placeholder);
      for (const product of data.catalogue) {
        const option = node("option", product.name);
        option.value = String(product.id);
        select.append(option);
      }
      if (item.my_product_id != null && !data.catalogue.some((product) => product.id === item.my_product_id)) {
        const hidden = node("option", `${item.my_product_name} (${t("products.hidden")})`);
        hidden.value = String(item.my_product_id);
        hidden.disabled = true;
        select.append(hidden);
      }
      select.value = item.my_product_id == null ? "" : String(item.my_product_id);
      select.addEventListener("change", async () => {
        mappingWrites += 1;
        select.disabled = true;
        try {
          await OrdersApp.api(`/api/competitor/products/${item.id}/mapping`, {
            method: "POST", body: JSON.stringify({ my_product_id: Number(select.value) }),
          });
          await refresh();
        } catch {
          select.value = item.my_product_id == null ? "" : String(item.my_product_id);
          OrdersApp.toast(t("competitors.error"), "error");
        } finally {
          select.disabled = false;
          mappingWrites -= 1;
          if (renderPending) renderWhenIdle();
        }
      });
      cell.append(select);
      row.append(cell, node("td", item.my_price_cents === null ? "—" : money(item.my_price_cents)));
      const diff = item.difference_cents;
      const difference = node("td", diff === null ? "—" : diff === 0 ? money(0) :
        `${diff > 0 ? "+" : "−"}${money(Math.abs(diff))} · ${diff > 0 ? t("competitors.higher") : t("competitors.lower")}`);
      if (diff) difference.className = diff > 0 ? "competitor-higher" : "competitor-lower";
      const actions = node("td");
      const edit = node("button", t("products.change"));
      edit.type = "button";
      edit.className = "btn btn-ghost";
      edit.disabled = item.my_product_id == null || item.my_price_cents == null;
      edit.setAttribute("aria-label", `${t("products.change")}: ${item.my_product_name || t("competitors.mine")}`);
      edit.addEventListener("click", () => {
        document.dispatchEvent(new CustomEvent("products:edit", { detail: {
          id: item.my_product_id, name: item.my_product_name, price_cents: item.my_price_cents,
        } }));
      });
      actions.append(edit);
      row.append(difference, actions);
      rows.append(row);
    }
    if (!rows.children.length) {
      const cell = node("td", t("competitors.empty"));
      cell.colSpan = 6;
      const row = node("tr");
      row.append(cell);
      rows.append(row);
    }
    const list = document.getElementById("competitorHistory");
    list.replaceChildren(...history.items.map((item) => node("li", historyLine(item))));
    if (!history.items.length) list.append(node("li", t("competitors.no_history")));
  }

  async function refresh() {
    if (document.hidden) return;
    if (busy) { refreshAgain = true; return; }
    busy = true;
    try {
      const sync = await OrdersApp.api("/api/competitor/sync", { method: "POST" });
      document.getElementById("competitorNotice").textContent = sync.ok === false ? t("competitors.sync_error") : "";
      const significant = document.getElementById("competitorThreshold").checked ? "1" : "0";
      const data = await OrdersApp.api(`/api/competitor/comparison?significant=${significant}`);
      const history = await OrdersApp.api(`/api/competitor/history?significant=${significant}`);
      if (cursor !== null) {
        const events = await OrdersApp.api(`/api/competitor/history?significant=${significant}&after=${cursor}`);
        if (events.items.length) {
          const message = t("competitors.notification") + " " + events.items.map(historyLine).join("; ");
          document.getElementById("competitorNotice").textContent = message;
          OrdersApp.toast(message);
          cursor = Math.max(cursor, ...events.items.map((item) => item.id));
        }
      }
      // Advance past filtered-out events too; toggling the filter must not replay them.
      cursor = Math.max(cursor || 0, data.latest_event_id || 0);
      try { localStorage.setItem(cursorKey, String(cursor)); } catch { /* Storage is optional. */ }
      snapshot = { data, history };
      renderWhenIdle();
    } catch {
      document.getElementById("competitorNotice").textContent = t("competitors.error");
    } finally {
      busy = false;
      if (refreshAgain) { refreshAgain = false; void refresh(); }
    }
  }

  function schedule() {
    clearTimeout(timer);
    if (!document.hidden) timer = setTimeout(async () => {
      await refresh();
      schedule();
    }, 15000);
  }

  document.addEventListener("DOMContentLoaded", async () => {
    document.getElementById("competitorRows").addEventListener("focusout", () => {
      // Wait until focus has reached its next target before replacing controls.
      setTimeout(() => { if (renderPending) renderWhenIdle(); }, 0);
    });
    // Obtain the session's CSRF token before enabling mapping writes.
    document.getElementById("competitorRefresh").addEventListener("click", refresh);
    document.getElementById("competitorThreshold").addEventListener("change", refresh);
    try {
      await OrdersApp.api("/api/me");
      await refresh();
      schedule();
    } catch {
      document.getElementById("competitorNotice").textContent = t("competitors.error");
    }
  });
  document.addEventListener("products:changed", () => { void refresh(); });
  document.addEventListener("visibilitychange", () => { void refresh(); schedule(); });
  window.addEventListener("pagehide", () => clearTimeout(timer));
  window.addEventListener("pageshow", schedule);
  I18N.onChange(() => {
    renderWhenIdle();
  });
})();
