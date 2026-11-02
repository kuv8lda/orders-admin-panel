/* Same-document sections: native links and history, no client-side framework. */
(() => {
  const views = {
    overview: "dash.title",
    analytics: "nav.analytics",
    orders: "nav.orders",
    products: "products.title",
    competitors: "competitors.title",
  };
  let current = null;

  function showView(requested, moveFocus = false) {
    const name = Object.hasOwn(views, requested) ? requested : "overview";
    const changed = current !== name;
    current = name;
    document.querySelectorAll("[data-views]").forEach((section) => {
      const visible = section.dataset.views.split(" ").includes(name);
      section.hidden = !visible;
      if (visible && changed && moveFocus && !window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
        section.getAnimations().forEach((animation) => animation.cancel());
        section.animate([{ opacity: .6, transform: "translateY(4px)" }, { opacity: 1, transform: "none" }], {
          duration: 200, easing: "cubic-bezier(.2,.8,.2,1)",
        });
      }
    });
    document.querySelectorAll(".sidebar a[data-nav]").forEach((link) => {
      const active = link.dataset.nav === name;
      link.classList.toggle("active", active);
      if (active) link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
    });
    const title = document.getElementById("workspaceTitle");
    title.dataset.i18n = views[name];
    I18N.apply(title);
    if (moveFocus && changed) {
      window.scrollTo({ top: 0, behavior: "instant" });
      title.focus({ preventScroll: true });
    }
    if (changed) document.dispatchEvent(new CustomEvent("workspace:change", { detail: name }));
    requestAnimationFrame(() => window.dispatchEvent(new Event("resize")));
  }

  document.addEventListener("DOMContentLoaded", () => {
    document.querySelector(".skip-link").addEventListener("click", (event) => {
      event.preventDefault();
      document.getElementById("workspaceTitle").focus();
    });
    document.querySelectorAll(".sidebar a[data-nav]").forEach((link) => {
      link.addEventListener("click", (event) => {
        if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        const name = link.dataset.nav;
        if (current !== name) history.pushState({}, "", `${location.pathname}${location.search}#${name}`);
        showView(name, true);
      });
    });
    const search = document.getElementById("searchInput");
    const clear = document.getElementById("searchClear");
    const syncClear = () => { clear.hidden = !search.value; };
    search.addEventListener("input", syncClear);
    clear.addEventListener("click", () => {
      search.value = "";
      search.dispatchEvent(new Event("input", { bubbles: true }));
      search.focus();
    });
    document.getElementById("resetFiltersBtn").addEventListener("click", () => queueMicrotask(syncClear));
    I18N.onChange(() => showView(current));
    window.addEventListener("popstate", () => showView(location.hash.slice(1)));
    window.addEventListener("hashchange", () => showView(location.hash.slice(1)));
    const shell = document.getElementById("dashShell");
    let saved = null;
    try { saved = localStorage.getItem("sidebarCollapsed"); } catch { /* Storage optional. */ }
    const narrowSidebar = matchMedia("(min-width: 641px) and (max-width: 980px)");
    shell.classList.toggle("collapsed", saved === null ? narrowSidebar.matches : saved === "true");
    const toggle = document.getElementById("collapseBtn");
    const syncToggle = () => toggle.setAttribute("aria-expanded", String(!shell.classList.contains("collapsed")));
    syncToggle();
    toggle.addEventListener("click", () => {
      shell.classList.toggle("collapsed");
      syncToggle();
      saved = String(shell.classList.contains("collapsed"));
      try { localStorage.setItem("sidebarCollapsed", saved); } catch { /* Storage optional. */ }
    });
    narrowSidebar.addEventListener("change", () => {
      if (saved !== null) return;
      shell.classList.toggle("collapsed", narrowSidebar.matches);
      syncToggle();
    });
    showView(location.hash.slice(1));
    syncClear();
  });
})();
