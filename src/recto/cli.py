"""recto 命令行 —— 统一入口。

云阅 Recto 是独立服务: 命令只碰自己的书库与状态, 不依赖任何外部书目库。

    push / pull / status        进度双向同步 (掌阅 ⇄ 内置阅读器)
    notes   pull/push           划线 / 书签
    time    pull/push/...       阅读时长
    library scan|list           书库扫描 / 列表
    link / links                云端映射 (写书库边车 JSON)
    acquire list|download|...   掌阅个人云书籍下载
    serve                       启动 Web 服务 (:5015)
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from recto.core.config import get_config


def _dump(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=1, default=str))


# ---------------------------------------------------------------- 基础命令
def cmd_version(_args) -> int:
    from recto import __version__

    print(f"recto {__version__}")
    return 0


def cmd_config(_args) -> int:
    _dump(get_config(reload=True).values)
    return 0


def cmd_paths(_args) -> int:
    cfg = get_config(reload=True)
    items = {
        "data_dir": str(cfg.data_dir),
        "library": str(cfg.books_dir),
        "download_dir": str(cfg.cloud_dl_dir),
        "state_dir": str(cfg.cloud_state_dir),
        "cloud_auth": str(cfg.cloud_auth_file),
        "corpus_dir": str(cfg.corpus_dir),
        "config_file": str(cfg.path) if cfg.path else None,
    }
    for k, v in items.items():
        exists = "  [存在]" if v and Path(v).exists() else ""
        print(f"{k:14s} {v}{exists}")
    return 0


def cmd_selftest(_args) -> int:
    from recto.services import reader_state, sync

    cfg = get_config(reload=True)
    print("== 云阅 Recto 自检 ==")
    ok = True
    for label, val in (("数据目录", cfg.data_dir), ("本地书库", cfg.books_dir),
                       ("下载目录", cfg.cloud_dl_dir), ("状态目录", cfg.cloud_state_dir),
                       ("云端凭据", cfg.cloud_auth_file), ("语料目录", cfg.corpus_dir)):
        exists = Path(val).exists()
        ok &= exists
        print(f"{label:10s} {val}  {'✓' if exists else '✗ 不存在'}")
    try:
        n = len(reader_state.list_books())
        print(f"{'书目':10s} {n} 本")
    except Exception as e:  # noqa: BLE001
        ok = False
        print(f"{'书目':10s} ✗ {e}")
    try:
        st = sync.status()
        print(f"{'云端映射':10s} {st['link_count']} 本")
    except Exception as e:  # noqa: BLE001
        print(f"{'云端映射':10s} ! {e}")
    print(f"{'监听端口':10s} {cfg.get('recto', 'port')}")
    return 0 if ok else 1


# ---------------------------------------------------------------- 进度
def cmd_push(args) -> int:
    from recto.services import sync as core

    out = core.push_once(dry_run=args.dry_run,
                         keys=args.book or None,
                         allow_rewind=args.allow_rewind)
    _dump(out)
    return 0 if not args.strict or out.get("failed", 0) == 0 else 1


def cmd_pull(args) -> int:
    from recto.services import sync as core

    out = core.pull_once(dry_run=args.dry_run,
                         keys=args.book or None,
                         only_forward=not args.overwrite)
    _dump(out)
    return 0 if not args.strict or not out.get("detail") else 0


def cmd_align(args) -> int:
    from recto.services import sync as core

    if args.fix:
        out = core.repair_positions(keys=args.book or None, dry_run=args.dry_run)
    else:
        out = core.audit_alignment(keys=args.book or None)
    _dump(out)
    return 0 if not args.strict or not out.get("bad") else 1


def cmd_status(_args) -> int:
    from recto.services import sync as core

    _dump(core.status())
    return 0


# ---------------------------------------------------------------- 映射
def cmd_link(args) -> int:
    from recto.core import sidecar
    from recto.services import sync as core

    book = Path(args.book).expanduser()
    if not book.exists():
        raise SystemExit(f"书不存在: {book}")
    fields = {}
    if args.cloud_id:
        fields["id64"] = args.cloud_id
        fields["id32"] = (args.cloud_id[:32].upper()
                          if len(args.cloud_id) >= 32 else core.file_md5(book))
    if args.cloud_name:
        fields["name"] = args.cloud_name
    if args.id32:
        fields["id32"] = args.id32.upper()
    if not fields:
        raise SystemExit("至少给 --cloud-id / --cloud-name / --id32")
    print(json.dumps(sidecar.set_cloud(book, **fields), ensure_ascii=False, indent=1))
    return 0


def cmd_links(_args) -> int:
    from recto.services import sync as core

    _dump(core.links(refresh=True))
    return 0


def cmd_sync_links(_args) -> int:
    from recto.services import sync as core

    _dump(core.ensure_links())
    return 0


# ---------------------------------------------------------------- 书库
def cmd_library(args) -> int:
    from recto.services import reader_state

    if args.action == "scan":
        books = reader_state.scan(force=args.force)
        print(f"{len(books)} 本")
        return 0
    for b in reader_state.list_books(args.q or ""):
        pct = (b.get("p_percent") or 0) * 100
        print(f"{b['key'][:8]}  {pct:5.1f}%  {b['title']}  ({b['path']})")
    return 0


def cmd_acquire(args) -> int:
    from recto.services import acquisition

    if args.action == "list":
        for b in acquisition.shelf_books():
            print(f"{b['id'][:12]}  {b['size'] / 1048576:8.1f}MB  {b['name']}")
        return 0
    if args.action == "download":
        jid = acquisition.start_download(args.key, name=args.name)
        _dump(acquisition.job_status(jid))
        return 0
    if args.action == "jobs":
        _dump(acquisition.jobs_list())
        return 0
    if args.action == "local":
        _dump(acquisition.local_files())
        return 0
    raise SystemExit(f"未知动作: {args.action}")


# ---------------------------------------------------------------- 服务
def cmd_passwd(args) -> int:
    """重置 Web 登录口令 (服务器端, 不验旧口令)."""
    from recto.app import webauth

    new = (args.new or "").strip()
    if not new:
        import getpass

        new = getpass.getpass("新口令 (至少 6 位): ").strip()
        again = getpass.getpass("再输一次: ").strip()
        if new != again:
            print("两次输入不一致")
            return 2
    ok, err = webauth.set_password(new, who="cli")
    if not ok:
        print(f"改不了口令: {err}")
        return 1
    print(f"口令已更新 (来源: {webauth.password_source()})。"
          f"登录名 {webauth.login_user()}, 生效文件 {webauth.password_file()}")
    return 0


def cmd_serve(args) -> int:
    from recto.app import server

    if args.host or args.port:
        server.HOST = args.host or server.HOST
        server.PORT = args.port or server.PORT
    server.main()
    return 0


def cmd_daemon(args) -> int:
    from recto.services import sync as core

    core.daemon(poll_seconds=args.interval)
    return 0


# ---------------------------------------------------------------- 划线 / 书签
def cmd_notes(args) -> int:
    from recto.services import notes

    keys = args.book or None
    if args.action == "pull":
        out = notes.pull(keys, dry_run=args.dry_run)
    elif args.action == "push":
        out = notes.push(keys, dry_run=args.dry_run)
    else:
        out = {"pull": notes.pull(keys, dry_run=args.dry_run),
               "push": notes.push(keys, dry_run=args.dry_run)}
    _dump(out)
    return 0 if not out.get("errors") else 1


# ---------------------------------------------------------------- 阅读时长
def cmd_time(args) -> int:
    from datetime import date, timedelta

    from recto.services import sessions

    today = date.today()
    day = date.fromisoformat(args.day) if args.day else today
    start = date.fromisoformat(args.start) if args.start else today - timedelta(days=6)
    end = date.fromisoformat(args.end) if args.end else today
    keys = args.book or None
    if args.action == "push":
        out = sessions.push(day, dry_run=args.dry_run, keys=keys)
    elif args.action == "backfill":
        out = sessions.backfill(start, end, dry_run=args.dry_run, keys=keys)
    elif args.action == "flush":
        out = sessions.flush(dry_run=args.dry_run)
    elif args.action == "queue":
        out = {"pending": sessions.queue_info(keys)}
    elif args.action == "pull":
        out = sessions.pull(start, end, keys=keys)
    else:
        out = {"pull": sessions.pull(start, end, keys=keys),
               "push": sessions.push(day, dry_run=args.dry_run, keys=keys)}
    _dump(out)
    return 0 if not out.get("errors") else 1


# ---------------------------------------------------------------- 入口
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="recto", description="云阅 Recto —— 阅读留痕一条线")
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("version", help="打印版本").set_defaults(func=cmd_version)
    sub.add_parser("config", help="打印生效配置").set_defaults(func=cmd_config)
    sub.add_parser("paths", help="打印关键路径").set_defaults(func=cmd_paths)
    sub.add_parser("selftest", help="自检").set_defaults(func=cmd_selftest)
    p_pw = sub.add_parser("passwd", help="重置 Web 登录口令 (服务器端, 不验旧口令)")
    p_pw.add_argument("new", nargs="?", default="", help="新口令; 省略则交互输入")
    p_pw.set_defaults(func=cmd_passwd)

    p_push = sub.add_parser("push", help="进度: 阅读器 -> 云端")
    p_push.add_argument("--dry-run", action="store_true")
    p_push.add_argument("--strict", action="store_true")
    p_push.add_argument("--allow-rewind", action="store_true", help="允许把云端进度往回拨")
    p_push.add_argument("--book", action="append", default=[], help="只处理这本书 (book key)")
    p_push.set_defaults(func=cmd_push)

    p_pull = sub.add_parser("pull", help="进度: 云端 -> 阅读器")
    p_pull.add_argument("--dry-run", action="store_true")
    p_pull.add_argument("--strict", action="store_true")
    p_pull.add_argument("--overwrite", action="store_true",
                        help="本地更靠前时也覆盖 (默认只前进)")
    p_pull.add_argument("--book", action="append", default=[], help="只处理这本书 (book key)")
    p_pull.set_defaults(func=cmd_pull)

    p_al = sub.add_parser("align", help="核对两侧「位置 <-> 百分比」是否自洽 (-f 修云端位置)")
    p_al.add_argument("-b", "--book", action="append", help="只查这几本书 (书 key)")
    p_al.add_argument("-f", "--fix", action="store_true", help="按云端自己的百分比重写 readPosition")
    p_al.add_argument("--dry-run", action="store_true")
    p_al.add_argument("--strict", action="store_true", help="有不一致时退出码 1")
    p_al.set_defaults(func=cmd_align)

    sub.add_parser("status", help="查每本书两侧进度").set_defaults(func=cmd_status)

    p_lk = sub.add_parser("link", help="给一本书写云端映射 (书库边车 JSON)")
    p_lk.add_argument("--book", required=True, help="书库里的原书路径")
    p_lk.add_argument("--cloud-id", default="", help="云端 book_id (64 hex)")
    p_lk.add_argument("--id32", default="", help="文件内容 MD5 (32 hex, 大写)")
    p_lk.add_argument("--cloud-name", default="", help="云端书名")
    p_lk.set_defaults(func=cmd_link)

    sub.add_parser("links", help="列云端映射").set_defaults(func=cmd_links)
    sub.add_parser("sync-links", help="按书名把书库里的书对上云端书架").set_defaults(func=cmd_sync_links)

    p_lib = sub.add_parser("library", help="书库")
    p_lib.add_argument("action", choices=("scan", "list"))
    p_lib.add_argument("--force", action="store_true")
    p_lib.add_argument("--q", default="")
    p_lib.set_defaults(func=cmd_library)

    p_acq = sub.add_parser("acquire", help="掌阅个人云书籍")
    p_acq.add_argument("action", choices=("list", "download", "jobs", "local"))
    p_acq.add_argument("key", nargs="?", default="", help="shelf:<id>")
    p_acq.add_argument("--name", default=None)
    p_acq.set_defaults(func=cmd_acquire)

    p_serve = sub.add_parser("serve", help="启动 Web 服务 (默认 :5015)")
    p_serve.add_argument("--host", default=None)
    p_serve.add_argument("--port", type=int, default=None)
    p_serve.set_defaults(func=cmd_serve)

    p_daemon = sub.add_parser("daemon", help="轮询拉云端进度")
    p_daemon.add_argument("--interval", type=int, default=60)
    p_daemon.set_defaults(func=cmd_daemon)

    p_nt = sub.add_parser("notes", help="划线/书签 双向同步")
    p_nt.add_argument("action", choices=("sync", "pull", "push"))
    p_nt.add_argument("--book", action="append", default=[], help="只处理这本书")
    p_nt.add_argument("--dry-run", action="store_true")
    p_nt.set_defaults(func=cmd_notes)

    p_tm = sub.add_parser("time", help="阅读时长 双向同步")
    p_tm.add_argument("action", choices=("sync", "pull", "push", "backfill", "flush", "queue"))
    p_tm.add_argument("--day", default=None, help="push 用: YYYY-MM-DD (默认今天)")
    p_tm.add_argument("--start", default=None, help="pull 用: 起始日")
    p_tm.add_argument("--end", default=None, help="pull 用: 结束日")
    p_tm.add_argument("--book", action="append", default=[], help="只处理这本书")
    p_tm.add_argument("--dry-run", action="store_true")
    p_tm.set_defaults(func=cmd_time)

    return p


def main(argv=None) -> int:
    p = build_parser()
    args = p.parse_args(argv)
    if not getattr(args, "func", None):
        p.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
