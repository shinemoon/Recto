"""口令落盘 —— 原子写 + 目录属主。

为什么单独一个模块:
`webauth.py` 里原来有两处 `f.write_text(...)` 直写口令文件。那是**非原子**的:
先 truncate 再写, 写窗口里读者 (另一个进程的 verify) 会读到空串或半截,
于是「我明明改了口令, 登录却说口令不对」。

另外口令文件常由 root 写 (面板跑在 root), 目录却属普通用户 —— 写完要交回去,
不然普通用户进程改不了。
"""
from __future__ import annotations

import os
from pathlib import Path


def own(path: Path) -> None:
    """root 写的文件交回目录属主 (目录常由非 root 的应用进程持有)。"""
    try:
        if os.geteuid() != 0:
            return
        st = path.parent.stat()
        os.chown(path, st.st_uid, st.st_gid)
    except Exception:  # noqa: BLE001 — 拿不到就算了, 只是会挡住非 root 的写
        pass


def write_secret(path: Path, text: str, *, mode: int = 0o600) -> bool:
    """原子写一个机密文件 → (成功?, 说明)

    步骤: 同目录临时文件 → chmod → 落盘 → fsync → os.replace 原子替换。
    `os.replace` 在同一文件系统内是原子的, 读者永远看到「旧的完整内容」或
    「新的完整内容」, 不会读到半截。
    """
    p = Path(path)
    tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        # 临时文件先 chmod 再写: 免得有一瞬间是 0644 (口令不该被别的用户读到)
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
        try:
            os.write(fd, text.encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        try:
            os.chmod(tmp, mode)
        except OSError:
            pass
        own(tmp)
        os.replace(tmp, p)
        try:
            os.chmod(p, mode)
        except OSError:
            pass
        own(p)
        # 目录项也刷一下, 掉电后 rename 才不会丢
        try:
            dfd = os.open(str(p.parent), os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        except OSError:
            pass
        return True, ""
    except OSError as e:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False, str(e)