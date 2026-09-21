#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gh_fetch_and_build.py — 給 GitHub Actions 用的每日更新腳本。
在 GitHub Actions runner 上執行（有正常網路，不受 Claude 沙盒的網域限制），
直接用 requests + yfinance 抓資料，不需要瀏覽器。

流程：
  1. 從 slickcharts.com 抓 S&P500 + Nasdaq100 成分股清單並合併去重
  2. 用 yfinance 批次下載每檔過去 2 年的日收盤價
  3. 估計年化波動率 sigma（63 日 + 252 日已實現波動平均）與歷史年化漂移 mu_hist（截尾 ±40%）
  4. 用 GBM 反射原理解析公式 + Broadie-Glasserman-Kou 連續性修正，
     計算 12 個月、每月觀察、105% 敲出的機率
  5. 輸出 data/latest.json, site/data.json, site/index.html（由 scripts/template.html 內嵌資料產生）
"""
import json
import math
import re
import sys
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import requests

BARRIER_RATIO = 1.05
HORIZON_YEARS = 1.0
MONITOR_PER_YEAR = 12
BGK_BETA = 0.5826
MIN_HISTORY_DAYS = 150
DRIFT_CLIP = 0.40

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def fetch_slickcharts_table(url):
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    tables = pd.read_html(r.text)
    # slickcharts 的成分股表通常是第一個 table，欄位含 Symbol/Company
    for t in tables:
        cols = [str(c) for c in t.columns]
        if any("Symbol" in c for c in cols) and len(t) > 50:
            return t
    raise RuntimeError(f"找不到成分股表格: {url}")


def get_universe():
    universe = {}

    sp500 = fetch_slickcharts_table("https://www.slickcharts.com/sp500")
    for _, row in sp500.iterrows():
        sym = str(row["Symbol"]).strip().upper()
        name = str(row.get("Company", "")).strip()
        if sym:
            universe.setdefault(sym, {"name": name, "idx": set()})
            universe[sym]["idx"].add("S&P500")
    log(f"S&P500: {len(sp500)} rows")

    nd100 = fetch_slickcharts_table("https://www.slickcharts.com/nasdaq100")
    for _, row in nd100.iterrows():
        sym = str(row["Symbol"]).strip().upper()
        name = str(row.get("Company", "")).strip()
        if sym:
            universe.setdefault(sym, {"name": name, "idx": set()})
            universe[sym]["idx"].add("Nasdaq100")
    log(f"Nasdaq100: {len(nd100)} rows")

    if not universe:
        raise RuntimeError("無法取得任何成分股清單，中止")
    return universe


def yahoo_symbol(t):
    return t.replace(".", "-")


def fetch_prices(tickers):
    import yfinance as yf
    log(f"開始下載 {len(tickers)} 檔股票的歷史價格 (2y)...")
    data = yf.download(
        tickers=tickers, period="2y", interval="1d",
        auto_adjust=True, group_by="ticker", threads=True, progress=False,
    )
    log("下載完成")
    return data


def compute_metrics(close_series: pd.Series):
    close_series = close_series.dropna()
    n = len(close_series)
    if n < MIN_HISTORY_DAYS:
        return None
    logret = np.log(close_series / close_series.shift(1)).dropna()
    if len(logret) < MIN_HISTORY_DAYS:
        return None
    sigma_63 = logret.tail(63).std() * math.sqrt(252)
    sigma_252 = logret.tail(252).std() * math.sqrt(252)
    sigma_252 = sigma_252 if not math.isnan(sigma_252) else sigma_63
    sigma = float(np.nanmean([sigma_63, sigma_252]))
    if sigma <= 0 or math.isnan(sigma):
        return None
    window = min(252, len(logret))
    total_logret = logret.tail(window).sum()
    mu_hist = float(total_logret * (252.0 / window))
    mu_hist = max(-DRIFT_CLIP, min(DRIFT_CLIP, mu_hist))
    s0 = float(close_series.iloc[-1])
    return {"s0": s0, "sigma": sigma, "mu_hist": mu_hist, "n_days": n}


def ko_probability(sigma, mu, barrier_ratio=BARRIER_RATIO, T=HORIZON_YEARS,
                    monitors_per_year=MONITOR_PER_YEAR):
    from scipy.stats import norm
    if sigma <= 0 or T <= 0:
        return 0.0
    nu = mu - 0.5 * sigma * sigma
    b = math.log(barrier_ratio)
    dt = 1.0 / monitors_per_year
    b_adj = b + BGK_BETA * sigma * math.sqrt(dt)
    sT = sigma * math.sqrt(T)
    d1 = (nu * T - b_adj) / sT
    d2 = (-nu * T - b_adj) / sT
    term1 = norm.cdf(d1)
    exponent = 2.0 * nu * b_adj / (sigma * sigma)
    try:
        factor = math.exp(exponent)
    except OverflowError:
        factor = float("inf")
    term2 = factor * norm.cdf(d2) if math.isfinite(factor) else 0.0
    p = term1 + term2
    return float(min(max(p, 0.0), 1.0))


def main():
    universe = get_universe()
    tickers = sorted(universe.keys())
    log(f"合併成分股共 {len(tickers)} 檔")

    ysyms = [yahoo_symbol(t) for t in tickers]
    raw = fetch_prices(ysyms)

    stocks = []
    errors = []
    for t, ysym in zip(tickers, ysyms):
        try:
            if isinstance(raw.columns, pd.MultiIndex):
                if (ysym, "Close") not in raw.columns:
                    continue
                close = raw[(ysym, "Close")]
            else:
                close = raw["Close"]
            m = compute_metrics(close)
            if m is None:
                continue
            p_zero = ko_probability(m["sigma"], 0.0)
            p_hist = ko_probability(m["sigma"], m["mu_hist"])
            stocks.append({
                "ticker": t,
                "name": universe[t]["name"],
                "idx": sorted(universe[t]["idx"]),
                "s0": round(m["s0"], 2),
                "sigma": round(m["sigma"], 4),
                "mu_hist": round(m["mu_hist"], 4),
                "p_zero_drift": round(p_zero, 4),
                "p_hist_drift": round(p_hist, 4),
            })
        except Exception as e:
            errors.append({"ticker": t, "error": str(e)})

    stocks.sort(key=lambda x: x["p_hist_drift"], reverse=True)
    log(f"成功計算 {len(stocks)} 檔，失敗 {len(errors)} 檔")

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "params": {
            "barrier_ratio": BARRIER_RATIO,
            "horizon_years": HORIZON_YEARS,
            "monitor_per_year": MONITOR_PER_YEAR,
        },
        "stocks": stocks,
        "errors": errors,
    }

    import os
    os.makedirs("data", exist_ok=True)
    os.makedirs("site", exist_ok=True)

    with open("data/latest.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    site_data = {k: v for k, v in out.items() if k != "errors"}
    with open("site/data.json", "w", encoding="utf-8") as f:
        json.dump(site_data, f, ensure_ascii=False, separators=(",", ":"))

    with open("scripts/template.html", "r", encoding="utf-8") as f:
        template = f.read()
    html = template.replace("__DATA_JSON__", json.dumps(site_data, ensure_ascii=False, separators=(",", ":")))
    with open("site/index.html", "w", encoding="utf-8") as f:
        f.write(html)

    log("完成，已寫入 data/latest.json, site/data.json, site/index.html")


if __name__ == "__main__":
    main()
