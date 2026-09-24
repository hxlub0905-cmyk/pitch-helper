"""`Cells agree` 扣掉雜訊之後（2026-09-24）—— `golden.noise_variance` 與
`golden.measure_agreement`。

為什麼要有這一支
----------------
F40 的一致性在週期**完全正確**時等於「訊號佔每一格變異數的比例」，所以雜訊
一大，對的週期也拿紅燈（實測 60×44、σ=60：0.31，畫面寫「this period is
wrong」）。校正本身一行乘法就寫完了，**危險的是估雜訊**：估多了，錯的週期也會
被抬上綠燈。所以這裡的斷言分兩邊，而第二邊比較重要：

* 對的週期：雜訊變大，校正後仍然是綠的；
* **錯的週期：校正後不准高過同一個圖樣「完全沒有雜訊」時的分數** —— 那是
  「如果沒有雜訊，這些格子對得多齊」的標準答案，校正只准逼近它，不准超過。

不需要 Qt（純 core）。
"""
from __future__ import annotations

import cv2
import numpy as np
import pytest

from pitchapp.core.algo import golden as algo_golden
from pitchapp.core.algo import template as algo_template
from pitchapp.ui.pitch_core import AGREE_GOOD_FROM, TONE_GOOD, agree_tone


def blobs(w=900, h=700, px=60, py=44):
    """一格裡一塊方塊 ＋ 一條細線（沒有雜訊，float）。"""
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    fx, fy = (xx % px) / px, (yy % py) / py
    img = (70 + 110 * ((fx > .2) & (fx < .55) & (fy > .25) & (fy < .7))
           + 40 * ((fx > .7) & (fx < .8))).astype(np.float64)
    return cv2.GaussianBlur(img, (0, 0), 1.2)


def checker(w=512, h=512, p=4):
    """**會騙倒常見估法的那一種**：2 px 的方格，邊緣佔滿每一個像素。"""
    y, x = np.mgrid[0:h, 0:w]
    return (70 + 110 * (((x // (p // 2)) + (y // (p // 2))) % 2)).astype(np.float64)


def lines(w=900, h=700, p=8):
    x = np.arange(w)[None, :].repeat(h, 0)
    img = (70 + 110 * (((x % p) / p) < 0.5)).astype(np.float64)
    return cv2.GaussianBlur(img, (0, 0), 0.8)


def u8(img):
    return np.clip(np.round(img), 0, 255).astype(np.uint8)


def noisy(clean, sigma, seed=0):
    rng = np.random.default_rng(seed)
    return u8(clean + rng.normal(0, sigma, clean.shape))


# --------------------------------------------------------------------------- #
# 1. 不給 noise_var 就是 F40 那個數字，一個 byte 都不變
# --------------------------------------------------------------------------- #
def test_without_noise_var_it_is_the_f40_number_byte_for_byte():
    img = noisy(blobs(), 30)
    for p in ((60, 44), (61, 44), (37, 29)):
        a = algo_golden.stack_agreement(img, *p)
        # 照 F40 的定義手算一次
        cells = [img[y:y + p[1], x:x + p[0]].astype(np.float64)
                 for (x, y) in algo_golden.tile_coords(img.shape, *p)]
        n = len(cells)
        raw = np.stack(cells).mean(axis=0).var() / np.mean([c.var() for c in cells])
        want = float(np.clip((raw - 1.0 / n) / (1.0 - 1.0 / n), 0.0, 1.0))
        assert a == want, (p, a, want)
        ag = algo_golden.measure_agreement(img, *p)
        assert ag.value == ag.raw == a and ag.noise_frac == 0.0


# --------------------------------------------------------------------------- #
# 2. 估雜訊：乾淨的圖必須是 0，白雜訊要估得準，不是白的只准少估
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("make", [blobs, checker, lines], ids=["blobs", "checker", "lines"])
def test_a_clean_image_has_no_noise(make):
    """⚠ **棋盤格是重點。** 最常見的估法（拉普拉斯核的中位數）在這張沒有任何
    雜訊的圖上說 97.7% 的變異數是雜訊 —— 用那個去校正，錯的週期也會變綠。"""
    img = u8(make())
    nv = algo_golden.noise_variance(img)
    assert nv < 0.01 * float(img.astype(np.float64).var()), nv


@pytest.mark.parametrize("sigma", [15, 40])
@pytest.mark.parametrize("make", [blobs, checker, lines], ids=["blobs", "checker", "lines"])
def test_white_noise_is_measured(make, sigma):
    clean = make()
    img = noisy(clean, sigma, seed=sigma)
    truth = float(np.var(img.astype(np.float64) - u8(clean)))
    assert 0.9 <= algo_golden.noise_variance(img) / truth <= 1.1


@pytest.mark.parametrize("kind", ["sharpened", "blurred"])
def test_noise_that_is_not_white_is_never_overestimated(kind):
    """銳化過的圖高頻雜訊被放大、模糊過的低頻比較多 —— 取「最小的那一帶」
    讓兩種都只會**少估**（校正不足），不會多估（把錯的週期抬上去）。"""
    rng = np.random.default_rng(4)
    clean = blobs()
    n = rng.normal(0, 30, clean.shape)
    if kind == "sharpened":
        img = clean + n
        img = img + (img - cv2.GaussianBlur(img, (0, 0), 1.5))
        ref = clean + (clean - cv2.GaussianBlur(clean, (0, 0), 1.5))
    else:
        n = cv2.GaussianBlur(n, (0, 0), 0.8)
        img, ref = clean + n * (30 / n.std()), clean
    img = u8(img)
    truth = float(np.var(img.astype(np.float64) - u8(ref)))
    assert algo_golden.noise_variance(img) <= 1.1 * truth


def test_too_small_to_estimate_means_no_correction():
    assert algo_golden.noise_variance(noisy(blobs(24, 20, 6, 5), 20)) == 0.0


# --------------------------------------------------------------------------- #
# 3. 校正之後：對的週期不再因為雜訊變紅，錯的週期不准被抬上去
# --------------------------------------------------------------------------- #
def _corrected(img, p):
    return algo_golden.measure_agreement(
        img, *p, noise_var=algo_golden.noise_variance(img))


@pytest.mark.parametrize("sigma", [10, 25, 40, 60])
def test_a_right_period_stays_green_as_the_noise_grows(sigma):
    img = noisy(blobs(), sigma, seed=sigma)
    ag = _corrected(img, (60, 44))
    assert ag.value >= AGREE_GOOD_FROM, (sigma, ag)
    assert agree_tone(ag.value) == TONE_GOOD


def test_the_bug_this_fixes_is_real():
    """修之前的樣子釘住：σ=60、週期完全正確，F40 的數字是紅的。"""
    img = noisy(blobs(), 60, seed=60)
    ag = _corrected(img, (60, 44))
    assert ag.raw < 0.5, ag.raw
    assert ag.value >= AGREE_GOOD_FROM, ag.value


@pytest.mark.parametrize("sigma", [10, 25, 40, 60])
@pytest.mark.parametrize("period", [(61, 44), (62, 44), (65, 44), (30, 44), (37, 29)])
def test_a_wrong_period_is_never_scored_above_its_noise_free_self(sigma, period):
    """**這一條是校正安全的證據。** 同一個圖樣、同一個週期、沒有雜訊時的分數
    就是「如果沒有雜訊，這些格子對得多齊」的標準答案。校正只准逼近它。"""
    clean = blobs()
    reference = algo_golden.stack_agreement(u8(clean), *period)
    ag = _corrected(noisy(clean, sigma, seed=sigma), period)
    assert ag.value <= reference + 0.03, (reference, ag)
    assert ag.value >= ag.raw, "校正只會往上（分母變小），不會往下"


@pytest.mark.parametrize("sigma", [10, 40, 60])
def test_the_ranking_of_periods_survives(sigma):
    """差一點的週期分數要照差多少排好（使用者靠它讀「差一點點」）。"""
    img = noisy(blobs(), sigma, seed=sigma)
    got = [_corrected(img, (p, 44)).value for p in (60, 61, 62, 65)]
    assert got == sorted(got, reverse=True), got


def test_pure_noise_still_agrees_on_nothing():
    rng = np.random.default_rng(0)
    img = u8(rng.normal(128, 60, (480, 600)))
    ag = _corrected(img, (60, 44))
    assert ag.noise_frac > 0.9
    assert ag.value < 0.1, ag


def test_the_correction_is_capped_and_says_so():
    """每一格四分之三以上是雜訊：放大到 4 倍就停，而 `noise_frac` 講得出來。"""
    faint = 100 + 0.25 * (blobs() - 70)          # 對比只剩四分之一
    ag = _corrected(noisy(faint, 40, seed=1), (60, 44))
    assert ag.noise_frac >= algo_golden.NOISE_FRACTION_CAP
    cap = 1.0 / (1.0 - algo_golden.NOISE_FRACTION_CAP)
    assert ag.value <= cap * ag.raw + 1e-9


# --------------------------------------------------------------------------- #
# 4. build_golden_cell 兩個數字都給
# --------------------------------------------------------------------------- #
def test_build_golden_cell_reports_both_numbers():
    img = noisy(blobs(), 40, seed=2)
    gc = algo_template.build_golden_cell(img, px=60.0, py=44.0)
    plain = algo_golden.stack_agreement(img, 60, 44, origin=tuple(int(v) for v in gc.origin))
    assert 0.0 < gc.noise_frac < 1.0
    assert gc.agreement >= gc.agreement_raw
    # `origin` 算進了錨定的捲動，所以跟疊的那一組只差整格 —— 原始分數要一樣。
    assert gc.agreement_raw == pytest.approx(plain, abs=0.02)


def test_resampling_leaves_an_integer_period_alone():
    img = noisy(blobs(), 10)
    assert algo_template.resample_to_pitch(img, 60.0, 44.0) is img
    out = algo_template.resample_to_pitch(img, 79.5, 44.0)
    assert out.shape == (700, int(round(900 * 80 / 79.5)))
