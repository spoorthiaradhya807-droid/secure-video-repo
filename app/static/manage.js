(() => {
  const csrf = document.querySelector('meta[name="csrf-token"]').content;
  const msg = document.getElementById("manage-msg");
  document.addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-delete]");
    if (!btn) return;
    if (!confirm(`Delete "${btn.dataset.title}" permanently? Students will no longer be able to watch it.`)) return;
    btn.disabled = true;
    try {
      const r = await fetch(btn.dataset.url, { method: "POST", credentials: "same-origin", headers: { "X-CSRFToken": csrf } });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(j.error || "Could not delete the video.");
      window.location.reload();
    } catch (err) {
      msg.textContent = err.message;
      msg.className = "hint err";
      btn.disabled = false;
    }
  });
})();
