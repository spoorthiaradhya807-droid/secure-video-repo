(() => {
  const $ = (id) => document.getElementById(id);
  const v = $("player");
  const ttl = parseInt(v.dataset.ttl, 10);
  let remaining = ttl;
  const badge = $("countdown"), bar = $("ttlbar"), status = $("status");
  const codeEl = $("signed-url"), showBtn = $("show-url"), copyBtn = $("copy-url");
  let currentUrl = v.getAttribute("src");

  const paint = () => {
    badge.textContent = remaining > 0 ? `Signed link expires in ${remaining}s` : "Signed link expired";
    bar.style.width = Math.max(0, (remaining / ttl) * 100) + "%";
  };
  paint();
  setInterval(() => { remaining = Math.max(0, remaining - 1); paint(); }, 1000);

  // If the link expires mid-playback S3 answers 403 -> ask the app for a fresh link
  // (only works while the user is still logged in and authorised) and resume.
  let renewing = false;
  v.addEventListener("error", async () => {
    if (renewing) return;
    renewing = true;
    status.textContent = "Renewing link…";
    try {
      const r = await fetch(`/api/stream/${v.dataset.videoId}`, { credentials: "same-origin" });
      if (!r.ok) throw new Error(r.status);
      const j = await r.json();
      const t = v.currentTime, wasPlaying = !v.paused;
      currentUrl = j.url;
      v.src = j.url;
      remaining = j.expires_in;
      codeEl.textContent = currentUrl;
      v.addEventListener("loadedmetadata", () => {
        v.currentTime = t;
        if (wasPlaying) v.play();
        status.textContent = "";
      }, { once: true });
    } catch (e) {
      status.textContent = "Access denied or session ended.";
    } finally {
      renewing = false;
    }
  });

  showBtn.addEventListener("click", () => {
    const show = codeEl.hidden;
    codeEl.textContent = currentUrl;
    codeEl.hidden = !show;
    copyBtn.hidden = !show;
    showBtn.lastChild.textContent = show ? " Hide signed URL" : " Show signed URL";
  });
  copyBtn.addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(currentUrl); status.textContent = "Copied"; }
    catch (e) { status.textContent = "Copy failed"; }
    setTimeout(() => (status.textContent = ""), 2000);
  });

  const del = $("delete-video");
  if (del) {
    del.addEventListener("click", async () => {
      if (!confirm(`Delete "${del.dataset.title}" permanently? Students will no longer be able to watch it.`)) return;
      del.disabled = true;
      try {
        const csrf = document.querySelector('meta[name="csrf-token"]').content;
        const r = await fetch(del.dataset.url, { method: "POST", credentials: "same-origin", headers: { "X-CSRFToken": csrf } });
        const j = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(j.error || "Could not delete the video.");
        window.location.href = "/";
      } catch (e) {
        status.textContent = e.message;
        del.disabled = false;
      }
    });
  }
})();
