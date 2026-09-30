"""「每隔 k 格有一格不一樣」（`golden.cell_alternation` / `template.every_kth_cell`）。

使用者 2026-09-30：「照這個做功能」—— 一個格子裡的東西**每隔一格才出現一次**
（每隔一根 fin 才有一個 contact）時，量週期常常量到一半，而疊出來那一格看起來
完全正常。這一支只**提醒**，不改任何數字，所以最要緊的是**不准誤報**：一個對的
週期被說「每隔一格不一樣」，會讓人去懷疑一個對的答案。

所以這裡一半的測試是「該講的要講」，另一半是「每一種會讓格子看起來不一樣、
但其實是同一個週期的東西，都不准講」：雜訊、旋轉與放大率漂移、缺陷與量測條、
縮放過的截圖（相鄰像素相關的雜訊）、JPEG、小數週期的取樣相位。

⚠ 不需要 Qt（核心批）。
"""
from __future__ import annotations

import cv2
import numpy as np
import pytest

from pitchapp.core.algo import golden as G
from pitchapp.core.algo import template as T
from pitchapp.ui import pitch_core as pc

SS = 4                                  # 超取樣：邊緣是面積平均，不是二值化


def render(fn, w=640, h=450, sigma=6.0, seed=0, blur=0.8, angle=0.0, mag=0.0):
    """``fn(X, Y)`` 在 ``SS`` 倍的格子上畫，面積平均縮回來，再模糊、加雜訊。"""
    rng = np.random.default_rng(seed)
    ys = (np.arange(h * SS) + 0.5) / SS + 3.3
    xs = (np.arange(w * SS) + 0.5) / SS + 5.1
    X, Y = np.meshgrid(xs, ys)
    if angle:
        t = np.radians(angle)
        cx, cy = w / 2, h / 2
        X, Y = (cx + (X - cx) * np.cos(t) - (Y - cy) * np.sin(t),
                cy + (X - cx) * np.sin(t) + (Y - cy) * np.cos(t))
    if mag:
        X = X * (1.0 + mag * (X / w - 0.5))
    hi = np.asarray(fn(X, Y), np.float64)
    lo = cv2.resize(hi, (w, h), interpolation=cv2.INTER_AREA)
    if blur:
        lo = cv2.GaussianBlur(lo, (0, 0), blur)
    if sigma:
        lo = lo + rng.normal(0, sigma, lo.shape)
    return np.clip(np.round(lo), 0, 255).astype(np.uint8)


def fins(every=2, contact=70.0, px=20.0, py=45.0):
    """fin 每 ``px`` 一根、gate 每 ``py`` 一條，contact **每 ``every`` 根 fin 一個**。"""
    def fn(X, Y):
        fx, fy = X % px, Y % py
        cx = X % (px * every)
        return (60 + 90 * (fx < 0.4 * px) + 50 * (fy < 0.27 * py)
                + contact * (((cx - 0.2 * px) ** 2 + (fy - 0.67 * py) ** 2)
                             < (0.2 * px) ** 2))
    return fn


def flagged(alts):
    return sorted((a.axis, a.k) for a in alts if a.flagged)


# --------------------------------------------------------------------------- #
# 1. 該講的要講
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("sigma", [0.0, 6.0, 20.0])
def test_a_contact_on_every_other_fin_is_found_on_that_axis(sigma):
    img = render(fins(every=2), sigma=sigma)
    alts = T.every_kth_cell(img, 20, 45)
    assert flagged(alts) == [("x", 2)]
    x2 = next(a for a in alts if (a.axis, a.k) == ("x", 2))
    assert x2.relevance >= G.ALT_MIN_RELEVANCE * 3, "不是擦邊過的"


def test_every_third_is_told_apart_from_every_other():
    """⚠ 真的是「每隔一格」的時候，k=3 的分數**也很高**（每一類裡一半 A 一半 B，
    中位數落在哪一邊看運氣）—— 乾淨的合成圖上甚至比 k=2 高。每一軸只准講
    ``fit`` 最好的那一個 k。反過來也一樣。"""
    two = T.every_kth_cell(render(fins(every=2), sigma=0.0), 20, 45)
    three = T.every_kth_cell(render(fins(every=3), sigma=0.0), 20, 45)
    assert flagged(two) == [("x", 2)]
    assert flagged(three) == [("x", 3)]
    loser = next(a for a in two if (a.axis, a.k) == ("x", 3))
    assert not loser.best and not loser.flagged


def test_the_other_axis_works_the_same_way():
    img = np.ascontiguousarray(render(fins(every=2), sigma=6.0).T)
    assert flagged(T.every_kth_cell(img, 45, 20)) == [("y", 2)]


def test_stripes_with_pitch_walk_are_found_on_a_one_dimensional_layout():
    """SADP 的 pitch walk：線每 16 px 一條，但每隔一個 space 窄 1 px。只有 X 有
    週期（Y 那一軸一格就是整張高）—— 那一軸切成幾條當「線」。"""
    def fn(X, Y):
        x2 = X % 32
        return 60 + 120 * ((x2 < 8) | ((x2 >= 15) & (x2 < 23)))
    img = render(fn, w=800, h=600, sigma=20.0, blur=1.0)
    alts = T.every_kth_cell(img, 16, 600, axes=(True, False))
    assert flagged(alts) == [("x", 2)]
    assert not any(a.axis == "y" for a in alts), "沒在用的軸不看"


def test_a_fractional_period_is_resampled_like_the_stack_is():
    img = render(fins(every=2, px=20.5, py=45.0), w=700, sigma=6.0)
    assert flagged(T.every_kth_cell(img, 20.5, 45)) == [("x", 2)]


# --------------------------------------------------------------------------- #
# 2. 不該講的不准講
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("sigma", [0.0, 6.0, 40.0, 90.0])
def test_a_regular_lattice_is_never_flagged(sigma):
    img = render(fins(every=1), sigma=sigma)
    assert flagged(T.every_kth_cell(img, 20, 45)) == []


def test_pure_noise_is_never_flagged():
    img = np.random.default_rng(5).normal(128, 40, (450, 640))
    img = np.clip(img, 0, 255).astype(np.uint8)
    assert flagged(T.every_kth_cell(img, 20, 45)) == []


def test_rotation_and_magnification_drift_are_not_alternation():
    """旋轉、放大率漂移讓格子沿著一軸**線性地**變 —— 二次差分把它消掉。"""
    for angle, mag in ((1.0, 0.0), (0.0, 0.004), (0.7, 0.003)):
        img = render(fins(every=1), sigma=8.0, angle=angle, mag=mag)
        assert flagged(T.every_kth_cell(img, 20, 45)) == [], (angle, mag)


def test_defects_and_an_info_bar_do_not_fake_it():
    """只佔少數格的東西，逐像素中位數看不到。"""
    img = render(fins(every=1), sigma=6.0).astype(np.float64)
    yy, xx = np.mgrid[0:img.shape[0], 0:img.shape[1]]
    img[np.hypot(xx - 400, yy - 180) < 50] = 235
    img[-40:, :] = 230
    img = np.clip(img, 0, 255).astype(np.uint8)
    cv2.putText(img, "EHT=1.00kV  Mag=50kX", (15, img.shape[0] - 12),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, 0, 2)
    assert flagged(T.every_kth_cell(img, 20, 45)) == []


@pytest.mark.parametrize("scale", [1.25, 1.5, 0.75])
def test_a_scaled_screenshot_is_not_flagged(scale):
    """縮放過的截圖：雜訊變成**相鄰像素相關**，而 `noise_variance` 幾乎估不到
    那一種（它的說明寫著 0.00–0.04）。雜訊的對照要靠「把線分兩半」那一個。"""
    img = render(fins(every=1), sigma=25.0)
    h, w = img.shape
    shot = cv2.resize(img, (int(round(w * scale)), int(round(h * scale))),
                      interpolation=cv2.INTER_LINEAR if scale > 1 else cv2.INTER_AREA)
    assert flagged(T.every_kth_cell(shot, 20 * scale, 45 * scale)) == []


def test_a_jpeg_is_not_flagged():
    img = render(fins(every=1), sigma=15.0)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 60])
    assert ok
    jpg = cv2.imdecode(buf, cv2.IMREAD_GRAYSCALE)
    assert flagged(T.every_kth_cell(jpg, 20, 45)) == []


@pytest.mark.parametrize("px", [30 + 1 / 3, 40.5, 36 + 2 / 3])
def test_the_pixel_grid_of_a_fractional_period_is_not_alternation(px):
    """週期 30.33 的時候第 0、3、6… 格落在同一個取樣相位 —— 邊緣銳利、沒有
    雜訊的圖上，那本身就是一個「每隔三格不一樣」（不模糊的時候 relevance
    0.0030，剛好誤報）。`every_kth_cell` 先模糊 1 px 再比。"""
    def fn(X, Y):
        fx, fy = (X % px) / px, (Y % 44) / 44
        return 60 + 140 * ((fx > 0.2) & (fx < 0.55) & (fy > 0.25) & (fy < 0.7))
    img = render(fn, w=1000, h=800, sigma=0.0, blur=0.0)
    alts = T.every_kth_cell(img, px, 44)
    assert flagged(alts) == []
    assert max(a.relevance for a in alts) < G.ALT_MIN_RELEVANCE / 3


# --------------------------------------------------------------------------- #
# 3. 回不出答案的時候不假裝有答案
# --------------------------------------------------------------------------- #
def test_too_few_cells_along_an_axis_leaves_that_axis_out():
    """格子不夠的軸**不在清單裡**，不是一個 0（0 讀起來像「問過了、沒有」）。"""
    img = render(fins(every=2), w=140, h=450)            # X 上只有 7 格
    alts = T.every_kth_cell(img, 20, 45)
    assert not any(a.axis == "x" for a in alts)
    assert any(a.axis == "y" for a in alts)


def test_nonsense_input_returns_nothing():
    assert T.every_kth_cell(np.zeros((0, 0), np.uint8), 20, 45) == []
    assert T.every_kth_cell(np.zeros((100, 100), np.uint8), 0, 45) == []
    assert flagged(T.every_kth_cell(np.full((450, 640), 128, np.uint8), 20, 45)) == []


def test_flagged_needs_both_thresholds_and_the_best_k():
    A = G.Alternation
    r, s = G.ALT_MIN_RELEVANCE, G.ALT_MIN_SIGNIFICANCE
    assert A("x", 2, r, s).flagged
    assert not A("x", 2, r * 0.99, s * 100).flagged, "看得出來才講"
    assert not A("x", 2, r * 100, s * 0.99).flagged, "雜訊不講"
    assert not A("x", 3, r * 10, s * 10, best=False).flagged, "只講最像的那個 k"


def test_a_big_image_only_looks_at_the_middle():
    """4096² 整張要 1.35 s、峰值 245 MB；只看中間 2048²（每軸至少 14 格）。
    答案不變：中間那一塊也每隔一格有一個 contact。"""
    img = render(fins(every=2), w=640, h=450, sigma=6.0)
    big = np.tile(img, (7, 7))[:3000, :4000]
    assert flagged(T.every_kth_cell(big, 20, 45)) == [("x", 2)]


# --------------------------------------------------------------------------- #
# 4. 兩類各疊一張（“Compare them”）
# --------------------------------------------------------------------------- #
def test_the_two_stacks_differ_exactly_where_the_contact_is():
    img = render(fins(every=2), sigma=0.0)
    a, b = T.every_kth_stacks(img, 20, 45, axis=0, k=2)
    assert a.shape == b.shape == (45, 20)
    diff = np.abs(a - b)
    assert diff.max() > 40, "一類有 contact、一類沒有"
    # 差異集中在一小塊（contact），不是整張都不一樣。
    assert (diff > diff.max() / 2).mean() < 0.15


def test_a_regular_lattice_stacks_the_same_twice():
    img = render(fins(every=1), sigma=0.0)
    a, b = T.every_kth_stacks(img, 20, 45, axis=0, k=2)
    assert np.abs(a - b).max() < 3


def test_stacks_need_every_class_to_have_a_cell():
    img = render(fins(every=2), w=30, h=100)
    assert T.every_kth_stacks(img, 20, 45, axis=0, k=2) == []
    assert T.every_kth_stacks(img, 20, 45, axis=0, k=1) == []


# --------------------------------------------------------------------------- #
# 5. 畫面上的字（`pitch_core`，不開視窗）
# --------------------------------------------------------------------------- #
class _GC:
    def __init__(self, alts, px=20.0, py=45.0):
        self.every_kth, self.period_x, self.period_y = alts, px, py


def test_the_hint_is_the_strongest_flagged_one():
    A = G.Alternation
    gc = _GC([A("x", 2, 0.01, 50.0), A("y", 3, 0.05, 80.0),
              A("y", 2, 0.5, 1.0)])                       # 最大的那個是雜訊
    assert pc.every_kth_hint(gc, (20.0, 45.0)) == (1, 3, 0.05, 80.0)
    assert pc.every_kth_hint(_GC([A("x", 2, 0.5, 1.0)]), (20.0, 45.0)) is None
    assert pc.every_kth_hint(None, (20.0, 45.0)) is None


def test_the_hint_is_dropped_when_the_stack_is_for_another_period():
    """按下 ×2 之後、新的疊圖回來之前，畫面上還是舊的那一份 —— 它說的是**舊的
    週期**的事。那一刻還講的話，警告會算出「再按一次 ×2」，也就是 4 倍。"""
    gc = _GC([G.Alternation("x", 2, 0.05, 100.0)])
    assert pc.every_kth_hint(gc, (20.0, 45.0)) is not None
    assert pc.every_kth_hint(gc, (40.0, 45.0)) is None
    assert pc.every_kth_hint(gc, (20.0, 450.0)) is None, "軸向改了也一樣"


def test_the_suggested_period_goes_first_in_or_try():
    class M:
        px, py, candidates = 20.0, 45.0, [(10.0, 45.0), (40.0, 45.0), (20.0, 90.0)]
    plain = pc.candidate_periods(M, (True, True), (20.0, 45.0))
    assert plain[0] == (60.0, 45.0), "沒有提醒的時候照舊：×3 排第一"
    got = pc.candidate_periods(M, (True, True), (20.0, 45.0), prefer=(40.0, 45.0))
    assert got[0] == (40.0, 45.0)
    assert got.count((40.0, 45.0)) == 1, "同一個值只列一次"
    three = pc.candidate_periods(M, (True, True), (20.0, 45.0), prefer=(20.0, 135.0))
    assert three[0] == (20.0, 135.0), "不在清單裡的也補進去"


def test_the_words_say_where_what_and_which_button():
    assert pc.scaled_period((20.0, 45.0), 0, 2) == (40.0, 45.0)
    assert pc.scaled_period((20.0, 45.0), 1, 3) == (20.0, 135.0)
    w2 = pc.every_kth_warning(0, 2, (40.0, 45.0), (True, True))
    assert "Every other cell along X" in w2 and "40 × 45" in w2
    assert "press ×2 on the X row" in w2
    w3 = pc.every_kth_warning(1, 3, (20.0, 135.0), (True, True))
    assert "Every 3rd cell along Y" in w3 and "“Or try”" in w3, "×3 沒有一軸一顆的鈕"
    assert pc.every_kth_card(0, 2) == "every other cell along X is different."
    assert pc.every_kth_title(1, 2) == "Every other cell along Y"
    assert "cells 1, 3, 5" in pc.every_kth_caption(2)
    assert "cells 3, 6, 9" in pc.every_kth_caption(3)
    note = pc.every_kth_note(0, 2, 0.0591, 1060.4)
    assert "5.9%" in note and "1060×" in note


def test_the_state_line_is_no_longer_than_the_one_it_replaces():
    """狀態行跟名字排同一排（`VERDICT_OK` 那一段量過）：不准比已經在用的
    最長那一句長。"""
    for text in pc.VERDICT_EVERY_KTH.values():
        assert len(text) <= len(pc.VERDICT_CHECK)


def test_the_compare_strip_puts_the_stacks_side_by_side():
    a = np.full((10, 6), 100.0)
    b = a.copy()
    b[4:6, 2:4] = 140.0
    strip = pc.compare_strip([a, b], gap=2)
    assert strip.dtype == np.uint8 and strip.shape == (10, 3 * 6 + 2 * 2)
    left, right, diff = strip[:, 0:6], strip[:, 8:14], strip[:, 16:22]
    assert left.max() == 0 and right.max() == 255, "兩張共用一個灰階範圍"
    assert diff[4:6, 2:4].min() == 255 and diff[0, 0] == 0, "差異自己拉滿"
    assert strip[0, 6] == 128, "中間隔一條中灰"
    same = pc.compare_strip([a, a])
    assert same[:, -6:].max() == 0, "一樣的兩張，差異那一張是黑的"
