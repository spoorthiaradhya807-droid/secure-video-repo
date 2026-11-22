(() => {
  const csrf = document.querySelector('meta[name="csrf-token"]').content;
  const msg = document.getElementById("team-msg");
  document.addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-decide]");
    if (!btn) return;
    const approve = btn.dataset.verb === "approve";
    const ask = approve
      ? `Make "${btn.dataset.name}" an instructor? They will be able to upload and delete their own videos.`
      : `Reject the request from "${btn.dataset.name}"?`;
    if (!confirm(ask)) return;
    btn.disabled = true;
    try {
      const r = await fetch(btn.dataset.url, { method: "POST", credentials: "same-origin", headers: { "X-CSRFToken": csrf } });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(j.error || "Could not save the decision.");
      window.location.reload();
    } catch (err) {
      msg.textContent = err.message;
      msg.className = "hint err";
      btn.disabled = false;
    }
  });
})();
