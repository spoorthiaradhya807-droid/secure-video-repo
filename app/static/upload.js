(() => {
  const $ = (id) => document.getElementById(id);
  const csrf = document.querySelector('meta[name="csrf-token"]').content;
  const form = $("upload-form"), drop = $("drop"), fileInput = $("file"), card = $("filecard");
  const title = $("title"), desc = $("description"), go = $("go"), cancelBtn = $("cancel");
  const prog = $("prog"), fill = $("bar-fill"), pct = $("pct"), speedEl = $("speed"), etaEl = $("eta");
  const partsEl = $("partsinfo"), msg = $("msg");
  const MAX = parseInt(form.dataset.maxBytes, 10);
  const CONCURRENCY = 3, RETRIES = 4;

  let file = null, duration = null, uploading = false, cancelled = false;
  let videoId = null, partSize = 0, totalParts = 0, failure = null;
  const xhrs = new Set(), loaded = {};

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const fmtSize = (n) => {
    const u = ["B", "KB", "MB", "GB", "TB"]; let i = 0;
    while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
    return (i ? n.toFixed(1) : n) + " " + u[i];
  };
  const fmtTime = (s) => {
    if (!isFinite(s) || s < 0) return "";
    s = Math.round(s);
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60;
    return (h ? h + "h " : "") + (h || m ? m + "m " : "") + x + "s";
  };
  const fmtDur = (s) => {
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60;
    return (h ? h + ":" + String(m).padStart(2, "0") : m) + ":" + String(x).padStart(2, "0");
  };
  const say = (text, cls) => { msg.textContent = text; msg.className = "hint " + (cls || ""); };

  async function api(url, body) {
    const r = await fetch(url, {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf },
      body: JSON.stringify(body || {}),
    });
    let j = {};
    try { j = await r.json(); } catch (e) { /* HTML error page */ }
    if (!r.ok) throw new Error(j.error || `Server error (${r.status})`);
    return j;
  }

  // ---------- file selection ----------
  function pick(f) {
    if (!f || uploading) return;
    const ext = f.name.split(".").pop().toLowerCase();
    if (!["mp4", "webm"].includes(ext)) return say("Only .mp4 and .webm files are allowed.", "err");
    if (f.size === 0) return say("That file is empty.", "err");
    if (f.size > MAX) return say(`File is too large. The limit is ${fmtSize(MAX)}.`, "err");
    file = f; duration = null;
    say("");
    $("fname").textContent = f.name;
    $("fmeta").textContent = fmtSize(f.size) + " · reading length…";
    card.hidden = false; drop.hidden = true; go.disabled = false;
    if (!title.value) title.value = f.name.replace(/\.[^.]+$/, "").replace(/[_-]+/g, " ");
    const probe = document.createElement("video");
    probe.preload = "metadata";
    probe.onloadedmetadata = () => {
      duration = isFinite(probe.duration) ? Math.round(probe.duration) : null;
      $("fmeta").textContent = fmtSize(f.size) + (duration ? " · " + fmtDur(duration) : "");
      URL.revokeObjectURL(probe.src);
    };
    probe.onerror = () => { $("fmeta").textContent = fmtSize(f.size); URL.revokeObjectURL(probe.src); };
    probe.src = URL.createObjectURL(f);
  }
  function clearFile() {
    file = null; fileInput.value = "";
    card.hidden = true; drop.hidden = false; go.disabled = true;
  }
  fileInput.addEventListener("change", () => pick(fileInput.files[0]));
  $("clear").addEventListener("click", () => { if (!uploading) clearFile(); });
  ["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", (e) => pick(e.dataTransfer.files[0]));

  // ---------- progress ----------
  let t0 = 0;
  function render() {
    const done = Object.values(loaded).reduce((a, b) => a + b, 0);
    const p = file ? Math.min(100, (done / file.size) * 100) : 0;
    fill.style.width = p + "%";
    pct.textContent = p.toFixed(p < 10 ? 1 : 0) + "%";
    const el = (Date.now() - t0) / 1000;
    const speed = el > 0.5 ? done / el : 0;
    speedEl.textContent = speed ? fmtSize(speed) + "/s" : "";
    etaEl.textContent = speed ? "~" + fmtTime((file.size - done) / speed) + " left" : "";
    const finished = Object.entries(loaded).filter(([n, b]) => b >= Math.min(partSize, file.size - (n - 1) * partSize)).length;
    partsEl.textContent = `${finished}/${totalParts} chunks`;
  }

  // ---------- chunk upload ----------
  function putPart(url, blob, onProgress) {
    return new Promise((resolve, reject) => {
      const x = new XMLHttpRequest();
      xhrs.add(x);
      x.open("PUT", url);
      x.upload.onprogress = (e) => { if (e.lengthComputable) onProgress(e.loaded); };
      x.onload = () => {
        xhrs.delete(x);
        if (x.status >= 200 && x.status < 300) {
          const etag = x.getResponseHeader("ETag");
          etag ? resolve(etag) : reject(new Error("Storage did not return an ETag (bucket CORS must expose ETag)."));
        } else reject(new Error(`Storage answered ${x.status}`));
      };
      x.onerror = () => { xhrs.delete(x); reject(new Error("Network error while sending a chunk")); };
      x.onabort = () => { xhrs.delete(x); reject(new DOMException("aborted", "AbortError")); };
      x.send(blob);
    });
  }

  async function uploadPart(n) {
    const start = (n - 1) * partSize, end = Math.min(start + partSize, file.size);
    const blob = file.slice(start, end);
    let last;
    for (let attempt = 0; attempt < RETRIES; attempt++) {
      if (cancelled || failure) throw new DOMException("stopped", "AbortError");
      try {
        const { url } = await api(`/api/upload/${videoId}/part/${n}`);   // fresh signed URL every attempt
        const etag = await putPart(url, blob, (b) => { loaded[n] = b; render(); });
        loaded[n] = blob.size; render();
        return { PartNumber: n, ETag: etag };
      } catch (err) {
        if (err.name === "AbortError") throw err;
        last = err; loaded[n] = 0; render();
        say(`Chunk ${n} failed (${err.message}). Retrying…`);
        await sleep(800 * 2 ** attempt);
      }
    }
    throw last;
  }

  const abortAll = () => xhrs.forEach((x) => x.abort());

  async function start() {
    uploading = true; cancelled = false; failure = null;
    Object.keys(loaded).forEach((k) => delete loaded[k]);
    go.disabled = true; cancelBtn.hidden = false; prog.hidden = false;
    fileInput.disabled = true; title.disabled = true; desc.disabled = true;
    say("Preparing the upload…");
    try {
      const init = await api("/api/upload/init", {
        title: title.value, description: desc.value,
        filename: file.name, size: file.size, duration,
      });
      videoId = init.video_id; partSize = init.part_size; totalParts = init.parts;
      t0 = Date.now(); say("Uploading directly to secure storage…"); render();

      const queue = Array.from({ length: totalParts }, (_, i) => i + 1);
      const done = [];
      const worker = async () => {
        try {
          while (queue.length && !failure && !cancelled) done.push(await uploadPart(queue.shift()));
        } catch (e) {
          if (e.name !== "AbortError") failure = failure || e;
          abortAll();
        }
      };
      await Promise.all(Array.from({ length: Math.min(CONCURRENCY, totalParts) }, worker));
      if (cancelled) throw new DOMException("cancelled", "AbortError");
      if (failure) throw failure;

      say("Verifying the file on the server…");
      await api(`/api/upload/${videoId}/complete`, { parts: done });
      fill.style.width = "100%"; pct.textContent = "100%"; etaEl.textContent = "";
      say("Upload complete and verified. It is now in the library.", "okc");
      uploading = false; videoId = null;
      clearFile(); title.value = ""; desc.value = "";
    } catch (err) {
      if (videoId) { try { await api(`/api/upload/${videoId}/abort`); } catch (e) { /* best effort */ } }
      say(err.name === "AbortError" ? "Upload cancelled." : "Upload failed: " + err.message, "err");
      prog.hidden = true;
    } finally {
      uploading = false; cancelBtn.hidden = true;
      fileInput.disabled = false; title.disabled = false; desc.disabled = false;
      go.disabled = !file;
    }
  }

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    if (!file || uploading) return;
    if (!title.value.trim()) return say("Please enter a title.", "err");
    start();
  });
  cancelBtn.addEventListener("click", () => { cancelled = true; abortAll(); });
  window.addEventListener("beforeunload", (e) => { if (uploading) { e.preventDefault(); e.returnValue = ""; } });
})();
