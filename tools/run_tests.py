"""统一跑测试 —— 跑之前给生产文件拍快照, 跑完逐个比对。

**为什么**: 2026-11-11 查出「改了口令又变回 root:root」的真凶是测试套件
在生产目录里删/建口令文件。修掉那一个测试还不够 —— 以后每加一个测试都可能
再踩一次。所以把「不许碰生产」做成**跑测试时的硬检查**: 任何测试动了生产的
口令 / 配置 / 数据, 这次测试就算失败。

跑法: cd <仓库根> && python3 tools/run_tests.py [测试名 ...]
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from _guard import diff, production_paths, snapshot  # noqa: E402


def main(argv: list[str]) -> int:
    only = argv[1:]
    tests = sorted((ROOT / "tests").glob("test_*.py"))
    if only:
        tests = [t for t in tests if any(k in t.name for k in only)]
        if not tests:
            print(f"没有匹配的测试: {only}")
            return 2

    before = snapshot(production_paths())
    print(f"护栏: 盯着 {len(before)} 个生产文件")
    for k in before:
        print(f"  · {k}")

    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    # 统一关掉生产留痕 (各测试自己也设, 这里兜底)
    env.setdefault("RECTO_AUTH_LOG", "off")

    failed: list[str] = []
    t0 = time.time()
    for t in tests:
        name = t.name
        r = subprocess.run([sys.executable, str(t)], cwd=str(ROOT),
                           env=env, capture_output=True, text=True, timeout=900)
        ok = r.returncode == 0
        tail = [ln for ln in r.stdout.splitlines() if ln.strip()][-1:] or [""]
        print(f"{'PASS' if ok else 'FAIL'} {name:<38} {tail[0][:70]}")
        if not ok:
            failed.append(name)
            out = (r.stdout or "") + (r.stderr or "")
            for ln in out.splitlines()[-25:]:
                print("      " + ln)

    print("-" * 60)
    changed = diff(before, snapshot(production_paths()))
    if changed:
        print("FAIL 护栏: 测试动了生产文件 —— 这些必须只读!")
        for c in changed:
            print("  !! " + c)
        return 3
    print(f"PASS 护栏: 生产文件一个字节都没动 ({len(before)} 个)")
    print(f"用时 {time.time() - t0:.1f}s")
    if failed:
        print(f"{len(failed)} 个测试失败: {', '.join(failed)}")
        return 1
    print(f"全部 {len(tests)} 个测试通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))