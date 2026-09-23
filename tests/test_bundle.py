"""搬運那條路：`tools/release.py` 產的兩個檔案要**能用**，不只是存在。

這一套是從來源專案（d4t）帶過來的，而那邊的每一條都是**踩過才寫的**。
受限機器的情況是：不能跑 git、不能下載任何東西，但看得到 GitHub 上的檔案
並且可以複製。所以取得程式碼的動作是「在瀏覽器上開 `bundle/…_bundle.py`
→ 按複製 → 貼進記事本存檔 → `python` 跑它」。

⚠ **這條路壞掉的時候，症狀不會出現在這裡 —— 會出現在那台機器上**，而且
通常是在需要它的那一天。所以下面每一條都在問同一件事的一個面向：
*貼過去、存下來、跑起來，還是原來那份程式碼嗎。*
"""
from __future__ import annotations

import ast
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(REPO, "tools")
sys.path.insert(0, TOOLS)

import make_filelist                                   # noqa: E402
import make_text_bundle                                # noqa: E402
import release                                         # noqa: E402


def _has_git() -> bool:
    try:
        subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=REPO,
                       check=True, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
    except (OSError, subprocess.CalledProcessError):
        return False
    return True


#: 這一整份都建立在 `git ls-files` 上。受限機器上沒有 git，而那台機器
#: **不需要**跑這些測試 —— 它只負責拿到程式碼並執行。
needs_git = pytest.mark.skipif(not _has_git(), reason="需要 git work tree")


@pytest.fixture(scope="module")
def built() -> str:
    """打一次包給整份測試共用 —— 壓縮是 1.3 秒，每條各打一次太貴。"""
    return make_text_bundle.build("pitch_helper_bundle.py", REPO)


def _write(path, text: str) -> None:
    with open(str(path), "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


# --------------------------------------------------------------------------- #
# 1. 兩個產出跟目前的檔案一致（忘了重跑 release.py 不會有任何症狀）
# --------------------------------------------------------------------------- #
@needs_git
def test_the_transfer_files_are_up_to_date():
    """⚠ **忘了跑 `tools/release.py` 不會有任何症狀** —— 直到受限機器上少一個
    檔案，或 `check_files.py` 說「你的檔案跟清單一致」而那份清單是三天前的。

    這一條的錯誤訊息就是要跑的那一行指令。
    """
    problems = release.stale(REPO)
    assert not problems, (
        "搬運檔過期了：\n    %s\n\n  跑：git add -A && python tools/release.py "
        "&& git add -A" % "\n    ".join(problems))


@needs_git
def test_release_check_agrees_with_writing_it_out():
    """`--check` 說「最新的」就必須真的是重產一次會得到的東西 ——
    不然它只是一個永遠說 OK 的擺設。"""
    assert release.stale(REPO) == []
    r = subprocess.run([sys.executable, os.path.join(TOOLS, "release.py"),
                        "--check"], cwd=REPO, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, timeout=300)
    assert r.returncode == 0, r.stdout.decode("utf-8", "replace")


@needs_git
def test_the_manifest_covers_every_tracked_file_and_not_itself():
    """清單列的是「要複製哪些檔案」，而它**不列自己**（它的 SHA 沒辦法包含
    自己的 SHA），也不列 `bundle/`（那是 repo 的複本不是內容 —— 列進去的話
    分批解包的「還缺幾個」永遠到不了 0）。"""
    listed = [ln.split(" ", 1)[1] for ln in make_filelist.build_lines(REPO)
              if not ln.startswith("#")]
    assert make_filelist.MANIFEST not in listed
    assert not [p for p in listed if p.startswith("bundle/")]
    assert "main.py" in listed and "pitchapp/ui/pitch_helper.py" in listed
    # ⚠ 唯一那份決策紀錄要搬得過去 —— 見 `make_filelist.EXCLUDE_DIRS` 的說明。
    assert "docs/F120-pitch-helper.md" in listed


@needs_git
def test_the_bundle_carries_the_file_listing(built):
    """⚠ **包裡面要含著那份清單。**

    這是移植這套工具的時候真的踩到的：清單是 `release.py` **當場產**的，而
    打包讀的是 `git ls-files` —— 第一次跑的時候那份清單還沒被 `git add`，
    於是包裡沒有它。症狀是受限機器解完包之後 `check_files.py` 說「找不到
    清單」，而清單正是它唯一的工作依據。

    `release.py` 的順序（先清單、再打包）與 `git add -A` 在前後**各一次**
    就是在防這件事。
    """
    paths = [rel for _sha, rel in release._bundle_entries(built)]
    assert make_filelist.MANIFEST in paths


# --------------------------------------------------------------------------- #
# 2. 貼過去、存下來、跑起來，還是原來那份程式碼嗎
# --------------------------------------------------------------------------- #
@needs_git
def test_the_text_bundle_round_trips_byte_for_byte(tmp_path, built):
    """整個 repo 打成一個純文字檔、解開、**逐位元組**比對。

    這是整份測試的主軸：下面幾條各自守一種弄壞它的方式。
    """
    out = tmp_path / "pitch_helper_bundle.py"
    _write(out, built)
    dest = tmp_path / "un"
    r = subprocess.run([sys.executable, str(out), "--dest", str(dest)],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       timeout=300)
    assert r.returncode == 0, r.stdout.decode("utf-8", "replace")

    for rel in make_filelist.tracked_files(REPO) + [make_filelist.MANIFEST]:
        src = pathlib.Path(REPO) / rel
        got = dest / rel
        assert got.is_file(), "%s 沒有被解出來" % rel
        assert got.read_bytes() == src.read_bytes(), rel


@needs_git
def test_the_bundle_is_still_valid_python(built):
    """⚠ 資料區的每一行都必須是**註解**。

    來源專案踩過：資料區是裸的文字，而 Python 在跑之前先編譯整個檔案，於是
    它去解析某個 .md 裡的全形括號然後 SyntaxError —— 包本身打得開不代表
    `python 那個檔案` 跑得動。
    """
    ast.parse(built, filename="bundle")

    # 那個分隔字串在產出的檔案裡出現**三次**（解包程式裡的賦值、真正的分隔
    # 行、以及資料區裡 —— `make_text_bundle.py` 自己也在 repo 裡），所以
    # split / rsplit 都不對：要找**整行剛好等於**它的那一行。
    lines = built.splitlines()
    body = lines[lines.index(make_text_bundle.SENTINEL) + 1:]
    assert body, "資料區是空的"
    offenders = [ln for ln in body if ln and not ln.startswith("#")]
    assert not offenders, offenders[:3]


@needs_git
def test_the_bundle_is_pure_ascii(built):
    """⚠ **一個非 ASCII 位元組都不准有。**

    來源專案 2026-09-03 在受限機器上撞到的：包裡有 31% 的中文位元組，而中文
    Windows 的記事本存檔是 ANSI（cp950）—— 中文因此變成 Big5 位元組，Python
    用 UTF-8 讀就死在 `SyntaxError: Non-UTF-8 code starting with '\\xe5'`。

    **純 ASCII 的檔案不可能這樣壞**，不管記事本挑哪一種編碼。所以資料區是
    逐檔 base64，而且**解包程式的檔頭與訊息全部是英文** —— 那一段是最不能
    壞的，它就是解包本身。

    ⚠ 這一條看的是**產出來的東西**不是程式碼：非 ASCII 可以從任何一個地方
    溜進去（檔頭、分隔行、一句錯誤訊息）。
    """
    bad = [(i, ln) for i, ln in enumerate(built.split("\n"), 1)
           if any(ord(c) > 127 for c in ln)]
    assert not bad, (
        "包裡有 %d 行含非 ASCII，第一行是第 %d 行：\n  %s\n\n"
        "受限機器的記事本會把它存成 cp950，然後 Python 讀不動整個檔案。"
        % (len(bad), bad[0][0], bad[0][1][:120]))


@needs_git
def test_a_bundle_saved_as_ansi_still_unpacks(built):
    """上面那條測「沒有非 ASCII」，這一條測「所以它撐得住」。

    用 cp950 存一次（＝中文 Windows 記事本的預設）照樣解得開；順便驗 CRLF
    與 UTF-8 BOM（記事本的另外兩種存法）。只有這一條會在「某天有人加了一個
    中文字、而上面那條被改鬆」的時候還抓得到。
    """
    for enc, newline in (("cp950", "\r\n"), ("utf-8-sig", "\r\n"),
                         ("utf-8", "\n")):
        d = tempfile.mkdtemp()
        try:
            path = os.path.join(d, "pitch_helper_bundle.py")
            with open(path, "w", encoding=enc, newline=newline) as f:
                f.write(built)
            r = subprocess.run([sys.executable, path, "--list"], cwd=d,
                               stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, timeout=300)
            out = r.stdout.decode("utf-8", "replace")
            assert r.returncode == 0, "存成 %s 之後解不開：\n%s" % (enc, out)
            assert "files." in out, out
        finally:
            shutil.rmtree(d, ignore_errors=True)


@needs_git
def test_the_bundle_survives_having_its_line_endings_changed(tmp_path, built):
    """瀏覽器下載、記事本另存、郵件過濾器 —— 任何一步都可能把 LF 換成 CRLF。

    格式以**行數**而不是位元組數為單位就是為了對它免疫；真的用位元組數的話，
    錯誤會出現在第一個檔案**之後的全部檔案**上。
    """
    crlf = tmp_path / "crlf.py"
    crlf.write_bytes(built.encode("utf-8").replace(b"\n", b"\r\n"))
    dest = tmp_path / "un_crlf"
    r = subprocess.run([sys.executable, str(crlf), "--dest", str(dest)],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       timeout=300)
    assert r.returncode == 0, r.stdout.decode("utf-8", "replace")
    one = pathlib.Path(REPO) / "main.py"
    assert (dest / "main.py").read_bytes() == one.read_bytes()


def test_a_tampered_bundle_refuses_to_land_anything(tmp_path):
    """SHA 對不上的時候**不可以寫出半份程式碼** ——「看起來拿到了但其實是
    壞的」比「拿不到」糟得多，因為沒有人會去懷疑它。"""
    body = b"print('hi')\n"
    sha = make_text_bundle.blob_sha(body)
    header = make_text_bundle.EXTRACTOR % {
        "build": "000000000000 2026-01-01",
        "name": "b.py", "sentinel": make_text_bundle.SENTINEL,
        "part": 1, "n_parts": 1, "total": 1}
    good = "\n".join([header, make_text_bundle.SENTINEL, "#ENC text",
                      "#F %s 2 pitchapp/x.py" % sha, "#print('hi')", "#"]) + "\n"
    bad = good.replace("#print('hi')", "#print('tampered')")

    for text, expect, should_exist in ((good, 0, True), (bad, 1, False)):
        path = tmp_path / ("b_%d.py" % expect)
        _write(path, text)
        dest = tmp_path / ("d_%d" % expect)
        r = subprocess.run([sys.executable, str(path), "--dest", str(dest)],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           timeout=120)
        out = r.stdout.decode("utf-8", "replace")
        assert r.returncode == expect, out
        assert (dest / "pitchapp" / "x.py").exists() is should_exist
        if expect:
            assert "SHA" in out


@needs_git
def test_the_bundle_does_not_contain_a_previous_bundle(built):
    """產出物自己不進包裡 —— 不然每打一次包，repo 就多一份上一次的包，
    而且是指數成長。"""
    paths = [rel for _sha, rel in release._bundle_entries(built)]
    assert not [p for p in paths if p.startswith("bundle/")], paths[:5]


# --------------------------------------------------------------------------- #
# 3. 這幾支工具要在「什麼都還沒裝」的機器上跑得動
# --------------------------------------------------------------------------- #
#: 受限機器上這幾支要在相依套件裝好**之前**就能跑，所以 stdlib-only ＋ 3.9。
#: 這裡**反過來列**（預設每一支都要守）而不是列一張名單：加一支新工具會自動
#: 被納入，不需要有人記得回來改名單。
BOOTSTRAP = sorted(p for p in os.listdir(TOOLS) if p.endswith(".py"))


def _stdlib_names():
    """標準函式庫的頂層模組名。

    ⚠ ``sys.stdlib_module_names`` 是 **Python 3.10+** 才有的，而這個 repo 的
    底線是 3.9（廠內機器可能是舊版）。3.9 走下面的**探測**：問直譯器自己
    「內建模組有哪些」加上掃一次 stdlib 目錄。

    ⚠ **不要改回一張手抄的名單。** 來源專案那張抄了 23 個名字，漏掉
    `csv` / `zlib` / `lzma` / `binascii`，而症狀藏了很久 —— 手寫的清單會漂，
    漂掉的時候測試通常還是綠的。
    """
    names = getattr(sys, "stdlib_module_names", None)
    if names:
        return set(names)

    import sysconfig

    out = set(sys.builtin_module_names)           # zlib / binascii 常在這裡
    stdlib = sysconfig.get_paths().get("stdlib") or ""
    # ⚠ Windows 把 C 寫的標準模組（`_lzma` …）放在 `DLLs\`，不在 `Lib\`
    # 也沒有 `lib-dynload`。
    for base in (stdlib, os.path.join(stdlib, "lib-dynload"),
                 os.path.join(os.path.dirname(stdlib), "DLLs")):
        if not os.path.isdir(base):
            continue
        for entry in os.listdir(base):
            full = os.path.join(base, entry)
            if entry.endswith(".py"):
                out.add(entry[:-3])
            elif entry.endswith((".so", ".pyd")):
                out.add(entry.split(".")[0])      # _lzma.cpython-39-…so
            elif os.path.isdir(full) and os.path.isfile(
                    os.path.join(full, "__init__.py")):
                out.add(entry)
    out.discard("")
    return out


#: 同一個資料夾裡的同伴互相 import 是可以的 —— 這條規則要禁的是**第三方
#: 套件**（那台機器上可能一個都裝不起來），不是同伴。
_SIBLINGS = {os.path.splitext(f)[0] for f in BOOTSTRAP}


@pytest.mark.parametrize("script", BOOTSTRAP)
def test_the_tools_import_only_stdlib_at_module_level(script):
    """⚠ 模組層 import 一個第三方套件 = 那台機器上一個 ImportError，
    而它發生在使用者**還沒有任何辦法裝東西**的時候。"""
    src = open(os.path.join(TOOLS, script), encoding="utf-8").read()
    tree = ast.parse(src)
    bad = []
    for node in tree.body:                      # 只看模組層，函式內的 lazy 不算
        if isinstance(node, ast.Import):
            bad += [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            bad.append(node.module.split(".")[0])
    known = _stdlib_names() | _SIBLINGS
    outside = [m for m in bad if m not in known]
    assert not outside, "%s 在模組層 import 了非標準函式庫：%s" % (script, outside)


def test_the_python39_probe_agrees_with_the_real_list():
    """3.9 那條**探測**的路要蓋得住工具真的用到的名字。

    只問一個方向：全等是問錯的問題（探測會多撈到 `_ctypes` 那種內部模組）。
    """
    used = set()
    for script in BOOTSTRAP:
        tree = ast.parse(open(os.path.join(TOOLS, script), encoding="utf-8").read())
        for node in tree.body:
            if isinstance(node, ast.Import):
                used.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                used.add(node.module.split(".")[0])
    import sysconfig                                          # noqa: F401
    saved = getattr(sys, "stdlib_module_names", None)
    try:
        if saved is not None:
            del sys.stdlib_module_names
        probed = _stdlib_names()
    finally:
        if saved is not None:
            sys.stdlib_module_names = saved
    missing = [m for m in used - _SIBLINGS if m not in probed]
    assert not missing, "3.9 的探測漏掉了：%s" % missing


@pytest.mark.parametrize("script", BOOTSTRAP)
def test_the_tools_are_python39_compatible(script):
    """受限機器上的 Python 可能是舊版 —— 這正是這個 repo 到處在防的事。"""
    src = open(os.path.join(TOOLS, script), encoding="utf-8").read()
    ast.parse(src, feature_version=(3, 9))


# --------------------------------------------------------------------------- #
# 4. 包裡的每一句話在這個 repo 裡都要是真的
# --------------------------------------------------------------------------- #
@needs_git
def test_the_next_step_it_prints_names_things_that_exist(built):
    """⚠ **解完包印出來的下一步，要指向這個 repo 真的有的東西。**

    這套工具是從 d4t 搬過來的，而它原本印的是 `tools/doctor.py` 與
    `docs/OFFLINE-INSTALL.md` —— 兩個在這裡都不存在。一句指向不存在的東西的
    指路，會讓拿到包的人以為自己拿到的是壞的。
    """
    lines = built.splitlines()
    head = "\n".join(lines[:lines.index(make_text_bundle.SENTINEL)])
    for named in ("requirements.txt", "main.py"):
        assert named in head, named
        assert os.path.exists(os.path.join(REPO, named)), named
    for gone in ("doctor.py", "OFFLINE-INSTALL"):
        assert gone not in head, "包裡還指著 %s，而這個 repo 沒有它" % gone


def test_the_bundle_is_offered_in_the_readme():
    """一條沒有人知道的搬運路徑等於沒有。"""
    text = open(os.path.join(REPO, "README.md"), encoding="utf-8").read()
    assert "bundle/pitch_helper_bundle.py" in text
    assert "tools/release.py" in text
