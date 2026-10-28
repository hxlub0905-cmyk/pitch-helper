"""一鍵打包成 exe（`build_exe.bat` → `tools/build_exe.py`）。

真的跑一次 PyInstaller 要一分多鐘、還要先裝 PyInstaller，所以這裡**不打包**。
這裡守的是那幾件「打出來會壞、而症狀要到那台 Windows 上才看得到」的事：

* `.bat` 在 cmd.exe 上讀不讀得動（純 ASCII、沒有 label、區塊裡的 echo 沒有括號）
* `requirements.txt` 在舊版 pip 上讀不讀得動（純 ASCII）
* 資料檔有沒有跟著進 exe（少了圖示不會報錯，只會安靜地沒有）
* exe 的圖示是不是一個真的 `.ico`
* 交給 PyInstaller 的路徑是不是絕對路徑
* 打完包之後那一步「驗兩個圖示」驗得出東西：讀 exe 裡的圖示、`--check-icon`
  開一次程式

⚠ 要畫圖示的那幾條要 Qt（`PySide6.QtSvg`），沒有就 skip。
"""
from __future__ import annotations

import os
import re
import struct
import subprocess
import sys

import pytest

from test_bundle import needs_git  # 同一個資料夾（pytest 把它放進 sys.path）

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tools"))

import build_exe                                       # noqa: E402

BAT = os.path.join(REPO, "build_exe.bat")


def _bat_lines():
    with open(BAT, "rb") as f:
        return f.read().split(b"\n")


def _code_lines():
    """.bat 裡不是 `rem` 註解的那些行（去掉行尾的 CR —— Windows 上 git 可能
    把它換成 CRLF，那在 cmd.exe 上是好的）。"""
    out = []
    for i, raw in enumerate(_bat_lines(), 1):
        line = raw.decode("ascii", "replace").rstrip("\r")
        s = line.strip().lower()
        if s and not (s == "rem" or s.startswith("rem ") or s == "@echo off"):
            out.append((i, line))
    return out


# --------------------------------------------------------------------------- #
# 1. build_exe.bat 在 cmd.exe 上讀得動
# --------------------------------------------------------------------------- #
def test_the_bat_is_pure_ascii():
    """⚠ **一個非 ASCII 位元組都不准有，註解也一樣。**

    cmd.exe 讀 .bat 用的是主控台的 code page（中文 Windows 是 cp950），不是
    UTF-8。中文字會變成亂碼；而且 cp950 是雙位元組編碼，UTF-8 的位元組被重新
    切開之後，緊鄰的 ASCII 字母（例如 `%VENV%` 的一部分）也可能被吃進亂碼裡
    —— 那時候壞的就不只是一句訊息。純 ASCII 在任何 code page 下都是同一份。
    """
    bad = [(i, ln) for i, ln in enumerate(_bat_lines(), 1)
           if any(b > 127 for b in ln)]
    assert not bad, "build_exe.bat 第 %d 行有非 ASCII：%r" % bad[0]


def test_the_bat_uses_no_labels():
    """⚠ **不准用 label、goto、`call :名字`。**

    這個 repo 每個檔案都是 LF 換行（搬運包要求，見 `make_text_bundle.py`），
    而 cmd.exe 在只有 LF 的 .bat 裡**找 label 會出錯** —— 找不到、或跳到錯的
    地方，看 label 剛好落在檔案的哪個位置而定，所以是時有時無的那一種。

    沒有 label 就沒有這個問題，所以流程全部用 `if ( ... )` 區塊寫。`::` 註解
    在 cmd.exe 眼裡也是 label，一起擋掉（註解用 `rem`）。
    """
    offenders = [(i, ln) for i, ln in _code_lines()
                 if ln.strip().startswith(":")
                 or re.search(r"\bgoto\b", ln, re.I)
                 or re.search(r"\bcall\s+:", ln, re.I)]
    assert not offenders, offenders


def test_no_bracket_in_echo_text_inside_a_block():
    """⚠ `( ... )` 區塊裡的 `echo 請看 (這裡)` —— 那個 `)` 會把區塊**提早結束**，
    後面的 `pause`、`exit /b 1` 變成不管成功失敗都會跑。

    錯誤訊息全部在區塊裡（失敗的那條路），也就是最少被跑到的那幾行。
    """
    depth, offenders = 0, []
    for i, ln in _code_lines():
        s = ln.strip()
        if s.lower().startswith("echo"):
            if depth and ("(" in s or ")" in s):
                offenders.append((i, ln))
            continue
        if s == ")":
            depth -= 1
        elif s.endswith("("):
            depth += 1
    assert depth == 0, "build_exe.bat 的括號沒有配對"
    assert not offenders, offenders


def test_the_bat_points_at_files_that_exist():
    """.bat 裡寫死的路徑改名的時候**不會有任何東西報錯** —— 直到有人在
    Windows 上雙擊它。"""
    text = b"\n".join(_bat_lines()).decode("ascii", "replace")
    for rel in ("tools\\build_exe.py", "requirements.txt"):
        assert rel in text
        assert os.path.isfile(os.path.join(REPO, *rel.split("\\"))), rel


def test_pip_can_read_requirements_txt_on_a_chinese_windows():
    """⚠ **`requirements.txt` 一個非 ASCII 位元組都不准有，註解也一樣。**

    2026-10-28 使用者雙擊 `build_exe.bat`，停在 `[3/4]`：
    `UnicodeDecodeError: 'cp950' codec can't decode byte 0xe2 in position 22`。
    0xe2 是註解裡那個 `⚠` 的第一個 byte。新建的 Python 3.9 venv 帶的是舊版
    pip，它讀 requirements 檔用的是主控台的 code page（cp950），不是 UTF-8
    （新版 pip 才改成先試 UTF-8）。本機用 pip 21.3.1 重現過同一個 byte、同一個
    位置。

    兩道防線，都要在：檔案本身是 ASCII；.bat 叫 pip 時加 `-X utf8`（UTF-8
    模式，舊版 pip 也照 UTF-8 讀）——那是給「有人在記事本裡加了一行中文註解」
    的那一天。
    """
    with open(os.path.join(REPO, "requirements.txt"), "rb") as f:
        lines = f.read().split(b"\n")
    bad = [(i, ln) for i, ln in enumerate(lines, 1) if any(b > 127 for b in ln)]
    assert not bad, "requirements.txt 第 %d 行有非 ASCII：%r" % bad[0]

    pip_lines = [ln for _i, ln in _code_lines() if "-m pip install" in ln]
    assert pip_lines, "build_exe.bat 不再用 pip 裝 requirements.txt 了？"
    for ln in pip_lines:
        assert "-X utf8 -m pip" in ln, ln


# --------------------------------------------------------------------------- #
# 2. 交給 PyInstaller 的東西
# --------------------------------------------------------------------------- #
@needs_git
def test_every_data_file_in_the_package_goes_into_the_exe():
    """`pitchapp/` 底下每一個 git 追蹤的非 `.py` 檔案都要在 `--add-data` 裡，
    而且落在 exe 裡的**同一個相對位置**（程式用 `dirname(__file__)` 找它們）。

    ⚠ 少一個**不會報錯**：`branding.pitch_icon()` 找不到檔案時回空圖示（刻意
    的，見那一支的說明），所以 exe 只會安靜地沒有圖示。對照組用 `git ls-files`
    而不是再掃一次磁碟 —— 同一種掃法拿來對照自己，什麼都證明不了。
    """
    out = subprocess.run(["git", "ls-files", "pitchapp"], cwd=REPO, check=True,
                         stdout=subprocess.PIPE).stdout.decode("utf-8")
    want = {p for p in out.split("\n") if p and not p.endswith(".py")}
    assert "pitchapp/ui/assets/pitch.svg" in want

    packed = build_exe.data_files(REPO)
    got = {os.path.relpath(src, REPO).replace(os.sep, "/") for src, _ in packed}
    assert want <= got, sorted(want - got)
    for src, dest in packed:
        assert not src.endswith(".py"), src
        assert dest == os.path.relpath(os.path.dirname(src),
                                       REPO).replace(os.sep, "/")


def test_every_path_handed_to_pyinstaller_is_absolute():
    """⚠ spec 檔放在 `build/`，而 PyInstaller 把 `--add-data`／`--icon` 的相對
    路徑原封不動寫進 spec，再解成**相對於 spec 所在的資料夾** —— 給它
    `pitchapp/ui/assets/pitch.svg` 的話，它會去找 `build/pitchapp/ui/assets/…`
    然後停在 `Unable to find`。（進入點它會自己換算，但一起給絕對路徑最省事。）"""
    icon = os.path.join(REPO, "build", "x.ico")
    args = build_exe.pyinstaller_args(REPO, icon=icon)
    assert os.path.isabs(args[-1]) and os.path.isfile(args[-1]), args[-1]
    for flag in ("--distpath", "--workpath", "--specpath", "--icon"):
        assert os.path.isabs(args[args.index(flag) + 1]), flag

    datas = [args[i + 1] for i, a in enumerate(args) if a == "--add-data"]
    assert datas
    for d in datas:
        src, dest = d.rsplit(os.pathsep, 1)
        assert os.path.isabs(src) and os.path.isfile(src), d
        assert not os.path.isabs(dest), d


def test_upx_is_off_whatever_the_mode():
    """UPX 壓過的 Qt DLL 常常載不起來，而 PATH 上剛好有 UPX 的時候 PyInstaller
    會自己去用它 —— 所以兩種模式都要明講 `--noupx`。"""
    one = build_exe.pyinstaller_args(REPO)
    folder = build_exe.pyinstaller_args(REPO, onedir=True, console=True)
    assert {"--onefile", "--windowed", "--noupx"} <= set(one)
    assert {"--onedir", "--console", "--noupx"} <= set(folder)


# --------------------------------------------------------------------------- #
# 3. exe 的圖示
# --------------------------------------------------------------------------- #
def test_the_exe_icon_is_a_real_multi_size_ico(tmp_path):
    """`pitch.svg` → `.ico`：每一個尺寸都是一張**畫了東西**的 PNG。

    Windows 不會因為 `.ico` 壞掉而拒絕 exe —— 它只會顯示一個預設圖示，而那要到
    有人在檔案總管裡看到的時候才會被發現。
    """
    pytest.importorskip("PySide6.QtSvg", exc_type=ImportError)
    cv2 = pytest.importorskip("cv2")
    import numpy as np

    ico = tmp_path / "PitchHelper.ico"
    build_exe.make_icon(os.path.join(REPO, build_exe.ICON_SVG), str(ico))
    data = ico.read_bytes()
    assert struct.unpack_from("<HHH", data) == (0, 1,
                                                len(build_exe.ICON_SIZES))
    end = 0
    for i, size in enumerate(build_exe.ICON_SIZES):
        w, h, _c, _r, planes, bpp, length, off = struct.unpack_from(
            "<BBBBHHII", data, 6 + 16 * i)
        assert (w or 256, h or 256, planes, bpp) == (size, size, 1, 32)
        png = data[off:off + length]
        assert png[:8] == b"\x89PNG\r\n\x1a\n"
        rgba = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_UNCHANGED)
        assert rgba.shape == (size, size, 4)
        alpha = rgba[:, :, 3]
        assert alpha.max() == 255, "%dpx 是空白的" % size
        assert alpha[0, 0] == 0, "%dpx 的角落不是透明的" % size
        end = off + length
    assert end == len(data)


# --------------------------------------------------------------------------- #
# 4. 打完包之後驗兩個圖示（`build_exe.exe_problem`）
# --------------------------------------------------------------------------- #
def _windows_exes_with_an_icon():
    """手邊一定有、而且**帶圖示**的真 Windows 執行檔：pip 自己帶的 launcher
    （`pip/_vendor/distlib/*.exe`，每個平台的 pip 都有）。拿真的 exe 而不是
    自己拼一個：自己拼的跟 `exe_icon` 用同一套理解，錯了也會對得上。"""
    import pip
    folder = os.path.join(os.path.dirname(pip.__file__), "_vendor", "distlib")
    if not os.path.isdir(folder):
        return []
    return sorted(os.path.join(folder, n) for n in os.listdir(folder)
                  if n.endswith(".exe"))


def test_the_icon_inside_a_real_windows_exe_can_be_read():
    exes = _windows_exes_with_an_icon()
    if not exes:
        pytest.skip("這個 pip 沒帶 distlib 的 launcher")
    for exe in exes:
        got = build_exe.exe_icon(exe)
        assert got, exe
        for size, length in got:
            assert size in (16, 24, 32, 48, 64, 128, 256), (exe, got)
            assert length > 0, (exe, got)

    # 不是 Windows 執行檔：回 None（不是 []，那代表「是 exe 但沒有圖示」）
    assert build_exe.exe_icon(os.path.join(REPO, "main.py")) is None


def test_an_exe_with_someone_elses_icon_does_not_pass(tmp_path):
    """⚠ 沒給 `--icon` 的時候 PyInstaller 放的是**它自己的**預設圖示 —— exe
    「有圖示」不代表是我們的，所以比的是每一張的邊長與位元組數。"""
    exes = _windows_exes_with_an_icon()
    if not exes:
        pytest.skip("這個 pip 沒帶 distlib 的 launcher")
    ico = tmp_path / "ours.ico"
    ico.write_bytes(build_exe.ico_bytes([(16, b"x" * 40), (256, b"y" * 90)]))
    assert build_exe.ico_icon(str(ico)) == [(16, 40), (256, 90)]

    problem = build_exe.exe_problem(exes[0], str(ico),
                                    str(tmp_path / "answer.txt"))
    assert problem and "is not the one made from" in problem, problem
    assert not (tmp_path / "answer.txt").exists(), "沒過第一關就不該去開 exe"


def test_a_program_that_cannot_start_does_not_pass(tmp_path):
    """exe 開不起來（少 DLL、一 import 就當）的時候不會留下答案 —— 那要說成
    「開不起來」，不是「沒有圖示」。"""
    answer = str(tmp_path / "answer.txt")
    problem = build_exe.runtime_icon_problem(
        [sys.executable, "-c", "import sys; sys.exit(3)"], answer)
    assert problem and "code 3" in problem and "does not start" in problem

    problem = build_exe.runtime_icon_problem(
        [sys.executable, "-c", "import time; time.sleep(30)"], answer,
        timeout=0.5)
    assert problem and "did not answer within" in problem, problem


@pytest.fixture
def qapp():
    pytest.importorskip("PySide6.QtSvg", exc_type=ImportError)
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_the_window_icon_check_asks_the_real_program(tmp_path, monkeypatch,
                                                     qapp):
    """`build_exe` 打完包用 `PitchHelper.exe --check-icon <檔案>` 問 exe 本人；
    這裡問的是 `python main.py` —— 同一個進入點、同一個旗標。

    ⚠ 旗標在兩邊各寫了一次（`build_exe.CHECK_ICON_FLAG` 與
    `pitch_helper.CHECK_ICON_FLAG`，前者不能 import 後者：那會把整個 UI 拉進
    打包工具），**這一條就是對兩邊的那一道**：對不上的話程式會當成一般啟動、
    開出視窗，然後停在 timeout。
    """
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    answer = tmp_path / "answer.txt"
    problem = build_exe.runtime_icon_problem(
        [sys.executable, os.path.join(REPO, build_exe.ENTRY)], str(answer),
        timeout=60)
    assert problem is None, problem
    assert answer.read_text(encoding="utf-8") == "ok\n"


def test_the_window_icon_check_says_why(tmp_path, monkeypatch, qapp):
    """圖示畫不出來的三種樣子，各自要有**看得懂的理由**（打包的人只看得到
    那一句）。"""
    from pitchapp.ui import branding

    monkeypatch.setattr(branding, "PITCH_ICON_PATH", str(tmp_path / "no.svg"))
    assert "is not there" in branding.icon_problem()

    broken = tmp_path / "broken.svg"
    broken.write_text("not an svg\n", encoding="utf-8")
    monkeypatch.setattr(branding, "PITCH_ICON_PATH", str(broken))
    assert "draws nothing" in branding.icon_problem()

    blank = tmp_path / "blank.svg"
    blank.write_text('<svg xmlns="http://www.w3.org/2000/svg" '
                     'viewBox="0 0 8 8" width="8" height="8"/>\n',
                     encoding="utf-8")
    monkeypatch.setattr(branding, "PITCH_ICON_PATH", str(blank))
    assert "see-through" in branding.icon_problem()

    monkeypatch.undo()
    assert branding.icon_problem() is None
