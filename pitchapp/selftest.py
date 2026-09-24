# pitch-helper 打包後的自我檢查 — authored 2026-09-24.
"""``PitchHelper.exe --self-test [報告檔]``：**打包出來的那個 exe 真的跑得起來嗎。**

`tools/build_exe.py` 打完包就叫這一支。PyInstaller 最常見的壞法**不會在打包
的時候出現** —— 它只看得到 import 敘述，所以：

* 執行期才去讀的檔案（`ui/assets/*.svg`）沒被帶進去 → 圖示安靜地變空白；
* Qt 的 SVG plugin 沒跟上 → 同上，而且一樣安靜；
* cv2 / numpy 少一顆 DLL → 雙擊之後一個錯誤對話框 —— 在使用者的機器上。

打包「成功」、exe 也產出來了，但它是壞的。所以打完就在**打出來的那個 exe
裡面**把每一段真的走一次，而不是在打包用的那個 Python 裡（那裡什麼都有，
問它等於沒問）。

* **不顯示任何視窗**：主視窗只建構、不 ``show()``。Linux 上沒有 display
  的時候（CI）改用 offscreen；Windows 上用原生的那一個 —— 打包進去的正是它。
* **結果寫進檔案**：視窗程式（``--windowed``）沒有 stdout（``sys.stdout``
  是 ``None``），而退出碼只講得出「過／沒過」。檔案講得出是哪一段、為什麼。
  有 stdout 的時候（``python main.py --self-test``）順便印一份。
* 退出碼 ``0`` ＝ 全過，``1`` ＝ 有一段沒過。
* **每一段都跑**，一段沒過不會擋下一段 —— 一次看到全部壞在哪，而不是修一個
  重打一次包（一次好幾分鐘）才看到下一個。
"""
from __future__ import annotations

import os
import platform
import sys
import traceback
from typing import Callable, List, Tuple

from pitchapp.core import log

__all__ = ["DEFAULT_REPORT", "run"]

#: 沒給報告檔的時候寫到目前目錄的這個名字。
DEFAULT_REPORT = "pitch-helper-selftest.txt"

#: 合成影像的週期。**刻意兩軸不同**：X、Y 對調的錯才抓得到。
_PX, _PY = 24, 16


def _headless_if_needed() -> None:
    """Linux 上沒有 display 就用 offscreen（一定要在建 ``QApplication`` 之前）。"""
    if sys.platform.startswith("linux") and not (
            os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _tiles():
    """直條 × 橫帶交叉的重複 layout（跟 `tests/test_ui_pitch_helper.tiles` 同一種）。"""
    import numpy as np
    rng = np.random.default_rng(3)
    y, x = np.mgrid[0:240, 0:320]
    col = (x % _PX) < _PX * 0.42
    row = (y % _PY) < _PY * 0.5
    img = 55 + 55 * col + 45 * row + 40 * (col & row)
    return np.clip(img + rng.normal(0, 5, img.shape), 0, 255).astype(np.uint8)


def _check_imaging() -> str:
    """numpy ＋ cv2：編一張 PNG 再解回來，要一模一樣（讀檔走的就是這一條）。"""
    import cv2
    import numpy as np
    img = _tiles()
    ok, buf = cv2.imencode(".png", img)
    if not ok:
        raise RuntimeError("cv2.imencode refused a PNG")
    back = cv2.imdecode(np.frombuffer(buf.tobytes(), np.uint8), cv2.IMREAD_UNCHANGED)
    if back is None or not np.array_equal(back, img):
        raise RuntimeError("PNG round trip changed the pixels")
    return "numpy %s, OpenCV %s" % (np.__version__, cv2.__version__)


def _check_measure() -> str:
    """整條量測走一次，答案要對 —— 不只是「沒有例外」。"""
    from pitchapp.core.algo import template as algo_template
    m = algo_template.measure_period(_tiles())
    if (round(m.px), round(m.py)) != (_PX, _PY):
        raise RuntimeError("measured %.2f x %.2f, expected %d x %d"
                           % (m.px, m.py, _PX, _PY))
    return "period %g x %g px" % (m.px, m.py)


def _check_qt() -> str:
    from PySide6 import __version__ as pyside_version
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([sys.argv[0]])
    return "PySide6 %s, platform %s" % (pyside_version, app.platformName())


def _check_icon() -> str:
    """圖示畫得出來 ＝ `assets/` 被帶進去了 **而且** SVG plugin 也在。

    兩件事分開講：修法不一樣（前者是 `--add-data`，後者是 Qt plugin）。
    """
    from pitchapp.ui import branding
    if not os.path.isfile(branding.PITCH_ICON_PATH):
        raise RuntimeError("icon file is missing: %s" % branding.PITCH_ICON_PATH)
    if branding.pitch_icon().pixmap(32, 32).isNull():
        raise RuntimeError("the icon file is there but Qt cannot draw it "
                           "(SVG plugin missing?)")
    return "pitch.svg renders"


def _check_window() -> str:
    """主視窗建得起來（只建構，不顯示）。"""
    from pitchapp.ui.pitch_helper import PitchHelperWindow
    win = PitchHelperWindow()
    try:
        return "main window builds"
    finally:
        win.close()
        win.deleteLater()


#: 順序有意義：Qt 那三段要在 ``QApplication`` 建好之後。
_CHECKS: List[Tuple[str, Callable[[], str]]] = [
    ("imaging", _check_imaging),
    ("measure", _check_measure),
    ("qt", _check_qt),
    ("icon", _check_icon),
    ("window", _check_window),
]


def _write_atomic(path: str, text: str) -> None:
    tmp = path + ".tmp"                               # atomic（鐵則 5）
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)


def run(report: str = "") -> int:
    """跑每一段、把結果寫進 ``report``，回退出碼（0 ＝ 全過）。"""
    _headless_if_needed()
    lines = ["pitch-helper self-test",
             "python %s, %s, %s" % (platform.python_version(), platform.platform(),
                                    "frozen" if getattr(sys, "frozen", False)
                                    else "from source")]
    passed = True
    for name, check in _CHECKS:
        try:
            detail = check()
        except Exception as e:
            # 不 raise 是刻意的（見模組說明：每一段都跑），但要留痕 —— 報告裡
            # 連 traceback 一起寫；logger 那一份給有接 log 的人。
            log.swallowed("selftest.run")
            passed = False
            lines.append("FAIL %-8s %s: %s" % (name, type(e).__name__, e))
            lines += ["       " + ln for ln in
                      traceback.format_exc().rstrip().splitlines()]
        else:
            lines.append("ok   %-8s %s" % (name, detail))
    lines.append("PASS" if passed else "FAIL")
    text = "\n".join(lines) + "\n"

    path = os.path.abspath(report or DEFAULT_REPORT)
    _write_atomic(path, text)
    if sys.stdout is not None:                        # --windowed 的 exe 沒有 stdout
        sys.stdout.write(text)
        sys.stdout.write("report: %s\n" % path)
    return 0 if passed else 1
