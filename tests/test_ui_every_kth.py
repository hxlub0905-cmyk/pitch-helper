"""「每隔 k 格不一樣」在視窗上（2026-09-30，使用者：「好，照這個做功能」）。

照使用者看過的那張示意圖：**狀態行**講一句、**證據卡**那一行換成「每隔一格
不一樣」並帶一個 “Compare them ›”、**那一軸的 ×2 框起來**、“Or try” 第一個是
建議的週期、**底下的警告**講「按哪裡」。只提醒，不自動改數字。

⚠ 這個檔案要 Qt（檔名 `test_ui_*`）。
"""
from __future__ import annotations

import dataclasses

import numpy as np
import pytest

from test_every_kth import fins, render  # 同一個資料夾（pytest 把它放進 sys.path）


@pytest.fixture(scope="module")
def ph():
    pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
    from pitchapp.ui import pitch_helper
    return pitch_helper


@pytest.fixture(scope="module")
def app():
    pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(app, ph):
    w = ph.PitchHelperWindow()
    yield w
    w.close()


def measured(img, px, py):
    """真的量一次，再把週期換成「量到一半」的那一個（引擎在這張合成圖上
    會量對 —— 要測的是量錯的時候畫面講不講）。"""
    from pitchapp.core.algo import template as algo_template
    m = algo_template.measure_period(img)
    return dataclasses.replace(m, px=float(px), py=float(py), conf_x=92.0,
                               conf_y=92.0, notes=[], doubts=[], candidates=[])


def settle(win, ph):
    """同步跑一次 worker（跟 `remeasure(reuse=True)` 一樣的參數）。"""
    w = ph._PitchWorker(win._work, win.axis(), win._m, win.override(),
                        win.stack_method(), win.skip_edges())
    got = []
    w.done.connect(lambda m, gc, err: got.append((m, gc, err)))
    w.run()
    win._on_done(*got[-1])
    return got[-1]


def shown(win, ph, every=2, px=20, py=45):
    img = render(fins(every=every), sigma=6.0)
    win.set_image(img, "fins.png")
    win._m = measured(img, px, py)
    settle(win, ph)


def test_every_other_cell_is_said_in_all_the_places_the_mockup_shows(win, ph):
    shown(win, ph)
    assert win.verdict() == (ph.TONE_WARN, "Every other cell differs")
    card = win.lab_stack.text()
    assert "every other cell along X is different." in card
    assert "Compare them" in card and "<a " in card
    assert [bool(b.property("suggested")) for b in win._double_buttons] == [True, False]
    assert win.warn.isVisibleTo(win)
    assert win.warn.text().startswith(
        "⚠  Every other cell along X looks different from its neighbours")
    assert "40 × 45" in win.warn.text() and "press ×2 on the X row" in win.warn.text()
    assert win._try_buttons[0].text() == "40 × 45"
    assert "Every other cell along X: the part that differs" in win.details.text()


def test_every_third_points_at_or_try_instead_of_a_button(win, ph):
    shown(win, ph, every=3)
    assert win.verdict() == (ph.TONE_WARN, "Every 3rd cell differs")
    assert "every 3rd cell along X" in win.lab_stack.text()
    assert not any(b.property("suggested") for b in win._double_buttons), \
        "×2 不是答案的時候不准框它"
    assert "“Or try”" in win.warn.text() and "60 × 45" in win.warn.text()
    assert win._try_buttons[0].text() == "60 × 45"


def test_a_regular_lattice_says_nothing_about_it(win, ph):
    shown(win, ph, every=1)
    assert win.verdict()[1] not in ph.VERDICT_EVERY_KTH.values()
    assert "<a " not in win.lab_stack.text()
    assert "landed on each other." in win.lab_stack.text()
    assert not any(b.property("suggested") for b in win._double_buttons)
    assert "Every other" not in win.warn.text()
    base = win._double_buttons[0].property("baseStyle")
    assert win._double_buttons[0].styleSheet() == base


def test_pressing_x2_drops_the_old_hint_at_once(win, ph):
    """按下去的那一刻，畫面上的疊圖還是 20 × 45 的那一份 —— 它的提醒是舊週期的
    事。還講的話警告會算出 80 × 45（「再按一次」）。"""
    shown(win, ph)
    win._double_buttons[0].click()
    assert win.override() == (40.0, None)
    assert win._every_kth() is None
    assert "Every other" not in win.warn.text()
    assert "80 × 45" not in win.warn.text()
    assert not any(b.property("suggested") for b in win._double_buttons)
    assert win._double_buttons[0].styleSheet() == \
        win._double_buttons[0].property("baseStyle"), "框要拿掉"
    if win._worker is not None:                 # 按鈕開了一條執行緒
        win._worker.stop()
        win._worker.wait(20000)
    settle(win, ph)
    assert win._every_kth() is None, "40 × 45 就是真的單元，不再每隔一格不一樣"


def test_compare_them_shows_both_stacks_and_can_apply_the_period(win, ph):
    from PySide6.QtWidgets import QDialogButtonBox
    from pitchapp.ui.image_view import ImageView
    shown(win, ph)
    dlg = win._every_kth_dialog()
    assert dlg is not None
    assert dlg.windowTitle() == "Every other cell along X"
    view = dlg.findChild(ImageView)
    assert view is not None
    ok = dlg.findChild(QDialogButtonBox).button(QDialogButtonBox.Ok)
    assert ok.text() == "Use 40 × 45"
    ok.click()
    assert win.override() == (40.0, None)
    if win._worker is not None:
        win._worker.stop()
        win._worker.wait(20000)


def test_no_hint_no_dialog(win, ph):
    shown(win, ph, every=1)
    assert win._every_kth_dialog() is None
    win.show_every_kth()                         # 不開、不炸


def test_a_failing_detector_never_costs_the_answer(win, ph, monkeypatch):
    """它只是一個提醒：算不出來要記下來、回空的，不准把整次量測講成失敗。"""
    from pitchapp.core.algo import template as algo_template

    def boom(*_a, **_k):
        raise RuntimeError("detector broke")
    monkeypatch.setattr(algo_template, "every_kth_cell", boom)
    img = render(fins(every=2), sigma=6.0)
    win.set_image(img, "fins.png")
    win._m = measured(img, 20, 45)
    m, gc, err = settle(win, ph)
    assert err == "" and gc is not None and gc.cell.size
    assert gc.every_kth == []
    assert win.verdict()[1] not in ph.VERDICT_EVERY_KTH.values()


def test_a_typed_half_period_is_still_warned_about(win, ph):
    """打進去的數字也可能是那一半 —— 提醒排在「Using your period」前面。"""
    img = render(fins(every=2), sigma=6.0)
    win.set_image(img, "fins.png")
    win._m = measured(img, 40, 45)
    win.spin_px.blockSignals(True)
    win.spin_px.setValue(20.0)
    win.spin_px.blockSignals(False)
    settle(win, ph)
    assert win.override() == (20.0, None)
    assert win.verdict() == (ph.TONE_WARN, "Every other cell differs")


def test_the_worker_fills_it_in(ph):
    img = render(fins(every=2), sigma=6.0)
    from pitchapp.core.algo import template as algo_template
    m = dataclasses.replace(algo_template.measure_period(img), px=20.0, py=45.0,
                            conf_x=92.0, conf_y=92.0)
    w = ph._PitchWorker(img, ph.AXIS_BOTH, measured=m)
    got = []
    w.done.connect(lambda mm, gc, err: got.append(gc))
    w.run()
    alts = got[-1].every_kth
    assert [(a.axis, a.k) for a in alts if a.flagged] == [("x", 2)]
    assert np.isfinite([a.relevance for a in alts]).all()
