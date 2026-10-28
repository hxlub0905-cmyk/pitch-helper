# d4t Studio 品牌資產 — authored 2026-08-19.
"""應用程式圖示與字標的**唯一出處**。

圖示是 SVG（``assets/d4t.svg``）。PySide6 帶著 Qt 的 SVG image plugin，
所以 :class:`QIcon` 直接吃得下向量檔，不必先轉成一堆點陣尺寸 ——
只有一份檔案要維護，而且在任何 DPI 上都是銳利的。

那個 4 是**用接線畫的**：兩個開口是進來的影像流、橫豎交會的那一點是算法、
往下走出去的是判定。三顆點的顏色就是 :mod:`d4t.ui.theme` 的三段式
（影像藍／算法橙／判定紫）—— 圖示跟畫布講的是同一套語言，改配色時兩邊要一起改。

找不到檔案時回一個**空的** :class:`QIcon` 而不是拋例外：少一顆圖示不該讓
Studio 開不起來，而受限機器是用「複製檔案」的方式部署的（見 ``AGENTS.md`` §2），
少一個檔案是真的會發生的事。
"""
from __future__ import annotations

import os
from typing import Optional

from PySide6.QtGui import QIcon, QImage

__all__ = ["ASSETS_DIR", "ICON_PATH", "PITCH_ICON_PATH", "WORDMARK_PATH",
           "WORDMARK_DARK_PATH", "app_icon", "icon_problem", "pitch_icon"]

#: 這個目錄裡的東西要跟著套件走 —— 見 ``pyproject.toml`` 的 package-data。
ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")

#: 視窗／工作列圖示。
ICON_PATH = os.path.join(ASSETS_DIR, "d4t.svg")
#: Pitch helper 自己的圖示（F120，2026-09-21 使用者：「這個 helper 要有一個
#: 自己的 icon（獨立於 d4t）」）。
#:
#: ⚠ **獨立不等於無關**：磚的形狀跟 `d4t.svg` 一樣（同一套工具的不同入口），
#: 分別在顏色 —— d4t 是三段式的藍／橙／紫，這一支只有 Measure 的橙，因為它
#: 從頭到尾只做量東西這一件事。
PITCH_ICON_PATH = os.path.join(ASSETS_DIR, "pitch.svg")

#: 字標（淺色底用）。
WORDMARK_PATH = os.path.join(ASSETS_DIR, "d4t-wordmark.svg")
#: 字標（深色底用）。
WORDMARK_DARK_PATH = os.path.join(ASSETS_DIR, "d4t-wordmark-dark.svg")


def app_icon() -> QIcon:
    """回 Studio 的視窗圖示；檔案不在就回空 :class:`QIcon`。

    刻意不快取：``QIcon`` 是延遲算圖的（真的要畫之前不會去解析 SVG），
    而一個跨 ``QApplication`` 生命週期的模組層快取在測試裡會變成
    指向已經拆掉的 Qt 物件。
    """
    if not os.path.isfile(ICON_PATH):
        return QIcon()
    return QIcon(ICON_PATH)


def pitch_icon() -> QIcon:
    """回 Pitch helper 的視窗圖示；檔案不在就回空 :class:`QIcon`。

    理由跟 :func:`app_icon` 一字不差（不快取、缺檔不拋例外）—— 兩支長一樣
    是因為它們**是同一件事的兩個實例**，不是因為忘了抽出來：多一層
    `_icon(path)` 買不到任何東西，而少一顆圖示不該讓視窗開不起來。
    """
    if not os.path.isfile(PITCH_ICON_PATH):
        return QIcon()
    return QIcon(PITCH_ICON_PATH)


#: :func:`icon_problem` 畫哪幾個尺寸：標題列（16）、工作列（32）、Alt+Tab（48）、
#: 檔案總管的大圖示（256）。
_CHECK_SIZES = (16, 32, 48, 256)


def icon_problem() -> Optional[str]:
    """Pitch helper 的視窗圖示畫不畫得出東西：畫得出來回 ``None``，否則回**理由**。

    :func:`pitch_icon` 出事的時候刻意不拋例外（視窗照開），代價是「圖示不見了」
    只看得到、不會報錯。打包成 exe 之後最容易這樣：SVG 要靠 Qt 的 svg plugin
    才畫得出來，plugin 沒被打包進去的時候 ``QIcon`` 是空的、視窗照開（2026-10-28
    在打包出來的程式裡拿掉那兩個 plugin 實測）。所以這裡**真的畫一次**，再看有
    沒有不透明的像素 —— `tools/build_exe.py` 打完包會用
    ``PitchHelper.exe --check-icon`` 問 exe 本人。

    要先有 ``QGuiApplication``（``QIcon.pixmap`` 需要）。
    """
    if not os.path.isfile(PITCH_ICON_PATH):
        return "the icon file is not there: %s" % PITCH_ICON_PATH
    icon = pitch_icon()
    for size in _CHECK_SIZES:
        img = icon.pixmap(size, size).toImage()
        if img.isNull():
            return ("the icon draws nothing at %dpx - Qt's SVG plugin is "
                    "missing, or %s is not a readable SVG"
                    % (size, PITCH_ICON_PATH))
        # ARGB32 在記憶體裡是 B, G, R, A —— 每四個 byte 的第四個是 alpha。
        argb = img.convertToFormat(QImage.Format_ARGB32)
        alpha = bytes(argb.constBits())[3::4]
        if max(alpha) < 255:
            return ("the icon draws only see-through pixels at %dpx - is %s "
                    "a broken SVG?" % (size, PITCH_ICON_PATH))
    return None
