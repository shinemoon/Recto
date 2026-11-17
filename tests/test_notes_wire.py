"""推给掌阅的笔记身份必须是 **小写** 32hex (App 的口径)。

2026-11-18 的真实故障: 网页里的划线与笔记在掌阅 App 的**书里**一条都看不到,
但账号级「笔记本」列表能看到两条同名书。根因是云端对 bookid 大小写敏感:

- App 的笔记身份 = `getFileMD5()` 裸值 (32 hex **小写**) —— 它打开书时按这个去拉
- 进度身份才是大写 (`mLocalBookFileMd5`)
- 我们一直推大写 → 内容确实存进云端了, 但存进的是 App 不看的那个抽屉

内部仍按大写归一去重 (拉取/去重/墓碑都用大写), 只在**发给云端**的边界转小写。

跑法: cd <仓库根> && PYTHONPATH=src python3 tests/test_notes_wire.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

TMP = Path(tempfile.mkdtemp())
os.environ["RECTO_DATA_DIR"] = str(TMP / "data")
os.environ["RECTO_LIBRARY_DIR"] = str(TMP / "library")
os.environ["RECTO_CLOUD_AUTH_FILE"] = str(TMP / "auth.json")
os.environ["RECTO_AUTH_DISABLE"] = "1"
os.environ["RECTO_AUTH_LOG"] = "off"
(TMP / "library").mkdir(parents=True, exist_ok=True)
(TMP / "auth.json").write_text(json.dumps({
    "usr": "i0000000000", "zysid": "test", "p29": "zye5b814",
    "p2": "104583", "p3": "101010010", "p4": "501603"}), "utf-8")

from recto.core import paths, sidecar  # noqa: E402
from recto.core.config import get_config  # noqa: E402
from recto.services import notes, reader_state, sync  # noqa: E402

get_config(reload=True)
paths.PATHS = paths.resolve()

PASS = FAIL = 0


def check(cond, msg):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {msg}")
    else:
        FAIL += 1
        print(f"  FAIL {msg}")


MD5 = "1E099652C2B290281A8A1D9FAE144748"
POS_A = "5_0_0$##3-0-5-822-0##$"
POS_B = "5_0_0$##3-0-5-930-0##$"


def _make_epub(path: Path) -> Path:
    body = ("<html><body>" + ("甲" * 400) + "</body></html>")
    nav = ('<?xml version="1.0"?><ncx><navMap>'
           '<navPoint><navLabel><text>一</text></navLabel>'
           '<content src="a.xhtml"/></navPoint></navMap></ncx>')
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml",
                   '<?xml version="1.0"?><container><rootfiles><rootfile '
                   'full-path="OEBPS/content.opf"/></rootfiles></container>')
        z.writestr("OEBPS/content.opf",
                   '<?xml version="1.0"?><package><metadata/><manifest>'
                   '<item id="a" href="a.xhtml" media-type="application/xhtml+xml"/>'
                   '<item id="n" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
                   '</manifest><spine toc="n"><itemref idref="a"/></spine></package>')
        z.writestr("OEBPS/toc.ncx", nav)
        z.writestr("OEBPS/a.xhtml", body)
    return path


print("== 1. 身份换算 ==")
check(notes.wire_key(f"{MD5}_{POS_A}{POS_B}") == f"{MD5.lower()}_{POS_A}{POS_B}",
      "wire_key: bookid 前缀转小写, 位置原样")
check(notes.norm_key(f"{MD5.lower()}_{POS_A}{POS_B}") == f"{MD5}_{POS_A}{POS_B}",
      "norm_key: 内部归一为大写")
check(notes.app_id(MD5) == MD5.lower(), "app_id: 32hex → 小写")
check(notes.app_id(MD5.lower()) == MD5.lower(), "app_id 幂等")

print("== 2. 造书 + 一条本地划线 ==")
epub = _make_epub(TMP / "library" / "测试书.epub")
sidecar.write(epub, {"cloud": {"id32": MD5, "id64": MD5}})
sync.links(refresh=True)
rows = sync.links()
check(len(rows) == 1, "书库识别到 1 本")
bkey = rows[0]["key"]
notes.file_md5 = lambda p: MD5          # 假装本地就是云端那份文件
notes.data_ver = lambda bid: {"bookid": bid, "booknotesVerList": [],
                              "bookmarksVerList": [], "delList": []}
notes.notebooks = lambda: []

with reader_state.conn() as c:
    c.execute("DELETE FROM rnote")
    c.execute("DELETE FROM rbookmark")
    c.commit()
reader_state.add_note(bkey, chapter=1, start_off=100, end_off=160,
                      summary="甲乙丙", remark="")

print("== 3. push: 发出去的 bookid / uniquecheck 都是小写 ==")
seen_payload = {}
real_upload = notes.upload


def _fake_upload(payload):
    seen_payload.update(payload)
    return {"status": 0, "code": 0, "msg": "ok", "res": "success"}


notes.upload = _fake_upload
res = notes.push()
notes.upload = real_upload
check(res["errors"] == [], "push 无错误")
bb = seen_payload.get("bookbase") or {}
check(bb.get("bookid") == MD5.lower(), f"bookbase.bookid 是小写 (实得 {bb.get('bookid')})")
item = (seen_payload.get("booknotes") or [{}])[0]
uc = item.get("uniquecheck") or ""
check(uc.startswith(MD5.lower()), "uniquecheck 前缀是小写身份")
check(MD5.lower() in uc and MD5 not in uc, "uniquecheck 全串不含大写身份")
check(str(item.get("positionstart") or "").endswith("##$")
      and "$##3-0-" in str(item.get("positionstart") or ""),
      f"位置字段格式未受影响 (实得 {item.get('positionstart')})")
check(item.get("chaptername") is not None, "仍带章名 (App 展示用)")

print("== 4. 本地状态仍按大写归一 (下次不会重推) ==")
row = reader_state.list_notes(bkey)[0]
check(row["zy_key"].startswith(MD5), f"zy_key 内部保持大写 (实得 {row['zy_key'][:8]})")
res2 = notes.push(dry_run=True)
check(res2["pushed"] == 0, "再推 dry-run 无待推 (不会重复写云端)")

print("== 5. 删除: 发给云端的 key 也是小写 ==")
del_got = {}
real_del = notes.delete_items


def _fake_del(book32, keys, type_=2):
    del_got.update({"book": book32, "keys": list(keys), "type": type_})
    return {"status": 0, "msg": "ok"}


notes.delete_items = _fake_del
with reader_state.conn() as c:
    c.execute("DELETE FROM rnote")
    c.commit()
notes.push()
notes.delete_items = real_del
check(del_got.get("book") == MD5.lower(), "删除请求的 bookid 是小写")
k0 = (del_got.get("keys") or [""])[0]
check(k0.startswith(MD5.lower()), f"删除请求的 key 是小写 (实得 {k0[:8]})")

print("== 6. 读回来的仍然归一为大写 (合并大小写两套) ==")
notes.data_ver = lambda bid: ({"bookid": bid,
                               "booknotesVerList": [{"uniquecheck": f"{bid}_{POS_A}{POS_B}",
                                                     "marktime": 100}],
                               "bookmarksVerList": [], "delList": []})
notes.pull()
got = reader_state.list_notes(bkey)
check(len(got) == 1, "拉回 1 条")
check(got[0]["zy_key"].startswith(MD5), "拉回的 zy_key 归一为大写")

print()
if FAIL:
    print(f"{FAIL} FAILED")
    raise SystemExit(1)
print(f"全部通过 ({PASS} 项)")
