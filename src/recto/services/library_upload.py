"""书架上传 —— 把本机文件直接放进书库。

为什么要有
----------
书库原来只有两个入口: 掌阅云下载, 或者用户自己往目录里丢文件。
「自己丢文件」在手机上几乎做不到 (要装文件管理器 + 复制粘贴路径),
在电脑上又得知道书库目录在哪 —— 于是「把书传进自己的书库」这件事
实际上没有 UI 入口。

这一条补上: 书架页一个按钮, 选文件 → 落地书库 → 立刻出现在书架上,
可读、可同步 (掌阅云同步那条路跟着走, 因为云端身份是文件内容 MD5)。

**为什么不用「先传到云端再下回来」绕**: 那要占用掌阅云额度、要网络、
还要云端允许; 本地传本地不需要任何外部依赖。

安全口径
--------
* **只落在书库里** —— 落点必须是配置的书库根 (或其 ``download_subdir``),
  不接受任意路径 (防 "上传即写任意文件")。
* **绝不覆盖已有书** —— 重名自动加序号 (``书 (1).epub``)。
* **类型白名单双确认** —— 扩展名 + **内容嗅探** (改后缀骗不过)。
  epub 必须是能打开的 zip; txt 必须是能解码的文字。
* **大小上限** —— 可配, 默认 200MB, 超了直接拒, 不写半个文件。
* **流式落盘** —— 大书 (epub 几十 MB) 边收边写, 不整份读进内存;
  校验在落盘后的**文件**上做 (只读头尾 + zip 目录), 不是在内存副本上。

**不自动传云端**: 上传只是进书库。要不要传到掌阅云由用户自己在
「上传到掌阅云」那张卡里点 —— 两件事分开, 免得"传一本书"悄悄变成
"把整个书库推上公网"。
"""
from __future__ import annotations

import logging
import os
import re
import unicodedata
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from recto.core import bookfile, paths
from recto.core.config import get_config

log = logging.getLogger("recto.library.upload")

#: 文件名里不能有的字符 (Windows / macOS / Linux 通用的保守集合)
_ILLEGAL = re.compile(r'[\\/:*?"<>|\x00-\x1f]')

#: 保留名 (Windows) —— 撞了就加前缀
_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

#: 嗅探时读多少头
_HEAD = 4096


class UploadError(ValueError):
    """上传被拒 —— 消息直接给人看 (所以要写得像人话)。"""


# --------------------------------------------------------------------------
# 白名单 / 上限
# --------------------------------------------------------------------------

def allowed_exts() -> list[str]:
    """允许的扩展名 —— 取书库扫描用的那一份 (``[library] scan_exts``)。

    不用另设一份: 能扫进书架 = 能读 = 值得收。上传了却读不了的书
    进书架只会让人困惑。
    """
    exts = get_config().get("library", "scan_exts") or [".epub", ".txt"]
    out: list[str] = []
    for e in exts:
        s = str(e).strip().lower()
        if not s:
            continue
        if not s.startswith("."):
            s = "." + s
        if s not in out:
            out.append(s)
    return out or [".epub", ".txt"]


def max_bytes() -> int:
    """单文件上限 (MB)。可配: ``[library] upload_max_mb`` (0 = 不限)。"""
    try:
        mb = int(get_config().get("library", "upload_max_mb") or 200)
    except (TypeError, ValueError):
        mb = 200
    if mb <= 0:
        return 0
    return mb * 1024 * 1024


def dest_dir() -> Path:
    """落点 = 书库根 (或 ``[library] download_subdir``)。

    与云端下载同落点: 两者都是"进书库", 不该有两个地方。
    """
    d = paths.PATHS.download_dir
    d.mkdir(parents=True, exist_ok=True)
    return d


# --------------------------------------------------------------------------
# 文件名
# --------------------------------------------------------------------------

def clean_name(raw: str) -> str:
    """上传来的文件名 → 安全的落盘名 (保留扩展名)。

    * 去掉路径 (只留最后一段) —— 客户端也可能送全路径
    * 去掉控制字符与文件系统非法字符
    * 归一化 unicode (NFC) —— 避免看起来一样的两个文件名
    * 保留原来的扩展名 (类型判定另有一次嗅探, 这里不改名)
    """
    name = str(raw or "").strip()
    name = name.replace("\\", "/").split("/")[-1]
    name = unicodedata.normalize("NFC", name)
    name = _ILLEGAL.sub("_", name).strip()
    name = re.sub(r"\s+", " ", name)
    if not name:
        raise UploadError("没有文件名")
    stem, dot, suf = name.rpartition(".")
    if not dot:
        stem, suf = name, ""
    stem = stem.strip(" .") or "book"
    if stem.upper() in _RESERVED:
        stem = "_" + stem
    out = f"{stem}.{suf}" if suf else stem
    return out[:180]


def unique_path(dest: Path, name: str) -> Path:
    """重名不覆盖 —— 加序号 (``书 (1).epub``、``书 (2).epub``…)。"""
    p = dest / name
    if not p.exists():
        return p
    stem, dot, suf = name.rpartition(".")
    if not dot:
        stem, suf = name, ""
    for i in range(1, 1000):
        cand = dest / (f"{stem} ({i}){('.' + suf) if suf else ''}")
        if not cand.exists():
            return cand
    raise UploadError(f"{dest} 里已有太多同名文件了, 换个名字")


# --------------------------------------------------------------------------
# 类型嗅探 (在**文件**上做, 不在内存副本上)
# --------------------------------------------------------------------------

def sniff_file(path: Path, suffix: str) -> str:
    """扩展名 + 内容双确认 → 真实类型。返回 ``"epub"`` / ``"txt"``。

    **改后缀骗不过**: 名为 .epub 的 zip 判 epub; 名为 .txt 的 zip 判错。
    epub 要求"是能打开的 zip 且里面有 mimetype 或 .opf" —— 不做完整校验
    (有些 epub 写得野), 但 zip 坏了 / 不是 zip 一律拒。
    """
    suf = (suffix or "").lower()
    try:
        size = path.stat().st_size
    except OSError as e:
        raise UploadError(f"读不到刚上传的文件: {e}")
    if not size:
        raise UploadError("文件是空的")
    with path.open("rb") as fh:
        head = fh.read(_HEAD)
    if suf == ".epub" or head[:4] == b"PK\x03\x04":
        if head[:4] != b"PK\x03\x04":
            raise UploadError("这是 epub 文件, 但内容不是有效的压缩包")
        try:
            with zipfile.ZipFile(path) as z:
                names = z.namelist()
                if not names:
                    raise UploadError("这个压缩包是空的")
                low = [n.lower() for n in names]
                if not any(n == "mimetype" or n.endswith(".opf") for n in low):
                    raise UploadError("这个压缩包不像 epub (里面没有 mimetype / .opf)")
        except zipfile.BadZipFile:
            raise UploadError("这不是有效的 epub (压缩包读不了)")
        return "epub"
    if suf in (".txt", ".text", ".md"):
        with path.open("rb") as fh:
            probe = fh.read(65536)
        if b"\x00" in probe:
            raise UploadError("这是文本文件, 但里面有二进制内容 —— 可能是改过扩展名")
        for enc in ("utf-8", "utf-8-sig", "gb18030", "big5"):
            try:
                probe.decode(enc)
                return "txt"
            except UnicodeDecodeError:
                continue
        raise UploadError("这个文本文件的编码认不出来 (试过 utf-8 / gb18030 / big5)")
    raise UploadError(f"不支持的文件类型: {suf or '(无扩展名)'}")


# --------------------------------------------------------------------------
# 落地
# --------------------------------------------------------------------------

def _share_file(path: Path) -> None:
    """服务可能以 root 跑 —— 文件属主跟随父目录, 让 CLI 侧也能读写。"""
    try:
        st, pst = path.stat(), path.parent.stat()
        if os.geteuid() == 0 and (st.st_uid != pst.st_uid or st.st_gid != pst.st_gid):
            os.chown(path, pst.st_uid, pst.st_gid)
        os.chmod(path, 0o644)
    except OSError:
        pass


def _dest_for(name: str, subdir: str) -> tuple[Path, Path]:
    """定落点: 返回 (目录, 目标文件路径)。"""
    dest = dest_dir()
    if subdir:
        sub = Path(str(subdir)).expanduser()
        if sub.is_absolute() or ".." in sub.parts:
            raise UploadError("子目录只能是书库里的相对路径")
        dest = dest / sub
        dest.mkdir(parents=True, exist_ok=True)
    return dest, unique_path(dest, name)


def save_stream(filename: str, chunks: Iterable[bytes], *,
                subdir: str = "") -> dict[str, Any]:
    """把一份上传的文件**流式**落进书库。

    ``chunks`` 是字节块的可迭代 (socket 读出来的那种)。边收边写临时文件,
    收完再校验 —— 校验不通过就把临时文件删掉, 书库里不留半个坏文件。

    返回 ``{"ok":True, "path":…, "name":…, "title":…, "author":…,
    "bytes":…, "kind":…}``; 失败抛 :class:`UploadError` (消息给人看)。
    """
    name = clean_name(filename)
    suf = Path(name).suffix.lower()
    allowed = allowed_exts()
    if suf not in allowed:
        raise UploadError(
            f"只收这些格式: {' / '.join(allowed)} (这个是 {suf or '无扩展名'})")

    limit = max_bytes()
    dest, target = _dest_for(name, subdir)
    tmp = target.with_name(f".{target.name}.{os.getpid()}.upload")
    total = 0
    try:
        with tmp.open("wb") as fh:
            for chunk in chunks:
                if not chunk:
                    continue
                total += len(chunk)
                if limit and total > limit:
                    raise UploadError(
                        f"文件太大, 上限 {limit // 1024 // 1024}MB")
                fh.write(chunk)
            fh.flush()
            os.fsync(fh.fileno())
        if not total:
            raise UploadError("文件是空的")
        kind = sniff_file(tmp, suf)
        os.replace(tmp, target)
    except UploadError:
        _unlink(tmp)
        raise
    except OSError as e:
        _unlink(tmp)
        raise UploadError(f"写不进书库: {e}")
    _share_file(target)

    meta = bookfile.read_meta(target)
    return {
        "ok": True,
        "path": str(target),
        "name": target.name,
        "title": meta.get("title") or target.stem,
        "author": meta.get("author") or "",
        "bytes": total,
        "kind": kind,
        "at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


def _unlink(p: Path) -> None:
    try:
        p.unlink()
    except OSError:
        pass


def save_upload(filename: str, data: bytes, *, subdir: str = "") -> dict[str, Any]:
    """整份上传 (小文件 / CLI / 测试用)。"""
    return save_stream(filename, [data], subdir=subdir)


def ingest_uploaded(path: str | Path) -> dict[str, Any]:
    """落盘之后补边车 + 重扫书库, 让这本书立刻出现在书架上。

    边车只写"来源"标记 (不是云端身份 —— 那是「接入同步」那一步的事)。
    """
    p = Path(path)
    meta = bookfile.read_meta(p)
    try:
        from recto.core import sidecar

        sidecar.write(p, {"book": {
            "title": meta.get("title") or p.stem,
            "author": meta.get("author") or "",
            "source": "local-upload",
            "added_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        }})
    except Exception as e:  # noqa: BLE001 — 边车写不进去不该挡住书出现
        log.warning("上传 · 边车写失败: %s", e)
    try:
        from recto.services import reader_state

        reader_state.scan(force=True)
    except Exception as e:  # noqa: BLE001
        log.warning("上传 · 重扫书库失败: %s", e)
    return {"key": bookfile.book_key(p), "title": meta.get("title") or p.stem}


def cleanup(path: str | Path) -> None:
    """上传失败时把半截文件清掉。"""
    _unlink(Path(path))


def copy_in(src: str | Path, *, name: str | None = None) -> dict[str, Any]:
    """把一个**已经在本机**的文件收进书库 (CLI / 测试用)。

    走的是同一套校验 (类型双确认 / 不覆盖 / 只落书库)。
    """
    s = Path(src).expanduser()
    if not s.exists():
        raise UploadError(f"文件不存在: {s}")
    if not s.is_file():
        raise UploadError(f"那不是一个文件: {s}")
    return save_upload(name or s.name, [s.read_bytes()])
