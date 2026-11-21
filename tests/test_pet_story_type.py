#!/usr/bin/env python3
"""书宠正文排版的**源码级**断言 —— 渐进增强属性 (hanging-punctuation /
text-autospace / text-spacing-trim) 在 Chromium 上拿不到 computed,
只能在 CSS 里验声明。
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

PASS = FAIL = 0


def ok(cond, name):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("PASS", name)
    else:
        FAIL += 1
        print("FAIL", name)


pet_css = (ROOT / "src/recto/app/ui/assets/pet.css").read_text("utf-8")
story_css = (ROOT / "src/recto/app/ui/assets/story.css").read_text("utf-8")

# ── 1. 前台读本: 版式规则 ──────────────────────────────────────────
ok(".rc-pet .adv-read-body > p {" in pet_css, "前台正文有专门的段落规则")
ok("font-family: var(--rc-serif)" in pet_css, "前台正文用衬线")

# 取 .rc-pet .adv-read-body > p 那一段规则
m = re.search(r"\.rc-pet \.adv-read-body > p \{(.*?)\}", pet_css, re.S)
blk = m.group(1) if m else ""
for name, pat in [
    ("版心有上限", r"max-width:\s*\d+em"),
    ("两端对齐", r"text-align:\s*justify"),
    ("汉字间均匀伸缩", r"text-justify:\s*inter-ideograph"),
    ("末行不撑满", r"text-align-last:\s*left"),
    ("避头尾 strict", r"line-break:\s*strict"),
    ("西文不拆词", r"word-break:\s*normal"),
    ("行首标点压缩 (保留首行缩进)", r"text-spacing-trim:\s*space-first"),
    ("中西文自动间距", r"text-autospace:\s*normal"),
    ("行末标点悬挂", r"hanging-punctuation:\s*allow-end"),
    ("孤行避免", r"text-wrap:\s*pretty"),
]:
    ok(re.search(pat, blk) is not None, "前台正文 · " + name)

# 居中: 版心靠 auto margin
ok(re.search(r"margin-left:\s*auto", blk) is not None
   and re.search(r"margin-right:\s*auto", blk) is not None,
   "前台正文 · 版心居中 (auto margin)")

# 首行缩进 2 字符
ok(re.search(r"\.rc-pet \.adv-read-body p \{[^}]*text-indent:\s*2em", pet_css) is not None,
   "前台正文 · 首行缩进 2 字符")

# ── 2. 内嵌块跟正文同宽同左缘 ─────────────────────────────────────
m2 = re.search(r"\.rc-pet \.adv-inline \{(.*?)\}", pet_css, re.S)
ib = m2.group(1) if m2 else ""
ok("max-width" in ib, "内嵌块 · 有版心上限 (跟正文同宽)")
ok(re.search(r"margin:[^;]*auto", ib) is not None, "内嵌块 · 居中 (跟正文同左缘)")
ok(re.search(r"padding-left:\s*1\.6em", ib) is not None, "内嵌块 · 缩进 1.6em (旁注感)")
ok(".rc-pet .adv-in { font-family: var(--rc-sans); }" in pet_css,
   "内嵌块 · 用无衬线 (跟正文衬线分开)")

# ── 3. 档案页: 同一套口径 ─────────────────────────────────────────
m3 = re.search(r"\.rc-pet-story \.arch-text p \{(.*?)\}", story_css, re.S)
ab = m3.group(1) if m3 else ""
for name, pat in [
    ("版心有上限", r"max-width:\s*\d+em"),
    ("两端对齐", r"text-align:\s*justify"),
    ("末行不撑满", r"text-align-last:\s*left"),
    ("避头尾 strict", r"line-break:\s*strict"),
    ("行首标点压缩", r"text-spacing-trim:\s*space-first"),
    ("中西文自动间距", r"text-autospace:\s*normal"),
    ("行末标点悬挂", r"hanging-punctuation:\s*allow-end"),
]:
    ok(re.search(pat, ab) is not None, "档案页正文 · " + name)
ok(".rc-pet-story .arch-text {" in story_css and "font-family: var(--rc-serif)" in story_css,
   "档案页正文 · 用衬线")

# ── 4. 一屏版 (桌面的书宠主页) 版心要更紧 ─────────────────────────
m4 = re.search(
    r"body\.rc-pet-page:not\(\.rc-pet-story-page\) \.adv-read-body > p \{(.*?)\}", pet_css, re.S)
ok(m4 is not None, "一屏版 · 有自己的版心规则")
if m4:
    em = re.search(r"max-width:\s*([0-9.]+)em", m4.group(1))
    ok(em and float(em.group(1)) <= 36, f"一屏版 · 版心更紧 ({em.group(1) if em else '?'}em)")

print(f"\n{PASS} PASS / {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
