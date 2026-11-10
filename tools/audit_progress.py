#!/usr/bin/env python3
"""进度口径自检: 本地 ↔ 云端 每一份记录都按同一套字节公式核对。

用法:
    PYTHONPATH=src python3 tools/audit_progress.py            # 全部映射书
    PYTHONPATH=src python3 tools/audit_progress.py 剑来       # 只看标题含"剑来"的

每本书输出:
    本地   (章, 偏移) → 比例 / readPosition
    云端   每一份记录 (32 hex / 64 hex / App 笔记本) 的来源、时间、位置、比例
    核对   position 反算出的比例 vs 记录里写的 readPercent, 差多少字节
    判定   一致 / 云端更新(可拉) / 云端落后(App 回写) / 云端自相矛盾

为什么要有这个工具: 2026-11-10 排查"网页写过、App 拉过却跳别处"时,
结论是两边公式完全一致 (diff 0.0 字节), 差异来自"同一本书云端同时存在多份
记录, 且 App 退出时会把它的本地位置回写覆盖我们的那条"。以后遇到同类现象,
先跑这个工具, 看是"算错"还是"谁写了哪份记录"。
"""
from __future__ import annotations

import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from recto.core import geometry                      # noqa: E402
from recto.core.paths import load_zy_auth           # noqa: E402
from recto.services import reader_state as rs       # noqa: E402
from recto.services import sync                     # noqa: E402


def _ts(v) -> str:
    try:
        f = float(v or 0)
    except (TypeError, ValueError):
        return "-"
    if f > 1e12:
        f /= 1000.0
    if not f:
        return "-"
    return datetime.datetime.fromtimestamp(f).strftime("%m-%d %H:%M:%S")


def _fmt(pct: float | None, total: int) -> str:
    if pct is None:
        return "-"
    return f"{pct * 100:8.4f}%"


def main(argv: list[str]) -> int:
    want = argv[1] if len(argv) > 1 else ""
    auth = load_zy_auth()
    rows = [r for r in sync.links(refresh=True) if want in r["title"]]
    state = sync._cloud_state(auth, rows)
    raw = {}
    keys = []
    for r in rows:
        for k in sync.app_book_keys(r["id64"] or r["id32"]):
            keys.append(k)
    try:
        raw = sync.cloud.get_progress(auth, keys) or {}
    except Exception as e:  # noqa: BLE001
        print(f"! 云端进度读取失败: {e}")

    bad = 0
    for r in rows:
        key = r["key"]
        prog = rs.get_progress(key)
        geo = sync._geometry_of(r["path"])
        total = rs.book_bytes(key)
        chapter = int(prog.get("chapter") or 0)
        offset = int(prog.get("offset") or 0)
        lp = float(prog.get("percent") or 0.0)
        pos = prog.get("position") or ""
        print(f"\n=== {r['title']}  ({key})  {total:,} 字节")
        if geo is None:
            print("   ! 拿不到几何 (文件不在?)")
            continue
        calc = geo.percent(chapter - 1, offset) if chapter else 0.0
        d = abs(calc - lp) * total
        print(f"   本地  第 {chapter} 篇 + {offset} 字节 → {_fmt(lp, total)}"
              f"   位置 {pos or '-'}")
        print(f"          几何反算 {_fmt(calc, total)}  差 {d:.1f} 字节"
              f"  {'OK' if d < 2 else '!! 本地不自洽'}")
        if d >= 2:
            bad += 1

        recs = []
        for k, v in raw.items():
            if str(k).strip().upper() not in {x.upper() for x in sync.app_book_keys(r["id64"] or r["id32"])}:
                continue
            rp = (v or {}).get("readProcess") or {}
            pct, ok = sync._normalize_percent(rp.get("readPercent"))
            if not ok:
                continue
            p = geometry.parse_position(str(rp.get("readPosition") or ""))
            recs.append({
                "key": "32" if str(k).strip().upper()[:32] == str(k).strip().upper() else "64",
                "src": rp.get("readingSource") or "-",
                "ts": v.get("updateTime") or rp.get("lastReadDate"),
                "pct": pct,
                "pos": str(rp.get("readPosition") or ""),
                "cid": p.get("chapter_id"),
                "f3": p.get("f3"),
                "chId": rp.get("chapterId"),
                "total": rp.get("totalCount"),
            })
        nb = sync._zy_notebook_pct()
        nbv = nb.get(str(r["id32"] or r["id64"])[:32].lower())
        if nbv:
            recs.append({"key": "note", "src": "notebook(App)", "ts": nbv[3],
                         "pct": nbv[0], "pos": f"{int(nbv[1]) + 1 if nbv[1] is not None else 0}_0_0"
                                       f"$##3-0-{int(nbv[1]) + 1 if nbv[1] is not None else 0}-{nbv[2]}-0##$",
                         "cid": None if nbv[1] is None else int(nbv[1]), "f3": nbv[2],
                         "chId": nbv[1], "total": None})
        for rec in sorted(recs, key=lambda x: float(x["ts"] or 0)):
            cid, f3 = rec["cid"], rec["f3"]
            cmp = ""
            if cid is not None and f3 is not None:
                back = geo.percent(int(cid), int(f3))
                delta = abs(back - rec["pct"]) * total
                cmp = f"  位置反算 {back * 100:8.4f}%  差 {delta:6.1f} 字节"
                if delta > 4:
                    cmp += " !! 自相矛盾"
                    bad += 1
            print(f"   云端[{rec['key']:<4}] {rec['src']:<14} {_ts(rec['ts'])} "
                  f"{_fmt(rec['pct'], total)} chId={rec['chId']} total={rec['total']} "
                  f"位置 {rec['pos'] or '-'}{cmp}")

        ent = state.get(r["id64"] or r["id32"]) or {}
        cp = ent.get("pct")
        if cp is not None:
            delta = (cp - lp) * total
            if abs(delta) < 2:
                verdict = "一致"
            elif delta > 0:
                verdict = f"云端更靠前 {delta:,.0f} 字节 (可拉)"
            else:
                verdict = (f"云端更靠后 {-delta:,.0f} 字节 —— 多半是 App 退出时"
                           f"回写覆盖了我们那条 (来源 {ent.get('src')})")
            print(f"   判定  {verdict}")
    print(f"\n自洽性问题: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
