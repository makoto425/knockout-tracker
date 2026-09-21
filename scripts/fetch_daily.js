// fetch_daily.js
// 在 Chrome 分頁已導覽至任一 query1.finance.yahoo.com 網址（同源）之後，
// 用 javascript_tool 貼上本檔內容執行一次以完成 setup，
// 之後再分批呼叫 window.runBatch(start, end, 12) 直到跑完整個 UNIVERSE。
//
// UNIVERSE 從 data/universe.json 讀出後貼進來（見 scripts/PLAYBOOK.md 的完整步驟）。

window.RESULTS = [];
window.ERRS = [];

window.computeMetrics = function(closes) {
  const c = closes.filter(x => x !== null && x !== undefined && !isNaN(x));
  if (c.length < 150) return null;
  const logret = [];
  for (let i = 1; i < c.length; i++) { logret.push(Math.log(c[i] / c[i - 1])); }
  if (logret.length < 150) return null;
  function stdSample(arr) {
    const n = arr.length;
    const mean = arr.reduce((a, b) => a + b, 0) / n;
    const variance = arr.reduce((a, b) => a + (b - mean) * (b - mean), 0) / (n - 1);
    return Math.sqrt(variance);
  }
  const sigma63 = stdSample(logret.slice(-63)) * Math.sqrt(252);
  const sigma252 = stdSample(logret.slice(-252)) * Math.sqrt(252);
  const sigma = (sigma63 + sigma252) / 2;
  if (!(sigma > 0)) return null;
  const w = Math.min(252, logret.length);
  const sumLog = logret.slice(-w).reduce((a, b) => a + b, 0);
  let muHist = sumLog * (252 / w);
  muHist = Math.max(-0.4, Math.min(0.4, muHist));
  return { s0: c[c.length - 1], sigma, muHist, nDays: c.length };
};

window.fetchOne = async function (entry) {
  const [ticker, ysym, idx] = entry;
  try {
    const r = await fetch(`https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(ysym)}?range=2y&interval=1d`);
    if (!r.ok) { window.ERRS.push({ ticker, error: 'http_' + r.status }); return; }
    const j = await r.json();
    const res = j.chart && j.chart.result && j.chart.result[0];
    if (!res) { window.ERRS.push({ ticker, error: 'no_result' }); return; }
    const meta = res.meta || {};
    const adj = res.indicators && res.indicators.adjclose && res.indicators.adjclose[0] && res.indicators.adjclose[0].adjclose;
    const rawClose = res.indicators && res.indicators.quote && res.indicators.quote[0] && res.indicators.quote[0].close;
    const closes = adj || rawClose;
    if (!closes) { window.ERRS.push({ ticker, error: 'no_closes' }); return; }
    const m = window.computeMetrics(closes);
    if (!m) { window.ERRS.push({ ticker, error: 'insufficient_data' }); return; }
    window.RESULTS.push({
      ticker, idx,
      name: meta.longName || meta.shortName || ticker,
      s0: Math.round(m.s0 * 100) / 100,
      sigma: Math.round(m.sigma * 10000) / 10000,
      muHist: Math.round(m.muHist * 10000) / 10000,
      nDays: m.nDays
    });
  } catch (e) {
    window.ERRS.push({ ticker, error: String(e) });
  }
};

window.runBatch = async function (start, end, concurrency) {
  const slice = window.UNIVERSE.slice(start, end);
  let idx = 0;
  async function worker() {
    while (idx < slice.length) {
      const my = slice[idx++];
      await window.fetchOne(my);
    }
  }
  const workers = [];
  for (let i = 0; i < concurrency; i++) workers.push(worker());
  await Promise.all(workers);
  return { done: window.RESULTS.length, errs: window.ERRS.length };
};
