(async () => {
  const r = await fetch("/api/analytics", { credentials: "same-origin" });
  if (!r.ok) return;
  const d = await r.json();
  const set = (id, v) => (document.getElementById(id).textContent = v);
  set("t-users", d.totals.users); set("t-videos", d.totals.videos);
  set("t-views", d.totals.views); set("t-denied", d.totals.denied);
  set("t-fails", d.totals.failed_logins);

  Chart.defaults.color = "#98a4c7";
  Chart.defaults.borderColor = "#25315e";
  const bar = (id, o, label, color) => new Chart(document.getElementById(id), {
    type: "bar",
    data: { labels: o.labels, datasets: [{ label, data: o.data, backgroundColor: color, borderRadius: 6 }] },
    options: { plugins: { legend: { display: false } }, scales: { y: { beginAtZero: true, ticks: { precision: 0 } } } },
  });
  bar("c-videos", d.views_per_video, "views", "#6366f1");
  bar("c-uploads", d.uploads_per_instructor, "uploads", "#22d3ee");
  bar("c-viewers", d.top_viewers, "views", "#34d399");

  new Chart(document.getElementById("c-daily"), {
    type: "line",
    data: {
      labels: d.daily.labels.map((x) => x.slice(5)),
      datasets: [
        { label: "views", data: d.daily.views, borderColor: "#6366f1", backgroundColor: "#6366f133", fill: true, tension: 0.35 },
        { label: "denied", data: d.daily.denied, borderColor: "#f87171", tension: 0.35 },
        { label: "failed logins", data: d.daily.failed_logins, borderColor: "#fbbf24", tension: 0.35 },
      ],
    },
    options: { scales: { y: { beginAtZero: true, ticks: { precision: 0 } } } },
  });
})();
