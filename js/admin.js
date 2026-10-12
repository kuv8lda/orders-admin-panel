document.addEventListener("DOMContentLoaded", () => {
  const form = document.getElementById("adminForm");
  const errBox = document.getElementById("loginError");
  const rateNote = document.getElementById("rateNote");
  const next = OrdersApp.safeNextPath(new URLSearchParams(location.search).get("next"));
  let rateTimer = null;

  function setRatelimitWait(seconds) {
    if (!rateNote) return;
    rateNote.classList.remove("hidden");
    let remaining = Math.min(seconds, 120);
    const upd = () => {
      rateNote.textContent = I18N.t("login.too_many", { s: Math.max(remaining, 0) });
      if (remaining <= 0) {
        rateNote.classList.add("hidden");
        if (rateTimer) clearInterval(rateTimer);
        form.querySelectorAll("input,button").forEach((el) => (el.disabled = false));
        return;
      }
      remaining -= 1;
    };
    upd();
    form.querySelectorAll("input,button").forEach((el) => (el.disabled = true));
    if (rateTimer) clearInterval(rateTimer);
    rateTimer = setInterval(upd, 1000);
  }

  document.getElementById("fillDemo")?.addEventListener("click", () => {
    if (form.username) form.username.value = "admin";
    if (form.password) form.password.value = "password123";
  });

  form?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const data = Object.fromEntries(new FormData(form));
    if (errBox) {
      errBox.textContent = "";
      errBox.classList.add("hidden");
    }
    try {
      await OrdersApp.api("/api/login", {
        method: "POST",
        body: JSON.stringify(data),
      });
      OrdersApp.toast(I18N.t("login.welcome"), "success");
      setTimeout(() => { window.location.href = next; }, 500);
    } catch (err) {
      if (err instanceof OrdersApp.ApiError) {
        if (err.status === 429) {
          const secs = (err.fields && err.fields.retry_after_seconds) || 60;
          setRatelimitWait(secs);
        } else {
          if (errBox) {
            errBox.textContent = I18N.apiMessage(err.message) || I18N.t("login.failed");
            errBox.classList.remove("hidden");
          }
          OrdersApp.toast(I18N.apiMessage(err.message) || I18N.t("login.failed"), "error");
        }
      } else {
        OrdersApp.toast(I18N.t("login.network"), "error");
      }
    }
  });
});
