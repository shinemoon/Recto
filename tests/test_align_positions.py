"""两端进度口径 + 章首题图 + 分层目录 (2026-11-05)。

跑法: python3 tests/test_align_positions.py
不用 pytest —— 跟其它测试保持同一种自跑脚本形态。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from recto.core import bookfile, geometry          # noqa: E402
from recto.services import sync                    # noqa: E402

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


class _Geo:
    catalog = 588
    sizes = [1] * 590


# ---------------------------------------------------------------------------
# 1. readPosition: spine 下标 -> pre  (超前一章的历史 bug)
# ---------------------------------------------------------------------------
pos = sync.position_for(_Geo(), 195, 49691)
ok(pos.startswith('195_0_0$##'), 'epub: spine 195 -> pre 195 (不是 196)', pos)
ok(geometry.parse_position(pos)['chapter_id'] == 194, 'pre 195 反解出 chapterId 194')
ok(sync.position_for(None, 7, 10).startswith('8_0_0$##'), 'txt: 章序号原样 (7 -> pre 8)')
ok(sync._catalog_chapter(_Geo(), 195) == 194, 'chapterId 字段与 pre 同一套编号')

if JL.exists():
    geo = geometry.load_geometry(JL)
    # 真实云端记录: readPercent 0.340034 ⇔ readPosition 195_...-49691-...
    ok(abs(geo.percent(194, 49691) - 0.340034) < 1e-6,
       '云端 pct/position 自洽 (0.340034 ⇔ chapterId 194)', f'{geo.percent(194, 49691):.8f}')
    ok(abs(geo.percent(195, 49691) - 0.340034) > 1e-3,
       'off-by-one 会让位置指向别的字节位置 (差一章)')
    back = sync._pos_pct(geo, '195_0_0$##3-0-195-49691-0##$')
    ok(abs(back - 0.340034) < 1e-6, '_pos_pct 能验云端位置', f'{back:.8f}')
    # 旧版写出去的那个位置必须被判成不自洽
    bad = sync._pos_pct(geo, '196_0_0$##3-0-196-49691-0##$')
    ok(abs(bad - 0.340034) > sync._POS_TOL, '旧位置 (pre=196) 被判不自洽', f'{bad:.6f}')
    # solve() 能从比例反解出正确位置 (修复路径)
    ch_id, f3 = geo.solve(0.340034)
    fixed = geometry.build_position(int(ch_id), int(f3))
    ok(fixed.startswith('195_0_0$##'), 'repair: 按 pct 反解出的位置 = pre 195', fixed)

# ---------------------------------------------------------------------------
# 2. 章首题图: 图片元素自己是块, 不再被丢弃
# ---------------------------------------------------------------------------
if JL.exists():
    doc = bookfile.BookDoc(str(JL))
    blocks = doc.blocks(183)
    first = blocks[0]
    ok(first.tag == 'img' and '<img' in first.html, '章 183 第一块是题图', first.html[:60])
    ok(first.off == 279, '题图字节偏移 279 (在 h2 之前)')
    ok('/read/api/book/' in first.html and 'asset?p=OEBPS/Images/top.jpg' in first.html,
       '题图走 asset 端点')
    ok(not first.text, '题图块没有文字 (会被当整幅插图排)')
    any_img = any(b.tag == 'img' for b in blocks)
    ok(any_img, '该章共 ' + str(sum(1 for b in blocks if b.tag == 'img')) + ' 个图片块')
    # 段内小图 (脚注标记) 仍是行内, 尺寸来自书内 CSS
    doc.close()

if FOX.exists():
    fdoc = bookfile.BookDoc(str(FOX))
    inline = None
    for i in range(len(fdoc.chapters)):
        for b in fdoc.blocks(i):
            if b.text and '<img' in b.html:
                inline = b
                break
        if inline:
            break
    ok(inline is not None, '示例短篇 章内脚注标记小图仍然存在')
    if inline is not None:
        ok('rfig-inl' in inline.html, '有文字的段里图片是行内 (rfig-inl)')
        ok(('width:0.85em' in inline.html) or ('em' in inline.html),
           '行内小图尺寸来自书内 CSS (不铺满整行)', inline.html[inline.html.find('<img'):][:120])
    fdoc.close()

# ---------------------------------------------------------------------------
# 3. 分层目录
# ---------------------------------------------------------------------------
if JL.exists():
    g = geometry.load_geometry(JL)
    nodes = g.toc_nodes
    ok(len(nodes) == 588, '示例长篇目录条目 588 条', str(len(nodes)))
    ok({n['depth'] for n in nodes} == {0, 1}, '示例长篇目录两级 (卷 / 章)')
    vols = [n for n in nodes if n['depth'] == 0]
    ok(len(vols) >= 50, '卷数 ' + str(len(vols)))
    ch1 = [n for n in nodes if n['depth'] == 1][0]
    ok(ch1['label'] != ch1['text'] and ch1['text'] in ch1['label'],
       'label = 卷名 + 章名, text = 章名')
    ok(g.toc.get(ch1['spine']) == ch1['label'], 'toc 扁平表与 toc_nodes 一致')

if FOX.exists():
    gf = geometry.load_geometry(FOX)
    ok(all(n['depth'] == 0 for n in gf.toc_nodes), '示例短篇 是单层目录 (不折叠)')

print()
if FAILED:
    print(f'{len(FAILED)} FAILED: ' + '; '.join(FAILED))
    raise SystemExit(1)
print(f'全部通过 ({PASS} 项)')
