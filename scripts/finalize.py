#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
finalize.py — 讀取 data/ko_results_raw.json（由 Chrome 端抓取並計算出的
S0 / sigma / mu_hist），計算「105% 敲出機率」並產生 site/index.html。

用法： python3 scripts/finalize.py   (在專案根目錄執行)
輸入： data/ko_results_raw.json  [{ticker, idx, name, s0, sigma, muHist, nDays}, ...]
輸出： data/latest.json, site/data.json, site/index.html
"""
import json
import math
import sys
from datetime import datetime, timezone

BARRIER_RATIO = 1.05
HORIZON_YEARS = 1.0
MONITOR_PER_YEAR = 12
BGK_BETA = 0.5826


def norm_cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def ko_probability(sigma, mu, barrier_ratio=BARRIER_RATIO, T=HORIZON_YEARS,
                    monitors_per_year=MONITOR_PER_YEAR):
    if sigma <= 0 or T <= 0:
        return 0.0
    nu = mu - 0.5 * sigma * sigma
    b = math.log(barrier_ratio)
    dt = 1.0 / monitors_per_year
    b_adj = b + BGK_BETA * sigma * math.sqrt(dt)
    sT = sigma * math.sqrt(T)
    d1 = (nu * T - b_adj) / sT
    d2 = (-nu * T - b_adj) / sT
    term1 = norm_cdf(d1)
    exponent = 2.0 * nu * b_adj / (sigma * sigma)
    try:
        factor = math.exp(exponent)
    except OverflowError:
        factor = float("inf")
    term2 = factor * norm_cdf(d2) if math.isfinite(factor) else 0.0
    p = term1 + term2
    return float(min(max(p, 0.0), 1.0))


def main():
    with open("data/ko_results_raw.json", "r", encoding="utf-8") as f:
        raw = json.load(f)

    stocks = []
    for r in raw:
        sigma = r["sigma"]
        mu_hist = r["muHist"]
        p_zero = ko_probability(sigma, 0.0)
        p_hist = ko_probability(sigma, mu_hist)
        stocks.append({
            "ticker": r["ticker"],
            "name": r.get("name", r["ticker"]),
            "idx": r["idx"],
            "s0": r["s0"],
            "sigma": round(sigma, 4),
            "mu_hist": round(mu_hist, 4),
            "p_zero_drift": round(p_zero, 4),
            "p_hist_drift": round(p_hist, 4),
        })

    stocks.sort(key=lambda x: x["p_hist_drift"], reverse=True)

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "params": {
            "barrier_ratio": BARRIER_RATIO,
            "horizon_years": HORIZON_YEARS,
            "monitor_per_year": MONITOR_PER_YEAR,
        },
        "stocks": stocks,
    }

    with open("data/latest.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    with open("site/data.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))

    with open("scripts/template.html", "r", encoding="utf-8") as f:
        template = f.read()
    html = template.replace("__DATA_JSON__", json.dumps(out, ensure_ascii=False, separators=(",", ":")))
    with open("site/index.html", "w", encoding="utf-8") as f:
        f.write(html)

    print(f"完成：{len(stocks)} 檔股票，已寫入 site/index.html", file=sys.stderr)
    print("Top 10 (歷史漂移假設):", file=sys.stderr)
    for s in stocks[:10]:
        print(f"  {s['ticker']:6s} {s['p_hist_drift']*100:5.1f}%  sigma={s['sigma']*100:5.1f}%  {s['name']}", file=sys.stderr)


if __name__ == "__main__":
    main()
