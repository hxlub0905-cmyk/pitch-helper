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
    python tools/build_exe.py --no-check   # 打完不開一次 exe 驗圖示

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
  而且兩份來源一定會漂。**產不出來就不打包**（2026-10-28 以前是印一句警告、
  打一個預設圖示的 exe —— 使用者：「確保打包後的 EXE 開啟時與執行時 ICON
  都會存在」）。
* **打完先驗兩個圖示才說 OK**（:func:`exe_problem`）。exe 有兩個圖示，而兩個
  不見的時候都**不會報錯**：
  - 檔案總管裡那一個（雙擊之前看到的）是 exe 本身的資源 —— :func:`exe_icon`
    讀出來，跟剛產的 ``.ico`` 逐張比；
  - 開起來之後視窗與工作列上那一個是程式自己用 SVG 畫的，svg plugin 沒被
    打包進去就是空的 —— :func:`runtime_icon_problem` 用 ``--check-icon``
    開一次 exe（不開視窗）問它本人。

  ``--no-check`` 跳過這一步。
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
from typing import List, Optional, Sequence, Tuple

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

#: 叫 exe 不開視窗、只回答「圖示畫不畫得出來」的旗標。跟
#: ``pitchapp/ui/pitch_helper.py`` 的 ``CHECK_ICON_FLAG`` 是同一個字串 —— 這裡
#: 不 import 它（那會把整個 UI 拉進來），`tests/test_build_exe.py` 拿這一個去
#: 真的跑一次程式，兩邊對不上就紅。
CHECK_ICON_FLAG = "--check-icon"

#: 等那一次自我檢查最多幾秒。單一 exe 每次開都要先解壓一百多 MB，防毒軟體
#: 第一次看到新的 exe 還會整個掃一遍。
CHECK_TIMEOUT = 300

#: PE 資源型別 ``RT_GROUP_ICON``：一組圖示有哪些尺寸。檔案總管顯示的就是 exe
#: 裡的第一組。
_RT_GROUP_ICON = 14

#: 資源目錄項目的最高位元：1 = 指到下一層目錄，0 = 指到資料。
_SUBDIR = 0x80000000


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

    呼叫端（:func:`main`）會因此停下來不打包，而「為什麼沒有圖示」一定要印
    出來 —— 只講「做不出來」的話，下一個人只能從頭猜（2026-09-30 在 Wine 上
    實際遇到：原因是 QtSvg 的 DLL 載不起來）。

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


def _icon_dir(data: bytes, at: int, entry_size: int) -> List[Tuple[int, int]]:
    """一份 ICONDIR（``.ico`` 檔頭，每張 16 bytes）或 GRPICONDIR（exe 裡的，
    每張 14 bytes）→ 每張的 ``(邊長, 位元組數)``。兩種的前 12 bytes 一樣
    （:func:`ico_bytes` 寫的那幾欄），PyInstaller 就是照抄過去的。"""
    count = struct.unpack_from("<H", data, at + 4)[0]
    out = []
    for i in range(count):
        e = at + 6 + entry_size * i
        out.append((data[e] or 256, struct.unpack_from("<I", data, e + 8)[0]))
    return out


def ico_icon(path: str) -> List[Tuple[int, int]]:
    """``.ico`` 裡每張的 ``(邊長, 位元組數)`` —— 拿來跟 :func:`exe_icon` 比。"""
    with open(path, "rb") as f:
        return _icon_dir(f.read(), 0, 16)


def exe_icon(path: str) -> Optional[List[Tuple[int, int]]]:
    """exe 裡第一組圖示（檔案總管顯示的那一組）每張的 ``(邊長, 位元組數)``。

    不是 Windows 執行檔（PE）回 ``None``；是 PE 但沒有圖示回 ``[]``；檔案壞掉
    拋 ``ValueError``／``struct.error``。

    為什麼自己讀：PyInstaller 說 OK 不代表**我們的**圖示進去了 —— 沒給
    ``--icon`` 的時候它放的是它自己的預設圖示，而那不會報錯，只會在檔案總管裡
    看起來不對。反過來，檔案總管有圖示快取，重打之後**看到舊圖示不代表沒換到**。
    兩件事都只有讀 exe 本身答得了：每一張的邊長與位元組數都跟剛產的 ``.ico``
    一樣，就是同一顆圖示。

    格式是 PE/COFF 規格的資源節：型別 → 名字 → 語言三層目錄，最底下是一份
    GRPICONDIR。（2026-10-28 拿 32 個真的 Windows 執行檔跟 ``pefile`` 對過：
    32／64 位元、ARM64、PyInstaller 自己的 bootloader。）
    """
    with open(path, "rb") as f:
        data = f.read()
    if data[:2] != b"MZ":
        return None
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    if data[pe:pe + 4] != b"PE\0\0":
        return None
    n_sections = struct.unpack_from("<H", data, pe + 6)[0]
    opt_size = struct.unpack_from("<H", data, pe + 20)[0]
    opt = pe + 24                                     # optional header
    magic = struct.unpack_from("<H", data, opt)[0]
    # data directory 的第 2 項是資源；它在 optional header 裡的位置 32 位元
    # （PE32，0x10B）與 64 位元（PE32+）不一樣。
    rsrc = struct.unpack_from("<I", data,
                              opt + (96 if magic == 0x10B else 112) + 16)[0]
    if not rsrc:
        return []
    # 每一節：(VirtualSize, VirtualAddress, SizeOfRawData, PointerToRawData)
    sections = [struct.unpack_from("<IIII", data, opt + opt_size + 40 * i + 8)
                for i in range(n_sections)]

    def at(rva: int) -> int:                          # 記憶體位址 → 檔案位置
        for vsize, va, raw_size, raw in sections:
            if va <= rva < va + max(vsize, raw_size):
                return raw + rva - va
        raise ValueError("address %#x is in no section" % rva)

    base = at(rsrc)

    def entries(offset: int) -> List[Tuple[int, int]]:
        named, ids = struct.unpack_from("<HH", data, base + offset + 12)
        return [struct.unpack_from("<II", data, base + offset + 16 + 8 * i)
                for i in range(named + ids)]

    def first(entry_list: List[Tuple[int, int]]) -> int:
        if not entry_list or not entry_list[0][1] & _SUBDIR:
            raise ValueError("the resource tree is not type/name/language")
        return entry_list[0][1] & ~_SUBDIR

    for kind, ptr in entries(0):
        if kind == _RT_GROUP_ICON and ptr & _SUBDIR:
            names = entries(ptr & ~_SUBDIR)
            langs = entries(first(names))
            if not langs or langs[0][1] & _SUBDIR:
                raise ValueError("the icon group has no data")
            grp = at(struct.unpack_from("<I", data, base + langs[0][1])[0])
            return _icon_dir(data, grp, 14)
    return []


def runtime_icon_problem(cmd: Sequence[str], answer_path: str,
                         timeout: float = CHECK_TIMEOUT) -> Optional[str]:
    """跑一次 ``cmd --check-icon answer_path``：程式不開視窗，自己畫一次視窗
    圖示，把答案寫進 ``answer_path``。畫得出來回 ``None``，否則回理由。

    ``cmd`` 是打好的 exe；測試裡是 ``python main.py`` —— 同一條路。
    """
    if os.path.exists(answer_path):
        os.remove(answer_path)                        # 舊的答案不算數
    try:
        r = subprocess.run(list(cmd) + [CHECK_ICON_FLAG, answer_path],
                           stdin=subprocess.DEVNULL, timeout=timeout)
    except subprocess.TimeoutExpired:
        return ("the program did not answer within %d s. Antivirus scanning "
                "a new exe can take that long: run it once by hand, or build "
                "again with --no-check" % timeout)
    except OSError as e:
        return "the program could not be started (%s)" % e
    try:
        with open(answer_path, encoding="utf-8") as f:
            answer = f.read().strip()
    except OSError:
        return ("the program exited with code %d without answering, so it "
                "probably does not start at all. Build again with --console "
                "and run it to see why" % r.returncode)
    return None if answer == "ok" else "the window icon: " + answer


def exe_problem(exe: str, ico: str, answer_path: str) -> Optional[str]:
    """打好的 exe 兩個圖示都在回 ``None``，否則回理由（見模組說明）。

    ``ico``：交給 PyInstaller 的那一顆；``answer_path``：
    :func:`runtime_icon_problem` 那一次自我檢查寫答案的地方。
    """
    try:
        got = exe_icon(exe)
    except (ValueError, struct.error) as e:
        return "could not read the icon out of %s (%s)" % (exe, e)
    if got is not None and got != ico_icon(ico):
        if not got:
            return ("%s has no icon, so Explorer shows the generic exe one"
                    % exe)
        return ("the icon in %s is not the one made from %s (it has %s px)"
                % (exe, ICON_SVG, "/".join(str(n) for n, _ in got)))
    return runtime_icon_problem([exe], answer_path)


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
    ap.add_argument("--no-check", action="store_true",
                    help="do not start the new exe once to check that it "
                         "has its icons")
    a = ap.parse_args(argv)

    if importlib.util.find_spec("PyInstaller") is None:
        print("PyInstaller is not installed in this Python:\n    %s\n\n"
              "Install it first:  %s -m pip install pyinstaller\n"
              "(or just double-click build_exe.bat, which does that for you)"
              % (sys.executable, sys.executable))
        return 2

    root = repo_root()
    ico = os.path.join(root, "build", APP_NAME + ".ico")
    try:
        make_icon(os.path.join(root, ICON_SVG), ico)
        print("icon: %s" % ico)
    except Exception as e:              # 理由一定要印出來（見 make_icon）
        print("\nCould not make the icon from %s (%s: %s)\n"
              "Not building: the exe would get the default icon."
              % (ICON_SVG, type(e).__name__, e))
        return 1

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
    if a.no_check:
        print("\nicons: not checked (--no-check)")
    else:
        print("\nchecking the icons: starting %s once, without a window ..."
              % APP_NAME)
        problem = exe_problem(out, ico, os.path.join(root, "build",
                                                     "icon-check.txt"))
        if problem:
            print("\nFAILED: %s\n\n%s is there, but it did not pass the "
                  "icon check." % (problem, out))
            return 1
        if os.name == "nt":
            print("icons: OK -- in the exe file (%s px) and in the window"
                  % "/".join(str(n) for n in ICON_SIZES))
            print("       Explorer caches icons: if it still shows the old "
                  "one, rename the exe or look again later.")
        else:
            print("icons: OK -- in the window (not a Windows exe, so it has "
                  "no file icon)")
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
