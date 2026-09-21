# 美股 105% 敲出機率排行 (Knockout Tracker)

針對新加坡銀行常見結構式票據（FCN/ELN 類）：每月觀察一次收盤價、12 個月期，
估算 S&P500 + Nasdaq100（合併去重）每檔股票觸及 105% 敲出的機率，每天自動更新。

方法論見 `PLAYBOOK.md` 與 `scripts/gh_fetch_and_build.py` 內的註解：GBM 反射原理
解析公式 + Broadie-Glasserman-Kou 連續性修正，波動率取 63 日/252 日已實現波動平均，
歷史漂移取過去一年對數報酬年化並截尾 ±40%。**僅供研究參考，不構成投資建議。**

## 部署到 GitHub Pages（全自動，不需要您的電腦介入）

1. 在 GitHub 建立一個新的 **public** repo（例如 `knockout-tracker`）。
2. 在這個資料夾（`knockout-tracker/`）執行：
   ```bash
   git remote add origin https://github.com/<你的帳號>/<repo名稱>.git
   git branch -M main   # 若失敗可省略，直接 push 目前分支(master)也可以
   git push -u origin main
   ```
3. 到 repo 的 **Settings → Pages**，Source 選擇 **GitHub Actions**。
4. 到 **Settings → Actions → General**，確認 Workflow permissions 是
   **Read and write permissions**（讓 workflow 可以把每日資料快照 commit 回 repo）。
5. 到 **Actions** 分頁，手動觸發一次 `Update knockout tracker`（workflow_dispatch）
   確認能成功跑完；之後每天 UTC 00:00（北京時間 08:00）會自動執行。
6. 完成後網站網址會是 `https://<你的帳號>.github.io/<repo名稱>/`。

## 本機測試

```bash
pip install -r requirements.txt
python scripts/gh_fetch_and_build.py
open site/index.html
```

## 檔案結構

```
data/latest.json          今天的完整資料（含失敗清單）
site/index.html           獨立網頁（資料內嵌，GitHub Pages 發布這個資料夾）
site/data.json            給網頁用的精簡資料
scripts/gh_fetch_and_build.py   GitHub Actions 用的抓資料+算機率+產生網頁腳本
scripts/template.html     網頁樣板（gh_fetch_and_build.py 會把資料塞進去）
.github/workflows/daily.yml     每日排程 workflow
```
