#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_software.py — 美股軟體上市公司「值得買入」綜合評分排行（每日由 GitHub Actions 執行）

流程：
  1. 候選池 = 內建種子清單 (SEED) ∪ Yahoo screener 搜尋到的「Software - Application /
     Software - Infrastructure、美國、市值 ≥ 20 億美元」公司（screener 失敗就只用種子清單）
  2. yfinance 抓每檔 Ticker.info（基本面 / 分析師預期）與 14 個月日線（動能、波動）
  3. 衍生指標 → 各指標在候選池內取百分位 → 依 6 大面向加權成 0–100 分
  4. 輸出 data/software_latest.json（面向分數都保留，網頁端可自行調整權重重新排序）

6 大面向（預設權重）：成長 25 / 獲利品質 20 / 未來性 20 / 估值 15 / 財務體質 10 / 動能 10
僅為量化篩選參考，不構成投資建議。
"""
import json
import math
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import numpy as np
import pandas as pd

OUT_FILE = "data/software_latest.json"
MIN_MCAP = 2e9
MIN_STOCKS_TO_PUBLISH = 30

DEFAULT_WEIGHTS = {"growth": 25, "quality": 20, "future": 20, "value": 15, "health": 10, "momentum": 10}

# 內建種子清單（美國總部的軟體 / SaaS / 資安 / 雲端基礎設施公司；個別下市或被併購的會因抓不到資料自動略過）
SEED = """
MSFT ORCL CRM ADBE NOW INTU PANW CRWD SNPS CDNS ADSK WDAY FTNT DDOG SNOW PLTR TEAM HUBS ZS NET MDB OKTA
DOCU TWLO ZM PAYC PCTY TYL FICO MANH GWRE APPF BILL DT ESTC GTLB CFLT S PATH U TTD APP ROP PTC BSY NTNX
DBX BOX FRSH ASAN SMAR PEGA QLYS TENB RPD CYBR AKAM GEN NCNO ALKT AI BRZE CWAN DOCN DV IAS PD PYCR QTWO
RNG SPSC VERX WK YEXT ZUO ALRM BL BLKB CCCS CVLT DOMO FIVN JAMF KVYO OLO PCOR PRGS RAMP SEMR TDC AGYS
ACIW DUOL IOT RBRK ZETA TOST SOUN BBAI APPN AVPT CLSK DAY ENFN EVCM GRND HCP INTA MANH MODN NABL NCR
PEGA PRO PWSC SAIL SPT TRAK VRNT WEAV XM LAW DLO FOUR GDDY MSTR PAYO CXM CRNC LPSN MGNI
""".split()

SOFTWARE_INDUSTRIES = ["Software - Application", "Software - Infrastructure"]


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def discover_via_screener():
    """用 yfinance screener 找出更多美國軟體股；任何錯誤都回傳空集合。"""
    found = set()
    try:
        import yfinance as yf
        from yfinance import EquityQuery
        q = EquityQuery("and", [
            EquityQuery("eq", ["region", "us"]),
            EquityQuery("is-in", ["industry"] + SOFTWARE_INDUSTRIES),
            EquityQuery("gt", ["intradaymarketcap", MIN_MCAP]),
        ])
        offset = 0
        while offset < 600:
            res = yf.screen(q, size=250, offset=offset, sortField="intradaymarketcap", sortAsc=False)
            quotes = res.get("quotes", []) if isinstance(res, dict) else []
            if not quotes:
                break
            for it in quotes:
                sym = it.get("symbol")
                if sym and "." not in sym.replace("-", ""):
                    found.add(sym)
            if len(quotes) < 250:
                break
            offset += 250
        log(f"screener 找到 {len(found)} 檔")
    except Exception as e:  # noqa
        log(f"screener 失敗，改用種子清單：{e}")
    return found


def get_info(sym, retries=3):
    import yfinance as yf
    for i in range(retries):
        try:
            info = yf.Ticker(sym).info
            if info and (info.get("marketCap") or info.get("currentPrice")):
                return info
        except Exception:
            pass
        time.sleep(1.5 * (i + 1))
    return None


def num(x):
    try:
        if x is None:
            return None
        x = float(x)
        return None if (math.isnan(x) or math.isinf(x)) else x
    except Exception:
        return None


def derive(sym, info, close):
    """從 info + 價格序列算出所有指標。拿不到的欄位為 None。"""
    price = num(info.get("currentPrice")) or num(info.get("regularMarketPrice"))
    mcap = num(info.get("marketCap"))
    rev = num(info.get("totalRevenue"))
    fcf = num(info.get("freeCashflow"))
    cash = num(info.get("totalCash"))
    debt = num(info.get("totalDebt"))
    rev_g = num(info.get("revenueGrowth"))
    op_m = num(info.get("operatingMargins"))
    fcf_m = fcf / rev if (fcf is not None and rev) else None
    tgt = num(info.get("targetMeanPrice"))
    n_an = num(info.get("numberOfAnalystOpinions")) or 0
    f_eps, t_eps = num(info.get("forwardEps")), num(info.get("trailingEps"))
    eps_improve = None
    if f_eps is not None and t_eps not in (None, 0):
        eps_improve = max(-1.0, min(2.0, (f_eps - t_eps) / abs(t_eps)))
    ev_s = num(info.get("enterpriseToRevenue"))
    fpe = num(info.get("forwardPE"))
    m = {
        "rev_growth": rev_g,
        "earn_growth": None if num(info.get("earningsGrowth")) is None else max(-1.0, min(3.0, num(info.get("earningsGrowth")))),
        "gross_margin": num(info.get("grossMargins")),
        "op_margin": op_m,
        "profit_margin": num(info.get("profitMargins")),
        "fcf_margin": fcf_m,
        "rule40": (rev_g + (fcf_m if fcf_m is not None else op_m)) if (rev_g is not None and (fcf_m is not None or op_m is not None)) else None,
        "eps_improve": eps_improve,
        "target_upside": (tgt / price - 1) if (tgt and price and n_an >= 5) else None,
        "rec_mean": num(info.get("recommendationMean")) if n_an >= 5 else None,
        "ev_sales": ev_s if (ev_s is not None and ev_s > 0) else None,
        "ev_sales_per_growth": (ev_s / max(rev_g, 0.02) / 100) if (ev_s and ev_s > 0 and rev_g is not None) else None,
        "fcf_yield": (fcf / mcap) if (fcf is not None and mcap) else None,
        "fwd_pe": fpe if (fpe is not None and fpe > 0) else None,
        "net_cash_pct": ((cash - debt) / mcap) if (cash is not None and debt is not None and mcap) else None,
        "current_ratio": None if num(info.get("currentRatio")) is None else max(0.0, min(5.0, num(info.get("currentRatio")))),
        "vol_1y": None, "ret_6m": None, "ret_12m_ex1m": None, "vs_200d": None,
    }
    if close is not None:
        c = close.dropna()
        if len(c) > 130:
            lr = np.log(c / c.shift(1)).dropna()
            m["vol_1y"] = float(lr.tail(252).std() * math.sqrt(252))
            m["ret_6m"] = float(c.iloc[-1] / c.iloc[-126] - 1)
            if len(c) > 252:
                m["ret_12m_ex1m"] = float(c.iloc[-22] / c.iloc[-253] - 1)
            if len(c) >= 200:
                m["vs_200d"] = float(c.iloc[-1] / c.tail(200).mean() - 1)
            if price is None:
                price = float(c.iloc[-1])
    return {
        "ticker": sym,
        "name": info.get("shortName") or info.get("longName") or sym,
        "industry": info.get("industry") or "",
        "country": info.get("country") or "",
        "price": None if price is None else round(price, 2),
        "mcap": mcap,
        "n_analysts": int(n_an),
        "m": {k: (None if v is None else round(float(v), 4)) for k, v in m.items()},
    }


# (指標, 越高越好?, 面向內權重)
CATEGORIES = {
    "growth":   [("rev_growth", True, 3), ("earn_growth", True, 1)],
    "quality":  [("gross_margin", True, 1), ("op_margin", True, 2), ("fcf_margin", True, 2), ("rule40", True, 3), ("profit_margin", True, 1)],
    "future":   [("target_upside", True, 2), ("rec_mean", False, 1), ("eps_improve", True, 2)],
    "value":    [("ev_sales_per_growth", False, 3), ("fcf_yield", True, 2), ("fwd_pe", False, 1), ("ev_sales", False, 1)],
    "health":   [("net_cash_pct", True, 2), ("current_ratio", True, 1), ("vol_1y", False, 1)],
    "momentum": [("ret_6m", True, 2), ("ret_12m_ex1m", True, 2), ("vs_200d", True, 1)],
}


def pct_rank(values, higher_better):
    """回傳與 values 等長的百分位 (0–1)；None 保持 None。前後 2% 先縮尾避免極端值。"""
    idx = [i for i, v in enumerate(values) if v is not None]
    out = [None] * len(values)
    if len(idx) < 8:
        return out
    arr = np.array([values[i] for i in idx], dtype=float)
    lo, hi = np.percentile(arr, [2, 98])
    arr = np.clip(arr, lo, hi)
    ranks = pd.Series(arr).rank(method="average").to_numpy()
    p = (ranks - 1) / (len(arr) - 1)
    if not higher_better:
        p = 1 - p
    for k, i in enumerate(idx):
        out[i] = float(p[k])
    return out


def score_all(stocks):
    n = len(stocks)
    pcts = {}
    for cat, metrics in CATEGORIES.items():
        for key, hb, _ in metrics:
            pcts[key] = pct_rank([s["m"].get(key) for s in stocks], hb)
    for j, s in enumerate(stocks):
        scores, cov = {}, {}
        for cat, metrics in CATEGORIES.items():
            tot_w = got_w = acc = 0.0
            for key, _, w in metrics:
                tot_w += w
                p = pcts[key][j]
                if p is not None:
                    got_w += w
                    acc += w * p
            cov[cat] = got_w / tot_w
            scores[cat] = round(100 * acc / got_w, 1) if (got_w > 0 and got_w / tot_w >= 0.5) else None
        s["scores"] = scores
        s["coverage"] = round(sum(cov.values()) / len(cov), 2)
        s["score"] = total_score(scores, DEFAULT_WEIGHTS)
    return stocks


def total_score(scores, weights):
    tw = sum(weights[c] for c in weights if scores.get(c) is not None)
    if tw <= 0 or sum(1 for c in scores if scores[c] is not None) < 4:
        return None
    return round(sum(weights[c] * scores[c] for c in weights if scores.get(c) is not None) / tw, 1)


def main():
    import yfinance as yf

    prev = {}
    if os.path.exists(OUT_FILE):
        try:
            with open(OUT_FILE, encoding="utf-8") as f:
                old = json.load(f)
            prev = {s["ticker"]: i + 1 for i, s in enumerate(old.get("stocks", []))}
        except Exception:
            pass

    discovered = discover_via_screener()
    seed = list(dict.fromkeys(SEED))
    symbols = list(dict.fromkeys(seed + sorted(discovered - set(seed))))
    log(f"候選 {len(symbols)} 檔（種子 {len(seed)} + screener 新增 {len(set(symbols) - set(seed))}）")

    ysyms = {s: s.replace(".", "-") for s in symbols}
    with ThreadPoolExecutor(max_workers=6) as ex:
        infos = dict(zip(symbols, ex.map(lambda s: get_info(ysyms[s]), symbols)))
    got = [s for s in symbols if infos[s]]
    log(f"info 取得 {len(got)}/{len(symbols)}")

    raw = yf.download(tickers=[ysyms[s] for s in got], period="14mo", interval="1d",
                      auto_adjust=True, group_by="ticker", threads=True, progress=False)

    stocks, skipped = [], []
    for s in got:
        info = infos[s]
        ind = info.get("industry") or ""
        if s not in seed and ind not in SOFTWARE_INDUSTRIES:
            skipped.append((s, "非軟體產業"))
            continue
        if s not in seed and info.get("country") not in (None, "", "United States"):
            skipped.append((s, "非美國公司"))
            continue
        mcap = num(info.get("marketCap"))
        if not mcap or mcap < MIN_MCAP:
            skipped.append((s, "市值不足"))
            continue
        try:
            close = raw[(ysyms[s], "Close")] if isinstance(raw.columns, pd.MultiIndex) else raw["Close"]
        except Exception:
            close = None
        stocks.append(derive(s, info, close))

    log(f"納入評分 {len(stocks)} 檔，略過 {len(skipped)} 檔")
    if len(stocks) < MIN_STOCKS_TO_PUBLISH:
        log(f"::warning::有效標的只有 {len(stocks)} 檔（< {MIN_STOCKS_TO_PUBLISH}），疑似 Yahoo 暫時異常，保留舊檔不覆寫")
        return 0

    score_all(stocks)
    stocks = [s for s in stocks if s["score"] is not None]
    stocks.sort(key=lambda s: s["score"], reverse=True)
    for i, s in enumerate(stocks, 1):
        s["rank"] = i
        s["prev_rank"] = prev.get(s["ticker"])

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "weights": DEFAULT_WEIGHTS,
        "universe_size": len(stocks),
        "stocks": stocks,
    }
    os.makedirs("data", exist_ok=True)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    log(f"已寫入 {OUT_FILE}，共 {len(stocks)} 檔")
    for s in stocks[:10]:
        log(f"  #{s['rank']:<3} {s['ticker']:6s} {s['score']:5.1f}  {s['name']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
