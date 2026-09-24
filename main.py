"""Pitch helper —— 丟一張圖進去，回答它的 cell period。

獨立的進入點：``python main.py``（或 ``python -m pitchapp``）。

``--self-test [報告檔]`` 不開視窗，只檢查這一份跑不跑得起來 —— 給打包出來的
exe 用的（`tools/build_exe.py` 打完包就叫它），見 `pitchapp/selftest.py`。
"""
from __future__ import annotations

import sys


def main() -> int:
    argv = sys.argv[1:]
    if argv[:1] == ["--self-test"]:
        from pitchapp.selftest import run as self_test
        return self_test(argv[1] if len(argv) > 1 else "")
    from pitchapp.ui.pitch_helper import run
    return run(argv)


if __name__ == "__main__":
    raise SystemExit(main())
