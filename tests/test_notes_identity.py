"""笔记身份合并 —— 云端同一本书的两种 32hex 身份 / 大小写 / 文件一致性。

2026-11-08 的真实故障: 掌阅设备新划的线落在**小写** bookid 下, 网页只按大写去拉,
于是"我在掌阅有笔记, 网页侧没有拉下来"。

跑法: cd <仓库根> && PYTHONPATH=src python3 tests/test_notes_identity.py
全部离线: 云端 HTTP 一律 monkeypatch。
"""
from __future__ import annotations

import hashlib
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

from recto.core import sidecar, paths  # noqa: E402
from recto.core.config import get_config  # noqa: E402
from recto.services import notes, reader_state, sync  # noqa: E402

get_config(reload=True)
paths.PATHS = paths.resolve()

FAILED = []


def ok(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        FAILED.append(msg)


def _make_epub(path: Path, texts=("第一章正文ABC", "第二章正文DEF")) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml",
                   '<?xml version="1.0"?><container version="1.0" '
                   'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                   '<rootfiles><rootfile full-path="OEBPS/content.opf" '
                   'media-type="application/oebps-package+xml"/></rootfiles></container>')
        items = "".join(
            f'<item id="c{i}" href="c{i}.xhtml" media-type="application/xhtml+xml"/>'
            for i in range(len(texts)))
        refs = "".join(f'<itemref idref="c{i}"/>' for i in range(len(texts)))
        z.writestr("OEBPS/content.opf",
                   '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" '
                   f'version="2.0"><manifest>{items}</manifest><spine>{refs}</spine></package>')
        for i, t in enumerate(texts):
            z.writestr(f"OEBPS/c{i}.xhtml",
                       f'<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml">'
                       f'<body><p>{t}</p></body></html>')
    return path


# --- 1. 身份: id64 前缀优先, 去重, 非 32hex 丢弃 --------------------------
UPPER = "A" * 32
OTHER = "B" * 32
ids = notes.cloud_ids({"id64": UPPER + "C" * 32, "id32": OTHER})
ok(ids == [UPPER, OTHER], "两个身份都在, id64 前缀优先")
ok(notes.cloud_ids({"id64": UPPER + "C" * 32, "id32": UPPER.lower()}) == [UPPER],
   "同一身份的两种大小写去重成一条")
ok(notes.cloud_ids({"id32": "not-hex"}) == [], "非 32hex 不算身份")
ok(notes.hex32("  b" * 0 + OTHER.lower()) == OTHER, "hex32 统一大写")

# --- 2. 一本真实的书进书目 ------------------------------------------------
book = _make_epub(TMP / "library" / "测试书.epub")
md5 = hashlib.md5(book.read_bytes()).hexdigest().upper()
sidecar.set_cloud(book, id64=md5 + "D" * 32, id32=md5, name="测试书")
reader_state.scan(force=True)
sync._LINK_CACHE["at"] = 0
rows = sync.links()
ok(len(rows) == 1 and rows[0]["path"] == str(book), "边车 + 书目 → 一条映射")
ok(notes.file_md5(str(book)) == md5, "file_md5 = 文件字节 MD5 (大写)")
ok(notes.cloud_ids(rows[0]) == [md5], "id64 前缀与 id32 相同 → 一个身份")

# --- 3. 云端两种身份 (大小写) 合并 ---------------------------------------
# uniquecheck 形态: `<book32>_<positionstart><positionend>` (两段位置串直接相连)
POS_A = "0_0_0$##3-0-0-100-0##$"
POS_B = "0_0_0$##3-0-0-140-0##$"
POS_C = "0_0_0$##3-0-0-200-0##$"
POS_D = "0_0_0$##3-0-0-240-0##$"
POS_DEAD = "1_0_0$##3-0-1-300-0##$"
POS_DEAD2 = "1_0_0$##3-0-1-340-0##$"
KEY_UP = f"{md5}_{POS_A}{POS_B}"          # 大写身份 (我们推的)
KEY_LO = f"{md5.lower()}_{POS_C}{POS_D}"  # 小写身份 (设备写的)


def _ver_for(bid: str) -> dict:
    """云端: 大写身份 1 条, 小写身份 1 条 (设备写的) + 1 条墓碑。"""
    if bid == md5.lower():
        return {"bookid": bid,
                "booknotesVerList": [{"uniquecheck": KEY_LO, "marktime": 222}],
                "bookmarksVerList": [],
                "delList": [f"{md5.lower()}_{POS_DEAD}{POS_DEAD2}"]}
    return {"bookid": bid,
            "booknotesVerList": [{"uniquecheck": KEY_UP, "marktime": 111}],
            "bookmarksVerList": [],
            "delList": []}


notes.data_ver = _ver_for
ver = notes.cloud_versions_multi([md5])
ok(len(ver["note"]) == 2, "两个身份的各一条都收进来 (按大写归一去重)")
ok(notes.norm_key(KEY_LO) in ver["note"], "只有小写身份才有的那条也进来了")
ok(len(ver["del"]) == 1, "墓碑保留 (它对应的条目哪个身份都没有)")
ok(ver["sources"] == {md5: {"note": 1, "mark": 0}}, "记录了哪些身份真的有内容")

# 同一条 key 在两个身份下 marktime 不同 → 取大的
def _ver_both(bid: str) -> dict:
    return {"bookid": bid,
            "booknotesVerList": [{"uniquecheck": f"{bid[:32]}_{POS_A}{POS_B}",
                                  "marktime": 111 if bid[:1].isupper() else 999}],
            "bookmarksVerList": [], "delList": []}


notes.data_ver = _ver_both
ver = notes.cloud_versions_multi([md5])
ok(ver["note"][KEY_UP] == 999, "同一 key 取较新的 marktime")

# --- 4. pull: 小写身份的笔记能拉下来 --------------------------------------
notes.data_ver = _ver_for
res = notes.pull()
ok(res["added"] == 2, "两条都新增 (含只有小写身份的那条)")
got = reader_state.list_notes(rows[0]["key"])
ok(len(got) == 2, "本地现在有两条划线")
keys = {notes.norm_key(n["zy_key"]) for n in got}
ok(KEY_LO.upper() in keys, "小写身份那条以大写 key 落库")
again = notes.pull()
ok(again["added"] == 0 and again["unchanged"] == 2, "再拉一次幂等")

# --- 5. 本地文件与云端不是同一份 → 拒绝按位置切/写 -----------------------
real_md5 = notes.file_md5
notes.file_md5 = lambda p: "F" * 32
bad = notes.pull()
ok(bad["added"] == 0 and any(b.get("skipped") == "text-mismatch" for b in bad["books"]),
   "文件不一致 → 拉取跳过并说明原因")
badpush = notes.push(dry_run=True)
ok(any(b.get("skipped") == "text-mismatch" for b in badpush["books"]),
   "文件不一致 → 推送也跳过 (位置是字节坐标, 推上去会跳错)")
notes.file_md5 = real_md5

# --- 6. 墓碑只算真的删掉了 ----------------------------------------------
reader_state.conn().execute("DELETE FROM rnote WHERE book=?", (rows[0]["key"],))
reader_state.conn().commit()
notes.data_ver = lambda bid: {"bookid": bid, "booknotesVerList": [],
                              "bookmarksVerList": [],
                              "delList": []}
notes.pull()
notes.data_ver = _ver_for
notes.pull()
before = notes.pull()
ok(before["deleted"] == 0, "没有本地条目可删时, 墓碑不报'删除 N'")
target = reader_state.list_notes(rows[0]["key"])[0]
dead_key = notes.norm_key(target["zy_key"])


def _ver_dead(bid: str) -> dict:
    return {"bookid": bid, "booknotesVerList": [], "bookmarksVerList": [],
            "delList": [dead_key]}


notes.data_ver = _ver_dead
after = notes.pull()
left = {notes.norm_key(n["zy_key"]) for n in reader_state.list_notes(rows[0]["key"])}
ok(after["deleted"] == 1 and dead_key not in left and len(left) == 1,
   "云端墓碑真的删掉本地条目 (其余保留)")
ok(notes.pull()["deleted"] == 0, "删完再拉不再计数")

# --- 7. 云端有、书库没有的书要点出来 ------------------------------------
notes.data_ver = _ver_for
notes.notebooks = lambda: [
    {"bookid": md5.lower(), "name": "测试书", "notenum": 2, "marknum": 0},
    {"bookid": "E" * 32, "name": "书库外的书", "notenum": 5, "marknum": 1},
]
only = notes.cloud_only_books()
ok([u["name"] for u in only] == ["书库外的书"], "书库外的书被单列出来")
ok(only[0]["notes"] == 5 and only[0]["marks"] == 1, "带计数")
res = notes.pull()
ok([u["name"] for u in res["unmapped"]] == ["书库外的书"], "拉取结果里带上它")
ok(notes.status()["cloud_only"][0]["book_id"] == "E" * 32, "status 也带")

print()
if FAILED:
    print(f"{len(FAILED)} FAILED")
    raise SystemExit(1)
print("全部通过")
