document.addEventListener("DOMContentLoaded", () => {
  const form = document.getElementById("adminForm");
  const errBox = document.getElementById("loginError");
  const rateNote = document.getElementById("rateNote");
  const next = new URLSearchParams(location.search).get("next") || "/dashboard";
  let rateTimer = null;

  function setRatelimitWait(seconds) {
    if (!rateNote) return;
    rateNote.classList.remove("hidden");
    let remaining = Math.min(seconds, 120);
    const upd = () => {
      rateNote.textContent = `Too many attempts. Try again in ${remaining}s.`;
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
      OrdersApp.toast("Welcome", "success");
      setTimeout(() => { window.location.href = next; }, 500);
    } catch (err) {
      if (err instanceof OrdersApp.ApiError) {
        if (err.status === 429) {
          const secs = (err.fields && err.fields.retry_after_seconds) || 60;
          setRatelimitWait(secs);
        } else {
          if (errBox) {
            errBox.textContent = err.message || "Invalid credentials";
            errBox.classList.remove("hidden");
          }
          OrdersApp.toast(err.message || "Login failed", "error");
        }
      } else {
        OrdersApp.toast("Network error. Try again.", "error");
      }
    }
  });
});
