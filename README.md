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

## 打包成 exe（給沒有 Python 的電腦）

**Windows 上雙擊 `build_exe.bat`**，等幾分鐘：

```
dist\PitchHelper\PitchHelper.exe                      ← 雙擊就能用
dist\PitchHelper-<版本>-<commit>-windows-x64.zip     ← 要帶走就帶這一個
```

到別台電腦：解開 zip → 雙擊 `PitchHelper\PitchHelper.exe`。那台電腦**不需要
Python**。⚠ 整個資料夾要一起帶走，只拿 `PitchHelper.exe` 是跑不起來的。

打包這台只需要 Python 3.9 以上（安裝時勾「Add python.exe to PATH」）與網路；
PyInstaller 與相依套件它會自己裝進 `.venv-build\`，不動你平常的 Python。
打完會**在打出來的 exe 裡跑一次自我檢查**（`PitchHelper.exe --self-test`），
沒過就不算成功 —— 打包成功不等於 exe 跑得起來。

```
build_exe.bat --onefile        單一 exe（每次啟動慢幾秒，也比較容易被防毒擋）
build_exe.bat --fresh          打包環境砍掉重裝
build_exe.bat --wheels D:\w    離線：只從這個資料夾裝套件
```

原理與每個選項的理由在 [`tools/build_exe.py`](tools/build_exe.py) 的檔頭。
**沒有 Windows 可用**的話：GitHub 上 Actions → `build-exe` → Run workflow，
跑完在那一次 run 的 Artifacts 下載 —— 雲端跑的就是同一個 `build_exe.bat`。

## 拿不到 git 的機器（廠內）

廠內那台機器不能跑 git、也不能下載任何東西，但**看得到 GitHub 上的檔案並且
可以複製**。所以整個 repo 另外打成**一個純文字 `.py`**：

1. 在 GitHub 上開 [`bundle/pitch_helper_bundle.py`](bundle/pitch_helper_bundle.py)，按複製鈕（或複製 raw）
2. 貼進記事本，存成 `pitch_helper_bundle.py`
3. `python pitch_helper_bundle.py` —— 它會解到 `.\pitch-helper\`
   （`--dest` 換地方、`--list` 只看內容不寫檔）

⚠ **整個包是純 ASCII**，一個非 ASCII 位元組都沒有。理由是來源專案踩過的：
中文 Windows 的記事本存檔是 ANSI（cp950），包裡只要有中文就會被存壞，而
Python 讀不動整個檔案（`SyntaxError: Non-UTF-8 code starting with '\xe5'`）。
每個檔案都帶著 git blob SHA，對不上就**一個檔案都不寫**。

更新的時候不必整包重來：`tools/FILELIST.txt` 是全部檔案的 SHA（幾 KB），
複製它再跑 `python tools/check_files.py`，它會說剩下要複製哪幾個。

**改完程式碼之後（在有 git 的機器上）要重產那兩個檔案**：

```bash
git add -A && python tools/release.py && git add -A
```

⚠ `git add` 要在**前面** —— 兩個產出都是從 `git ls-files` 產的，還沒 add 的
新檔案會**安靜地不在裡面**。忘了跑不會有任何症狀，直到廠內那台機器上少一個
檔案；`tests/test_bundle.py` 會在它們過期的時候變紅。

## 授權

專有／內部使用 —— 見 [`LICENSE`](LICENSE)。
