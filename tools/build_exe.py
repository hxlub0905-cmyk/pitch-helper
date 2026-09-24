#!/usr/bin/env python3
# pitch-helper 一鍵打包 exe — authored 2026-09-24.
"""把 pitch-helper 打成**不需要安裝 Python** 的 Windows 程式（PyInstaller）。

**Windows 上雙擊 repo 根目錄的 `build_exe.bat` 就好** —— 它只負責找到 Python
再叫這一支。也可以直接跑：

    python tools/build_exe.py                 # 資料夾版（預設）＋ 一個 zip
    python tools/build_exe.py --onefile       # 單一 exe
    python tools/build_exe.py --fresh         # 打包環境砍掉重裝
    python tools/build_exe.py --wheels D:\\wheels   # 離線：只從這個資料夾裝套件
    python tools/build_exe.py --no-venv       # 用目前這個 Python（要已經裝好一切）

產出都在 `dist/`（已被 .gitignore）：

    dist/PitchHelper/PitchHelper.exe                      資料夾版：整個資料夾一起帶走
    dist/PitchHelper-<版本>-<commit>-windows-x64.zip     同一個資料夾壓成一個檔
    dist/PitchHelper.exe                                  --onefile

它做的事（任何一步失敗都停下來，講是哪一步、接下來怎麼辦）：

0. 建一個**打包專用**的 venv（`.venv-build/`），裝好 requirements.txt 與
   PyInstaller，然後用那個 Python 重跑自己。**不用你平常的 Python 環境是刻意
   的**：PyInstaller 會把環境裡看得到、被 import 到的東西都帶進去，平常的環境
   裝了什麼（以及哪一版）不是這支控制得了的。requirements.txt 沒變就不重裝
   （`.venv-build/.stamp`），所以第二次起快很多。
1. 檢查套件都在。
2. 從 `pitchapp/ui/assets/pitch.svg` 畫出 exe 的圖示（`.ico`）。
3. PyInstaller。
4. **在打出來的 exe 裡跑 `--self-test`**（`pitchapp/selftest.py`）——
   打包成功不等於 exe 跑得起來：少帶一個資料檔、少一個 Qt plugin，打包的時候
   都看不到，要到使用者雙擊的時候才看得到。
5. 資料夾版壓成 zip。

為什麼預設是資料夾版而不是單一 exe
----------------------------------
單一 exe 每次啟動都要先把自己（~200 MB）解到暫存資料夾，要好幾秒；而那種
「自己解壓自己再執行」的行為正是防毒軟體在抓的，公司機上被擋的機會大得多。
資料夾版沒有這兩個問題，要搬的時候搬 zip（一個檔案）。真的要單一 exe 就
`--onefile`。

⚠ **PyInstaller 不能跨平台打包**：Windows 上打出 .exe，Linux／macOS 上打出
那個平台的執行檔。沒有 Windows 可以用的話，GitHub 上的 `build-exe` workflow
會在雲端的 Windows 上跑**同一個** `build_exe.bat`（見 README）。
"""
from __future__ import annotations

import argparse
import hashlib
import os
import platform
import re
import shutil
import struct
import subprocess
import sys
import time
import zipfile
from typing import List, Sequence, Tuple

NAME = "PitchHelper"

#: ⚠ 版本釘死，理由同 `pyproject.toml` 的 dev 相依：浮動版本＝某天早上打出來的
#: exe 自己壞了，而沒有人改過任何一行。要升級就改這一行。
PYINSTALLER = "pyinstaller==6.22.3"

#: 打包專用的 venv（repo 根目錄底下；以 `.` 開頭，pytest 不會掃進去）。
VENV = ".venv-build"

#: 打包用 venv 裡的套件換名 —— 只有這一個。`opencv-python` 帶著 GUI 那一半
#: （highgui；Linux 版還自帶一套 Qt5，會跟 PySide6 搶 plugin），而這個 app 一個
#: highgui 函式都沒用：讀寫影像全走 imdecode/imencode（`core/ingest/imageio.py`）。
#: `.github/workflows/ci.yml` 做的是同一件事。
SWAP = {"opencv-python": "opencv-python-headless"}

#: 打包環境要 import 得到的東西（import 名稱 → 裝不起來時要裝的套件）。
NEEDED = (("PyInstaller", PYINSTALLER), ("PySide6", "PySide6"),
          ("numpy", "numpy"), ("cv2", "opencv-python-headless"))

ICON_SVG = ("pitchapp", "ui", "assets", "pitch.svg")
#: Windows 檔案總管從小圖示到「超大圖示」會用到的尺寸。
ICON_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)

#: 打出來的 exe 自我檢查多久沒結束就算失敗。**要有這條**：`--windowed` 的 exe
#: 在 import 階段就失敗的話會跳一個錯誤對話框，然後一直等人去按。
SELFTEST_TIMEOUT = 300

#: 資料檔裡不帶的東西（其他全部跟著 `pitchapp/` 走，見 :func:`data_files`）。
_SKIP_SUFFIXES = (".py", ".pyc", ".pyo", ".tmp")


class BuildError(Exception):
    """某一步失敗了：``str(e)`` 是發生什麼事，``hint`` 是接下來怎麼辦。"""

    def __init__(self, message: str, hint: str = "", code: int = 1):
        super().__init__(message)
        self.hint = hint
        self.code = code


def say(msg: str = "") -> None:
    print(msg, flush=True)


def repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# --------------------------------------------------------------------------- #
# 純函式（測試直接測這幾支，不必真的打一次包）
# --------------------------------------------------------------------------- #
def build_requirements(root: str) -> List[str]:
    """requirements.txt 的每一行，套上 :data:`SWAP`（版本條件原樣保留）。"""
    out = []
    with open(os.path.join(root, "requirements.txt"), encoding="utf-8") as f:
        for raw in f:
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            name = re.split(r"[<>=!~\[;\s]", line, maxsplit=1)[0]
            if name.lower() in SWAP:
                line = SWAP[name.lower()] + line[len(name):]
            out.append(line)
    return out


def data_files(root: str) -> List[Tuple[str, str]]:
    """`pitchapp/` 底下每一個**不是程式碼**的檔案 → ``(來源, exe 裡的資料夾)``。

    PyInstaller 只看得到 import，執行期才用路徑去讀的檔案（`ui/assets/*.svg`，
    以後的 `ui/locales/*.json`）它不知道 —— 要一個一個告訴它。這裡用**掃的**
    而不是列一張名單：多一個資料檔就自動帶上，不需要有人記得回來改這裡。
    """
    pkg = os.path.join(root, "pitchapp")
    out = []
    for dirpath, dirnames, filenames in os.walk(pkg):
        dirnames[:] = sorted(d for d in dirnames
                             if d != "__pycache__" and not d.startswith("."))
        for fn in sorted(filenames):
            if fn.startswith(".") or fn.endswith(_SKIP_SUFFIXES):
                continue
            src = os.path.join(dirpath, fn)
            dest = os.path.relpath(dirpath, root).replace(os.sep, "/")
            out.append((src, dest))
    return out


def ico_bytes(pngs: Sequence[Tuple[int, bytes]]) -> bytes:
    """幾張正方形 PNG → 一個 `.ico`（Vista 起 ICO 裡可以直接放 PNG）。

    自己寫而不是叫 Pillow：多裝一個套件只為了一個十幾行就寫得完的檔頭，
    而打包環境裝的東西越少越好（見模組說明第 0 步）。
    """
    head = struct.pack("<HHH", 0, 1, len(pngs))       # reserved, type=icon, count
    offset = len(head) + 16 * len(pngs)
    entries, blobs = [], []
    for size, png in pngs:
        dim = 0 if size >= 256 else size             # 256 在檔頭裡寫成 0
        entries.append(struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32,
                                   len(png), offset))
        blobs.append(png)
        offset += len(png)
    return head + b"".join(entries) + b"".join(blobs)


def project_version(root: str) -> str:
    """`pyproject.toml` 的 ``version``（3.9 沒有 tomllib，而這一行夠用）。"""
    try:
        with open(os.path.join(root, "pyproject.toml"), encoding="utf-8") as f:
            m = re.search(r'^version\s*=\s*"([^"]+)"', f.read(), re.M)
    except OSError:
        return "0"
    return m.group(1) if m else "0"


def commit_id(root: str) -> str:
    """``git rev-parse --short HEAD``（有沒 commit 的改動就加 ``-dirty``）。

    沒有 git 的機器（用 bundle 搬過去的那一台）回 ``nogit``。
    """
    try:
        sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=root,
                             check=True, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL).stdout.decode().strip()
        dirty = subprocess.run(["git", "status", "--porcelain",
                                "--untracked-files=no"], cwd=root, check=True,
                               stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "nogit"
    return sha + ("-dirty" if dirty else "") if sha else "nogit"


def platform_tag() -> str:
    osname = {"win32": "windows", "darwin": "macos"}.get(sys.platform, sys.platform)
    arch = platform.machine().lower()
    arch = {"amd64": "x64", "x86_64": "x64", "aarch64": "arm64"}.get(arch, arch)
    return "%s-%s" % (osname, arch or "unknown")


def zip_name(root: str) -> str:
    """zip 的檔名帶著版本、commit、平台 —— 解壓之後資料夾只叫 `PitchHelper`，
    這個檔名是「這是哪一版」唯一的記號。"""
    return "%s-%s-%s-%s.zip" % (NAME, project_version(root), commit_id(root),
                                platform_tag())


def exe_path(root: str, onefile: bool) -> str:
    exe = NAME + (".exe" if sys.platform == "win32" else "")
    dist = os.path.join(root, "dist")
    return os.path.join(dist, exe) if onefile else os.path.join(dist, NAME, exe)


def pyinstaller_args(root: str, onefile: bool, icon: str = "") -> List[str]:
    """PyInstaller 的參數 —— 打包的設定**全部**在這裡，沒有另外一份 .spec。"""
    work = os.path.join(root, "build", "pyinstaller")
    args = [os.path.join(root, "main.py"),
            "--name", NAME,
            "--windowed",                     # 不開黑色的命令列視窗
            "--onefile" if onefile else "--onedir",
            "--noconfirm", "--clean",
            "--log-level", "WARN",            # 它的 INFO 有幾千行，會把我們的訊息淹掉
            "--distpath", os.path.join(root, "dist"),
            "--workpath", work,
            "--specpath", work]
    if icon:
        args += ["--icon", icon]
    for src, dest in data_files(root):
        args += ["--add-data", "%s%s%s" % (src, os.pathsep, dest)]
    return args


# --------------------------------------------------------------------------- #
# 第 0 步：打包專用的 venv
# --------------------------------------------------------------------------- #
def venv_python(venv_dir: str) -> str:
    if sys.platform == "win32":
        return os.path.join(venv_dir, "Scripts", "python.exe")
    return os.path.join(venv_dir, "bin", "python")


def _runs(py: str) -> bool:
    try:
        return subprocess.run([py, "-c", "import sys"],
                              stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL).returncode == 0
    except OSError:
        return False


def _stamp(packages: Sequence[str], wheels: str) -> str:
    """「這個 venv 裝的是哪一份清單」—— 清單一變就重裝。"""
    key = "\n".join(list(packages) + [PYINSTALLER, "wheels=" + wheels])
    return hashlib.sha1(key.encode("utf-8")).hexdigest()


def ensure_venv(root: str, wheels: str = "", fresh: bool = False) -> str:
    """建好（或沿用）`.venv-build`，回它的 python。"""
    venv_dir = os.path.join(root, VENV)
    py = venv_python(venv_dir)
    if fresh and os.path.isdir(venv_dir):
        say("  刪掉舊的 %s（--fresh）" % VENV)
        shutil.rmtree(venv_dir)
    if os.path.isdir(venv_dir) and not _runs(py):
        # 建它的那個 Python 被升級或移除之後，venv 會變成一個空殼
        say("  %s 壞了（建它的 Python 可能被升級或移除了），重建" % VENV)
        shutil.rmtree(venv_dir)
    if not os.path.isdir(venv_dir):
        say("  建立 %s（用 %s，Python %s）"
            % (VENV, sys.executable, platform.python_version()))
        r = subprocess.run([sys.executable, "-m", "venv", venv_dir])
        if r.returncode != 0 or not _runs(py):
            raise BuildError(
                "建不出 venv（%s）" % venv_dir,
                "Debian／Ubuntu 要先 `apt install python3-venv`；或者在一個已經裝好"
                "相依套件與 %s 的 Python 上加 --no-venv。" % PYINSTALLER, code=2)

    packages = build_requirements(root)
    stamp_path = os.path.join(venv_dir, ".stamp")
    want = _stamp(packages, wheels)
    try:
        with open(stamp_path, encoding="utf-8") as f:
            if f.read().strip() == want:
                say("  套件已經裝好了（requirements.txt 沒變就不重裝；要重裝加 --fresh）")
                return py
    except OSError:
        pass

    pip = [py, "-m", "pip", "install", "--disable-pip-version-check"]
    if wheels:
        pip += ["--no-index", "--find-links", wheels]
    else:
        # 舊版 Python 帶的舊 pip 認不得新的 wheel 標籤；升級失敗不算錯。
        subprocess.run(pip + ["--quiet", "--upgrade", "pip"])
    say("  安裝：%s" % ", ".join(packages + [PYINSTALLER]))
    r = subprocess.run(pip + packages + [PYINSTALLER])
    if r.returncode != 0:
        raise BuildError(
            "裝不起來（pip 的訊息在上面）",
            ("--wheels 那個資料夾裡要有全部的 wheel（包括它們的相依套件）。"
             if wheels else
             "連不上網路的話，用 --wheels 指向一個放好 wheel 的資料夾。"), code=2)
    tmp = stamp_path + ".tmp"                         # atomic（鐵則 5）
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(want + "\n")
    os.replace(tmp, stamp_path)
    return py


# --------------------------------------------------------------------------- #
# 第 1–5 步（在打包用的 Python 裡跑）
# --------------------------------------------------------------------------- #
def check_env() -> None:
    import importlib.util
    missing = [pkg for mod, pkg in NEEDED if importlib.util.find_spec(mod) is None]
    if missing:
        raise BuildError(
            "這個 Python（%s）少了：%s" % (sys.executable, ", ".join(missing)),
            "拿掉 --no-venv 讓它自己建一個打包環境；或者先 "
            "`python -m pip install %s -r requirements.txt`。" % PYINSTALLER, code=2)
    import PyInstaller
    say("  Python %s（%s）" % (platform.python_version(), sys.executable))
    say("  PyInstaller %s" % PyInstaller.__version__)
    if PyInstaller.__version__ != PYINSTALLER.split("==")[1]:
        say("  ⚠ 釘的是 %s；用別的版本打出來的東西沒有被驗過" % PYINSTALLER)


def render_pngs(svg_path: str, sizes: Sequence[int]) -> List[Tuple[int, bytes]]:
    """用 Qt 把 SVG 畫成幾個尺寸的 PNG（PySide6 本來就要裝，不多一個套件）。"""
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
    from PySide6.QtGui import QGuiApplication, QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer

    if QGuiApplication.instance() is None:
        # 不開任何視窗，所以原生的 platform plugin 就夠了 —— 它一定在（app 自己
        # 就靠它）。只有 Linux 沒有 display 的時候改 offscreen：找不到 plugin
        # 在 Qt 裡是 abort()，try/except 接不住，會連整個打包一起帶走。
        # 那個設定只給這個行程用，建完就還原：之後啟動的子行程（PyInstaller、
        # 打出來的 exe）要看到原本的環境。
        saved = os.environ.get("QT_QPA_PLATFORM")
        if sys.platform.startswith("linux") and not (
                os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
            os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            _KEEP.append(QGuiApplication([sys.argv[0]]))
        finally:
            if saved is None:
                os.environ.pop("QT_QPA_PLATFORM", None)
            else:
                os.environ["QT_QPA_PLATFORM"] = saved

    renderer = QSvgRenderer(svg_path)
    if not renderer.isValid():
        raise RuntimeError("Qt cannot read %s" % svg_path)
    out = []
    for n in sizes:
        img = QImage(n, n, QImage.Format.Format_ARGB32)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        renderer.render(p)
        p.end()
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        img.save(buf, "PNG")
        buf.close()
        out.append((n, bytes(ba.data())))
    return out


#: 讓 :func:`render_pngs` 建的 QGuiApplication 活到行程結束。
_KEEP: list = []


def make_icon(root: str) -> str:
    """畫出 exe 的圖示，回 `.ico` 的路徑；畫不出來回 ``""``（不擋打包）。

    只有 Windows 的 exe 吃 `.ico`。少一顆圖示是外觀問題，不值得讓整個打包失敗 ——
    視窗裡的圖示本來就是執行期從 SVG 讀的（`ui/branding.py`），不受影響。
    """
    if sys.platform != "win32":
        say("  跳過（只有 Windows 的 exe 帶圖示）")
        return ""
    svg = os.path.join(root, *ICON_SVG)
    ico = os.path.join(root, "build", "pyinstaller", NAME + ".ico")
    try:
        data = ico_bytes(render_pngs(svg, ICON_SIZES))
        os.makedirs(os.path.dirname(ico), exist_ok=True)
        with open(ico + ".tmp", "wb") as f:
            f.write(data)
        os.replace(ico + ".tmp", ico)
    except Exception as e:
        say("  ⚠ 畫不出圖示（%s: %s），exe 會用 PyInstaller 預設的圖示"
            % (type(e).__name__, e))
        return ""
    say("  %s（%d 個尺寸）" % (os.path.relpath(ico, root), len(ICON_SIZES)))
    return ico


def run_pyinstaller(root: str, onefile: bool, icon: str) -> str:
    cmd = [sys.executable, "-m", "PyInstaller"] + pyinstaller_args(root, onefile, icon)
    r = subprocess.run(cmd, cwd=root)
    exe = exe_path(root, onefile)
    if r.returncode != 0 or not os.path.isfile(exe):
        raise BuildError(
            "PyInstaller 失敗（它的訊息在上面）",
            "最常見的原因是舊的 %s 還開著，或防毒軟體鎖住了 dist\\ 裡的檔案 —— "
            "關掉它再跑一次；還是不行就加 --fresh。" % os.path.basename(exe))
    return exe


def self_test(root: str, exe: str) -> None:
    """在打出來的 exe 裡跑 ``--self-test``（見 `pitchapp/selftest.py`）。"""
    report = os.path.join(root, "build", "pyinstaller", "selftest.txt")
    if os.path.exists(report):
        os.remove(report)
    try:
        r = subprocess.run([exe, "--self-test", report], cwd=os.path.dirname(exe),
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           timeout=SELFTEST_TIMEOUT)
        code, output = r.returncode, r.stdout
    except subprocess.TimeoutExpired as e:
        code, output = None, e.output or b""
    text = ""
    if os.path.isfile(report):
        with open(report, encoding="utf-8") as f:
            text = f.read()
        for line in text.rstrip().splitlines():
            say("  " + line)
    lines = text.rstrip().splitlines()
    if code == 0 and lines and lines[-1] == "PASS":
        return
    if not text and output:
        # 連報告都沒寫出來 ＝ 死在 import 階段；那時候唯一的線索是它印的東西
        for line in output.decode("utf-8", "replace").rstrip().splitlines()[-25:]:
            say("  | " + line)
    if code is None:
        raise BuildError(
            "打出來的 exe 在 %d 秒內沒有結束" % SELFTEST_TIMEOUT,
            "最常見的原因是它在一開始就失敗、跳了一個錯誤對話框在等人按。"
            "直接雙擊 %s 看它說什麼。" % exe)
    raise BuildError(
        "打出來的 exe 沒通過自我檢查（退出碼 %s）" % code,
        "上面寫著是哪一段沒過。exe 還在 %s，可以直接雙擊看看。" % exe)


def make_zip(root: str, folder: str) -> str:
    """資料夾版壓成一個 zip（裡面的最上層是 `PitchHelper/`）。"""
    path = os.path.join(root, "dist", zip_name(root))
    base = os.path.dirname(folder)
    tmp = path + ".tmp"                               # atomic（鐵則 5）
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for dirpath, dirnames, filenames in os.walk(folder):
            dirnames.sort()
            for fn in sorted(filenames):
                full = os.path.join(dirpath, fn)
                z.write(full, os.path.relpath(full, base))
    os.replace(tmp, path)
    return path


def _mb(path: str) -> str:
    if os.path.isdir(path):
        n = sum(os.path.getsize(os.path.join(d, f))
                for d, _, fs in os.walk(path) for f in fs)
    else:
        n = os.path.getsize(path)
    return "%.0f MB" % (n / 1024.0 / 1024.0)


def build(root: str, onefile: bool) -> None:
    t0 = time.time()
    steps = 4 if onefile else 5

    def step(i: int, what: str) -> None:
        say("")
        say("[%d/%d] %s" % (i, steps, what))

    step(1, "檢查打包環境")
    check_env()
    step(2, "圖示")
    icon = make_icon(root)
    step(3, "PyInstaller（要幾分鐘）")
    exe = run_pyinstaller(root, onefile, icon)
    step(4, "自我檢查：在打出來的 exe 裡跑一次")
    self_test(root, exe)
    out = None
    if not onefile:
        step(5, "壓縮")
        out = make_zip(root, os.path.dirname(exe))
        say("  %s（%s）" % (os.path.relpath(out, root), _mb(out)))

    mins, secs = divmod(int(time.time() - t0), 60)
    say("")
    say("✓ 完成（%d 分 %d 秒）" % (mins, secs))
    say("  程式：%s（%s）" % (os.path.relpath(exe, root),
                            _mb(exe if onefile else os.path.dirname(exe))))
    if out:
        say("  要帶走的：%s" % os.path.relpath(out, root))
        say("  到另一台電腦：解壓縮 → 雙擊 %s\\%s" % (NAME, os.path.basename(exe)))
        say("  ⚠ 整個資料夾要一起帶走 —— 只拿 %s 是跑不起來的。"
            % os.path.basename(exe))


def main(argv=None) -> int:
    # 輸出被導到檔案（`build_exe.bat > log.txt`）時，中文 Windows 的 cp950 印不出
    # ✓ 這種字元 —— 打包成功了卻死在印「成功」那一行，不值得。
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    if sys.version_info < (3, 9):
        say("✗ 需要 Python 3.9 以上（現在是 %s）。" % platform.python_version())
        return 2

    ap = argparse.ArgumentParser(
        description="One-click build of %s (PyInstaller). "
                    "Windows: just double-click build_exe.bat." % NAME)
    ap.add_argument("--onefile", action="store_true",
                    help="打成單一 exe（啟動較慢；預設是資料夾版＋zip）")
    ap.add_argument("--fresh", action="store_true",
                    help="刪掉 %s 從頭裝" % VENV)
    ap.add_argument("--wheels", metavar="DIR", default="",
                    help="離線安裝：只從這個資料夾裝套件（pip --no-index --find-links）")
    ap.add_argument("--no-venv", action="store_true",
                    help="不建 %s，用目前這個 Python（它要已經裝好相依套件與 %s）"
                         % (VENV, PYINSTALLER))
    raw = list(sys.argv[1:] if argv is None else argv)
    a = ap.parse_args(raw)
    root = repo_root()

    try:
        if not a.no_venv:
            wheels = os.path.abspath(a.wheels) if a.wheels else ""
            if wheels and not os.path.isdir(wheels):
                raise BuildError("--wheels 指的資料夾不存在：%s" % wheels, code=2)
            say("準備打包用的 Python 環境（%s）" % VENV)
            py = ensure_venv(root, wheels, a.fresh)
            # 用那個 Python 重跑自己（`--no-venv` 讓它不要再建一次）
            return subprocess.run([py, os.path.abspath(__file__)] + raw
                                  + ["--no-venv"], cwd=root).returncode
        build(root, a.onefile)
    except BuildError as e:
        say("")
        say("✗ %s" % e)
        if e.hint:
            say("  %s" % e.hint)
        return e.code
    except KeyboardInterrupt:
        say("")
        say("✗ 中斷了。")
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
