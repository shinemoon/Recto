"""解耦后的核心行为 —— 边车 JSON / 进度判定 / 笔记位置 / 时长队列。

跑法: cd <仓库根> && PYTHONPATH=src python3 tests/test_sync_notes.py

全部离线: 云端 HTTP 一律 monkeypatch, 不碰真实账号。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import zipfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

TMP = Path(tempfile.mkdtemp())
os.environ["RECTO_DATA_DIR"] = str(TMP / "data")
os.environ["RECTO_LIBRARY_DIR"] = str(TMP / "library")
os.environ["RECTO_CLOUD_AUTH_FILE"] = str(TMP / "auth.json")
os.environ["RECTO_AUTH_DISABLE"] = "1"
(TMP / "library").mkdir(parents=True, exist_ok=True)
(TMP / "auth.json").write_text(json.dumps({
    "usr": "i0000000000", "zysid": "test", "p29": "zye5b814",
    "p2": "104583", "p3": "101010010", "p4": "501603"}), "utf-8")

from recto.core import sidecar, paths  # noqa: E402
from recto.core.config import get_config  # noqa: E402
from recto.services import notes, reader_state, sessions, sync  # noqa: E402

get_config(reload=True)
paths.PATHS = paths.resolve()

FAILED = []


def ok(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        FAILED.append(msg)


def _make_epub(path: Path, texts=("第一章正文ABC", "第二章正文DEF")) -> Path:
    """最小可解析 epub: opf + spine + 两个 xhtml。"""
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


# --- 1. 边车 JSON (跟书同目录) -------------------------------------------
book = _make_epub(TMP / "library" / "测试书.epub")
sc = sidecar.sidecar_path(book)
ok(sc == TMP / "library" / "测试书.recto.json", "边车默认与书同目录: <书名>.recto.json")
ok(sidecar.read(book) == {}, "没有边车时返回空 dict")

sidecar.set_cloud(book, id64="A" * 64, id32="B" * 32, name="测试书")
sidecar.write(book, {"notes": {"x": 1}})
sidecar.set_cloud(book, total_count=8)
data = sidecar.read(book)
ok(data["cloud"]["id64"] == "A" * 64 and data["cloud"]["total_count"] == 8,
   "多次写是深合并, 不丢字段")
ok(data["notes"]["x"] == 1, "别的段不被覆盖")
ok(sidecar.cloud_link(book)["id32"] == "B" * 32, "cloud_link 读得到映射")
ok(sidecar.find_keywords(book) is None, "没有 keywords 文件时返回 None")
sidecar.keywords_path(book).write_text('{"a":1}', "utf-8")
ok(sidecar.find_keywords(book) == sidecar.keywords_path(book), "认得 <书名>.keywords.json")

entries = sidecar.iter_sidecars()
ok(len(entries) == 1 and entries[0][0].name == "测试书.epub", "枚举带映射的边车")

# --- 2. 进度口径 / 判定 ---------------------------------------------------
ok(sync.app_book_keys("A" * 64) == ["A" * 32, "A" * 64], "64 hex 双 key: 32 在前")
ok(sync.app_book_keys("12345") == ["12345"], "非 64 hex 原样返回")
ok(sync._normalize_percent("0.5") == (0.5, True), "readPercent 0.5")
ok(sync._normalize_percent("100")[1] is False, "越界百分数判坏值")
ok(sync._normalize_percent("x")[1] is False, "非数字判坏值")


class _Geo:
    def __init__(self, catalog, n):
        self.catalog = catalog
        self.sizes = [1] * n


ok(sync._catalog_chapter(_Geo(8, 9), 7) == 6, "epub chapterId = spine - 1 (狐 spine 7 -> 6)")
ok(sync._catalog_chapter(_Geo(588, 590), 159) == 158, "前置 2 篇 (示例长篇口径) 差 2")
ok(sync._catalog_chapter(None, 3) == 3, "没有几何时原样返回")

ok(sync.push_decision(None, False, 0.3, False)[0] is True, "没有基线: 允许")
ok(sync.push_decision(0.5, False, 0.3, False)[0] is False, "回退: 挡")
ok(sync.push_decision(0.5, False, 0.5, False)[0] is False, "持平: noop")
ok(sync.push_decision(0.5, False, 0.5, False, meta_stale=True)[0] is True, "持平 + 元信息错: 允许")
ok(sync.push_decision(0.5, True, 0.4, False)[0] is False, "云端已完成: 不回退")
ok(sync.push_decision(0.9, False, 1.0, True)[0] is True, "读完: 允许")

# --- 3. 笔记位置 / 正文 ---------------------------------------------------
b32 = "C" * 32
pos = "1_0_0$##3-0-1-0-0##$"
co = notes.parse_chapter_offset(pos)
ok(co == (0, 0), "位置串解得 (chapterId, f3); pre=1 → chapterId 0")
ok(notes.kb32("c" * 32) == "C" * 32, "bookid32 统一大写")
ok(notes.norm_key("abc") == "ABC", "uniquecheck 归一")
ok(notes.mark_key(b32, pos) == f"{b32}_{pos}", "书签 key 形态")

# 用合成 epub 切正文: 第二章 c1(索引 1) 的正文
txt = notes.text_of_span(str(book), "1_0_0$##3-0-1-0-0##$", "1_0_0$##3-0-1-0-0##$")
ok("第二章正文" in txt or "第一章正文" in txt or txt == "", "零长度选择能取到上下文或空")
ok(notes.text_of_span(str(book), "bad", "bad") == "", "坏位置返回空串")

# --- 4. 阅读时长: 位图 / 队列 / 上传 --------------------------------------
from datetime import datetime  # noqa: E402

b64 = sessions.bitmap_from_minutes([0, 59, 479])
ok(set(sessions.minutes_from_bitmap(b64)) == {0, 59, 479}, "分钟位图往返")
merged, n = sessions._union(sessions.bitmap_from_minutes([1, 2]),
                            sessions.bitmap_from_minutes([2, 3]))
ok(n == 3 and set(sessions.minutes_from_bitmap(merged)) == {1, 2, 3}, "位图并集")
ok(len(sessions.bitmap_from_span(datetime(2026, 1, 1, 10, 0),
                                 datetime(2026, 1, 1, 10, 5))) > 0, "时间段 → 位图")

reader_state.scan(force=True)
bkey = reader_state.book_key_by_path(book)
ok(bkey is not None, "书进了阅读器书目")
reader_state.save_progress(bkey, 1, 0, 0.5, "2_0_0$##3-0-2-0-0##$")
# 本地位图: 今天 2 分钟
reader_state.conn().execute(
    "INSERT OR REPLACE INTO rtime(book,day,bitmap,minutes,first_at,updated_at) VALUES(?,?,?,?,?,?)",
    (bkey, date.today().isoformat(), sessions.bitmap_from_minutes([600, 601]), 2, "x", "x"))
reader_state.conn().commit()

enq = sessions.enqueue(date.today(), keys=[bkey])
ok(enq["queued"] == 1, "本地有分钟 → 入队")

posted = []


def _fake_post(data):
    posted.append(data)
    return {"code": 0}


sessions._post_report = _fake_post
res = sessions.flush()
ok(res["cleared"] == 1 and posted, "flush 成功后清队列")
ok(list(posted[0].values())[0][0]["local_bid"].endswith("_local"), "上传 key 以 _local 结尾")


class _DummyGeo:
    catalog = 588
    sizes = [0] * 590


def _fail_post(data):
    raise RuntimeError("network down")


sessions._post_report = _fail_post
# 又读了几分钟 → 本地位图比"已推上去的"多, 这次是真有东西要推
reader_state.conn().execute(
    "UPDATE rtime SET bitmap=?, minutes=? WHERE book=? AND day=?",
    (sessions.bitmap_from_minutes([600, 601, 602]), 3, bkey, date.today().isoformat()))
reader_state.conn().commit()
sessions.enqueue(date.today(), keys=[bkey])
res = sessions.flush()
ok(res["kept"] == 1 and res["errors"], "上传失败留队, 不丢数据")
sessions._post_report = _fake_post
sessions.flush()

# 已经推上去的分钟不该每轮再推一遍 (定时通道每 10 分钟跑一次, 否则全是噪声)
again = sessions.enqueue(date.today(), keys=[bkey])
ok(again["queued"] == 0 and again["unchanged"] == 1, "同为位置图不重复入队")
posted.clear()
pushed_again = sessions.push(date.today(), keys=[bkey])
ok(pushed_again["written"] == 0 and not posted, "没新增分钟 → 一次网络都不打")

# --- 5. 云端状态合并 (不触网) --------------------------------------------
sync.cloud.get_progress = lambda auth, ids: {
    "A" * 32: {"bookId": "A" * 32, "updateTime": 100,
               "readProcess": {"readPercent": "0.4", "chapterId": "3", "totalCount": "8",
                               "readPosition": "4_0_0$##3-0-4-100-0##$"}}
}
sync._zy_notebook_pct = lambda force=False: {("C" * 32).lower(): (0.6, "5", 10, 200)}
rows = [{"id64": "A" * 64, "id32": "A" * 32}, {"id64": "C" * 32, "id32": "C" * 32}]
st = sync._cloud_state({"usr": "x"}, rows)
ok(abs(st["A" * 64]["pct"] - 0.4) < 1e-9, "getProgress 进度读入")
ok(abs(st["C" * 32]["pct"] - 0.6) < 1e-9, "只有一份记录时直接用它")
ok(abs(st["C" * 32]["max_pct"] - 0.6) < 1e-9, "max_pct 单独给出")

# 三份记录: 最新那条说了算, 不能用"最大百分比"顶掉读者的真实位置
# (2026-11-05: 手机读到 31.7%, 桥接此前推过 34% → 取最大就跳到没读到的地方)
sync.cloud.get_progress = lambda auth, ids: {
    "D" * 32: {"bookId": "D" * 32, "updateTime": 300,
               "readProcess": {"readPercent": "0.3177", "chapterId": "182",
                               "readPosition": "183_0_0$##3-0-183-34656-0##$"}},
    "D" * 64: {"bookId": "D" * 64, "updateTime": 200,
               "readProcess": {"readPercent": "0.34", "chapterId": "194",
                               "readPosition": "195_0_0$##3-0-195-49691-0##$"}},
}
sync._zy_notebook_pct = lambda force=False: {}
rows = [{"id64": "D" * 64, "id32": "D" * 32}]
st = sync._cloud_state({"usr": "x"}, rows)
ent = st["D" * 64]
ok(abs(ent["pct"] - 0.3177) < 1e-9, "取最新记录 (不是最大百分比)")
ok(abs(ent["max_pct"] - 0.34) < 1e-9, "max_pct 仍是三条里最靠前的 (给防回拨闸门用)")
ok(ent["chapter_id"] == "182", "随最新记录一起带出 chapterId")

# push 写 readPosition 时 spine 下标直接当 chapterId 会超前一章
from recto.core import geometry as _geo  # noqa: E402

ok(_geo.build_position(194, 49691).startswith("195_0_0"),
   "build_position(chapterId) → pre = chapterId+1 (spine 下标)")
ok(sync._catalog_chapter(None, 195) == 195, "txt 书 chapterId = 章序号")
ok(sync._catalog_chapter(_DummyGeo(), 195) == 194,
   "epub chapterId = spine - 1 (与 readPosition 同一套编号)")

# --- 6. 下载目录只能落在书库内 -------------------------------------------
from recto.services import acquisition  # noqa: E402

ok(Path(acquisition.dl_dir()) == (TMP / "library").resolve(), "下载目录 = 书库")
try:
    acquisition.safe_dir("/tmp/outside-recto")
    ok(False, "书库外的目录应被拒绝")
except acquisition.CloudError:
    ok(True, "书库外的目录被拒绝")

print()
if FAILED:
    print(f"{len(FAILED)} FAILED")
    raise SystemExit(1)
print("全部通过")
