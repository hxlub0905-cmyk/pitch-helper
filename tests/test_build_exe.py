"""一鍵打包（`build_exe.bat` → `tools/build_exe.py`）與打包後的自我檢查。

真的打一次包要好幾分鐘、還要 PyInstaller，所以這裡**不打包** —— 測的是打包
之前就決定好的那幾件事（帶哪些檔案、給 PyInstaller 什麼參數、圖示長什麼樣），
以及 `--self-test` 本人。打出來的 exe 真的跑不跑得起來，由 `build_exe.py` 自己
在打完之後問那個 exe（第 4 步），以及 `.github/workflows/build-exe.yml`。
"""
from __future__ import annotations

import os
import re
import struct
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tools"))

import build_exe                                       # noqa: E402

BAT = os.path.join(REPO, "build_exe.bat")
WORKFLOW = os.path.join(REPO, ".github", "workflows", "build-exe.yml")


# --------------------------------------------------------------------------- #
# 1. 雙擊的那一個檔案
# --------------------------------------------------------------------------- #
def test_the_bat_survives_notepad_and_the_bundle():
    """`build_exe.bat` 要**純 ASCII、LF、沒有 label**。

    * ASCII：中文 Windows 的記事本存成 cp950，而 cmd.exe 用 OEM code page 讀
      批次檔 —— 裡面有中文就會變成一堆亂碼指令。
    * LF：`tools/make_text_bundle.py` 拒收含 CR 的檔案（以行為單位的打包會
      弄壞它），所以這個檔案在 repo 裡只能是 LF。
    * 沒有 label／goto：cmd.exe 在 LF 結尾的批次檔裡**找 label 會找錯位置**
      （跨 512 位元組邊界時），而那種錯只會在某些長度下出現。一般的行與
      `( )` 區塊不受影響。
    """
    data = open(BAT, "rb").read()
    assert b"\r" not in data
    bad = [i for i, b in enumerate(data) if b > 0x7F]
    assert not bad, "非 ASCII 在第 %d 個位元組" % bad[0]
    lines = [ln.strip().lower() for ln in data.decode("ascii").split("\n")]
    code = [ln for ln in lines if ln and not ln.startswith("rem")]
    assert not [ln for ln in code if ln.startswith(":")], "有 label"
    assert not [ln for ln in code if re.search(r"\b(goto|call\s+:)", ln)], "有 goto"
    assert any("tools\\build_exe.py %*" in ln for ln in code)


def test_the_bat_does_not_wait_for_a_key_in_ci():
    """雙擊的人要 `pause`（不然視窗一閃就關），CI 不能有（沒有人會按）。"""
    text = open(BAT, encoding="ascii").read()
    pauses = [ln.strip() for ln in text.split("\n")
              if ln.strip().endswith("pause")]
    assert pauses and all(ln == "if not defined CI pause" for ln in pauses)


# --------------------------------------------------------------------------- #
# 2. 帶什麼進去、給 PyInstaller 什麼
# --------------------------------------------------------------------------- #
def test_requirements_follow_the_file_with_opencv_headless(tmp_path):
    """打包環境**跟著 requirements.txt 走**（不是另一份清單），只換 opencv。"""
    (tmp_path / "requirements.txt").write_text(
        "# comment\nnumpy>=1.24\nopencv-python>=4.8  # gui half unused\n\n"
        "PySide6\n", encoding="utf-8")
    assert build_exe.build_requirements(str(tmp_path)) == [
        "numpy>=1.24", "opencv-python-headless>=4.8", "PySide6"]

    real = build_exe.build_requirements(REPO)
    assert "opencv-python-headless" in real and "opencv-python" not in real
    assert len(real) == len([ln for ln in open(
        os.path.join(REPO, "requirements.txt"), encoding="utf-8")
        if ln.strip() and not ln.strip().startswith("#")])


def test_every_asset_goes_into_the_exe_and_no_code_does():
    """PyInstaller 看不到執行期才讀的檔案 —— 少帶一個，圖示就安靜地變空白。"""
    files = build_exe.data_files(REPO)
    got = {(os.path.relpath(src, REPO).replace(os.sep, "/"), dest)
           for src, dest in files}
    assets = os.path.join(REPO, "pitchapp", "ui", "assets")
    for fn in os.listdir(assets):
        assert ("pitchapp/ui/assets/" + fn, "pitchapp/ui/assets") in got, fn
    assert not [p for p, _ in got if p.endswith((".py", ".pyc"))]
    assert not [p for p, _ in got if "__pycache__" in p]
    assert os.path.isfile(os.path.join(REPO, *build_exe.ICON_SVG))


def test_pyinstaller_gets_the_entry_the_assets_and_no_console(tmp_path):
    args = build_exe.pyinstaller_args(REPO, onefile=False)
    assert args[0] == os.path.join(REPO, "main.py")
    assert "--windowed" in args and "--onedir" in args and "--onefile" not in args
    assert args[args.index("--name") + 1] == build_exe.NAME
    datas = [args[i + 1] for i, a in enumerate(args) if a == "--add-data"]
    svg = os.path.join(REPO, "pitchapp", "ui", "assets", "pitch.svg")
    assert svg + os.pathsep + "pitchapp/ui/assets" in datas
    assert "--icon" not in args

    one = build_exe.pyinstaller_args(REPO, onefile=True, icon="x.ico")
    assert "--onefile" in one and "--onedir" not in one
    assert one[one.index("--icon") + 1] == "x.ico"


def test_the_zip_name_says_which_build_it_is():
    """解壓之後資料夾只叫 `PitchHelper` —— zip 的檔名是唯一的版本記號。"""
    name = build_exe.zip_name(REPO)
    m = re.match(r"^PitchHelper-(.+)-([0-9a-f]{4,}(?:-dirty)?|nogit)-(\w+)-(\w+)\.zip$",
                 name)
    assert m, name
    try:
        import tomllib
    except ImportError:                                   # 3.9／3.10
        return
    with open(os.path.join(REPO, "pyproject.toml"), "rb") as f:
        assert m.group(1) == tomllib.load(f)["project"]["version"]


def test_a_machine_without_git_still_gets_a_name(tmp_path):
    """用 bundle 搬過去的那一台沒有 git —— 檔名照樣產得出來。"""
    assert build_exe.commit_id(str(tmp_path)) == "nogit"


def test_a_missing_wheels_folder_stops_before_touching_anything(tmp_path, capsys):
    """打錯路徑要當場講，而不是先建一個 venv 再讓 pip 講一句看不懂的話。"""
    rc = build_exe.main(["--wheels", str(tmp_path / "nope")])
    assert rc == 2
    assert "nope" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# 3. 圖示
# --------------------------------------------------------------------------- #
def _read_ico(data: bytes):
    reserved, kind, count = struct.unpack_from("<HHH", data, 0)
    assert (reserved, kind) == (0, 1)
    out = []
    for i in range(count):
        w, h, _c, _r, planes, bits, size, off = struct.unpack_from(
            "<BBBBHHII", data, 6 + 16 * i)
        out.append((w or 256, h or 256, planes, bits, data[off:off + size]))
    return out


def test_the_ico_header_points_at_each_png():
    pngs = [(16, b"\x89PNG-a"), (48, b"\x89PNG-bb"), (256, b"\x89PNG-ccc")]
    entries = _read_ico(build_exe.ico_bytes(pngs))
    assert [(w, h, blob) for w, h, _p, _b, blob in entries] == [
        (16, 16, b"\x89PNG-a"), (48, 48, b"\x89PNG-bb"), (256, 256, b"\x89PNG-ccc")]
    assert all(p == 1 and b == 32 for _w, _h, p, b, _blob in entries)


def test_the_icon_is_drawn_from_the_real_svg():
    """真的畫一次：每個尺寸都是一張那麼大、而且不是全透明的 PNG。"""
    pytest.importorskip("PySide6.QtSvg", exc_type=ImportError)
    from PySide6.QtGui import QImage

    svg = os.path.join(REPO, *build_exe.ICON_SVG)
    entries = _read_ico(build_exe.ico_bytes(build_exe.render_pngs(svg, (16, 256))))
    for want, (w, h, _p, _b, png) in zip((16, 256), entries):
        assert png.startswith(b"\x89PNG\r\n\x1a\n")
        img = QImage.fromData(png, "PNG")
        assert (img.width(), img.height(), w, h) == (want, want, want, want)
        assert img.pixelColor(want // 2, want // 2).alpha() > 0


# --------------------------------------------------------------------------- #
# 4. `--self-test`
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("entry", [["main.py"], ["-m", "pitchapp"]])
def test_the_self_test_passes_from_source(tmp_path, entry):
    """兩個入口都認得 `--self-test`（它們必須一字不差，見
    `test_ui_pitch_helper.test_both_entry_points_call_run`），而且在這份原始碼
    上是全過的 —— 不然打包出來的那一份沒過時，分不出是誰壞了。"""
    pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
    pytest.importorskip("cv2")
    report = tmp_path / "report.txt"
    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    r = subprocess.run([sys.executable] + entry + ["--self-test", str(report)],
                       cwd=REPO, env=env, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, timeout=300)
    out = r.stdout.decode("utf-8", "replace")
    assert r.returncode == 0, out
    lines = report.read_text(encoding="utf-8").splitlines()
    assert lines[-1] == "PASS", out
    for name in ("imaging", "measure", "qt", "icon", "window"):
        assert any(ln.startswith("ok   " + name) for ln in lines), name


def test_a_failing_check_is_reported_and_the_rest_still_run(tmp_path, monkeypatch):
    """一段沒過**不擋**下一段（一次看到全部壞在哪），退出碼是 1，報告講名字。"""
    from pitchapp import selftest

    def boom() -> str:
        raise ImportError("DLL load failed")

    monkeypatch.setattr(selftest, "_headless_if_needed", lambda: None)
    monkeypatch.setattr(selftest, "_CHECKS", [
        ("first", lambda: "fine"), ("broken", boom), ("after", lambda: "ran")])
    report = tmp_path / "r.txt"
    assert selftest.run(str(report)) == 1
    text = report.read_text(encoding="utf-8")
    assert "ok   first" in text and "ok   after" in text
    assert "FAIL broken   ImportError: DLL load failed" in text
    assert "Traceback" in text
    assert text.rstrip().splitlines()[-1] == "FAIL"


# --------------------------------------------------------------------------- #
# 5. 雲端那一條路與說明
# --------------------------------------------------------------------------- #
def test_the_cloud_build_runs_the_same_bat():
    """GitHub 上的 `build-exe` 跑的是**同一個** `build_exe.bat` —— 另外寫一份步驟
    會漂，而漂掉的那一份正是沒有人在本機雙擊過的那一份。

    它的 `paths:` 要蓋住打包會用到的每一個檔案，不然改了其中一個不會觸發
    一次 Windows 上的打包，而壞掉的症狀只看得到在 Windows 上。
    """
    text = open(WORKFLOW, encoding="utf-8").read()
    assert re.search(r"^\s*run:\s*build_exe\.bat\s*$", text, re.M)
    for path in ("build_exe.bat", "tools/build_exe.py", "pitchapp/selftest.py",
                 "main.py", "requirements.txt", ".github/workflows/build-exe.yml"):
        assert "- " + path in text, path


def test_the_one_click_build_is_offered_in_the_readme():
    """一條沒有人知道的路等於沒有。"""
    text = open(os.path.join(REPO, "README.md"), encoding="utf-8").read()
    assert "build_exe.bat" in text and "tools/build_exe.py" in text
