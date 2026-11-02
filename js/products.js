/* The catalogue owns editable prices; competitor prices remain read-only. */
(() => {
  const t = (key) => I18N.t(key);
  const byId = (id) => document.getElementById(id);
  const node = (tag, text) => {
    const result = document.createElement(tag);
    if (text !== undefined) result.textContent = text;
    return result;
  };
  const money = (cents) => new Intl.NumberFormat(I18N.getLang(), {
    style: "currency", currency: "RUB", minimumFractionDigits: 2,
  }).format(cents / 100);
  let products = [];
  let history = [];
  let editing = null;
  let saving = false;
  let requestVersion = 0;
  const returnFocus = new WeakMap();

  function historyLine(item) {
    const time = new Date(item.created_at).toLocaleString(I18N.getLang());
    return `${time} — ${item.name}: ${money(item.old_price_cents)} → ${money(item.new_price_cents)}`;
  }

  function renderHistory() {
    const fill = (target, items) => {
      target.replaceChildren(...items.map((item) => node("li", historyLine(item))));
      if (!items.length) target.append(node("li", t("products.no_history")));
    };
    fill(byId("productHistory"), history);
    fill(byId("productModalHistory"), history.filter((item) => item.product_id === editing?.id));
  }

  function render() {
    const rows = byId("productRows");
    rows.replaceChildren();
    const showHidden = byId("productsShowHidden").checked;
    for (const product of products.filter((item) => item.is_active || showHidden)) {
      const row = node("tr");
      if (!product.is_active) row.className = "product-hidden";
      row.append(node("td", product.name), node("td", money(product.price_cents)),
        node("td", product.is_active ? t("products.active") : t("products.hidden")));
      const cell = node("td");
      const actions = node("div");
      actions.className = "product-actions";
      const edit = node("button", t("products.change"));
      edit.type = "button";
      edit.className = "btn btn-ghost";
      edit.addEventListener("click", () => openEditor(product));
      const toggle = node("button", product.is_active ? t("products.hide") : t("products.reactivate"));
      toggle.type = "button";
      toggle.className = "btn btn-ghost";
      toggle.addEventListener("click", () => toggleActive(product, toggle));
      actions.append(edit, toggle);
      cell.append(actions);
      row.append(cell);
      rows.append(row);
    }
    if (!rows.children.length) {
      const row = node("tr");
      const cell = node("td", t("products.empty"));
      cell.colSpan = 4;
      row.append(cell);
      rows.append(row);
    }
    const datalist = byId("productsDatalist");
    datalist.replaceChildren(...products.filter((item) => item.is_active).map((product) => {
      const option = node("option");
      option.value = product.name;
      return option;
    }));
    renderHistory();
  }

  async function refresh() {
    const version = ++requestVersion;
    try {
      const [data, changes] = await Promise.all([
        OrdersApp.api("/api/products"), OrdersApp.api("/api/products/history"),
      ]);
      if (version !== requestVersion) return;
      products = data.items;
      history = changes.items;
      byId("productsNotice").textContent = "";
      render();
    } catch {
      if (version === requestVersion) byId("productsNotice").textContent = t("products.error");
    }
  }

  function clearErrors() {
    OrdersApp.clearFieldErrors(byId("productForm"));
    byId("productName").removeAttribute("aria-invalid");
    byId("productPrice").removeAttribute("aria-invalid");
    byId("productFormNotice").textContent = "";
  }

  function openEditor(product) {
    if (saving) return;
    editing = product || null;
    returnFocus.set(byId("productModal"), document.activeElement);
    if (editing) byId("productModalTitle").dataset.i18n = "products.edit_title";
    else byId("productModalTitle").dataset.i18n = "products.new_title";
    I18N.apply(byId("productModalTitle"));
    byId("productName").value = editing?.name || "";
    byId("productPrice").value = editing ? (editing.price_cents / 100).toFixed(2) : "";
    byId("productModalHistoryWrap").hidden = !editing;
    clearErrors();
    renderHistory();
    byId("productModal").setAttribute("aria-hidden", "false");
    OrdersApp.openModal("productModal");
    byId(editing ? "productPrice" : "productName").focus();
  }

  function closeEditor() {
    OrdersApp.closeModal("productModal");
    byId("productModal").setAttribute("aria-hidden", "true");
    returnFocus.get(byId("productModal"))?.focus();
  }

  function parsePrice(raw) {
    const value = raw.trim().replace(",", ".");
    if (!/^\d+(?:\.\d{1,2})?$/.test(value)) return null;
    const [whole, fraction = ""] = value.split(".");
    const cents = Number(whole) * 100 + Number(fraction.padEnd(2, "0"));
    return Number.isSafeInteger(cents) && cents >= 1 && cents <= 1000000000 ? cents : null;
  }

  async function save(event) {
    event.preventDefault();
    if (saving) return;
    clearErrors();
    const name = byId("productName").value.trim();
    const price = parsePrice(byId("productPrice").value);
    const errors = {};
    if (name.length < 2 || name.length > 80) errors.name = t("products.name_invalid");
    if (price === null) errors.price_cents = t("products.price_invalid");
    if (Object.keys(errors).length) {
      OrdersApp.renderFieldErrors(byId("productForm"), errors);
      byId(errors.name ? "productName" : "productPrice").focus();
      return;
    }
    saving = true;
    byId("productForm").setAttribute("aria-busy", "true");
    byId("productSave").disabled = true;
    try {
      const product = await OrdersApp.api(editing ? `/api/products/${editing.id}` : "/api/products", {
        method: editing ? "PATCH" : "POST", body: JSON.stringify({ name, price_cents: price }),
      });
      editing = product;
      closeEditor();
      OrdersApp.toast(t("products.saved"), "success");
      await refresh();
      document.dispatchEvent(new CustomEvent("products:changed", { detail: product }));
    } catch (error) {
      OrdersApp.renderFieldErrors(byId("productForm"), error.fields);
      byId("productFormNotice").textContent = t("products.save_error");
    } finally {
      saving = false;
      byId("productForm").setAttribute("aria-busy", "false");
      byId("productSave").disabled = false;
    }
  }

  async function toggleActive(product, button) {
    button.disabled = true;
    try {
      const updated = await OrdersApp.api(`/api/products/${product.id}`, {
        method: "PATCH", body: JSON.stringify({ is_active: !product.is_active }),
      });
      OrdersApp.toast(updated.is_active ? t("products.active_done") : t("products.hidden_done"), "success");
      await refresh();
      document.dispatchEvent(new CustomEvent("products:changed", { detail: updated }));
    } catch {
      OrdersApp.toast(t("products.save_error"), "error");
    } finally { button.disabled = false; }
  }

  document.addEventListener("DOMContentLoaded", async () => {
    byId("productAdd").addEventListener("click", () => openEditor(null));
    byId("productForm").addEventListener("submit", save);
    byId("productsRefresh").addEventListener("click", refresh);
    byId("productsShowHidden").addEventListener("change", render);
    try {
      await OrdersApp.api("/api/me");
      await refresh();
    } catch { byId("productsNotice").textContent = t("products.error"); }
  });
  document.addEventListener("products:edit", (event) => openEditor(event.detail));
  document.addEventListener("click", (event) => {
    if (event.target.dataset?.close === "productModal" || event.target === byId("productModal")) closeEditor();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && byId("productModal").getAttribute("aria-hidden") === "false") closeEditor();
  });
  I18N.onChange(() => { render(); });
})();
