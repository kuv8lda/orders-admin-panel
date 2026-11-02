const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

let _csrfToken = null;

function setCsrfToken(tok) {
  _csrfToken = tok;
}

function getCsrfToken() {
  return _csrfToken;
}

class ApiError extends Error {
  constructor(status, message, fields) {
    super(message || `API error ${status}`);
    this.name = "ApiError";
    this.status = status;
    this.message = message || `API error ${status}`;
    this.fields = fields || {};
  }
}

// Путь для перехода после входа: только внутри сайта ("//host" и "/\\host" не пропускаем).
function safeNextPath(value) {
  return /^\/(?![/\\])/.test(value || "") ? value : "/dashboard";
}

function ensureToastContainer() {
  if ($("#toastContainer")) return;
  const wrap = document.createElement("div");
  wrap.id = "toastContainer";
  wrap.className = "toast-container";
  wrap.setAttribute("aria-live", "polite");
  wrap.setAttribute("aria-atomic", "true");
  document.body.append(wrap);
}

function toast(message, variant) {
  ensureToastContainer();
  const el = document.createElement("div");
  el.className = "toast show";
  el.textContent = message;
  if (variant === "error" || variant === "success") el.classList.add(`toast--${variant}`);
  $("#toastContainer").appendChild(el);
  setTimeout(() => {
    el.classList.remove("show");
    setTimeout(() => el.remove(), 400);
  }, 3400);
}

let manualTheme = false;
function setTheme(theme, persist = true) {
  document.documentElement.dataset.theme = theme;
  if (persist) {
    manualTheme = true;
    try { localStorage.setItem("theme", theme); } catch { /* Theme still works without storage. */ }
  }
  const btn = $("#themeToggle");
  if (btn) btn.innerHTML = theme === "dark" ? '<i class="fa-solid fa-sun"></i>' : '<i class="fa-solid fa-moon"></i>';
}

function initTheme() {
  const preference = window.matchMedia("(prefers-color-scheme: dark)");
  const savedTheme = () => {
    try { return localStorage.getItem("theme"); } catch { return null; }
  };
  setTheme(savedTheme() || (preference.matches ? "dark" : "light"), false);
  preference.addEventListener("change", () => {
    if (!manualTheme && !savedTheme()) setTheme(preference.matches ? "dark" : "light", false);
  });
}

const modalFocus = new WeakMap();
const overlayTimers = new WeakMap();
const reducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

function syncOverlayLock() {
  document.body.classList.toggle("overlay-open", !!document.querySelector(".modal-backdrop.open, .modal-backdrop.closing, .drawer.open, .drawer.closing"));
}

function animateClose(el, duration) {
  clearTimeout(overlayTimers.get(el));
  el.classList.remove("open");
  el.setAttribute("aria-hidden", "true");
  el.inert = true;
  if (!reducedMotion()) el.classList.add("closing");
  const finish = () => { el.classList.remove("closing"); syncOverlayLock(); };
  if (reducedMotion()) finish();
  else overlayTimers.set(el, setTimeout(finish, duration));
}

function openModal(id) {
  const el = document.getElementById(id);
  if (!el || el.classList.contains("open")) return;
  clearTimeout(overlayTimers.get(el));
  el.classList.remove("closing");
  el.inert = false;
  modalFocus.set(el, document.activeElement);
  el.classList.add("open");
  syncOverlayLock();
  el.setAttribute("aria-hidden", "false");
  const dialog = el.querySelector(".modal");
  if (dialog) {
    dialog.setAttribute("role", "dialog");
    dialog.setAttribute("aria-modal", "true");
    const heading = dialog.querySelector("h3");
    if (heading) {
      heading.id ||= `${id}Title`;
      dialog.setAttribute("aria-labelledby", heading.id);
    }
    dialog.querySelector("input, button, textarea, select")?.focus();
  }
}
function closeModal(id) {
  const el = document.getElementById(id);
  if (!el || !el.classList.contains("open")) return;
  animateClose(el, 160);
  const previous = modalFocus.get(el);
  if (previous?.isConnected) previous.focus();
}

function closeDrawer(id) {
  const el = document.getElementById(id);
  if (el) animateClose(el, 200);
}

function pageTransition(href) {
  if (reducedMotion()) { window.location.href = href; return; }
  document.body.classList.add("page-leaving");
  setTimeout(() => { window.location.href = href; }, 160);
}
window.addEventListener?.("pageshow", () => document.body.classList.remove("page-leaving"));

function initNav() {
  const page = document.body.dataset.page;
  $$(`.nav-links a[data-page="${page}"]`).forEach((a) => a.classList.add("active"));
  $("#hamburger")?.addEventListener("click", () => $("#navLinks")?.classList.toggle("open"));
  $$(".nav-links a, .page-link").forEach((a) => {
    a.addEventListener("click", (e) => {
      const href = a.getAttribute("href");
      if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || a.target === "_blank") return;
      if (!href || href.startsWith("#") || href.startsWith("http") || href.startsWith("mailto:")) return;
      e.preventDefault();
      pageTransition(href);
    });
  });
}

function initBackToTop() {
  const btn = $("#toTop");
  if (!btn) return;
  window.addEventListener("scroll", () => {
    btn.classList.toggle("show", window.scrollY > 300);
  });
  btn.addEventListener("click", () => window.scrollTo({ top: 0, behavior: "smooth" }));
}

async function api(path, options = {}) {
  const method = (options.method || "GET").toUpperCase();
  const headers = {
    "Content-Type": "application/json",
    ...(options.headers || {}),
  };
  if (_csrfToken && ["POST", "PATCH", "DELETE", "PUT"].includes(method)) {
    headers["X-CSRF-Token"] = _csrfToken;
  }
  const res = await fetch(path, {
    headers,
    credentials: "same-origin",
    ...options,
    method,
  });
  let body = null;
  const text = await res.text();
  if (text) {
    try { body = JSON.parse(text); } catch { body = text; }
  }
  if (res.status === 401 && location.pathname.startsWith("/dashboard")) {
    window.location.href = "/admin?next=" + encodeURIComponent(location.pathname + location.search);
    throw new ApiError(401, "unauthorized");
  }
  if (res.status === 422 && body && typeof body === "object" && body.errors) {
    throw new ApiError(422, body.error || "validation_failed", body.errors);
  }
  if (res.status === 429) {
    const msg = (body && body.error) ? body.error : "too_many_attempts";
    const secs = (body && body.retry_after_seconds) || 60;
    throw new ApiError(429, msg, { retry_after_seconds: secs });
  }
  if (res.status === 403) {
    throw new ApiError(403, (body && body.error) || "forbidden");
  }
  if (res.status === 409) {
    throw new ApiError(409, (body && body.error) || "conflict", body);
  }
  if (!res.ok) {
    const msg = (body && body.error) ? body.error : `HTTP ${res.status}`;
    throw new ApiError(res.status, msg);
  }
  if (body && typeof body === "object" && body.csrf_token) {
    setCsrfToken(body.csrf_token);
  }
  return body;
}

function injectChrome() {
  const page = document.body.dataset.page;
  const header = document.createElement("header");
  header.className = "site-nav";
  header.innerHTML = `
    <div class="nav-inner">
      <button class="hamburger" id="hamburger" aria-label="Меню" data-i18n-attr="aria-label:nav.menu"><i class="fa-solid fa-bars"></i></button>
      <a class="logo page-link" href="/dashboard">Хлебный день</a>
      <div class="nav-actions">
        <button class="btn btn-ghost" id="langToggle" type="button" aria-label="Сменить язык" title="Сменить язык" data-i18n-attr="aria-label:lang.switch;title:lang.switch">EN</button>
        <button class="icon-btn" id="themeToggle" aria-label="Сменить тему" data-i18n-attr="aria-label:nav.theme"></button>
        <button class="btn btn-ghost" id="loginBtn" type="button" data-i18n="auth.login">Войти</button>
      </div>
    </div>`;
  if (page === "dashboard") {
    document.getElementById("workspaceControls")?.append(header.querySelector(".nav-actions"));
  } else {
    document.body.prepend(header);
  }

  const extras = document.createElement("div");
  extras.innerHTML = `
    <button class="to-top" id="toTop" aria-label="Наверх" data-i18n-attr="aria-label:nav.top"><i class="fa-solid fa-arrow-up"></i></button>
`;
  document.body.append(extras);
}

async function bootstrapAuthUI() {
  const btn = $("#loginBtn");
  if (!btn) return;
  try {
    const me = await api("/api/me");
    if (me && me.username) {
      btn.dataset.i18n = "auth.logout";
      btn.addEventListener("click", async () => {
        try {
          await api("/api/logout", { method: "POST" });
        } catch {
        }
        window.location.href = "/";
      });
    } else {
      btn.dataset.i18n = "auth.login";
      btn.addEventListener("click", () => { window.location.href = "/admin"; });
    }
  } catch {
    btn.dataset.i18n = "auth.login";
    btn.addEventListener("click", () => { window.location.href = "/admin"; });
  }
  I18N.apply(btn.parentNode);
}

const fieldDescriptions = new WeakMap();
function clearFieldErrors(form) {
  $$('[name]', form).forEach((input) => {
    if (!fieldDescriptions.has(input)) return;
    const previous = fieldDescriptions.get(input);
    if (previous === null) input.removeAttribute("aria-describedby");
    else input.setAttribute("aria-describedby", previous);
    fieldDescriptions.delete(input);
  });
  $$("[data-error-for]", form).forEach((el) => { el.textContent = ""; });
  $$("[aria-invalid]", form).forEach((el) => el.removeAttribute("aria-invalid"));
}

function renderFieldErrors(form, fields) {
  Object.entries(fields || {}).forEach(([k, v]) => {
    const el = form.querySelector(`[data-error-for="${k}"]`);
    if (el) {
      const raw = typeof v === "string" ? v : (Array.isArray(v) ? v[0] : "");
      el.textContent = raw ? I18N.apiMessage(raw) : I18N.t("api.field.invalid");
    }
    const input = form.querySelector(`[name="${k}"]`);
    if (input) {
      input.setAttribute("aria-invalid", "true");
      if (el) {
        el.id ||= `${form.id}-${k}-error`;
        el.setAttribute("aria-live", "polite");
        if (!fieldDescriptions.has(input)) fieldDescriptions.set(input, input.getAttribute("aria-describedby"));
        const descriptions = new Set((input.getAttribute("aria-describedby") || "").split(" ").filter(Boolean));
        descriptions.add(el.id);
        input.setAttribute("aria-describedby", [...descriptions].join(" "));
      }
    }
  });
}

document.addEventListener("DOMContentLoaded", () => {
  injectChrome();
  I18N.apply();
  initTheme();
  initNav();
  initBackToTop();
  document.body.classList.add("page-fade");
  document.body.style.opacity = "1";

  $("#langToggle")?.addEventListener("click", () => {
    I18N.setLang(I18N.getLang() === "ru" ? "en" : "ru");
  });

  $("#themeToggle")?.addEventListener("click", () => {
    setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
  });

  document.addEventListener("click", (e) => {
    const closeId = e.target.dataset?.close;
    if (closeId) closeModal(closeId);
    if (e.target.classList.contains("modal-backdrop") && e.target.id !== "drawerBackdrop") closeModal(e.target.id);
  });

  document.addEventListener("keydown", (e) => {
    const overlay = $(".modal-backdrop.open .modal") || $(".drawer.open");
    if (e.key === "Tab" && overlay) {
      const controls = [...overlay.querySelectorAll('button:not(:disabled), input:not(:disabled), textarea:not(:disabled), select:not(:disabled), a[href], [tabindex="0"]')].filter((el) => el.getClientRects().length);
      const first = controls[0];
      const last = controls[controls.length - 1];
      if (e.shiftKey && (document.activeElement === first || !overlay.contains(document.activeElement))) { e.preventDefault(); last?.focus(); }
      else if (!e.shiftKey && (document.activeElement === last || !overlay.contains(document.activeElement))) { e.preventDefault(); first?.focus(); }
    }
    if (e.key !== "Escape") return;
    $$(".modal-backdrop.open").forEach((el) => closeModal(el.id));
    if ($(".drawer.open")) $("#drawerClose")?.click();
  });

  bootstrapAuthUI();
});

window.OrdersApp = {
  toast,
  openModal,
  closeModal,
  closeDrawer,
  api,
  setCsrfToken,
  getCsrfToken,
  ApiError,
  clearFieldErrors,
  renderFieldErrors,
  safeNextPath,
  pageTransition,
};
window.$ = $;
window.$$ = $$;
