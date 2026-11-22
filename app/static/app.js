(() => {
  // toasts
  document.addEventListener("click", (e) => {
    const d = e.target.closest("[data-dismiss]");
    if (d) d.closest(".toast").remove();
    const t = e.target.closest("[data-toggle-pw]");
    if (t) {
      const input = t.closest(".input").querySelector("input");
      const show = input.type === "password";
      input.type = show ? "text" : "password";
      t.setAttribute("aria-label", show ? "Hide password" : "Show password");
    }
  });
  document.addEventListener("click", async (e) => {
    const c = e.target.closest("[data-copy]");
    if (!c) return;
    const label = c.querySelector("span");
    try {
      await navigator.clipboard.writeText(document.getElementById(c.dataset.copy).textContent.trim());
      label.textContent = "Copied";
    } catch (err) { label.textContent = "Copy failed"; }
    setTimeout(() => (label.textContent = "Copy link"), 2000);
  });
  setTimeout(() => document.querySelectorAll(".toast.success,.toast.info").forEach((t) => t.remove()), 8000);

  // password strength meter
  const pw = document.querySelector("[data-strength]");
  const meter = document.getElementById("meter");
  const mtext = document.getElementById("meter-text");
  if (pw && meter) {
    const levels = [["Too short", "#f87171"], ["Weak", "#f87171"], ["Fair", "#fbbf24"], ["Good", "#34d399"], ["Strong", "#22d3ee"]];
    pw.addEventListener("input", () => {
      const v = pw.value;
      let s = 0;
      if (v.length >= 10) s++;
      if (v.length >= 14) s++;
      if (/[a-z]/.test(v) && /[A-Z]/.test(v)) s++;
      if (/\d/.test(v) && /[A-Za-z]/.test(v)) s++;
      if (/[^A-Za-z0-9]/.test(v)) s++;
      s = v.length < 10 ? Math.min(s, 1) - (v.length ? 0 : 1) : Math.min(s, 4);
      s = Math.max(s, 0);
      meter.style.width = (v ? (s + 1) * 20 : 0) + "%";
      meter.style.background = levels[s][1];
      if (mtext) mtext.textContent = v ? levels[s][0] : "At least 10 characters, mixing letters and numbers.";
    });
  }

  // client-side filtering (library search, audit log)
  const box = document.querySelector("[data-filterable]");
  if (box) {
    const items = [...box.querySelectorAll("[data-search]")];
    const q = document.querySelector("[data-filter-q]");
    const t = document.querySelector("[data-filter-type]");
    const none = document.getElementById("noresults");
    const run = () => {
      const s = (q ? q.value : "").trim().toLowerCase();
      const ty = t ? t.value : "";
      let n = 0;
      items.forEach((el) => {
        const ok = el.dataset.search.includes(s) && (!ty || el.dataset.type === ty);
        el.hidden = !ok;
        if (ok) n++;
      });
      if (none) none.hidden = n > 0;
    };
    if (q) q.addEventListener("input", run);
    if (t) t.addEventListener("change", run);
  }
})();
