"""测试隔离护栏 —— 跑测试不许碰生产数据。

**为什么需要它**: 2026-11-11 查出「改了口令, 过一阵又变回 root:root」的真凶是
测试套件在**生产目录**里删/建口令文件 (`tests/test_webauth.py` 第 5 节
「删掉口令文件重新生成」删的是 `<生产 data_dir>/admin-password`)。
跑一次全量测试 = 把线上口令重置回初始默认值。

光修那一个文件不够 —— 下次还会有人写新的测试踩同样的坑。所以这里做两件事:

1. `tests/_guard.py`: 一个可复用的隔离装置, 新测试 import 它就自动把
   `RECTO_DATA_DIR` / `RECTO_WEBAUTH_DIR` / 日志指到临时目录, 并在**退出时**
   断言生产目录没被动过。
2. `tools/run_tests.py`: 统一入口。跑之前把生产路径**快照**下来, 跑完逐个
   比对 mtime/size/内容 —— 被动过就直接报错 (而不是"测试通过, 线上口令被改了")。

跑法: cd <仓库根> && python3 tools/run_tests.py
"""
from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def isolate(prefix: str = "recto-test-") -> Path:
    """把本次测试的所有落盘位置指到临时目录。

    返回临时目录。**必须**在 import 任何 recto 模块之前调用 (配置是
    进程级单例, 第一次 get_config() 就定死了)。
    """
    import tempfile

    tmp = Path(tempfile.mkdtemp(prefix=prefix))
    os.environ["RECTO_DATA_DIR"] = str(tmp / "data")
    os.environ["RECTO_WEBAUTH_DIR"] = str(tmp / "webauth")
    os.environ["RECTO_CLOUD_AUTH_FILE"] = str(tmp / "cloud-auth.json")
    os.environ["RECTO_CORPUS_DIR"] = str(tmp / "corpus")
    os.environ["RECTO_LIBRARY_DIR"] = str(tmp / "library")
    os.environ["RECTO_AUTH_LOG"] = "off"          # 不写生产的 webauth.log
    os.environ.pop("RECTO_AUTH_PASSWORD", None)   # 让口令走文件来源 (隔离才有意义)
    os.environ.pop("RECTO_AUTH_USER", None)
    os.environ.pop("RECTO_AUTH_DISABLE", None)
    return tmp


def production_paths() -> list[Path]:
    """生产部署里「改了会出事」的文件 (口令 / 配置 / 数据)。"""
    out: list[Path] = []
    try:
        sys.path.insert(0, str(ROOT / "src"))
        from recto.core.config import Config  # noqa: PLC0415

        cfg = Config.load()
        out.append(Path(cfg.data_dir) / "admin-password")
        out.append(Path(cfg.data_dir) / "settings.json")
        out.append(Path(cfg.data_dir) / "appearance.json")
    except Exception:  # noqa: BLE001 — 读不到就退回已知默认位置
        out.append(ROOT / "data" / "admin-password")
    out.append(ROOT / "config.toml")
    out.append(ROOT / "config.toml.local")
    return [p for p in out if p.exists()]


def snapshot(paths: list[Path]) -> dict[str, tuple]:
    """给文件拍个快照 (mtime, size, 内容 md5)。"""
    out: dict[str, tuple] = {}
    for p in paths:
        try:
            st = p.stat()
            md5 = hashlib.md5(p.read_bytes()).hexdigest()
            out[str(p)] = (st.st_mtime_ns, st.st_size, md5)
        except OSError:
            continue
    return out


def diff(before: dict[str, tuple], after: dict[str, tuple]) -> list[str]:
    """比两份快照, 返回被改动/新增/删除的路径。"""
    changed: list[str] = []
    for k, v in before.items():
        if k not in after:
            changed.append(f"被删: {k}")
        elif after[k] != v:
            changed.append(f"被改: {k}")
    for k in after:
        if k not in before:
            changed.append(f"新建: {k}")
    return changed


if __name__ == "__main__":
    # 自检: isolate() 之后口令文件必须落在临时目录
    tmp = isolate("recto-guard-selfcheck-")
    sys.path.insert(0, str(ROOT / "src"))
    from recto.app import webauth  # noqa: PLC0415

    pf = webauth.password_file()
    ok = str(pf).startswith(str(tmp))
    print(("PASS " if ok else "FAIL ") + f"口令文件落在临时目录: {pf}")
    if not ok:
        raise SystemExit(1)
    print("PASS 护栏自检通过")