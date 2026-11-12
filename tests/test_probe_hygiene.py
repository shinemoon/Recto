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
