// Chart.js wrappers that follow the CSS design tokens (and re-theme on light/dark switch).

const charts = new Map();

export function token(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

export const toneColor = (tone) => token({ ok: "--ok", bad: "--bad", warn: "--warn", neutral: "--neutral", info: "--info", brand: "--brand" }[tone] || "--brand");

function baseOptions() {
  const muted = token("--muted");
  const grid = token("--border");
  return {
    responsive: true,
    maintainAspectRatio: false,
    animation: { duration: 600, easing: "easeOutQuart" },
    plugins: {
      legend: { display: false },
      tooltip: {
        backgroundColor: token("--text"), titleColor: token("--bg"), bodyColor: token("--bg"),
        padding: 10, cornerRadius: 8, boxPadding: 4, usePointStyle: true,
      },
    },
    scales: {
      x: { grid: { display: false }, border: { display: false }, ticks: { color: muted, font: { size: 11 } } },
      y: { grid: { color: grid, drawTicks: false }, border: { display: false }, ticks: { color: muted, font: { size: 11 }, precision: 0, padding: 8 }, beginAtZero: true },
    },
  };
}

/** Create or replace the chart on a canvas. Returns null if Chart.js failed to load. */
export function draw(canvas, config) {
  if (!window.Chart || !canvas) return null;
  charts.get(canvas)?.destroy();
  const chart = new window.Chart(canvas, config);
  charts.set(canvas, chart);
  return chart;
}

export function stackedBars(canvas, labels, series) {
  const opts = baseOptions();
  opts.scales.x.stacked = true;
  opts.scales.y.stacked = true;
  opts.plugins.legend = { display: true, position: "bottom", labels: { color: token("--text-2"), usePointStyle: true, pointStyle: "rectRounded", boxWidth: 10, padding: 14, font: { size: 11.5 } } };
  return draw(canvas, {
    type: "bar",
    data: { labels, datasets: series.map((s) => ({ label: s.label, data: s.data, backgroundColor: toneColor(s.tone), borderRadius: 5, borderSkipped: false, maxBarThickness: 26 })) },
    options: opts,
  });
}

export function doughnut(canvas, labels, values, tones) {
  const opts = baseOptions();
  delete opts.scales;
  return draw(canvas, {
    type: "doughnut",
    data: { labels, datasets: [{ data: values, backgroundColor: tones.map(toneColor), borderColor: token("--surface"), borderWidth: 3, hoverOffset: 6 }] },
    options: { ...opts, cutout: "72%" },
  });
}

export function horizontalBars(canvas, labels, series) {
  const opts = baseOptions();
  opts.indexAxis = "y";
  opts.scales.x = { ...opts.scales.y, stacked: true };
  opts.scales.y = { grid: { display: false }, border: { display: false }, stacked: true, ticks: { color: token("--text-2"), font: { size: 12 } } };
  opts.plugins.legend = { display: true, position: "bottom", labels: { color: token("--text-2"), usePointStyle: true, pointStyle: "rectRounded", boxWidth: 10, padding: 14, font: { size: 11.5 } } };
  return draw(canvas, {
    type: "bar",
    data: { labels, datasets: series.map((s) => ({ label: s.label, data: s.data, backgroundColor: toneColor(s.tone), borderRadius: 5, borderSkipped: false, barThickness: 16 })) },
    options: opts,
  });
}

export function groupedBars(canvas, labels, series, { money = false } = {}) {
  const opts = baseOptions();
  opts.plugins.legend = { display: true, position: "bottom", labels: { color: token("--text-2"), usePointStyle: true, pointStyle: "rectRounded", boxWidth: 10, padding: 14, font: { size: 11.5 } } };
  if (money) opts.scales.y.ticks.callback = (v) => Number(v).toLocaleString();
  return draw(canvas, {
    type: "bar",
    data: { labels, datasets: series.map((s) => ({ label: s.label, data: s.data, backgroundColor: toneColor(s.tone), borderRadius: 5, borderSkipped: false, maxBarThickness: 22, categoryPercentage: 0.7 })) },
    options: opts,
  });
}

export function sparkline(canvas, values, tone) {
  const color = toneColor(tone);
  return draw(canvas, {
    type: "line",
    data: { labels: values.map((_, i) => i), datasets: [{ data: values, borderColor: color, borderWidth: 2, tension: 0.4, pointRadius: 0, fill: true,
      backgroundColor: (ctx) => {
        const { chart } = ctx;
        if (!chart.chartArea) return "transparent";
        const g = chart.ctx.createLinearGradient(0, chart.chartArea.top, 0, chart.chartArea.bottom);
        g.addColorStop(0, `${color}55`);
        g.addColorStop(1, `${color}00`);
        return g;
      } }] },
    options: { responsive: true, maintainAspectRatio: false, animation: false, plugins: { legend: { display: false }, tooltip: { enabled: false } },
      scales: { x: { display: false }, y: { display: false, beginAtZero: true } } },
  });
}
