"""本地书接入云端同步 (cloudlink) —— 身份来自文件内容 MD5。

跑法: cd <仓库根> && python3 tests/test_cloudlink.py

全部离线: 云书架 / 拉进度一律 monkeypatch, 不碰真实账号。
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

from recto.core import sidecar, paths  # noqa: E402
from recto.core.config import get_config  # noqa: E402
from recto.services import cloudlink, reader_state, sync  # noqa: E402

get_config(reload=True)
paths.PATHS = paths.resolve()

FAILED = []


def ok(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        FAILED.append(msg)


def make_epub(path: Path) -> None:
    """最小 epub (2 篇正文), 用来算 catalog_count / md5。"""
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml",
                   '<?xml version="1.0"?><container version="1.0" '
                   'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                   '<rootfiles><rootfile full-path="OEBPS/content.opf" '
                   'media-type="application/oebps-package+xml"/></rootfiles></container>')
        z.writestr("OEBPS/content.opf",
                   '<?xml version="1.0"?><package version="2.0" '
                   'xmlns="http://www.idpf.org/2007/opf"><metadata/>'
                   '<manifest><item id="c1" href="a.html" media-type="application/xhtml+xml"/>'
                   '<item id="c2" href="b.html" media-type="application/xhtml+xml"/></manifest>'
                   '<spine><itemref idref="c1"/><itemref idref="c2"/></spine></package>')
        z.writestr("OEBPS/a.html", "<html><body><p>甲</p></body></html>")
        z.writestr("OEBPS/b.html", "<html><body><p>乙</p></body></html>")


# ---------------------------------------------------------------- 准备两本书
lib = TMP / "library"
book_a = lib / "本地书甲.epub"
book_b = lib / "本地书乙.epub"
make_epub(book_a)
make_epub(book_b)
book_b.write_bytes(book_b.read_bytes() + b"\n<!-- diff -->\n")  # 内容不同 ⇒ 指纹不同

md5_a = sync.file_md5(book_a).upper()
md5_b = sync.file_md5(book_b).upper()
reader_state.scan(force=True)
key_a = reader_state.book_key_by_path(str(book_a))
key_b = reader_state.book_key_by_path(str(book_b))
ok(bool(key_a and key_b), "书库扫描认出两本本地书")

FAKE_SHELF = [{"key": f"shelf:{md5_a}{'0'*32}", "id": md5_a + "0" * 32,
               "name": "本地书甲", "size": book_a.stat().st_size}]
calls = {"pull": [], "upload": []}


def fake_shelf(*a, **kw):
    return list(FAKE_SHELF)


def fake_pull(key, **kw):
    calls["pull"].append(key)
    return {"ok": True, "changed": True, "cloud_pct": 0.25,
            "anchor": {"chapter": 1, "offset": 10, "percent": 0.25}}


import recto.services.acquisition as acquisition  # noqa: E402

acquisition.shelf_books = fake_shelf  # type: ignore[assignment]
sync.pull_book = fake_pull  # type: ignore[assignment]
cloudlink._SHELF["at"] = 0.0
cloudlink._MD5.clear()

# ---------------------------------------------------------------- items()
rows = cloudlink.items(refresh=True)
by_key = {r["key"]: r for r in rows}
ok(len(rows) == 2, "items() 列出两本没有云端身份的书")
ok(by_key[key_a]["md5"] == md5_a, "指纹 = 文件内容 MD5 (32 大写)")
ok(by_key[key_a]["in_shelf"] is True, "云书架里有同一份文件 ⇒ in_shelf")
ok(by_key[key_b]["in_shelf"] is False, "另一本不在云书架")
ok(all(not r["attached"] for r in rows), "初始都未接入")

s = cloudlink.summary(refresh=True)
ok(s["total"] == 2 and s["attached"] == 0, "summary 计数")
ok(s["upload"]["available"] is False, "文件上传当前不可用 (需要 App 原生签名)")
ok(len(s["upload"]["endpoints"]) == 3, "upload_capability 记录三条实测端点")

# ---------------------------------------------------------------- attach
res = cloudlink.attach(key_a)
ok(res["ok"] and res["md5"] == md5_a, "attach 返回指纹")
ok(res["in_shelf"] is True, "attach 认出云书架命中")
ok(res["total_count"] == 0, "目录解析不出章数时记 0 (不覆盖云端现值)")
link = sidecar.cloud_link(str(book_a)) or {}
ok(link.get("id32") == md5_a, "边车写入 cloud.id32")
ok(link.get("id64") == FAKE_SHELF[0]["id"], "云书架命中时同时写 id64")
ok(link.get("identity") == "file-md5", "身份来源可追溯")
ok("total_count" not in link, "章数未知就不写进边车")
ok(calls["pull"] and calls["pull"][-1] == key_a, "attach 后立刻拉一次进度")

# 云书架命中 ⇒ 写完 id64 就交给常规同步面板 (这里不再列它)
ok(len(cloudlink.items(refresh=True)) == 1, "接入 id64 之后从本面板消失")

# 不在云书架的那本: 只写 id32, 留在面板里标「已接入」
res_b = cloudlink.attach(key_b)
link_b = sidecar.cloud_link(str(book_b)) or {}
ok(link_b.get("id32") == md5_b, "只按指纹接入 (id32)")
ok(link_b.get("id64") == "", "没有同一份文件时不瞎写 id64")
rows = {r["key"]: r for r in cloudlink.items(refresh=True)}
ok(rows[key_b]["attached"] is True, "接入后 items 标 attached")
ok(len(rows) == 1, "面板里只剩没进云书架的那本")

# ---------------------------------------------------------------- detach
cloudlink.detach(key_b)
ok(sidecar.cloud_link(str(book_b)) is None, "detach 清掉云端身份")
after = {r["key"]: r for r in cloudlink.items(refresh=True)}
ok(after.get(key_b, {}).get("attached") is False, "detach 后又回到「未接入」")

# 取消接入不动云端记录 / 本地进度
prog = reader_state.get_progress(key_b) or {}
ok("percent" in prog, "detach 不动本地进度")

# ---------------------------------------------------------------- 边界
try:
    cloudlink.attach("no-such-book")
    ok(False, "未知 book key 应当报错")
except ValueError:
    ok(True, "未知 book key 报错")
ok(cloudlink._catalog(TMP / "x.txt") == 0, "非 epub 不算章数 (交给云端现值)")

print()
print("FAILED:", len(FAILED))
for f in FAILED:
    print("  -", f)
sys.exit(1 if FAILED else 0)
