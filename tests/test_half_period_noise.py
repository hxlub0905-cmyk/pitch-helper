"""半週期檢查扣雜訊、引擎的話分「疑點／自己修對的」、候選加 ×3（2026-09-24）。

三件事都是用**複雜 pattern**驗算時抓到的（`docs/F120-pitch-helper.md` §31）：

* 半週期檢查的門檻是絕對的 0.10，而雜訊會把自相關等比例壓低 —— 同一張「每隔
  一根 fin 才有一個 contact」的圖，乾淨時加倍成 40（對），吵一點就答 20（錯）；
* 狀態燈把引擎的每一句話都當警告：量對的有六成亮黃燈，量錯的反而四成打綠勾；
* 「每 3 條線才有一個 via」量到 24、真的是 72，候選裡只有 ×2 與 ÷2。

不需要 Qt（純 core ＋ `pitch_core`）。
"""
from __future__ import annotations

import cv2
import numpy as np
import pytest

from pitchapp.core.algo import period2d as algo_period2d
from pitchapp.core.algo import template as algo_template
from pitchapp.ui import pitch_core


def u8(img):
    return np.clip(np.round(img), 0, 255).astype(np.uint8)


def fins(sigma, seed=0, w=640, h=450, r=4.6):
    """fin 每 20 px 一根、gate 每 45 px 一條、**每隔一根 fin** 一個 contact。
    真的重複單元是 40 × 45；只看 fin 的話是 20。"""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    img = 55 + 80 * (((xx % 20) >= 7) & ((xx % 20) < 13))
    img = np.where((yy % 45) < 14, 165.0, img)
    img = np.where(((xx % 40) - 10) ** 2 + ((yy % 45) - 29.5) ** 2 <= r * r, 225.0, img)
    return u8(cv2.GaussianBlur(img, (0, 0), 1.0) + rng.normal(0, sigma, (h, w)))


def regular(kind, sigma, seed=0, w=900, h=700):
    """**沒有**隔格差異的規則晶格 —— 半週期檢查在這些圖上絕對不准加倍。"""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    if kind == "tiles":
        img = 55 + 55 * ((xx % 60) < 25) + 45 * ((yy % 44) < 22)
    elif kind == "blobs":
        fx, fy = (xx % 50) / 50, (yy % 38) / 38
        img = 70 + 120 * ((fx > .25) & (fx < .6) & (fy > .2) & (fy < .7))
    else:
        img = 80 + 130 * ((((xx % 12) - 6) ** 2 + ((yy % 12) - 6) ** 2) < 9)
    return u8(cv2.GaussianBlur(img.astype(np.float64), (0, 0), 1.0)
              + rng.normal(0, sigma, (h, w)))


TRUTH = {"tiles": (60, 44), "blobs": (50, 38), "dots": (12, 12)}


# --------------------------------------------------------------------------- #
# 1. 半週期檢查扣雜訊
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("seed", [0, 1, 2])
def test_a_noisy_every_other_fin_contact_is_still_40(seed):
    img = fins(45, seed)
    m = algo_template.measure_period(img)
    assert (m.px, m.py) == (40.0, 45.0), (m.px, m.py, m.half_gain_x)
    assert m.doubled[0]


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_the_bug_this_fixes_is_real(seed):
    """修之前的樣子釘住：不扣雜訊的話，同一張圖的增益掉到 0.10 以下 → 答 20。"""
    img = fins(45, seed)
    two = algo_period2d.estimate_period_2d(img)
    plain = algo_period2d.half_period_check(img, 20.0, 45.0, ac=two.ac)
    fixed = algo_period2d.half_period_check(img, 20.0, 45.0, ac=two.ac,
                                            noise_frac=two.noise_frac)
    assert not plain.doubled_x and plain.gain_x < algo_period2d.HALF_PERIOD_GAIN
    assert fixed.doubled_x and fixed.gain_x > algo_period2d.HALF_PERIOD_GAIN


def test_a_clean_image_does_not_change():
    m = algo_template.measure_period(fins(0))
    assert (m.px, m.py) == (40.0, 45.0)


@pytest.mark.parametrize("sigma", [40, 80, 130])
@pytest.mark.parametrize("kind", sorted(TRUTH))
def test_a_regular_lattice_is_never_doubled_however_noisy(kind, sigma):
    """**這一條是校正安全的證據。** 規則晶格上 ``ac[2q] - ac[q]`` ≈ 0，放大四倍
    還是 ≈ 0 —— 雜訊再大也不准冒出一個假的加倍。"""
    for seed in range(3):
        m = algo_template.measure_period(regular(kind, sigma, seed))
        assert not any(m.doubled), (kind, sigma, seed, m.half_gain_x, m.half_gain_y)
        assert (m.px, m.py) == TRUTH[kind], (kind, sigma, seed, m.px, m.py)


def test_without_noise_frac_it_is_the_old_check():
    img = fins(45)
    two = algo_period2d.estimate_period_2d(img)
    a = algo_period2d.half_period_check(img, 20.0, 45.0, ac=two.ac)
    b = algo_period2d.half_period_check(img, 20.0, 45.0, ac=two.ac, noise_frac=0.0)
    assert (a.px, a.py, a.gain_x, a.gain_y, a.stagger) == \
        (b.px, b.py, b.gain_x, b.gain_y, b.stagger)


def test_the_noise_fraction_is_measured_on_the_same_window():
    """乾淨的圖 ≈ 0；加了白雜訊的圖 ≈ 雜訊佔自相關原點值的比例。"""
    clean = regular("tiles", 0)
    assert algo_period2d.estimate_period_2d(clean).noise_frac < 0.01
    noisy = regular("tiles", 40)
    hp, _raw, _s = algo_period2d._hp_window(noisy)
    hp_clean, _r, _s2 = algo_period2d._hp_window(clean)
    truth = 1.0 - float(np.mean(hp_clean.astype(np.float64) ** 2)) / \
        float(np.mean(hp.astype(np.float64) ** 2))
    got = algo_period2d.estimate_period_2d(noisy).noise_frac
    assert got == pytest.approx(truth, abs=0.05), (got, truth)
    assert got <= truth + 0.02, "只准少估：多估會把沒有交錯的圖也加倍"


# --------------------------------------------------------------------------- #
# 2. 引擎的話：疑點 vs 自己修對的
# --------------------------------------------------------------------------- #
def staggered(sigma=10, seed=0, w=900, h=700):
    """交錯排列：每一列錯半格，真的單元 40 × 70（投影法只看得到 35）。"""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    xs = xx + 20 * (np.floor(yy / 35) % 2)
    img = (70 + 120 * ((((xs % 40) - 20) ** 2 + ((yy % 35) - 17) ** 2) < 64)).astype(np.float64)
    return u8(cv2.GaussianBlur(img, (0, 0), 1.0) + rng.normal(0, sigma, (h, w)))


def test_a_correction_the_engine_got_right_is_not_a_doubt():
    """交錯的圖：引擎換了量法、加了倍 —— 那是它**修對了**，不是疑點。"""
    m = algo_template.measure_period(staggered())
    assert (m.px, m.py) == (40.0, 70.0)
    assert m.notes, "它確實說了話"
    assert m.doubts == [], m.doubts


def test_only_one_method_seeing_a_period_is_a_doubt(monkeypatch):
    """投影法什麼都沒看到、只有二維自相關看到 —— 沒有人背書，那就是疑點。
    （逼出這個情況：讓投影法回「沒有」，二維照常量。）"""
    from types import SimpleNamespace
    from pitchapp.core.algo import period as algo_period
    nothing = SimpleNamespace(px=None, py=None, confidence_x=0.0, confidence_y=0.0,
                              warnings=[], candidates=[])
    monkeypatch.setattr(algo_period, "estimate_period", lambda *_a, **_k: nothing)
    m = algo_template.measure_period(regular("tiles", 10))
    said = [d for d in m.doubts if "2-D autocorrelation" in d]
    assert len(said) == 2, m.doubts


def stripes(sigma, seed=0, vertical=True, p=16, w=900, h=700):
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    img = (70 + 110 * (((xx if vertical else yy) % p) < p / 2)).astype(np.float64)
    return u8(cv2.GaussianBlur(img, (0, 0), 1.0) + rng.normal(0, sigma, (h, w)))


@pytest.mark.parametrize("sigma", [20, 30, 40])
@pytest.mark.parametrize("vertical", [True, False], ids=["vertical", "horizontal"])
def test_stripes_get_no_fake_period_on_the_empty_axis(vertical, sigma):
    """⚠ **2026-09-24 修的**：條紋沿著線的方向平移完全一樣，那一軸的自相關是一片
    接近 1 的高原；雜訊把它壓到 0.9 以下之後，「平的」判不出來，於是從高原上的
    雜訊起伏裡挑出一個 4–7 px 的假週期（σ 20–40 全錯，更吵反而對）。"""
    for seed in range(3):
        m = algo_template.measure_period(stripes(sigma, seed, vertical))
        along, across = (m.py, m.px) if vertical else (m.px, m.py)
        assert along < 2, ("假週期", vertical, sigma, seed, m.px, m.py)
        assert across == 16.0


def test_the_stripe_bug_this_fixes_is_real():
    """修之前的樣子釘住：不扣雜訊的話，同一條高原判不出「平的」。"""
    img = stripes(20)
    two = algo_period2d.estimate_period_2d(img)
    line = two.ac[:two.ac.shape[0] // 2, 0]
    assert algo_period2d._axis_period(line, 4)[0] is not None, "舊的判法：報出一個週期"
    got = algo_period2d._axis_period(line, 4, two.noise_frac)
    assert got[0] is None and got[3], "扣過雜訊：平的"


@pytest.mark.parametrize("sigma", [20, 40])
def test_a_weak_real_period_along_the_lines_is_kept(sigma):
    """反向：線上每 70 px 真的有一個 via（對比不高）—— 那一軸**不是**平的，
    扣雜訊不准把它抹成「沒有週期」。"""
    for seed in range(3):
        rng = np.random.default_rng(seed)
        yy, xx = np.mgrid[0:700, 0:900].astype(np.float64)
        img = 60 + 110 * (((xx % 24) >= 7) & ((xx % 24) < 17))
        img = img + 60 * (((xx % 24) >= 8) & ((xx % 24) < 16)
                          & ((yy % 70) >= 31) & ((yy % 70) < 39))
        img = u8(cv2.GaussianBlur(img.astype(np.float64), (0, 0), 1.0)
                 + rng.normal(0, sigma, img.shape))
        m = algo_template.measure_period(img)
        assert (m.px, m.py) == (24.0, 70.0), (sigma, seed, m.px, m.py)


def test_every_doubt_is_also_a_note():
    for img in (staggered(), fins(45), regular("tiles", 60)):
        m = algo_template.measure_period(img)
        assert set(m.doubts) <= set(m.notes)


# --------------------------------------------------------------------------- #
# 3. 候選：×3，而且排在最前面
# --------------------------------------------------------------------------- #
class _M:
    def __init__(self, px, py, cands):
        self.px, self.py, self.candidates = float(px), float(py), cands


def test_three_times_is_offered_first():
    """每 3 條線才有一個 via：量到 24 × 70，真的是 72 × 70。"""
    m = _M(24, 70, [(24, 70), (12, 70), (48, 70), (24, 35), (24, 140)])
    got = pitch_core.candidate_periods(m, (True, True), (24.0, 70.0))
    assert got[0] == (72.0, 70.0), got
    assert (24.0, 210.0) in got


def test_a_candidate_must_fit_twice_in_the_image():
    m = _M(24, 70, [(24, 70), (12, 70), (48, 70)])
    got = pitch_core.candidate_periods(m, (True, True), (24.0, 70.0),
                                       limit=(60.0, 1000.0))
    assert all(x <= 60.0 for x, _y in got), got
    assert (72.0, 70.0) not in got


def test_three_times_only_on_the_axis_in_use():
    m = _M(24, 70, [(24, 70), (12, 70), (48, 70), (24, 35)])
    got = pitch_core.candidate_periods(m, (True, False), (24.0, 70.0))
    assert got[0] == (72.0, 70.0)
    assert all(y == 70.0 for _x, y in got), got
