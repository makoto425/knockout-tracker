# Knockout Tracker — 每日更新流程（給排程任務用）

專案資料夾：`~/Documents/knockout-tracker`（在 device_bash 裡是 `$HOME/mnt/Documents/knockout-tracker`）

## 背景

此帳號的網路政策把雲端沙盒與本機 Cowork VM（device_bash）對外連線鎖死，只放行
GitHub / PyPI / npm，連不上 Yahoo Finance。因此**價格資料必須透過 Claude in Chrome
（使用者的真實 Chrome，不受此限制）抓取**，抓到的精簡結果（每檔股票的 S0 / sigma /
mu_hist，不是完整歷史價格）再用 device_bash 做最終運算與產生網頁。

## 每日執行步驟

1. **確認 Chrome 可用**：`tabs_context_mcp({createIfEmpty:true})`。若擴充功能未連線，
   請使用者開啟 Chrome 並保持執行中，再重試。

2. **導覽到 Yahoo Finance 網域**（讓後續 fetch() 屬於同源請求）：
   `navigate(url: "https://query1.finance.yahoo.com/v8/finance/chart/AAPL?range=5d&interval=1d")`

3. **載入 UNIVERSE**：讀取 `data/universe.json`（device_bash: `cat data/universe.json`），
   內容是 `[[ticker, yahooSymbol, [idx,...]], ...]`，共 ~515 檔。
   若這份清單超過 30 天沒更新，建議重新從 https://www.slickcharts.com/sp500 與
   https://www.slickcharts.com/nasdaq100 各抓一次（server-rendered table，用
   `document.querySelector('table')` 直接解析即可），合併去重後覆寫 `data/universe.json`。

4. **在 Chrome 分頁執行 `scripts/fetch_daily.js` 的內容**（用 javascript_tool 貼上整段程式碼），
   接著設定 `window.UNIVERSE = <上面讀到的 universe.json 內容>`（也可以在同一段程式碼開頭一起貼）。

5. **分批呼叫 `window.runBatch(start, end, 12)`**，每批約 130 檔，直到跑完全部（4 次左右）。
   完成後 `window.RESULTS` 會是每檔的 `{ticker, idx, name, s0, sigma, muHist, nDays}`。

6. **把結果存成檔案**：
   ```js
   const blob = new Blob([JSON.stringify(window.RESULTS)], {type:'application/json'});
   const url = URL.createObjectURL(blob);
   const a = document.createElement('a'); a.href = url; a.download = 'ko_results.json';
   document.body.appendChild(a); a.click();
   ```
   這會存到使用者的 `~/Downloads/ko_results.json`。若尚未取得 Downloads 資料夾權限，
   用 `device_request_folder_access(["~/Downloads"])` 申請一次。

7. **搬移並執行最終運算**（device_bash）：
   ```bash
   cd "$HOME/mnt/Documents/knockout-tracker"
   cp "$HOME/mnt/Downloads/ko_results.json" data/ko_results_raw.json
   python3 scripts/finalize.py
   ```
   這會重新計算 105% 敲出機率（反射原理 + BGK 連續性修正），並產生：
   - `data/latest.json`（完整備份）
   - `site/data.json`（給網頁用）
   - `site/index.html`（可直接雙擊在瀏覽器打開的獨立頁面，資料已內嵌）

8. **完成**：跟使用者說明今天 Top 10、資料時間戳，並提醒 `site/index.html` 已更新
   （若使用者要求，可用 SendUserFile 或請他們直接打開該檔案）。

## 方法論摘要（不要更動，除非使用者要求調整假設）

- 觀察方式：每月觀察一次收盤價，12 個月期
- 敲出條件：收盤價 ≥ 買入價 × 105%
- sigma：過去 63 日與 252 日已實現波動率（年化）的平均
- mu_hist：過去 252 個交易日對數報酬年化，截尾於 ±40%
- 機率公式：GBM 反射原理解析公式 + Broadie-Glasserman-Kou 連續性修正（月度離散監控近似）
- 網頁上的「漂移假設」滑桿讓使用者在 0%（零漂移，保守）到 100%（完全採用歷史動能）之間調整，
  即時在瀏覽器端重新計算排序，不需要重新抓資料
