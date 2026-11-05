"""进度精度: 显示标尺按书大小自适应 + 云端位置优先 (2026-11-05)。

症状: 云端 31.82%, 拉下来界面显示 31.8% —— 固定 1 位小数。对 41MB 的
长篇, 0.1% = 41KB ≈ 一万多字, 进度条会长时间"不动"。

两条修法一起测:
  1. 显示位数 = f(全书字节), 一个刻度 ≈ 2KB 正文 (1~3 位)
  2. 拉取时优先用 readPosition 的 (chapterId, f3) 精确字节, 而不是先把它
     折算成 0-1 比例再反解 (float32 会差 1 个字节)

跑法: python3 tests/test_precision.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from recto.core import geometry                    # noqa: E402
from recto.services import reader_state, sync       # noqa: E402

PASS = 0
FAILED: list[str] = []


def ok(cond, name, extra=''):
    global PASS
    if cond:
        PASS += 1
        print(f'PASS {name}' + (f' — {extra}' if extra else ''))
    else:
        FAILED.append(name)
        print(f'FAIL {name}' + (f' — {extra}' if extra else ''))


JL = ROOT / 'library' / '示例长篇（多卷）.epub'
FOX = ROOT / 'library' / '示例短篇.epub'

# 云端实测: 示例长篇 readPercent 0.31820855, readPosition chapterId=182 f3=55447
JL_PCT = 0.31820855
JL_CID, JL_F3 = 182, 55447

# ---------------------------------------------------------------------------
# 1. 位数策略
# ---------------------------------------------------------------------------
ok(geometry.pct_decimals(327151) == 1, '小书 (320KB) 1 位小数够', str(geometry.pct_decimals(327151)))
ok(geometry.pct_decimals(4_000_000) == 2, '中等书 (4MB) 用 2 位', str(geometry.pct_decimals(4_000_000)))
ok(geometry.pct_decimals(41907094) == 3, '长篇 (40MB) 用 3 位', str(geometry.pct_decimals(41907094)))
ok(geometry.pct_decimals(0) == 2, '字节未知时取中间值 2 位')
ok(geometry.pct_decimals(-5) == 2, '负字节不炸')
ok(geometry.pct_decimals(200_000_000) == 3, '200MB 也封顶 3 位 (不无限加)')
ds = [geometry.pct_decimals(b) for b in (327151, 2_000_000, 4_000_000, 20_000_000, 41_907_094)]
ok(ds == sorted(ds), '书越大位数不减', str(ds))

# 一个刻度该 ≲ 2KB 正文（短书允许略粗，但要有下界）
for b in (327151, 4_000_000, 41_907_094):
    d = geometry.pct_decimals(b)
    step = b * 10 ** (-(d + 2))
    ok(step <= 2048 * 1.01 or d == 1, f'{b} 的一个刻度 {step:.0f} 字节 ≤ 2KB', f'd={d}')

# ---------------------------------------------------------------------------
# 2. 显示: 向下取整 + 去尾零
# ---------------------------------------------------------------------------
ok(geometry.format_pct(JL_PCT, 41907094) == '31.82%',
   '长篇: 0.31820855 -> 31.82% (不再是 31.8%)',
   geometry.format_pct(JL_PCT, 41907094))
ok(geometry.format_pct(0.31820855, 327151) == '31.8%', '小书仍只到 1 位')
ok(geometry.format_pct(0.31800, 41907094) == '31.8%', '尾零去掉, 不显示 31.800%')
ok(geometry.format_pct(0.318999, 41907094) == '31.899%', '向下取整: 不夸大')
ok(geometry.format_pct(1.0, 41907094) == '100%', '读完是 100%')
ok(geometry.format_pct(0, 41907094) == '0%', '没读是 0%')
ok(geometry.format_pct(None, 41907094) == '0%', 'None 不炸')
ok(geometry.format_pct(0.0000012, 41907094) == '0.000%'.lstrip('0').replace('.000%', '0%')
   or geometry.format_pct(0.0000012, 41907094) == '0%', '极小值落到 0%')
ok(geometry.format_pct(1.0000001, 41907094) == '100%', '轻微超界钳到 100%')

# 前端必须用同一套规则: 目标字节 2048 + 自适应, 不能退回固定 1 位
console_src = (ROOT / 'src/recto/app/console.py').read_text()
js_src = (ROOT / 'src/recto/app/ui/assets/reader.js').read_text()
ok('const pctDec' in console_src and '2048' in console_src, '控制台 JS 有自适应位数')
ok('Math.min(3' in console_src, '控制台 JS 位数封顶 3')
ok('const pct = (v, bytes)' in console_src, '控制台 pct() 接受字节数')
ok('toFixed(1) + "%"' not in console_src, '控制台不再有固定 1 位百分比')
ok('function pctText(p, bytes)' in js_src, '阅读器 pctText 接受字节数')
ok('Math.floor(p * 1000) / 10' not in js_src, '阅读器不再按 0.1% 截断')
ok('2048' in js_src, '阅读器同一套 2KB 刻度')

# ---------------------------------------------------------------------------
# 3. 云端位置 (chapterId, f3) 比百分比更准
# ---------------------------------------------------------------------------
_real_get_book = reader_state.get_book
_cache_backup = dict(reader_state._GEO_CACHE)

if JL.exists():
    reader_state.get_book = lambda k: (
        {'key': k, 'path': str(JL), 'title': '示例长篇', 'bytes': JL.stat().st_size}
        if k == '__jl__' else _real_get_book(k))
    try:
        spans, total, kind = reader_state.geometry_of('__jl__')
        # 全书字节 = spine 各篇**解压后**长度之和, 不等于文件大小 (epub 是 zip)
        ok(kind == 'epub' and total > 40_000_000,
           'geometry_of 给出 epub + 全书字节', f'{total}')
        ok(len(spans) == 590, '章跨度数量 = spine 数', str(len(spans)))

        pos_anchor = reader_state.anchor_from_position('__jl__', JL_CID, JL_F3)
        ok(pos_anchor == (183, 55447), 'readPosition 直接落到 (spine 183, +55447)',
           str(pos_anchor))

        pct_anchor = reader_state.anchor_from_percent('__jl__', JL_PCT)
        ok(pct_anchor[0] == 183, '按比例反解落在同一章', str(pct_anchor))
        ok(abs(pct_anchor[1] - JL_F3) <= 4,
           '两条路字节差在 float32 量级 (≤4 字节)',
           f'percent={pct_anchor[1]} position={JL_F3}')

        back = reader_state.percent_of_cloud_position('__jl__', JL_CID, JL_F3)
        ok(abs(back - JL_PCT) < 1e-7, '位置反解回比例与云端 readPercent 一致',
           f'{back:.8f}')

        # 精确位置优先
        ch, off, basis = sync._anchor_for_cloud('__jl__', {
            'pct': JL_PCT, 'pos_chapter': JL_CID, 'pos_f3': JL_F3})
        ok((ch, off, basis) == (183, 55447, 'position'),
           '拉取走 position 分支 (字节精确)', f'basis={basis} off={off}')

        # 位置与比例矛盾 (不是同一份文件) → 退回按比例
        ch2, off2, basis2 = sync._anchor_for_cloud('__jl__', {
            'pct': 0.5, 'pos_chapter': JL_CID, 'pos_f3': JL_F3})
        ok(basis2 == 'percent', '位置与比例差得远时退回 percent', f'basis={basis2}')
        ok(abs((spans[ch2][0] + off2) - 0.5 * total) < 10_000,
           '退回后按 50% 落点 (章起点 + 章内偏移)',
           str((ch2, off2, spans[ch2][0] + off2)))

        # 位置缺失 → percent
        _ch3, _off3, basis3 = sync._anchor_for_cloud('__jl__', {'pct': JL_PCT})
        ok(basis3 == 'percent', '没有 readPosition 时退回 percent')

        # 越界位置被钳住, 不越到别的章
        ch4, off4 = reader_state.anchor_from_position('__jl__', 10 ** 6, 10 ** 9)
        ok(ch4 == len(spans) - 1 and off4 == max(0, spans[-1][1] - 1),
           '越界 chapterId / f3 钳到最后一章', str((ch4, off4)))
    finally:
        reader_state.get_book = _real_get_book
        reader_state._GEO_CACHE.clear()
        reader_state._GEO_CACHE.update(_cache_backup)
else:
    print('SKIP 真实 epub 检查 (library 里没有示例长篇)')

if FOX.exists():
    reader_state._GEO_CACHE.clear()
    _real2 = reader_state.get_book
    reader_state.get_book = lambda k: (
        {'key': k, 'path': str(FOX), 'title': '示例短篇', 'bytes': FOX.stat().st_size}
        if k == '__fox__' else _real2(k))
    try:
        ok(reader_state.anchor_from_position('__fox__', 7, 92086) == (8, 92086),
           '小书: chapterId 7 -> spine 8, f3 原样')
        ok(geometry.format_pct(0.99761578, FOX.stat().st_size) == '99.7%',
           '小书 99.76% 显示 99.7% (1 位)')
    finally:
        reader_state.get_book = _real2
        reader_state._GEO_CACHE.clear()
        reader_state._GEO_CACHE.update(_cache_backup)

# ---------------------------------------------------------------------------
# 4. 数据结构: 拉取结果要带字节数 + 依据, 界面才知道用几位小数
# ---------------------------------------------------------------------------
sync_src = (ROOT / 'src/recto/services/sync.py').read_text()
ok('"bytes": nbytes' in sync_src, '拉取 detail 带全书字节')
ok('"basis": basis' in sync_src, '拉取 detail 带依据 (position/percent)')
ok('_POS_GUARD_BYTES' in sync_src, '位置/比例矛盾有 4KB 闸门')
ok('_POS_GUARD_BYTES = 4096' in sync_src, '闸门阈值 4KB')
ok('"bytes": int(row.get("bytes") or 0)' in sync_src,
   '映射表带字节数 (对齐判断按书大小给容差)')
ok('geometry.format_pct' in sync_src, '日志百分比走自适应格式化')

# ---------------------------------------------------------------------------
print()
print(f'{PASS} passed / {len(FAILED)} failed')
if FAILED:
    print('FAILED:', FAILED)
    sys.exit(1)
