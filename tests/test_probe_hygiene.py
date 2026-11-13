"""探针卫生: 碰用户状态的探针必须快照 + 还原。

    python3 tests/test_probe_hygiene.py

事故 (2026-11-04): `probe_btn_groups.mjs` 为了扫六套皮肤, 直接 POST
`/api/appearance` 切皮肤, 扫完不还原 → 用户自己选的外观每次都被顶成
`material-dark`; 用户看到的是"每次重启外观都重置成 Material 深色"。
"""
from __future__ import annotations

import re
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"

PASS = 0
FAIL = 0


def ok(cond, name):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}")


def _probes_writing_appearance():
    out = []
    for p in sorted(TOOLS.glob("*.mjs")):
        if p.name == "_skins.mjs":
            continue
        txt = p.read_text("utf-8", errors="replace")
        if "/api/appearance" in txt and re.search(r"method:\s*'POST'", txt):
            out.append((p, txt))
    return out


def case_probes_snapshot_and_restore():
    probes = _probes_writing_appearance()
    ok(bool(probes), f"找到写外观的探针 ({len(probes)} 个)")
    bad = []
    for p, txt in probes:
        has_snap = ("skinSnapshot" in txt) or "snap" in txt
        has_restore = ("restoreSkin" in txt) or re.search(
            r"body:\s*JSON\.stringify\(snap\)", txt) is not None
        if not (has_snap and has_restore):
            bad.append(p.name)
    ok(not bad, f"全部探针都 快照+还原 (漏: {bad})")


def case_no_hardcoded_restore():
    """最后一次写外观必须来自快照, 不能是硬编码的皮肤 id。"""
    bad = []
    for p, txt in _probes_writing_appearance():
        hard = [m.start() for m in re.finditer(
            r"JSON\.stringify\(\{\s*skin:\s*'[\w-]+'\s*\}\)", txt)]
        snap = [m.start() for m in re.finditer(
            r"restoreSkin\(|JSON\.stringify\(snap\)", txt)]
        if hard and (not snap or max(hard) > max(snap)):
            bad.append(p.name)
    ok(not bad, f"最后一次写外观来自快照 (违规: {bad})")


def case_helper_exists():
    helper = TOOLS / "_skins.mjs"
    ok(helper.is_file(), "tools/_skins.mjs 在")
    if not helper.is_file():
        return
    txt = helper.read_text("utf-8")
    for fn in ("skinSnapshot", "restoreSkin", "setSkin", "withSkinGuard"):
        ok(f"export async function {fn}" in txt, f"_skins.mjs 提供 {fn}")
    ok("finally" in txt, "withSkinGuard 用 finally 保底")


def case_reader_progress_probes_restore():
    """翻页的探针会真的写进度 —— 必须开头快照、收尾写回。

    事故: 翻页效果探针在用户正在读的书上翻了十几页, 关掉浏览器就走了;
    下次打开这本书直接落到半路, `probe_reader_ux` 的「初始顶栏可见」也跟着红。
    """
    probes = []
    for p in sorted(TOOLS.glob("*.mjs")):
        txt = p.read_text("utf-8", errors="replace")
        if re.search(r"/progress['\"]", txt) and re.search(r"method:\s*'POST'", txt):
            probes.append((p, txt))
    ok(bool(probes), f"找到写阅读进度的探针 ({len(probes)} 个)")
    bad = [p.name for p, t in probes
           if "readProgress(" not in t or "writeProgress(" not in t]
    ok(not bad, f"全部快照+写回阅读进度 (漏: {bad})")
    # 写回必须发生在最后 (收尾), 不能在开头就把快照写回去
    late = []
    for p, t in probes:
        calls = [m.start() for m in re.finditer(r"writeProgress\(", t)]
        first_read = [m.start() for m in re.finditer(r"readProgress\(", t)]
        if calls and first_read and max(calls) < max(first_read):
            late.append(p.name)
    ok(not late, f"写回发生在快照之后 (违规: {late})")


def case_reader_time_probes_restore():
    """要在阅读器里待很久的探针会真的给书写入阅读时长 —— 必须快照 + 还原。

    事故 (2026-11-12 自查): 验收"后台不累计时长"的探针在阅读器里挂了 3-4 分钟,
    服务端按 30 秒心跳给那本书真加了 8 分钟; 阅读时长会流进书架统计 / 陪读成长 /
    导出到纸间, 所以跑完必须还原 (tools/rtime_snapshot.py)。
    """
    probes = []
    for p in sorted(TOOLS.glob("*.mjs")):
        txt = p.read_text("utf-8", errors="replace")
        if "RV_TIME_BOOK" in txt:            # 时长类探针的标记
            probes.append((p, txt))
    ok(bool(probes), f"找到读时长的探针 ({len(probes)} 个)")
    bad = [p.name for p, t in probes
           if "rtime_snapshot.py" not in t
           or "'save'" not in t and '"save"' not in t
           or "'restore'" not in t and '"restore"' not in t]
    ok(not bad, f"全部 快照+还原阅读时长 (漏: {bad})")

    helper = TOOLS / "rtime_snapshot.py"
    ok(helper.is_file(), "tools/rtime_snapshot.py 在")
    if helper.is_file():
        htxt = helper.read_text("utf-8")
        for cmd in ("save", "restore", "clear"):
            ok(f'"{cmd}"' in htxt, f"快照工具支持 {cmd}")
        ok("DELETE FROM rsession" in htxt, "还原会清掉新增的会话行")
        ok("DELETE FROM rtime" in htxt, "原本没有时长时还原为删除该行")


def case_pet_char_probe_restores():
    """书宠人物探针会真的入门派 + 觉醒性情 (都落盘) —— 必须快照 + 还原。

    事故 (2026-11-12): `probe_pet_char.mjs` 第一次跑完, 用户存档里就多了
    「墨池剑庐 + 观海诀 + 一气呵成 + 两条上品性情」—— 用户自己从没点过。
    """
    probes = []
    for p in sorted(TOOLS.glob("*.mjs")):
        txt = p.read_text("utf-8", errors="replace")
        # 探针是"点按钮"驱动的 (前端 JS 里发请求), 所以探针文件里看不到 /api/char/xxx,
        # 只能认它操作了人物面板上的按钮挂点。
        if re.search(r"data-(school|trait|attr)", txt):
            probes.append((p, txt))
        # 人格改名字会同步到人物卡 —— 探针改名字也得还原存档
        elif "pet-name-input" in txt and re.search(r"btn-persona-save", txt):
            probes.append((p, txt))
    ok(bool(probes), f"找到改书宠人物的探针 ({len(probes)} 个)")
    bad = [p.name for p, t in probes
           if "pet_char_snapshot.py" not in t
           or "'save'" not in t and '"save"' not in t
           or "'restore'" not in t and '"restore"' not in t]
    ok(not bad, f"全部 快照+还原书宠存档 (漏: {bad})")

    helper = TOOLS / "pet_char_snapshot.py"
    ok(helper.is_file(), "tools/pet_char_snapshot.py 在")
    if helper.is_file():
        htxt = helper.read_text("utf-8")
        for cmd in ("save", "restore", "reset", "clear"):
            ok(f'"{cmd}"' in htxt, f"快照工具支持 {cmd}")
        # 还原 = 以快照为底重建 + 只增不减的成长项与当前取大。
        # 为什么不是整份 copyfile: 陪读心跳一直在写 (修为/等级/分钟/锚点),
        # 整份覆盖会把刚写进去的盖掉 (真踩过: 探针跑完等级退回快照那一刻)。
        # 为什么不能只回填几个字段: 探针开跑会 reset, 只回填门派/性情就补不回
        # 等级与技能 (真踩过: 用户的 4 级书宠被留成 1 级无门派)。
        ok("shutil.copyfile(SNAP, CHAR)" not in htxt,
           "还原不是整份 copyfile (会盖掉心跳写的修为)")
        ok("max(" in htxt and "credited_minutes" in htxt,
           "成长项 (分钟/锚点/修为) 与当前取大, 不盲覆盖")
        ok("dict(snap)" in htxt,
           "以快照为底重建 (门派/性情/根骨/潜能都在快照里)")
        ok("level" in htxt and "skills" in htxt,
           "等级与技能也回填 (探针会 reset, 只回填几个字段补不回来)")


def main():
    global PASS, FAIL
    for name, fn in sorted(globals().items()):
        if name.startswith("case_") and callable(fn):
            print(f"\n[{name[5:]}]")
            try:
                fn()
            except Exception:  # noqa: BLE001
                FAIL += 1
                print("  FAIL 异常:")
                traceback.print_exc()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
