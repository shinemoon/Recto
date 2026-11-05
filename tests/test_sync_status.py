"""云端映射状态表 —— 之前表里全是空的 (进度/时间戳 undefined)，这里钉住真实字段。

跑法: cd <仓库根> && python3 tests/test_sync_status.py

全离线: 云端 HTTP 一律 monkeypatch。
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
(TMP / "library").mkdir(parents=True, exist_ok=True)
(TMP / "auth.json").write_text(json.dumps({
    "usr": "i0000000000", "zysid": "test", "p29": "zye5b814",
    "p2": "104583", "p3": "101010010", "p4": "501603"}), "utf-8")

from recto.core import paths, sidecar  # noqa: E402
from recto.core.config import get_config  # noqa: E402
from recto.services import reader_state, sync  # noqa: E402

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


book = _make_epub(TMP / "library" / "状态书.epub", texts=("状态书第一章AAA",))
reader_state.scan()
key = reader_state.book_key_by_path(book)
ok(bool(key), "书被阅读器收进来 (有 key)")

sidecar.set_cloud(book, id64="A" * 64, id32="b" * 32, name="状态书", total_count=8)
sidecar.touch_cloud(book, last_pull_at="2026-11-04T11:16:13+08:00", last_pull_pct=0.54299391)
sidecar.touch_cloud(book, last_push_at="2026-11-03T22:50:59+08:00", last_push_pct=0.5)
reader_state.save_progress(key, 2, 100, 0.31, "")

st = sync.status()
ok(st["link_count"] == 1, "status() 认出 1 本映射")
row = st["links"][0]
ok(row["local_percent"] == 0.31, "本地进度来自阅读器 (0.31)")
ok(row["cloud_percent"] == 0.54299391, "云端进度优先用上次拉取值")
ok(row["last_push_at"] == "2026-11-03T22:50:59+08:00", "上次推送时间戳带出来")
ok(row["last_pull_at"] == "2026-11-04T11:16:13+08:00", "上次拉取时间戳带出来")
ok(row["id32"] == "bbbbbbbb", "id 只给 8 位给人看")
ok(row["missing"] is False, "正常行不标文件缺失")

# 只有推送过、没拉过的书: 云端值退回 last_push_pct
book3 = _make_epub(TMP / "library" / "只推过.epub", texts=("只推过第一章BBB",))
reader_state.scan()
key3 = reader_state.book_key_by_path(book3)
sidecar.set_cloud(book3, id64="E" * 64, id32="f" * 32, name="只推过", total_count=8)
sidecar.touch_cloud(book3, last_push_at="2026-11-03T22:50:59+08:00", last_push_pct=0.5)
sync._LINK_CACHE["at"] = 0.0
rows3 = {r["book"]: r for r in sync.status()["links"]}
ok(rows3["只推过"]["cloud_percent"] == 0.5, "没拉过时云端值退回上次推送值")
ok(rows3["只推过"]["last_pull_at"] is None, "从没拉过 → 上次拉取为空 (渲染成 —)")

# 书文件消失, 边车还在 —— 不能从表里凭空少一本
book.unlink()
sync._LINK_CACHE["at"] = 0.0
st3 = sync.status()
miss = [r for r in st3["links"] if r["missing"]]
ok(len(miss) == 1, "文件没了, 映射行仍在 (不静默消失)")
ok(miss and miss[0]["book"] == "状态书", "缺失行带着原书名")
ok(miss and miss[0]["local_percent"] is None, "缺失行的本地进度为 None (渲染成 —)")
ok(st3["link_count"] == len(st3["links"]), "link_count 与行数一致")

# --- probe: 只读云端, 不写任何进度 -------------------------------------
book2 = _make_epub(TMP / "library" / "探针书.epub", texts=("探针书第一章CCC",))
reader_state.scan()
key2 = reader_state.book_key_by_path(book2)
sidecar.set_cloud(book2, id64="C" * 64, id32="d" * 32, name="探针书", total_count=8)
sync._LINK_CACHE["at"] = 0.0

calls = {}


def fake_get_progress(auth, keys):
    calls["keys"] = list(keys)
    return {k: {"readProcess": {"readPercent": "0.618", "readComplete": "0",
                                "chapterId": "3", "totalCount": "8"}} for k in keys}


sync.cloud.get_progress = fake_get_progress
sync._zy_notebook_pct = lambda: {}
before = reader_state.get_progress(key2) or {}
pr = sync.probe(auth={"usr": "x"})
after = reader_state.get_progress(key2) or {}
ok(pr["ok"] is True and pr["books"], "probe() 返回每本书的云端值")
ent = pr["books"].get(key2) or {}
ok(abs(float(ent.get("cloud_percent")) - 0.618) < 1e-9, "probe 读到云端 61.8%")
ok(ent.get("chapter_id") == "3", "probe 带上云端 chapterId")
ok(before.get("percent") == after.get("percent"), "probe 不写本地进度 (只读)")
ok(len(calls.get("keys") or []) >= 1, "probe 真的请求了云端 (32/64 双 key)")

# 云端进度为 0 (没读过) 不能当成"未知"
sync.cloud.get_progress = lambda auth, keys: {
    k: {"readProcess": {"readPercent": "0"}} for k in keys}
pr0 = sync.probe(auth={"usr": "x"})
ok(pr0["books"][key2]["cloud_percent"] == 0.0, "云端 0% 是有效值, 不是 None")

print()
if FAILED:
    print(f"{len(FAILED)} FAILED")
    for f in FAILED:
        print(" -", f)
    sys.exit(1)
print("ALL PASS")
