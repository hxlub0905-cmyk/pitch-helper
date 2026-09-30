#!/usr/bin/env python3
# pitch-helper 打包成 exe — authored 2026-09-30.
"""把 pitch helper 打成**一個 Windows 執行檔**（PyInstaller）。

一鍵的那一顆是 repo 根的 ``build_exe.bat``（雙擊就好）：它找 Python、建一個
專用的 ``.venv-build\\``、裝 ``requirements.txt`` + PyInstaller，然後叫這一支。
手上已經有一個裝好 PyInstaller 與相依套件的 Python（例如不能上網的機器）的話，
直接跑這一支也一樣：

    python tools/build_exe.py              # dist/PitchHelper.exe（單一檔案）
    python tools/build_exe.py --onedir     # dist/PitchHelper/PitchHelper.exe（資料夾）
    python tools/build_exe.py --console    # 多一個主控台視窗，看得到 traceback

為什麼設定寫在這裡而不是 .bat 裡
--------------------------------
``.bat`` 必須是**純 ASCII、LF 換行、而且不准用 label／goto**（理由見
`tests/test_build_exe.py`）。在那種限制下寫邏輯很容易寫錯，而且沒有東西測得到
它。所以 .bat 只做「找 Python、裝套件、叫這一支」，**打包本身的每一個決定都在
這裡** —— 這裡有 ruff、有測試。

幾個刻意的決定
--------------
* **資料檔不列清單，用掃的**（:func:`data_files`）：``pitchapp/`` 底下每一個
  不是 ``.py`` 的檔案都跟著走。列清單的話，下一個加進 ``assets/`` 的圖示會
  **安靜地**不在 exe 裡 —— 而 ``branding`` 找不到檔案時是回空圖示、不拋例外，
  所以連錯誤都不會有。
* **每一個路徑都是絕對路徑**（:func:`pyinstaller_args`）：spec 檔放在
  ``build/``，而 PyInstaller 把 ``--add-data``／``--icon`` 的相對路徑**原封
  不動**寫進 spec，再解成相對於 spec 所在的資料夾（實測：
  ``Unable to find '…/build/assets/a.svg'``）。只有進入點那一個它會自己換算。
* **exe 的圖示從 ``pitch.svg`` 當場產**（:func:`make_icon`），不 commit 一份
  ``.ico``：二進位檔進不了搬運包（``make_text_bundle`` 只收 UTF-8 文字），
  而且兩份來源一定會漂。
* ``--noupx``：UPX 壓過的 Qt DLL 常常載不起來（PyInstaller 的已知問題），
  而 PATH 上剛好有 UPX 的時候 PyInstaller 會自己去用它。
* 預設 ``--windowed``（沒有黑色主控台視窗）。代價是當掉的時候看不到訊息 ——
  那時候用 ``--console`` 重打一次。
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import struct
import subprocess
import sys
from typing import List, Optional, Tuple

#: exe 的名字（也是 ``dist/`` 底下的資料夾名）。
APP_NAME = "PitchHelper"

#: 進入點（相對於 repo 根）。
ENTRY = "main.py"

#: 要把資料檔一起帶走的套件（相對於 repo 根）。
PACKAGE = "pitchapp"

#: exe 圖示的來源（相對於 repo 根）—— 就是視窗圖示那一份。
ICON_SVG = os.path.join("pitchapp", "ui", "assets", "pitch.svg")

#: ICO 裡要放的尺寸。Windows 檔案總管依顯示大小挑最近的一張，少了中間的
#: 尺寸它會自己縮放 —— 縮出來是糊的。
ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)

#: 不算資料檔的副檔名。
_CODE_EXT = (".py", ".pyc", ".pyo")


def repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def data_files(root: str) -> List[Tuple[str, str]]:
    """``pitchapp/`` 底下每一個資料檔 → ``(絕對路徑, exe 裡的目的資料夾)``。

    目的資料夾就是它在 repo 裡的資料夾（``pitchapp/ui/assets``），因為程式是用
    ``os.path.dirname(__file__)`` 找它們的，而 PyInstaller 讓 ``__file__`` 指向
    解開之後的同一個相對位置。
    """
    out: List[Tuple[str, str]] = []
    base = os.path.join(root, PACKAGE)
    for folder, dirs, files in os.walk(base):
        dirs[:] = sorted(d for d in dirs
                         if d != "__pycache__" and not d.startswith("."))
        rel_dir = os.path.relpath(folder, root).replace(os.sep, "/")
        for name in sorted(files):
            if name.startswith(".") or name.endswith(_CODE_EXT):
                continue
            out.append((os.path.join(folder, name), rel_dir))
    return out


def _png_bytes(renderer, size: int) -> bytes:
    """把 SVG 畫成一張 ``size``×``size`` 的透明底 PNG。"""
    from PySide6.QtCore import QBuffer, QIODevice, Qt
    from PySide6.QtGui import QImage, QPainter

    img = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setRenderHint(QPainter.SmoothPixmapTransform, True)
    renderer.render(p)
    p.end()
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    if not img.save(buf, "PNG"):
        raise RuntimeError("could not encode a %dpx PNG" % size)
    return bytes(buf.data())


def ico_bytes(pngs: List[Tuple[int, bytes]]) -> bytes:
    """把幾張 PNG 包成一個 ``.ico``（Vista 之後的 ICO 可以直接裝 PNG）。

    格式：ICONDIR（6 bytes）＋每張一個 ICONDIRENTRY（16 bytes）＋PNG 本體。
    寬高欄位只有一個 byte，所以 256 寫成 0 —— 那是規格，不是溢位。
    """
    head = struct.pack("<HHH", 0, 1, len(pngs))
    offset = len(head) + 16 * len(pngs)
    entries, bodies = [], []
    for size, png in pngs:
        dim = 0 if size >= 256 else size
        entries.append(struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32,
                                   len(png), offset))
        bodies.append(png)
        offset += len(png)
    return head + b"".join(entries) + b"".join(bodies)


def make_icon(svg_path: str, ico_path: str) -> None:
    """``pitch.svg`` → ``.ico``。做不出來就拋例外，**理由在例外裡**。

    要不要因此停下來是呼叫端（:func:`main`）的事：少一顆圖示不值得讓整個打包
    失敗，但「為什麼沒有圖示」一定要印出來 —— 只講「做不出來」的話，下一個人
    只能從頭猜（2026-09-30 在 Wine 上實際遇到：原因是 QtSvg 的 DLL 載不起來）。

    不需要 ``QApplication``：畫在 ``QImage`` 上、而且那張 SVG 沒有文字，
    所以用不到字型資料庫。
    """
    from PySide6.QtCore import Qt
    from PySide6.QtSvg import QSvgRenderer

    renderer = QSvgRenderer(svg_path)
    if not renderer.isValid():
        raise ValueError("not a readable SVG: %s" % svg_path)
    renderer.setAspectRatioMode(Qt.KeepAspectRatio)
    data = ico_bytes([(s, _png_bytes(renderer, s)) for s in ICON_SIZES])
    folder = os.path.dirname(os.path.abspath(ico_path))
    os.makedirs(folder, exist_ok=True)
    tmp = ico_path + ".tmp"                           # atomic（鐵則 5）
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, ico_path)


def pyinstaller_args(root: str, onedir: bool = False, console: bool = False,
                     icon: Optional[str] = None) -> List[str]:
    """交給 ``python -m PyInstaller`` 的參數。**每一個路徑都是絕對路徑。**"""
    build = os.path.join(root, "build")
    args = [
        "--noconfirm",                    # dist/ 已經有舊的就直接蓋掉，不要停下來問
        "--clean",
        "--log-level", "WARN",
        "--name", APP_NAME,
        "--onedir" if onedir else "--onefile",
        "--console" if console else "--windowed",
        "--noupx",
        "--distpath", os.path.join(root, "dist"),
        "--workpath", build,
        "--specpath", build,
    ]
    for src, dest in data_files(root):
        args += ["--add-data", "%s%s%s" % (src, os.pathsep, dest)]
    if icon:
        args += ["--icon", icon]
    args.append(os.path.join(root, ENTRY))
    return args


def output_path(root: str, onedir: bool) -> str:
    exe = APP_NAME + (".exe" if os.name == "nt" else "")
    if onedir:
        return os.path.join(root, "dist", APP_NAME, exe)
    return os.path.join(root, "dist", exe)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Build the pitch helper into a Windows executable "
                    "(PyInstaller).")
    ap.add_argument("--onedir", action="store_true",
                    help="build a folder (dist/%s/) instead of one .exe -- "
                         "starts faster, but you copy the whole folder"
                         % APP_NAME)
    ap.add_argument("--console", action="store_true",
                    help="keep a console window, so a crash shows its "
                         "traceback")
    a = ap.parse_args(argv)

    if importlib.util.find_spec("PyInstaller") is None:
        print("PyInstaller is not installed in this Python:\n    %s\n\n"
              "Install it first:  %s -m pip install pyinstaller\n"
              "(or just double-click build_exe.bat, which does that for you)"
              % (sys.executable, sys.executable))
        return 2

    root = repo_root()
    ico: Optional[str] = os.path.join(root, "build", APP_NAME + ".ico")
    try:
        make_icon(os.path.join(root, ICON_SVG), ico)
        print("icon: %s" % ico)
    except Exception as e:              # 少一顆圖示不值得讓整個打包失敗
        print("warning: could not make the icon from %s (%s: %s)\n"
              "         the exe will use the default icon"
              % (ICON_SVG, type(e).__name__, e))
        ico = None

    args = pyinstaller_args(root, onedir=a.onedir, console=a.console,
                            icon=ico)
    print("building %s (%s, %s) -- this takes a few minutes ..."
          % (APP_NAME, "folder" if a.onedir else "single file",
             "console" if a.console else "windowed"))
    r = subprocess.run([sys.executable, "-m", "PyInstaller"] + args,
                       cwd=root)
    if r.returncode != 0:
        print("\nPyInstaller failed (exit code %d). If %s is still running, "
              "close it and try again." % (r.returncode, APP_NAME))
        return r.returncode

    out = output_path(root, a.onedir)
    if not os.path.isfile(out):
        print("\nPyInstaller said OK but %s is not there." % out)
        return 1
    # --onedir 的 exe 本身只是一個幾 MB 的啟動器 —— 報它的大小會讓人以為
    # 只要搬那一個檔案，所以報的是整個資料夾。
    size = _tree_size(os.path.dirname(out)) if a.onedir else os.path.getsize(out)
    print("\nOK: %s  (%.0f MB)" % (out, size / 1024.0 / 1024.0))
    if a.onedir:
        print("Copy the whole %s folder -- the .exe does not run on its own."
              % os.path.dirname(out))
    return 0


def _tree_size(folder: str) -> int:
    return sum(os.path.getsize(os.path.join(d, f))
               for d, _dirs, files in os.walk(folder) for f in files)


if __name__ == "__main__":
    sys.exit(main())
