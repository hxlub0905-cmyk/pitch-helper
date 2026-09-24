# Vendored into d4t on 2026-07-27.
# Source project: cell-period-estimator —
#   cell_period_estimator/core/stacking.py (vendored wholesale)
# Adaptations:
#   - Module vendored unchanged (pure NumPy/OpenCV, no Qt in the source).
#   - No algorithmic changes.
"""Golden-Cell stacking, sharpness, and stacking agreement.

Pure NumPy / OpenCV.  Given a period ``(px, py)`` these helpers tile the
image into cells and average / median them into a single "Golden Cell".

⚠ **Two different questions, two different functions** (F40, 2026-08-27).
The vendored module docstring used to say "when the period is correct the
cells align and the stack is sharp; when it is wrong the cells drift and
the stack ghosts (blurs), which is what the sharpness metrics quantify."
**The last clause is false**, and it cost this project a silently useless
warning for months:

``ghosting_score``
    "How much edge energy is in *this one image*."  It never sees the
    cells that went into the stack, so it cannot tell "sharp because the
    cells aligned" from "sharp because two ghosts each contributed an
    edge".  Its value scales with contrast, noise and cell size, so it is
    only meaningful **relative to another stack of the same image**.
``stack_agreement``
    "Did the cells actually agree with each other."  Dimensionless,
    comparable across images, and 0 when they agree no better than
    chance.  This is the one to threshold against a fixed number.
    ⚠ **Comparable across images only once the noise is taken out**
    (2026-09-24): on its own it equals the signal share of each cell's
    variance when the period is exactly right, so a noisy image scores a
    correct period red.  :func:`measure_agreement` with
    :func:`noise_variance` removes that — see there.

Measured, on a line/space pattern (period 40) — the shape no test covered
until F40:

============================  ==========  ==============  ============
                              correct 40  half-period 60  pure noise
============================  ==========  ==============  ============
``ghosting_score`` (0..100)   43.0        **37.3–76.1**   **up to 99.4**
``stack_agreement`` (0..1)    0.24–0.99   **0.000**       0.003
============================  ==========  ==============  ============

The pure-noise column is the one to remember: an image with *nothing to
stack* scored 99.4 / 100 "sharpness" at σ=60, because noise is edges.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

import cv2
import numpy as np


# --------------------------------------------------------------------------- #
# 疊得算不算糊：**一個門檻，一個家**
# --------------------------------------------------------------------------- #
# ⚠ 這個常數 2026-09-22 從 `ui/template_dialog.py` 搬到這裡（F120 第十七輪）。
# 它是 `stack_agreement` 的門檻，本來就該住在那支旁邊 —— 而它待在一支 1,300
# 行的 Qt 對話框裡的代價是：`pitch_helper` 為了**一個 float** 得把整支對話框
# import 進來。那條線寫在 `docs/plans/F120-pitch-helper.md` §28 的拆解清單上，
# 而它是清單裡唯一一條「真的該剪」的。`template_dialog` 現在從這裡轉出去，
# 所以 `template_dialog.BLURRED_BELOW` 這個名字照樣在。
#: 一致性低於這個值就講「疊出來是糊的」（`golden.stack_agreement`，0–1）。
#:
#: **這個數字是量出來的，不是挑的**（F40）。正確的週期在 repo 自己的擬真產生器
#: （`tools/make_mgepi_real.render_die`）上落在 0.89–0.97；條紋圖上六種對比／
#: 雜訊組合落在 0.24–0.99。不成比例的錯週期（47、42、38）是 0.00–0.08，
#: 純雜訊 0.003。0.5 因此放行全部擬真的，唯一擋下的正確案例是「對比 0.25 ＋
#: 雜訊 20」的 0.243 —— 那個 stack 本來就不該被信任。
#:
#: ⚠ **這一條抓的是「格子對不上」，不是「週期挑錯了」——三種壞法有三個機制，
#: 不要指望一個數字全包**（量過的，方波條紋、真值 40）：
#:
#: =====================  ==========  =========================================
#: 壞法                   agreement   誰抓它
#: =====================  ==========  =========================================
#: 不成比例的週期（47）    0.000       **這一條**
#: 完全沒有東西可疊        0.003       **這一條**
#: cell 是 k 倍（80）      0.995       `cell_self_period` 的 k× 提示（而且 2×
#:                                    的 cell 是**合法的**，使用者要得到）
#: 一半／1.5 倍（20、60）  0.69–0.93   `estimate_period` 的諧波修正（自相關那
#:                                    一層），以及 60 也會觸發 k× 提示
#: =====================  ==========  =========================================
#:
#: 對稱性高的圖案上，半週期的格子彼此**真的**蠻像的 —— 0.93 是誠實的回答，
#: 不是漏抓。那一題屬於上游。
#:
#: ⚠ 這個門檻**只能**跟 `agreement` 比。以前它掛在 `ghosting`（銳利度）上，
#: 而那個量沒有正規化 —— 見模組說明。
BLURRED_BELOW = 0.5


def _to_gray(image: np.ndarray) -> np.ndarray:
    arr = np.asarray(image)
    if arr.ndim == 3:
        if arr.shape[2] == 4:
            arr = cv2.cvtColor(arr, cv2.COLOR_BGRA2GRAY)
        else:
            arr = cv2.cvtColor(arr, cv2.COLOR_BGR2GRAY)
    return arr


def tile_coords(shape: Tuple[int, ...], px: int, py: int,
                origin: Tuple[int, int] = (0, 0)) -> List[Tuple[int, int]]:
    """Top-left ``(x, y)`` of every *complete* cell.

    Cells that would run past the image border are skipped.  This is the
    single source of truth for cell placement used by both stacking and
    period refinement.
    """
    h, w = shape[:2]
    # 原點也收成整數：`GoldenCell.origin` 從 F105 起可以是小數（原圖座標），
    # 而 `range` 吃到 float 會炸。整數進來就是恆等，逐位元組不變。
    ox, oy = int(origin[0]), int(origin[1])
    px, py = int(px), int(py)
    if px < 1 or py < 1:
        return []
    xs = range(ox, w - px + 1, px)
    ys = range(oy, h - py + 1, py)
    return [(x, y) for y in ys for x in xs]


def cell_origins(shape: Tuple[int, ...], px: float, py: float,
                 origin: Tuple[float, float] = (0.0, 0.0)
                 ) -> List[Tuple[float, float]]:
    """小數週期版的 :func:`tile_coords`：每一個**完整**格子的左上角 ``(x, y)``。

    F105：週期可以是 79.5 這種數（見 `template.build_golden_cell`）。格子的
    位置是 ``origin + (i·px, k·py)``，而不是整數步進 —— 79.5 對 79 在 4000 px
    上差 25 px，格線就是這樣「靠邊會滑」的。

    整數參數時逐元素等於 ``tile_coords``（測試釘著）；回傳的是 float，
    **拿去切片之前要自己決定怎麼取整** —— 疊圖不走這裡（疊圖先把影像重採樣成
    整數 pitch 再走 ``tile_coords``），這一支是給格線檢視與「幾格」用的。
    """
    h, w = int(shape[0]), int(shape[1])
    fx, fy = float(px), float(py)
    if not (fx >= 1.0 and fy >= 1.0) or h < 1 or w < 1:
        return []
    ox, oy = float(origin[0]), float(origin[1])
    out: List[Tuple[float, float]] = []
    # 跟 `tile_coords` 一樣：右／下超出影像的那一格不算。用 1e-9 吃掉
    # 「i·px 剛好等於邊界」的浮點誤差，免得整數參數時少一格。
    ny = int(np.floor((h - oy - fy) / fy + 1e-9)) + 1 if h - oy >= fy else 0
    nx = int(np.floor((w - ox - fx) / fx + 1e-9)) + 1 if w - ox >= fx else 0
    for k in range(max(0, ny)):
        y = oy + k * fy
        for i in range(max(0, nx)):
            out.append((ox + i * fx, y))
    return out


def stack_cells(image: np.ndarray, px: int, py: int, method: str = "mean",
                origin: Tuple[int, int] = (0, 0),
                sample_n: Optional[int] = None, seed: int = 0) -> np.ndarray:
    """Stack all (or ``sample_n`` random) cells into one ``(py, px)`` image.

    ``method="mean"`` (default) is sensitive to phase error and makes
    ghosting obvious; ``method="median"`` is robust to sparse defects.
    """
    gray = _to_gray(image)
    px, py = int(px), int(py)
    coords = tile_coords(gray.shape, px, py, origin)
    if not coords:
        return np.zeros((max(py, 1), max(px, 1)), dtype=np.uint8)

    if sample_n is not None and 0 < sample_n < len(coords):
        rng = np.random.default_rng(seed)
        idx = rng.choice(len(coords), size=sample_n, replace=False)
        coords = [coords[i] for i in idx]

    if method != "median" and sample_n is None:
        # **平均那條路不要把每一格各切一份出來**（F86，2026-09-07）。
        #
        # 原本的寫法是 `np.stack([...每一格...])` 再對 axis 0 取平均，而那個
        # 中間陣列是「格數 × py × px × 8 bytes」—— 7680×7680 的圖上是
        # **469 MB**，配一次 0.33 秒。`choose_origin` 的相位搜尋要疊 281 次，
        # 所以整整 93 秒花在配置與丟棄同一塊記憶體上（實測 130 s／張，而且
        # 是在 UI 執行緒上跑）。
        #
        # reshape 成 ``(ny, py, nx, px)`` 再對 (0, 2) 取平均是**同一組數字的
        # 同一個平均**，只是不必把它們搬到別的地方去加。實測**逐位元組相同**
        # （`tests/test_period_golden.py` 釘著），而且 7 倍快。
        #
        # ⚠ 只走得了 ``mean`` 而且沒有抽樣：中位數要看得到每一格
        # （那正是它對稀疏缺陷免疫的原因），抽樣挑的格子不連續，兩者都
        # reshape 不出來。
        ox, oy = int(origin[0]), int(origin[1])
        h, w = gray.shape[:2]
        nx, ny = (w - ox) // px, (h - oy) // py
        block = gray[oy:oy + ny * py, ox:ox + nx * px]
        stacked = block.reshape(ny, py, nx, px).mean(axis=(0, 2),
                                                     dtype=np.float64)
        return np.clip(stacked, 0, 255).astype(np.uint8)

    cells = np.stack([
        gray[y:y + py, x:x + px].astype(np.float64) for (x, y) in coords
    ])
    if method == "median":
        stacked = np.median(cells, axis=0)
    else:
        stacked = cells.mean(axis=0)
    return np.clip(stacked, 0, 255).astype(np.uint8)


def ghosting_score(stacked: np.ndarray) -> Tuple[float, float, float]:
    """Quantify the sharpness of a stacked cell.

    Returns ``(score_0_100, laplacian_var, edge_contrast)``.  ``score``
    is a saturating 0..100 mapping for display; ``laplacian_var`` is the
    raw (unsaturated) value callers should rank by.

    ⚠ **What this is not** (F40).  It takes *one* image and asks how much
    high-frequency energy is in it.  It never sees the cells that were
    stacked, so it cannot answer "did they align" — and the two questions
    genuinely come apart: a mis-phased stack superimposes two copies and
    so carries *two* sets of edges, which **raises** this number.  Use
    :func:`stack_agreement` for "did they align".

    It is also **not comparable across images**: the value scales with
    contrast, noise and cell size.  ``0..100`` looks like a percentage
    and is not one — pure noise reaches 99.4 at σ=60.  Rank two stacks of
    the *same* image with it; never compare it to a fixed threshold.
    """
    g = stacked.astype(np.float64)
    if g.size == 0:
        return 0.0, 0.0, 0.0
    lap_var = float(cv2.Laplacian(g, cv2.CV_64F).var())
    gx = cv2.Sobel(g, cv2.CV_64F, 1, 0, ksize=3)
    gy = cv2.Sobel(g, cv2.CV_64F, 0, 1, ksize=3)
    edge_contrast = float(np.hypot(gx, gy).mean())
    # Saturating map: high lap_var -> sharp -> approaches 100.
    score = float(np.clip(100.0 * (1.0 - np.exp(-lap_var / 200.0)), 0.0, 100.0))
    return score, lap_var, edge_contrast


def stack_agreement(image: np.ndarray, px: int, py: int,
                    origin: Tuple[int, int] = (0, 0),
                    noise_var: float = 0.0) -> float:
    """Did the cells actually agree with each other?  ``0``..``1`` (F40).

    ``1`` = every cell landed on top of the others; ``0`` = they agree no
    better than an unrelated pile of pixels.  Unlike :func:`ghosting_score`
    this is dimensionless, so it is the one that may be compared against a
    fixed threshold.

    How
    ---
    Cells that align average into something that keeps its structure;
    cells that do not average into mush.  So compare the variance of the
    stack against the variance of a typical single cell::

        a = var(mean(cells)) / mean(var(cell_i))        # in (1/n, 1]

    ⚠ **The ``1/n`` floor has to come off.**  ``n`` unrelated cells still
    leave ``var(mean) ≈ var(cell)/n`` behind, so a small image that only
    fits two cells scores 0.5 for *any* period.  Measured on the repo's
    own synthetic die (81×81, two cells): pure noise is ``0.499`` before
    the correction and ``0.000`` after it.  Without this the threshold
    would mean something different on every image size — which is exactly
    the bug this function exists to replace.

    Returns ``0.0`` when fewer than two whole cells fit (nothing was
    stacked, so nothing agreed) or when the cells are flat.

    ``noise_var`` (2026-09-24) — see :func:`measure_agreement`.  ``0`` (the
    default) is the F40 number, byte for byte.
    """
    return measure_agreement(image, px, py, origin, noise_var).value


# --------------------------------------------------------------------------- #
# 雜訊不是對不齊（2026-09-24）
# --------------------------------------------------------------------------- #
#: 雜訊校正最多把分數放大到 ``1 / (1 - 這個值)`` 倍（0.75 → 4 倍）。
#:
#: 超過它的圖，每一格裡**四分之三以上是雜訊** —— 校正的倍數再往上走，分子
#: 那一點抽樣誤差也會被放大成一個看起來像證據的數字。所以到這裡就停，而畫面
#: 要講「這張圖太吵，看圖不要只看分數」（`Agreement.noise_frac` 就是讓它講的）。
NOISE_FRACTION_CAP = 0.75

#: 估雜訊用的頻帶（半徑，cycles/pixel）：每一帶取功率譜的中位數，**取最小的
#: 那一帶**。見 :func:`noise_variance`。
_NOISE_BANDS = (0.10, 0.20, 0.30, 0.40, 0.75)
_NOISE_TILE = 256
_NOISE_TILES_PER_SIDE = 4
#: 貼著 kx=0／ky=0 的幾條頻率不看：一維的結構（條紋、掃描線、邊界接縫）
#: 全擠在那兩條線上，而它們不是雜訊。
_NOISE_AXIS_BINS = 2
_NOISE_MIN_SIDE = 32


def noise_variance(image: np.ndarray) -> float:
    """每個像素**彼此獨立的那一部分**的變異數（灰階²）—— 拿去校正一致性用。

    為什麼要有它（2026-09-24）
    --------------------------
    :func:`stack_agreement` 在週期完全正確時等於 ``V_S / (V_S + N)``：訊號
    佔每一格變異數的比例（推導見 :func:`measure_agreement`）。也就是說**雜訊
    越大分數越低，而週期一個像素都沒有錯** —— 實測同一張 60×44 的圖、同一個
    對的週期：σ=10 → 0.94，σ=25 → 0.71（黃），σ=60 → 0.31（紅，畫面寫
    「this period is wrong」）。要把雜訊從分數裡拿掉，得先知道它有多大。

    怎麼估 —— 以及為什麼**不是**最常見的那一種
    ------------------------------------------
    最常見的做法（Immerkær 1996：拉普拉斯型的核取絕對值平均／中位數）
    **在這裡是危險的**：它把邊緣當成雜訊。實測一張**完全沒有雜訊**的 4 px
    棋盤格，它說 **97.7%** 的變異數是雜訊 —— 而雜訊估多了，校正就會把
    **錯的週期**也抬成綠燈。

    這一支看的是**功率譜**：週期性的結構在頻譜上是一顆顆**稀疏的峰**
    （倒晶格上的點），白雜訊是**一整片平的底**。所以：

    1. 切成最多 4×4 塊 256² 的小塊，各乘 Hann 窗做 FFT（窗讓峰不會漏成一片）；
    2. 丟掉貼著兩條軸的頻率（一維結構擠在那裡）與半徑 < 0.1 的低頻
       （照明梯度、缺陷、量測條這些不重複的東西在那裡）；
    3. 其餘分成幾個半徑帶，每一帶取**中位數**（峰是少數，中位數看到的是底），
       除以 ln 2（複數高斯的 ``|F|²`` 是指數分布，中位數 = ln 2 × 平均）；
    4. **取最小的那一帶。**

    第 4 步是它安全的原因：白雜訊每一帶都一樣，最小值就是答案；**不是白的
    雜訊**（銳化過的影像高頻被放大、模糊過的低頻比較多）各帶不一樣，而最小
    的那一帶一定**不超過**全頻的平均 —— 也就是說它只會**少估**，少估的代價
    是校正得不夠（分數偏保守），不會把錯的週期抬上去。

    實測（`noise_variance / 真正加進去的雜訊變異數`，11 種圖樣 × 7 種雜訊）：
    乾淨影像（含棋盤格、條紋、文字、大缺陷）**全部 0.000**；白雜訊 0.97–1.05、
    隨訊號變化的（Poisson 型）0.92–1.04；JPEG 0.82–1.03；銳化過 0.65–0.96；
    相鄰像素相關的雜訊（模糊過、掃描方向拖尾、放大過的圖）0.00–0.04 ——
    **那一種這裡幾乎不校正**，分數會跟以前一樣偏低，而那是刻意選的那一邊。
    120 px 的小圖頻率格子少，白雜訊最低估到 0.70（同樣是少估）。

    小於 32 px 的圖估不出來，回 ``0``（＝不校正，也就是以前的行為）。
    """
    g = np.asarray(_to_gray(image), dtype=np.float64)
    if g.ndim != 2 or min(g.shape) < _NOISE_MIN_SIDE:
        return 0.0
    h, w = g.shape
    t = min(_NOISE_TILE, 1 << int(np.floor(np.log2(min(h, w)))))
    ny = max(1, min(_NOISE_TILES_PER_SIDE, h // t))
    nx = max(1, min(_NOISE_TILES_PER_SIDE, w // t))
    ys = np.linspace(0, h - t, ny).astype(int)
    xs = np.linspace(0, w - t, nx).astype(int)
    win = np.outer(np.hanning(t), np.hanning(t))
    norm = float((win * win).sum())
    f = np.fft.fftfreq(t)
    radius = np.hypot(f[:, None], f[None, :])
    k = np.abs(f * t)
    off_axis = (k[:, None] > _NOISE_AXIS_BINS) & (k[None, :] > _NOISE_AXIS_BINS)
    power = []
    for y in ys:
        for x in xs:
            tile = g[y:y + t, x:x + t]
            tile = tile - tile.mean()
            power.append(np.abs(np.fft.fft2(tile * win)) ** 2 / norm)
    p = np.stack(power)
    floors = []
    for lo, hi in zip(_NOISE_BANDS[:-1], _NOISE_BANDS[1:]):
        band = off_axis & (radius >= lo) & (radius < hi)
        if band.any():
            floors.append(float(np.median(p[:, band])) / float(np.log(2.0)))
    return max(0.0, min(floors)) if floors else 0.0


@dataclass(frozen=True)
class Agreement:
    """:func:`measure_agreement` 的答案。"""

    #: 0–1。給了 ``noise_var`` 就是**扣掉雜訊之後**的；沒給就等於 ``raw``。
    value: float
    #: F40 的那個數字（沒有雜訊校正）—— 留著是為了看得到校正了多少。
    raw: float
    #: 每一格的變異數裡估計有幾成是雜訊（0–1）。≥ :data:`NOISE_FRACTION_CAP`
    #: 的時候校正已經封頂，畫面要說「太吵，看圖」。
    noise_frac: float
    #: 疊了幾格。
    n: int


def measure_agreement(image: np.ndarray, px: int, py: int,
                      origin: Tuple[int, int] = (0, 0),
                      noise_var: float = 0.0) -> Agreement:
    """:func:`stack_agreement` 本人，外加它扣掉雜訊的版本（2026-09-24）。

    雜訊為什麼可以整個扣掉
    ----------------------
    每一格 = 訊號 ``S_i`` ＋ 雜訊（每像素變異數 ``N``，格與格之間獨立）。
    ``V_S`` 是一格裡訊號的變異數。那麼::

        mean(var(cell_i)) = V_S + N
        var(mean(cells))  = var(mean(S_i)) + N/n

    F40 的分數是 ``(var(mean)/mean(var) - 1/n) / (1 - 1/n)``，代進去，**分子
    裡的 N 剛好消掉**::

        a = (var(mean(S_i)) - V_S/n) / ((1 - 1/n) · (V_S + N))

    雜訊只活在分母裡。週期完全正確時 ``var(mean(S_i)) = V_S``，``a`` 就是
    ``V_S / (V_S + N)`` —— 訊號佔的比例，跟週期無關。把分母換成 ``V_S`` 就是
    只看訊號的那個答案::

        a_denoised = a · (V_S + N) / V_S = a / (1 - N / mean(var(cell_i)))

    它回答的是「**如果這張圖沒有雜訊**，這些格子對得多齊」—— 那才是週期對不對
    的問題。錯的週期分子本來就小，乘上去還是小（驗證見
    `tests/test_agreement_noise.py`：對照同一個圖樣**沒加雜訊**時的分數，
    校正後不准高出去）。

    ⚠ ``N`` 要是**同一批像素**的雜訊（`build_golden_cell` 疊的是重採樣過的
    那一張，雜訊就要在那一張上估）。放大倍數封頂在
    ``1 / (1 - NOISE_FRACTION_CAP)``。
    """
    gray = _to_gray(image)
    px, py = int(px), int(py)
    coords = tile_coords(gray.shape, px, py, origin)
    cells = [gray[y:y + py, x:x + px].astype(np.float64) for (x, y) in coords]
    cells = [c for c in cells if c.shape == (py, px)]
    n = len(cells)
    if n < 2:
        return Agreement(0.0, 0.0, 0.0, n)
    per_cell = float(np.mean([float(c.var()) for c in cells]))
    if per_cell < 1e-9:                 # a flat crop agrees with itself
        return Agreement(0.0, 0.0, 0.0, n)   # trivially — that is not evidence
    stacked_var = float(np.stack(cells).mean(axis=0).var())
    raw = stacked_var / per_cell
    floor = 1.0 / float(n)
    a = (raw - floor) / (1.0 - floor)
    plain = float(np.clip(a, 0.0, 1.0))
    nv = float(noise_var or 0.0)
    if not nv > 0.0:
        return Agreement(plain, plain, 0.0, n)
    frac = min(1.0, nv / per_cell)
    signal = 1.0 - min(frac, NOISE_FRACTION_CAP)
    return Agreement(float(np.clip(a / signal, 0.0, 1.0)), plain, frac, n)


# ``refine_period`` / ``candidate_periods`` were deleted on 2026-08-27 (F40).
# They came in with the vendored module and never gained a production caller —
# ``estimate_period`` finds the period by autocorrelation and never asks this
# module anything.  Measured before deleting: ``refine_period`` starting from
# 26 walks to 20 when the truth is 28, because it ranks candidates by the
# sharpness of the stack, and sharpness is not alignment (see the module
# docstring above).  Keeping a broken helper alive for its own test is how a
# wrong answer waits for a first caller.
