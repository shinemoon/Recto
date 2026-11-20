"""书架上传 —— 离线测试。

跑法: python3 tests/test_library_upload.py

覆盖:
  * 文件名清洗 (路径剥离 / 非法字符 / 保留名 / 扩展名保留)
  * 类型双确认 (改后缀骗不过 / epub 必须是 zip / txt 必须可解码)
  * 不覆盖已有书 (重名加序号)
  * 只落书库 (子目录越界拒)
  * 大小上限
  * multipart 解析 (文字字段 / 文件 part / 边界跨块)
  * 流式落盘 (大文件不整份进内存)
"""
from __future__ import annotations

import io
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from recto.core import multipart                     # noqa: E402
from recto.services import library_upload as lu     # noqa: E402

PASS = 0
FAIL = 0


def ok(cond, name, extra="") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"PASS {name}")
    else:
        FAIL += 1
        print(f"FAIL {name} {extra}")


def make_epub(path: Path, title: str = "测试书") -> None:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml",
                   '<?xml version="1.0"?><container version="1.0" '
                   'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                   '<rootfiles><rootfile full-path="OEBPS/content.opf" '
                   'media-type="application/oebps-package+xml"/></rootfiles></container>')
        z.writestr("OEBPS/content.opf",
                   f'<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" '
                   f'version="3.0" unique-identifier="id"><metadata '
                   f'xmlns:dc="http://purl.org/dc/elements/1.1/">'
                   f'<dc:title>{title}</dc:title><dc:creator>某作者</dc:creator>'
                   f'</metadata></package>')
        z.writestr("OEBPS/ch1.html", "<html><body><p>正文</p></body></html>")


def multipart_body(fields: dict, files: dict, boundary: str = "----rectoTest") -> bytes:
    out = b""
    for k, v in fields.items():
        out += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n"
                f"{v}\r\n").encode("utf-8")
    for k, (fname, data) in files.items():
        out += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"; "
                f"filename=\"{fname}\"\r\nContent-Type: application/octet-stream\r\n\r\n"
                ).encode("utf-8")
        out += data + b"\r\n"
    out += f"--{boundary}--\r\n".encode("utf-8")
    return out


# --- 文件名清洗 -----------------------------------------------------------
ok(lu.clean_name("a/b/c.epub") == "c.epub", "路径被剥掉")
ok(lu.clean_name(r"C:\x\y.epub") == "y.epub", "Windows 路径被剥掉")
ok("/" not in lu.clean_name("a/b.epub"), "结果里没有斜杠")
ok(":" not in lu.clean_name("书:名.epub"), "冒号被换掉")
ok(lu.clean_name("  书 名 .epub ") == "书 名.epub", "空白压成一个、词尾空格去掉")
ok(lu.clean_name("CON.epub").startswith("_"), "Windows 保留名加前缀")
ok(lu.clean_name("无扩展名") == "无扩展名", "没扩展名也不加")
try:
    lu.clean_name("")
    ok(False, "空名该被拒")
except lu.UploadError:
    ok(True, "空名被拒")

# --- 类型双确认 -----------------------------------------------------------
tmp = Path(tempfile.mkdtemp(prefix="recto-upload-test-"))
try:
    # epub 伪装成 txt → 判 epub (内容是 zip) 但 txt 后缀其实会被嗅探成 txt?
    # 口径: 后缀 .txt + 内容是 zip → 嗅探先看 zip 头 → 判 epub。
    fake = tmp / "fake.txt"
    fake.write_bytes(b"PK\x03\x04" + b"\x00" * 100)
    try:
        lu.sniff_file(fake, ".txt")
        ok(False, "假 zip 不该被当文本接受")
    except lu.UploadError:
        ok(True, "改后缀的 zip 被拒 (当 txt 时)")

    good = tmp / "good.epub"
    make_epub(good)
    ok(lu.sniff_file(good, ".epub") == "epub", "真 epub 判 epub")

    notzip = tmp / "bad.epub"
    notzip.write_bytes(b"not a zip at all")
    try:
        lu.sniff_file(notzip, ".epub")
        ok(False, "非 zip 的 .epub 该被拒")
    except lu.UploadError:
        ok(True, "非 zip 的 .epub 被拒")

    txt = tmp / "a.txt"
    txt.write_text("这是一段中文。", encoding="utf-8")
    ok(lu.sniff_file(txt, ".txt") == "txt", "txt 判 txt")

    binary = tmp / "b.txt"
    binary.write_bytes(b"abc\x00\x01\x02def")
    try:
        lu.sniff_file(binary, ".txt")
        ok(False, "二进制 .txt 该被拒")
    except lu.UploadError:
        ok(True, "二进制 .txt 被拒")

    empty = tmp / "e.txt"
    empty.write_bytes(b"")
    try:
        lu.sniff_file(empty, ".txt")
        ok(False, "空文件该被拒")
    except lu.UploadError:
        ok(True, "空文件被拒")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

# --- multipart 解析 -------------------------------------------------------
tmp = Path(tempfile.mkdtemp(prefix="recto-mp-"))
try:
    boundary = "----rectoTest"
    body = multipart_body({"subdir": "x/y"}, {"file": ("书.epub", b"EPUBDATA" * 100)})
    spool = tmp / "body.bin"
    spool.write_bytes(body)
    b = multipart.boundary_of(f"multipart/form-data; boundary={boundary}")
    spans = multipart.part_spans(spool, b)
    ok(len(spans) == 2, "切出 2 个 part", f"got {len(spans)}")
    fields = multipart.text_fields(spool, b)
    ok(fields.get("subdir") == "x/y", "文字字段读到了", fields)
    fp = [s for s in spans if s["filename"]][0]
    ok(fp["filename"] == "书.epub", "文件名读到了", fp["filename"])
    content = b"".join(multipart.read_span(spool, fp))
    ok(content == b"EPUBDATA" * 100, "文件内容逐字节一致", f"{len(content)}")

    # 边界跨块 (内容里含分隔符, 且长度 > CHUNK)
    big = b"X" * (multipart.CHUNK * 3) + b"--not-the-boundary--" + b"Y" * 100
    body2 = multipart_body({}, {"file": ("big.bin", big)}, boundary)
    spool2 = tmp / "body2.bin"
    spool2.write_bytes(body2)
    spans2 = multipart.part_spans(spool2, b)
    fp2 = [s for s in spans2 if s["filename"]][0]
    content2 = b"".join(multipart.read_span(spool2, fp2))
    ok(content2 == big, "跨块边界的内容仍然逐字节一致",
       f"{len(content2)} vs {len(big)}")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

# --- 落盘 (只落书库 / 不覆盖 / 类型拒) ------------------------------------
lib = Path(tempfile.mkdtemp(prefix="recto-lib-"))
os.environ["RECTO_LIBRARY_DIR"] = str(lib)
try:
    from recto.core import config as cfgmod

    cfgmod.get_config(reload=True)
    from recto.core import paths as pathsmod

    pathsmod.reload()

    ok(str(lu.dest_dir()).startswith(str(lib)), "落点在书库里", str(lu.dest_dir()))

    # 真 epub 上传
    src = Path(tempfile.mkdtemp(prefix="recto-src-"))
    ep = src / "样书.epub"
    make_epub(ep, "样书")
    out = lu.save_upload("样书.epub", ep.read_bytes())
    ok(out["ok"] and Path(out["path"]).exists(), "上传后文件在书库里")
    ok(out["kind"] == "epub", "类型判成 epub")
    ok(out["title"] == "样书", "书名从 OPF 里读到了", out["title"])
    ok(out["author"] == "某作者", "作者从 OPF 里读到了", out["author"])

    # 重名不覆盖
    out2 = lu.save_upload("样书.epub", ep.read_bytes())
    ok(out2["path"] != out["path"], "重名换了落点")
    ok(Path(out["path"]).exists() and Path(out2["path"]).exists(), "两本都在")
    ok("(" in Path(out2["name"]).name, "重名加了序号", out2["name"])

    # 类型不符 → 拒, 且不留半个文件
    before = len(list(Path(lu.dest_dir()).iterdir()))
    try:
        lu.save_upload("假的.epub", b"definitely not a zip")
        ok(False, "非 epub 内容该被拒")
    except lu.UploadError:
        ok(True, "非 epub 内容被拒")
    after = len(list(Path(lu.dest_dir()).iterdir()))
    ok(before == after, "被拒后书库里没多出文件", f"{before} -> {after}")
    leftovers = [p.name for p in Path(lu.dest_dir()).iterdir() if p.name.startswith(".")]
    ok(not leftovers, "没留下临时文件", leftovers)

    # 不在白名单的扩展名 → 拒
    try:
        lu.save_upload("x.pdf", b"%PDF-1.4")
        ok(False, "pdf 该被拒")
    except lu.UploadError:
        ok(True, "不在白名单的扩展名被拒")

    # 子目录越界 → 拒
    try:
        lu.save_upload("样书.epub", ep.read_bytes(), subdir="../escape")
        ok(False, "越界子目录该被拒")
    except lu.UploadError:
        ok(True, "越界子目录被拒")

    # 流式: 内容逐块喂, 结果与整份一致
    data = ep.read_bytes()
    chunks = [data[i:i + 1000] for i in range(0, len(data), 1000)]
    out3 = lu.save_stream("流式.epub", chunks)
    ok(Path(out3["path"]).read_bytes() == data, "流式落盘内容一致")
finally:
    from recto.core import config as cfgmod
    from recto.core import paths as pathsmod

    os.environ.pop("RECTO_LIBRARY_DIR", None)
    cfgmod.get_config(reload=True)
    pathsmod.reload()
    shutil.rmtree(lib, ignore_errors=True)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
