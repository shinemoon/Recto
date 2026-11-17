"""笔记身份: 一本书只有一个云抽屉, 写进"已经有内容"的那一个。

2026-11-18 的真实故障: 网页里的划线与笔记在掌阅 App 的**书里**一条都看不到,
但账号级「笔记本」列表能看到两条同名书。根因是云端对 bookid 大小写敏感 ——
同一本书在两个大小写下是两套互不相干的抽屉:

- 手机端自己写的笔记落在**大写**抽屉 (九诗心 4 条, deviceName=meizuMEIZU 22);
- 老设备 (iReader Smart X3 Pro / Ocean 3 Plus) 留下的是**小写**抽屉。

所以口径不是"统一大写"也不是"统一小写", 而是 **一本书只写一个抽屉**:

1. 内部 (`zy_key` / `zy_seen` / 墓碑差集 / 幂等) 永远用**大写**这一个坐标系;
2. 发给云端时, 前缀换成这本书的写入身份 `wire_id()` —— 已有内容的那个抽屉,
   都没有才用大写;
3. `notes.identity_report(fix=True)` 把"两个抽屉都有"的书并成一个 (先拉后推,
   只删另一个抽屉里现在大写也有的条目; 拿不到正文的一律不动)。

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
check(notes.wire_key(f"{MD5.lower()}_{POS_A}{POS_B}") == f"{MD5}_{POS_A}{POS_B}",
      "wire_key: bookid 前缀转大写, 位置原样")
check(notes.norm_key(f"{MD5.lower()}_{POS_A}{POS_B}") == f"{MD5}_{POS_A}{POS_B}",
      "norm_key: 内部归一为大写")
check(notes.app_id(MD5) == MD5, "app_id: 32hex → 大写")
check(notes.app_id(MD5.lower()) == MD5, "app_id 幂等 (输小写也归一到大写)")

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
check(bb.get("bookid") == MD5, f"bookbase.bookid 是大写 (实得 {bb.get('bookid')})")
item = (seen_payload.get("booknotes") or [{}])[0]
uc = item.get("uniquecheck") or ""
check(uc.startswith(MD5), "uniquecheck 前缀是大写身份")
check(MD5 in uc and MD5.lower() not in uc, "uniquecheck 全串不含小写身份")
check(str(item.get("positionstart") or "").endswith("##$")
      and "$##3-0-" in str(item.get("positionstart") or ""),
      f"位置字段格式未受影响 (实得 {item.get('positionstart')})")
check(item.get("chaptername") is not None, "仍带章名 (App 展示用)")

print("== 4. 本地状态仍按大写归一 (下次不会重推) ==")
row = reader_state.list_notes(bkey)[0]
check(row["zy_key"].startswith(MD5), f"zy_key 内部保持大写 (实得 {row['zy_key'][:8]})")
res2 = notes.push(dry_run=True)
check(res2["pushed"] == 0, "再推 dry-run 无待推 (不会重复写云端)")

print("== 5. 删除: 发给云端的 key 也是大写 ==")
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
check(del_got.get("book") == MD5, "删除请求的 bookid 是大写")
k0 = (del_got.get("keys") or [""])[0]
check(k0.startswith(MD5), f"删除请求的 key 是大写 (实得 {k0[:8]})")

print("== 6. 读回来的仍然归一为大写 (合并大小写两套) ==")
notes.data_ver = lambda bid: ({"bookid": bid,
                               "booknotesVerList": [{"uniquecheck": f"{bid}_{POS_A}{POS_B}",
                                                     "marktime": 100}],
                               "bookmarksVerList": [], "delList": []})
notes.pull()
got = reader_state.list_notes(bkey)
check(len(got) == 1, "拉回 1 条")
check(got[0]["zy_key"].startswith(MD5), "拉回的 zy_key 归一为大写")

print("== 7. wire_id: 写进\"已经有内容\"的那个抽屉 ==")
LOW = MD5.lower()
check(notes.wire_id([MD5]) == MD5, "没有云端内容 → 大写")
check(notes.wire_id([MD5], {}) == MD5, "sources 为空 → 大写")
check(notes.wire_id([MD5], {LOW: {"note": 2, "mark": 0}}) == LOW,
      "只有小写有货 → 小写 (老设备的抽屉, 别在旁边另开一间)")
check(notes.wire_id([MD5], {MD5: {"note": 1}, LOW: {"note": 2}}) == MD5,
      "两边都有 → 大写 (当前 App 的口径)")
check(notes.wire_id([MD5], {MD5: {"note": 0, "mark": 0}}) == MD5,
      "有记录但零条目 → 仍算空")
check(notes.wire_key(f"{MD5}_{POS_A}{POS_B}", LOW).startswith(LOW),
      "wire_key(book=小写) 把前缀换成小写")
check(notes.wire_key(f"{LOW}_{POS_A}{POS_B}", MD5).startswith(MD5),
      "wire_key(book=大写) 把小写前缀纠成大写")
check(notes.wire_key(f"{MD5}_{POS_A}{POS_B}", MD5) == f"{MD5}_{POS_A}{POS_B}",
      "位置部分一字不动")

print("== 8. identity_report: 两个抽屉都有 → 合并成一个 ==")
UC_A = f"{MD5}_{POS_A}{POS_B}"
LC_A = f"{LOW}_{POS_A}{POS_B}"
POS_C = "5_0_0$##3-0-5-1000-0##$"
POS_D = "5_0_0$##3-0-5-1040-0##$"
UC_B = f"{MD5}_{POS_C}{POS_D}"
LC_B = f"{LOW}_{POS_C}{POS_D}"
cloud = {"up": {UC_A}, "lo": {LC_A, LC_B}}


def _fake_ver(bid):
    slot = "up" if str(bid) == str(bid).upper() else "lo"
    items = [{"uniquecheck": k, "marktime": 100} for k in sorted(cloud[slot])]
    return {"bookid": bid, "booknotesVerList": items, "bookmarksVerList": [], "delList": []}


def _fake_upload2(payload):
    bb = payload.get("bookbase") or {}
    slot = "up" if str(bb.get("bookid") or "") == str(bb.get("bookid") or "").upper() else "lo"
    for it in (payload.get("booknotes") or []):
        cloud[slot].add(str(it.get("uniquecheck") or ""))
    return {"status": 0, "code": 0, "msg": "ok"}


def _fake_del2(book32, keys, type_=2):
    slot = "up" if str(book32) == str(book32).upper() else "lo"
    for k in keys:
        cloud[slot].discard(str(k))
    return {"status": 0, "msg": "ok"}


notes.data_ver = _fake_ver
notes.upload = _fake_upload2
notes.delete_items = _fake_del2

with reader_state.conn() as c:
    c.execute("DELETE FROM rnote")
    c.execute("DELETE FROM rbookmark")
    c.commit()
# 清掉前几段留下的 seen 快照: 否则 push 会把"本地已删但云端还有"的旧条目当成
# 待删, 顺手把刚铺好的云端状态也删掉 (这段是身份的账, 不是删除语义的账)
_sc = notes._state_conn()
_sc.execute("DELETE FROM zy_seen")
_sc.commit()
_sc.close()
# 本地这一条与云端 UC_A 是**同一处**划线 (ch5 822~930), 这样不会多出第三条
reader_state.add_note(bkey, chapter=5, start_off=822, end_off=930, summary="甲乙丙", remark="")
# 先把本地这一条推成"大写抽屉"的状态 (模拟已经同步过)
notes.push()

rep = notes.identity_report()
item = (rep.get("books") or [{}])[0]
check(item.get("dual") is True, "识别出两个抽屉都有条目")
check(item.get("write") == MD5, "写入身份取大写 (两边都有时的口径)")
check(rep.get("dual") == [item.get("book")], "dual 列表里有这本书")

rep2 = notes.identity_report(fix=True)
item2 = (rep2.get("books") or [{}])[0]
check(item2.get("action") == "merged", f"fix 执行了合并 (实得 {item2.get('action')})")
check(item2.get("cases_after") == {"upper": 2},
      f"合并后只剩大写一个抽屉且两条齐 (实得 {item2.get('cases_after')})")
check(not cloud["lo"], "小写抽屉已清空")
check(UC_A in cloud["up"] and UC_B in cloud["up"], "两条都在大写抽屉里")
got = reader_state.list_notes(bkey)
check(len(got) == 2, f"本地拉到了两条 (实得 {len(got)})")
check(all(str(r["zy_key"]).startswith(MD5) for r in got), "本地 zy_key 全部大写")
check(notes.identity_report().get("dual") == [], "复查: 不再分裂")

print("== 9. 小写抽屉独占的书: 写回小写, 不另开一间 ==")
cloud["up"] = set()
cloud["lo"] = {LC_A}
rep3 = notes.identity_report()
item3 = (rep3.get("books") or [{}])[0]
check(item3.get("dual") is False, "只有一个抽屉 → 不算分裂")
check(item3.get("write") == LOW, f"写入身份是小写 (实得 {item3.get('write')})")

print("== 10. 本地 key 大小写漂移会被归一 ==")
with reader_state.conn() as c:
    c.execute("UPDATE rnote SET zy_key=? WHERE zy_key<>''", (f"{LOW}_{POS_A}{POS_B}",))
    c.commit()
check(notes.status()["identity"]["local_key_case_issues"] >= 1, "status 报出本地 key 大小写问题")
notes._upper_local_keys()
check(notes.status()["identity"]["local_key_case_issues"] == 0, "归一后清零")
notes.pull(quiet=True)
check(notes.status()["identity"]["local_key_case_issues"] == 0, "pull 也顺手归一 (幂等)")

print()
if FAIL:
    print(f"{FAIL} FAILED")
    raise SystemExit(1)
print(f"全部通过 ({PASS} 项)")
