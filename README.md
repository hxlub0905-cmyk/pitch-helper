# Pitch helper

丟一張圖進去，回答它的 **cell period**（重複單元的週期）。

```bash
pip install -r requirements.txt
python main.py                 # 或 python -m pitchapp
```

## 它回答什麼

一張 layout 的影像進去，出來的是 X／Y 兩個方向的週期（像素；填了 pixel size
就多一欄 µm），外加**兩張證據圖**：原圖上的切線，以及所有格子疊在一起的
Golden Cell。疊起來是**銳利**的就表示週期對了，**糊掉**就表示不對 —— 那比
一個分數好懂。

量不準的時候可以自己接手：打一個週期進去（它會用**同一把尺**給你那個週期的
信心分數）、從候選裡挑一個、`×2`、或直接在圖上拉尺規量。

## 這個資料夾是什麼

它是一個**完整、可以單獨跑的程式**，不需要 d4t。

原本它是 [d4t](https://github.com/hxlub0905-cmyk/d4t)（半導體 E-beam
Inspection 的 ADC 工具）裡的一個小工具，2026-09-23 由 `tools/extract_app.py`
抽出來搬家。**這裡是唯一的家** —— d4t 那邊之後會把它移除，所以不會有兩份要對。

`pitchapp/` 底下的目錄形狀跟 d4t 一樣，所以帶過來的程式碼一個字都沒改
（只有 `import d4t.…` 換成 `import pitchapp.…`）：

* `pitchapp/core/` — 純運算，**不 import Qt**
* `pitchapp/ui/` — 視窗與元件

共 28 支模組。原始碼裡那些 `# Vendored into d4t on …` 的檔頭是**來源的履歷**，
不要清掉。

## 開發

```bash
pip install -r requirements.txt && pip install pytest ruff==0.15.8
ruff check                                        # 幾秒，先跑這個
QT_QPA_PLATFORM=offscreen python -m pytest -q     # 141 條（Windows 不用設）
```

⚠ **動版面之前先讀 [`docs/F120-pitch-helper.md`](docs/F120-pitch-helper.md)**
—— 那是這個工具十七輪的決策紀錄，裡面每一條都是**量出來的**不是挑的。不讀它
就會把已經試過而且被否決的東西再做一次。

沿用的規矩（來自 d4t，每一條都有測試守著或有故事）：

* `core/` 不 import Qt —— UI 只透過 callback 與它互動
* **Python 3.9 相容語法**（廠內機器可能是舊版）
* 檔案寫入一律 atomic（`.tmp` + `os.replace`）
* `except` 之後不 raise **不等於**不記 —— 前面要先 `core.log.swallowed("模組.函式")`
* 會跳 modal 對話框的東西要有一個關得掉的旗標，否則 headless 測試會永遠停在那裡
* 不要寫死視窗尺寸，用 `ui/fit_screen.fit()`
* **同一件事只寫在一個地方** —— 抄出來的第二份一定會漂

## 授權

專有／內部使用 —— 見 [`LICENSE`](LICENSE)。
