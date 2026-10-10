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
  if (variant === "error") {
    el.style.borderLeft = "4px solid var(--danger)";
  } else if (variant === "success") {
    el.style.borderLeft = "4px solid var(--ok)";
  }
  $("#toastContainer").appendChild(el);
  setTimeout(() => {
    el.classList.remove("show");
    setTimeout(() => el.remove(), 400);
  }, 3400);
}

function setTheme(theme) {
  document.documentElement.dataset.theme = theme;
  localStorage.setItem("theme", theme);
  const btn = $("#themeToggle");
  if (btn) btn.innerHTML = theme === "dark" ? '<i class="fa-solid fa-sun"></i>' : '<i class="fa-solid fa-moon"></i>';
}

function initTheme() {
  setTheme(localStorage.getItem("theme") || "light");
}

function openModal(id) {
  const el = document.getElementById(id);
  if (el) el.classList.add("open");
}
function closeModal(id) {
  const el = document.getElementById(id);
  if (el) el.classList.remove("open");
}

function closeDrawer(id) {
  const el = document.getElementById(id);
  if (el) el.classList.remove("open");
}

function pageTransition(href) {
  document.body.style.opacity = "0";
  setTimeout(() => { window.location.href = href; }, 180);
}

function initNav() {
  const page = document.body.dataset.page;
  $$(`.nav-links a[data-page="${page}"]`).forEach((a) => a.classList.add("active"));
  $("#hamburger")?.addEventListener("click", () => $("#navLinks")?.classList.toggle("open"));
  $$(".nav-links a, .page-link").forEach((a) => {
    a.addEventListener("click", (e) => {
      const href = a.getAttribute("href");
      if (!href || href.startsWith("#") || href.startsWith("http") || href.startsWith("mailto:")) return;
      e.preventDefault();
      pageTransition(href);
    });
  });
}

function initReveal() {
  const io = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (entry.isIntersecting) entry.target.classList.add("in");
    });
  }, { threshold: 0.12 });
  $$(".reveal").forEach((el) => io.observe(el));
}

function initTilt() {
  $$(".tilt").forEach((card) => {
    card.addEventListener("mousemove", (e) => {
      const r = card.getBoundingClientRect();
      const x = (e.clientX - r.left) / r.width - 0.5;
      const y = (e.clientY - r.top) / r.height - 0.5;
      card.style.transform = `rotateY(${x * 12}deg) rotateX(${-y * 12}deg) translateY(-4px)`;
    });
    card.addEventListener("mouseleave", () => { card.style.transform = ""; });
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
    const msg = (body && body.error) ? body.error : "Too many requests";
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
  const isLoginPage = page === "admin";
  const header = document.createElement("header");
  header.className = "site-nav";
  header.innerHTML = `
    <div class="nav-inner">
      <button class="hamburger" id="hamburger" aria-label="Menu"><i class="fa-solid fa-bars"></i></button>
      <a class="logo page-link" href="/dashboard">Orders<span>.</span></a>
      <div class="nav-actions">
        <button class="icon-btn" id="themeToggle" aria-label="Toggle theme"></button>
        <button class="btn btn-ghost" id="loginBtn" type="button">Log in</button>
      </div>
    </div>`;
  document.body.prepend(header);

  const extras = document.createElement("div");
  extras.innerHTML = `
    <button class="to-top" id="toTop" aria-label="Back to top"><i class="fa-solid fa-arrow-up"></i></button>
`;
  document.body.append(extras);
}

async function bootstrapAuthUI() {
  const btn = $("#loginBtn");
  if (!btn) return;
  try {
    const me = await api("/api/me");
    if (me && me.username) {
      btn.textContent = "Log out";
      btn.addEventListener("click", async () => {
        try {
          await api("/api/logout", { method: "POST" });
        } catch {
        }
        window.location.href = "/";
      });
    } else {
      btn.textContent = "Log in";
      btn.addEventListener("click", () => { window.location.href = "/admin"; });
    }
  } catch {
    btn.textContent = "Log in";
    btn.addEventListener("click", () => { window.location.href = "/admin"; });
  }
}

function clearFieldErrors(form) {
  $$("[data-error-for]", form).forEach((el) => { el.textContent = ""; });
}

function renderFieldErrors(form, fields) {
  Object.entries(fields || {}).forEach(([k, v]) => {
    const el = form.querySelector(`[data-error-for="${k}"]`);
    if (el) el.textContent = typeof v === "string" ? v : (Array.isArray(v) ? v[0] : "Invalid");
    const input = form.querySelector(`[name="${k}"]`);
    if (input) input.setAttribute("aria-invalid", "true");
  });
}

document.addEventListener("DOMContentLoaded", () => {
  injectChrome();
  initTheme();
  initNav();
  initReveal();
  initTilt();
  initBackToTop();
  document.body.classList.add("page-fade");
  document.body.style.opacity = "1";

  $("#themeToggle")?.addEventListener("click", () => {
    setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
  });

  document.addEventListener("click", (e) => {
    const closeId = e.target.dataset?.close;
    if (closeId) closeModal(closeId);
    if (e.target.classList.contains("modal-backdrop")) e.target.classList.remove("open");
  });

  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    $$(".modal-backdrop.open").forEach((el) => el.classList.remove("open"));
    $$(".drawer.open").forEach((el) => el.classList.remove("open"));
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
};
window.$ = $;
window.$$ = $$;
