"""云端划线的"尾巴"与「书库没有的书」这两件事。

背景 (真事, 2026-11-08): 用户报
  ① 「云端是正确没有截断的」—— 我们从云端位置切出来的正文普遍短 2-10 个字
  ② 「书库没有的书划线，同样也拉到本地，只需要在摘录中记录即可」

① 的根因: App 写的 `positionend` 一律停在**最后一个完整句子之前** (30/30 条都是),
   位置作为条目身份不能改, 但正文得补到本句结束。
② 的根因: `notes.pull` 只遍历书库里的书 (`sync.links()`), 云端其余书的划线从来没落地。

这一组钉住:
  1. `_snap_to_sentence`: 补到句末 (含收尾引号), 遇标签/换行/超长就放弃
  2. `text_of_span` 真的用上了补齐
  3. `parse_pdf_span` 兼容三种 PDF 型 uniquecheck (12/11/10 段 + `#&#`)
  4. `pull_unmapped` 把云端其余书的划线/书签落到 rbook(kind=cloud)+rnote, 幂等
  5. `scan()` 不清理 cloud 占位书目
  6. 摘录看得见它们 (标 `_noText`), 书架看不见, 导出到纸间自动跳过空正文

跑法: python3 tests/test_notes_tail_cloudonly.py
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

TMP = tempfile.mkdtemp(prefix="recto-tailnote-test-")
LIB = Path(TMP) / "library"
LIB.mkdir(parents=True, exist_ok=True)
os.environ["RECTO_DATA_DIR"] = TMP
os.environ["RECTO_LIBRARY_DIR"] = str(LIB)
os.environ["RECTO_AUTH_LOG"] = "off"

from recto.app import excerpts as EX                 # noqa: E402
from recto.services import notes as N                # noqa: E402
from recto.services import reader_state              # noqa: E402

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {extra}")


def at(raw: str, sub: str) -> int:
    return raw.encode("utf-8").index(sub.encode("utf-8"))


print("== 1. 补齐到句末 ==")
raw = "这才是对的。后面还有别的话"
snap = N._snap_to_sentence(raw.encode(), at(raw, "的"))
check("停在句号前会补齐", raw.encode()[:snap].decode() == "这才是对的。",
      repr(raw.encode()[:snap].decode()))

raw = "他说：“剑来！”然后走了"
snap = N._snap_to_sentence(raw.encode(), at(raw, "剑"))
check("补到句末 + 收尾引号", raw.encode()[:snap].decode() == "他说：“剑来！”",
      repr(raw.encode()[:snap].decode()))

raw = "他说道\r\n后面一句。"
snap = N._snap_to_sentence(raw.encode(), at(raw, "道") + len("道".encode()))
check("遇换行不补", raw.encode()[:snap].decode() == "他说道", repr(raw.encode()[:snap].decode()))

raw = "一段话<p>后面。</p>"
snap = N._snap_to_sentence(raw.encode(), at(raw, "话") + len("话".encode()))
check("遇标签不补", raw.encode()[:snap].decode() == "一段话", repr(raw.encode()[:snap].decode()))

raw = "一二三四五六七八九十一二三四五六七八九十没有标点"
snap = N._snap_to_sentence(raw.encode(), 0)
check("超长无标点不补", snap == 0, snap)

raw = "短句没有句号"
snap = N._snap_to_sentence(raw.encode(), at(raw, "没"))
check("同段内无句末标点不补", raw.encode()[:snap].decode() == "短句", repr(raw.encode()[:snap].decode()))

print("== 2. text_of_span 用上补齐 ==")
FAKE = "他说道：这才是对的。下一句在此。"
N._chapter_raw = lambda epub, ch: FAKE.encode()          # type: ignore
pos_s = "3_0_0$##3-0-5-0-0##$"
pos_e = f"3_0_0$##3-0-5-{at(FAKE, '的')}-0##$"
txt = N.text_of_span("/nowhere.epub", pos_s, pos_e)
check("切出来的正文是完整句子", txt.endswith("这才是对的。"), txt)

print("== 3. PDF 型位置 ==")
forms = [
    ("B_" + "1-1-76-91.276062-718.933044-1491-1-76-225.626068-500.254028-414", 76),
    ("B_" + "1-1-47-101.764679-252.0159911-1-47-162.213623-92.214470", 47),
    ("B_" + "1-1-529-86.331146-736.6676031-1-529-99.960236-722.259766", 529),
    ("B_" + "176#&#0001000000001.0000000000", 176),
]
for uc, page in forms:
    got = N.parse_pdf_span(uc)
    check(f"页号 {page}", got is not None and got[0] == page, got)
check("$## 形态不当 PDF 解析", N.parse_pdf_span("B_5_0_0$##3-0-5-100-0##$") is None)

loc = N._locate_any("B_5_0_0$##3-0-5-100-0##$5_0_0$##3-0-5-120-0##$", span=True)
check("epub 位置 → 篇下标 + 章名", loc == (5, 100, 120, "第 5 篇"), loc)
loc = N._locate_any(forms[0][0], span=True)
check("PDF 位置 → 页号 + 页名", loc == (76, 0, 0, "第 76 页"), loc)

print("== 4. pull_unmapped ==")
CLOUD_BOOK = "C" * 32
NOTES = {
    "note": {f"{CLOUD_BOOK}_5_0_0$##3-0-5-100-0##$5_0_0$##3-0-5-160-0##$": 111111,
             f"{CLOUD_BOOK}_8_0_0$##3-0-8-10-0##$8_0_0$##3-0-8-40-0##$": 222222},
    "mark": {f"{CLOUD_BOOK}_3_0_0$##3-0-3-77-0##$": 333333},
    "del": set(), "ids": [CLOUD_BOOK], "sources": {CLOUD_BOOK: {"note": 2, "mark": 1}},
}
N.notebooks = lambda: [{"bookid": CLOUD_BOOK.lower(), "name": "云端独有书",
                        "notenum": 2, "marknum": 1}]              # type: ignore
N.cloud_versions_multi = lambda ids: dict(NOTES)                  # type: ignore
N._sync.links = lambda refresh=False: []                          # type: ignore

r1 = N.pull_unmapped(quiet=True, force=True, min_gap=0)
check("首轮落 1 本 2 划线 1 书签",
      (r1["books"], r1["notes"], r1["marks"]) == (1, 2, 1), r1)
row = reader_state.conn().execute(
    "SELECT * FROM rbook WHERE kind='cloud'").fetchone()
check("书目是 cloud 占位", row is not None and row["title"] == "云端独有书"
      and not row["path"], dict(row) if row else None)
key = row["key"] if row else ""
notes_rows = reader_state.list_notes(key)
check("划线正文留空 (没有原书)", all(not (x["summary"] or "") for x in notes_rows),
      [x["summary"] for x in notes_rows])
check("章名存住了", all((x["chapter_label"] or "").startswith("第 ") for x in notes_rows),
      [x["chapter_label"] for x in notes_rows])
check("位置是篇下标", sorted(x["chapter"] for x in notes_rows) == [5, 8],
      sorted(x["chapter"] for x in notes_rows))

r2 = N.pull_unmapped(quiet=True, force=True, min_gap=0)
cnt = reader_state.conn().execute(
    "SELECT COUNT(*) AS n FROM rnote WHERE src='cloud-only'").fetchone()["n"]
check("再跑幂等 (不重复插)", cnt == 2, cnt)
check("再跑计数仍报 2", r2["notes"] == 2, r2)

print("== 5. scan 不清理占位书目 ==")
reader_state.scan(force=True)
still = reader_state.conn().execute(
    "SELECT COUNT(*) AS n FROM rbook WHERE kind='cloud'").fetchone()["n"]
check("云占位书目还在", still == 1, still)

print("== 6. 摘录看得见 / 书架看不见 / 导出跳过 ==")
books = reader_state.list_books()
check("书架不列云端占位书", all(b["key"] != key for b in books),
      [b["title"] for b in books])

rows, bmap = EX._load_view(Path(TMP))
mine = [x for x in rows if x.get("_noText")]
check("摘录里有它们并且标了 _noText", len(mine) == 2, len(mine))
check("书名来自云端", any(v.get("name") == "云端独有书" for v in bmap.values()),
      list(bmap.values()))
html = EX._render_pane("excerpts", {"source": "rd"}) if hasattr(EX, "_render_pane") else ""
if html:
    check("页面写明原文不在书库", "原文不在书库" in html)

out = EX._build_zhijian_payloads(Path(TMP), "127.0.0.1", 8080, book_name="",
                                 author="", source="rd", day_from="", day_to="",
                                 text_q="", include_time=False, safety_cap=500)
entries = [e for pl in (out.get("payloads") or []) for e in pl["entries"]]
check("导出不带空正文条目", not entries, out)
check("导出不带空书", not out.get("payloads"), out.get("payloads"))

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
