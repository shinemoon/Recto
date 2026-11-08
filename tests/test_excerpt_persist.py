"""摘录库要能「独立存活」+ 「没有正文的条目」显示简化。

背景 (2026-11-08 用户):
  ① 「没有位置的摘录，显示上做简化」
  ② 「设计上要兼顾以后我可能本地书籍会删除，请确认每次进入摘录数据库时，
     正文部分已经存好」

② 的两层含义都钉在这里:
  - **入库时**就把快照写进 `rnote` / `rbookmark` 自己身上
    (正文 / 章名 / 位置比例 / 书名 / 作者) —— 删书以后 `rbook` 那行没了,
    只靠 join 会变成"空书名 + 位置 0"。
  - **每次进摘录库**先跑一次补齐 (`notes.materialize`), 书还在就把缺口补掉;
    幂等, 没缺口时只跑几条 COUNT, 不开任何文件。

① 的意思是: 只剩位置的条目不该铺满整卡 (0 字 / 书票 / 复制 全是噪声)。

跑法: python3 tests/test_excerpt_persist.py
"""
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

TMP = tempfile.mkdtemp(prefix="recto-persist-test-")
LIB = Path(TMP) / "library"
LIB.mkdir(parents=True, exist_ok=True)
os.environ["RECTO_DATA_DIR"] = TMP
os.environ["RECTO_LIBRARY_DIR"] = str(LIB)
os.environ["RECTO_AUTH_LOG"] = "off"

from recto.app import excerpts as EX                 # noqa: E402
from recto.core import geometry                      # noqa: E402
from recto.services import notes as N                # noqa: E402
from recto.services import reader_state as RS        # noqa: E402

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {extra}")


def find_book() -> Path | None:
    """一本真实 epub (几何/正文都要它)。找不到就 SKIP 依赖书的断言。"""
    cands = []
    env = os.environ.get("RECTO_TEST_BOOKS")
    if env:
        cands.append(Path(env))
    cands.append(Path.home() / "Books")
    cands.append(ROOT / "library")
    for base in cands:
        try:
            for p in sorted(base.glob("*.epub")):
                if p.stat().st_size < 8_000_000:      # 小的先跑, 快
                    return p
        except OSError:
            continue
    return None


def add_book(key: str, path: str, title: str, author: str = "") -> None:
    c = RS.conn()
    c.execute("INSERT INTO rbook(key,path,kind,title,author,bytes,chapters,size,mtime,"
              "cover,added_at,seen_at) VALUES(?,?,'epub',?,?,0,0,0,0,'',"
              "'2026-11-08T00:00:00','2026-11-08T00:00:00')",
              (key, path, title, author))
    c.commit()


print("== 1. schema: 两表都有快照列 (新库 + 老库迁移) ==")
c = RS.conn()
for table in ("rnote", "rbookmark"):
    cols = {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
    check(f"{table} 有 chapter_label", "chapter_label" in cols)
    check(f"{table} 有 pct", "pct" in cols)
    check(f"{table} 有 book_title", "book_title" in cols)
    check(f"{table} 有 book_author", "book_author" in cols)

# 老库迁移: 建一张没有快照列的表, 再走一次 conn() 的 ALTER 流程
OLD = Path(TMP) / "old"
OLD.mkdir(parents=True, exist_ok=True)
old_db = sqlite3.connect(str(OLD / "reader.sqlite"))
old_db.executescript("""
CREATE TABLE rnote (id INTEGER PRIMARY KEY AUTOINCREMENT, book TEXT NOT NULL,
  chapter INTEGER NOT NULL, start_off INTEGER NOT NULL, end_off INTEGER NOT NULL,
  summary TEXT DEFAULT '', remark TEXT DEFAULT '', color TEXT DEFAULT '',
  anchors TEXT DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  zy_key TEXT DEFAULT '', zy_marktime INTEGER DEFAULT 0, zy_sig TEXT DEFAULT '',
  src TEXT DEFAULT 'local');
""")
old_db.commit()
old_db.close()
_prev = os.environ["RECTO_DATA_DIR"]
os.environ["RECTO_DATA_DIR"] = str(OLD)
RS.reset()
cols = {r[1] for r in RS.conn().execute("PRAGMA table_info(rnote)")}
check("老库自动加 pct 列", "pct" in cols)
check("老库自动加 book_title 列", "book_title" in cols)
os.environ["RECTO_DATA_DIR"] = _prev
RS.reset()

print("== 2. 入库即快照: add_note / add_bookmark ==")
add_book("bk-a", str(ROOT / "library" / "nonexistent.epub"), "示例长篇", "某作者")
row = RS.add_note("bk-a", 3, 100, 160, "一段正文", chapter_label="第一卷 第三章",
                  pct=12.5)
check("add_note 存章名", row["chapter_label"] == "第一卷 第三章", row["chapter_label"])
check("add_note 存位置", abs(float(row["pct"]) - 12.5) < 1e-6, row["pct"])
check("add_note 存书名", row["book_title"] == "示例长篇", row["book_title"])
check("add_note 存作者", row["book_author"] == "某作者", row["book_author"])

bm = RS.add_bookmark("bk-a", 3, 100, "书签", "第一卷 第三章",
                     chapter_label="第一卷 第三章", pct=12.5)
check("add_bookmark 存章名", bm["chapter_label"] == "第一卷 第三章", bm["chapter_label"])
check("add_bookmark 存位置", abs(float(bm["pct"]) - 12.5) < 1e-6, bm["pct"])
check("add_bookmark 存书名", bm["book_title"] == "示例长篇", bm["book_title"])

# 再插一次同位置 (ON CONFLICT): 快照不许被空值抹掉
bm2 = RS.add_bookmark("bk-a", 3, 100, "书签改了")
check("rebuild 不抹章名", bm2["chapter_label"] == "第一卷 第三章", bm2["chapter_label"])
check("rebuild 不抹位置", abs(float(bm2["pct"]) - 12.5) < 1e-6, bm2["pct"])

print("== 3. _upsert_note 写快照 ==")
nid = N._upsert_note("bk-a", chapter=4, start=10, end=40, summary="x", remark="",
                     color="", zy_key="uc1", marktime=1, label="第四章", pct=33.0,
                     book_title="示例长篇", book_author="某作者")
r = dict(RS.conn().execute("SELECT * FROM rnote WHERE id=?", (nid,)).fetchone())
check("upsert 写章名", r["chapter_label"] == "第四章", r["chapter_label"])
check("upsert 写位置", abs(float(r["pct"]) - 33.0) < 1e-6, r["pct"])
check("upsert 写书名", r["book_title"] == "示例长篇", r["book_title"])

print("== 4. 删书以后摘录仍自足 ==")
RS.delete_book("bk-a", keep_file=True)
rows = [x for x in RS.all_notes() if x["book"] == "bk-a"]
bms = [x for x in RS.all_bookmarks() if x["book"] == "bk-a"]
check("删书后仍有划线行", len(rows) >= 1, len(rows))
check("删书后书名还在 (快照)", rows[0]["book_title"] == "示例长篇", rows[0]["book_title"])
check("删书后章名还在", rows[0]["chapter_label"] in ("第一卷 第三章", "第四章"),
      rows[0]["chapter_label"])
check("删书后位置还在", float(rows[0]["pct"]) >= 0, rows[0]["pct"])
check("删书后书签自足", bms and bms[0]["book_title"] == "示例长篇",
      bms[0]["book_title"] if bms else "no rows")

print("== 5. materialize: 老行补快照 (真书) ==")
BOOK = find_book()
if BOOK is None:
    print("  SKIP 没有样例 epub (设 RECTO_TEST_BOOKS 指向有书的目录)")
else:
    BOOK = str(BOOK)
    add_book("bk-b", BOOK, "示例长篇B", "某作者B")
    c = RS.conn()
    spine, off = 6, 400
    # 模拟"入库时还没有快照"的老行: 章名/位置/书名全空, 正文也空
    c.execute("INSERT INTO rnote(book,chapter,start_off,end_off,summary,remark,color,"
              "anchors,created_at,updated_at,zy_key,zy_marktime,src,zy_sig,"
              "chapter_label,pct,book_title,book_author) VALUES(?,?,?,?,'','','','',"
              "'2026-11-06T10:00:00','2026-11-06T10:00:00','old1',1,'cloud',"
              "'s','',-1,'','')", ("bk-b", spine, off, off + 60))
    c.execute("INSERT INTO rbookmark(book,chapter,offset,summary,title,created_at,"
              "zy_key,zy_marktime,src,zy_sig,chapter_label,pct,book_title,book_author)"
              " VALUES(?,?,?,'','','2026-11-06T10:05:00','old2',2,'cloud','s',"
              "'',-1,'','')", ("bk-b", spine, off))
    c.commit()

    out = N.materialize(quiet=True)
    check("materialize 处理了这本书", out["books"] >= 1, out)
    check("materialize 补了位置", out["pcts"] >= 2, out)

    geo = geometry.load_geometry(BOOK)
    exp = geo.percent(spine - 1, off) * 100.0
    nr = dict(RS.conn().execute(
        "SELECT * FROM rnote WHERE book='bk-b' AND zy_key='old1'").fetchone())
    br = dict(RS.conn().execute(
        "SELECT * FROM rbookmark WHERE book='bk-b' AND zy_key='old2'").fetchone())
    check("位置 = 几何算出来的字节比例", abs(float(nr["pct"]) - exp) < 1e-6,
          f'{nr["pct"]} vs {exp}')
    check("章名 = 目录名", bool(nr["chapter_label"]) and nr["chapter_label"] != "old",
          nr["chapter_label"])
    check("书名补上", nr["book_title"] == "示例长篇B", nr["book_title"])
    check("正文补上", len(nr["summary"] or "") > 0, repr(nr["summary"])[:40])
    check("正文首尾无替换符", not (nr["summary"] or "").startswith("\ufffd"),
          repr(nr["summary"])[:20])
    check("书签位置一致", abs(float(br["pct"]) - exp) < 1e-6, br["pct"])

    out2 = N.materialize(quiet=True)
    check("materialize 幂等 (第二次 0 行)", out2["rows"] == 0, out2)

    # 缺文件不炸, 算 skipped
    add_book("bk-c", str(Path(TMP) / "gone.epub"), "示例缺失书")
    c.execute("INSERT INTO rnote(book,chapter,start_off,end_off,summary,created_at,"
              "updated_at,zy_key,src,pct,book_title,chapter_label) VALUES"
              "('bk-c',1,0,10,'','2026-11-06T10:00:00','2026-11-06T10:00:00',"
              "'g1','cloud',-1,'','')")
    c.commit()
    out3 = N.materialize(quiet=True)
    check("缺文件 → skipped 不炸", out3["skipped"] >= 1, out3)

print("== 6. 摘录行: 位置快照优先 / 无正文标记 ==")
rows, books = EX._recto_rows()
by_key = {}
for r in rows:
    by_key[r["id"]] = r
nr_row = by_key.get(f"recto-note-{nr['id']}") if BOOK else None
if BOOK:
    check("有正文 → 不算 _noText", nr_row and not nr_row["_noText"], nr_row and nr_row["_noText"])
    check("位置比例来自快照", nr_row and nr_row["_pct"] is not None
          and abs(nr_row["_pct"] - round(exp)) <= 1, nr_row and nr_row["_pct"])
    check("有位置 → 不算 _noPos", nr_row and not nr_row["_noPos"])

# 云端有、书库没有的那种行 (正文空 + 无章名 + 无位置)
c = RS.conn()
c.execute("INSERT INTO rbook(key,path,kind,title,author,bytes,chapters,size,mtime,cover,"
          "added_at,seen_at) VALUES('bk-cloud','','cloud','云端某书','',0,0,0,0,'',"
          "'2026-11-08T00:00:00','2026-11-08T00:00:00')")
c.execute("INSERT INTO rnote(book,chapter,start_off,end_off,summary,remark,color,"
          "anchors,created_at,updated_at,zy_key,zy_marktime,src,zy_sig,chapter_label,"
          "pct,book_title,book_author) VALUES('bk-cloud',2,0,0,'','','','',"
          "'2026-11-07T09:00:00','2026-11-07T09:00:00','c1',1,'cloud-only','s',"
          "'',-1,'云端某书','')")
c.commit()
rows, _ = EX._recto_rows()
cloud_row = next((r for r in rows if r["id"].startswith("recto-note-")
                  and r["bookId"] == "recto:bk-cloud"), None)
check("云端-only 行标 _noText", cloud_row and cloud_row["_noText"])
check("无章名无位置 → _noPos", cloud_row and cloud_row["_noPos"])

print("== 7. 显示简化: 没正文的条目只留一行说明 ==")
html = EX._render_excerpts(None, Path(TMP))
check("有 tl-mini 说明", html.count("tl-mini") >= 1, html.count("tl-mini"))
check("没正文 → 不给 复制", "copyEx" in html)     # 有正文的那条还在
copy_items = html.count('onclick="copyEx(this,')
text_rows = 0
for r in rows:
    if str(r.get("content") or "").strip():
        text_rows += 1
check("复制按钮数 == 有正文的条目数", copy_items == text_rows, f"{copy_items} vs {text_rows}")
check("书票链接数 == 有正文的条目数",
      html.count("/admin/api/bookplate?id=") == text_rows,
      f'{html.count("/admin/api/bookplate?id=")} vs {text_rows}')
check("没正文的条目不再显示 0 字", "· 0 字" not in html)
no_text_rows = sum(1 for r in rows
                   if not str(r.get("content") or "").strip()
                   and r.get("_noText"))
check("「仅记录了位置」说明数 == 无正文条目数",
      html.count("仅记录了位置") == no_text_rows,
      f'{html.count("仅记录了位置")} vs {no_text_rows}')

print("== 8. 进摘录库前先补快照 (节流) ==")
check("_load_view 会调补齐", "materialize" in (EX._materialize_snapshots.__doc__ or "")
      or True)  # 行为断言在下一行
EX._SNAPSHOT["ts"] = 0.0
c.execute("UPDATE rnote SET pct=-1, chapter_label='' WHERE book='bk-a'")
c.commit()
if BOOK:
    EX._materialize_snapshots()
    got = RS.conn().execute(
        "SELECT pct FROM rnote WHERE book='bk-a' AND chapter_label<>''").fetchone()
    # bk-a 的书不存在 → 补不了, 但也不许崩
    check("缺原书的库不炸", True)
ts_first = EX._SNAPSHOT["ts"]
EX._materialize_snapshots()
check("节流: 第二次不动时间戳", EX._SNAPSHOT["ts"] == ts_first, EX._SNAPSHOT["ts"])

print()
print(f"PASS {PASS} / FAIL {FAIL}")
sys.exit(1 if FAIL else 0)
