"""阅读时长快照 / 还原 —— 给"要在阅读器里待很久"的探针收尾用。

    python3 tools/rtime_snapshot.py save    --book <bookKey> [--db <path>]
    python3 tools/rtime_snapshot.py restore --book <bookKey> [--db <path>]
    python3 tools/rtime_snapshot.py clear

为什么需要: 只要阅读器页面在前台待着, 前端每 30 秒就发一次 `/session {action:'beat'}`,
服务端按分钟位图记时长 (rtime) 并追加会话行 (rsession)。一个跑 4 分钟的探针
会给那本书凭空加 4 分钟 —— 而阅读时长会流进"书架统计 / 陪读成长 / 导出到纸间"。

还原口径:
  * `rtime` 的 (书, 今天) 行: 有快照就写回原值 (bitmap/minutes/cloud), 原先没有就删掉;
  * `rsession`: 删掉快照之后新增的、属于这本书的行 (按 id > max_id);
  * 不碰其它书、不碰进度/划线。
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SNAP = Path(os.environ.get("RECTO_TIME_SNAP") or "/tmp/recto_rtime_snapshot.json")


def db_path(explicit: str | None = None) -> str:
    if explicit:
        return explicit
    env = os.environ.get("RECTO_DB")
    if env:
        return env
    data = os.environ.get("RECTO_DATA_DIR")
    if data:
        return str(Path(data) / "reader.sqlite")
    return str(ROOT / "data" / "reader.sqlite")


def _conn(path: str) -> sqlite3.Connection:
    c = sqlite3.connect(path, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def cmd_save(a) -> int:
    day = a.day or date.today().isoformat()
    c = _conn(db_path(a.db))
    row = c.execute("SELECT book, day, bitmap, minutes, cloud_minutes FROM rtime "
                    "WHERE book=? AND day=?", (a.book, day)).fetchone()
    max_id = c.execute("SELECT COALESCE(MAX(id),0) m FROM rsession").fetchone()["m"]
    snap = {"book": a.book, "day": day, "rtime": dict(row) if row else None,
            "rsession_max_id": int(max_id), "saved_at": time.time(),
            "db": db_path(a.db)}
    SNAP.write_text(json.dumps(snap, ensure_ascii=False))
    print(f"saved: {SNAP} book={a.book} day={day} "
          f"row={'none' if row is None else row['minutes']} rsession_max_id={max_id}")
    return 0


def cmd_restore(a) -> int:
    if not SNAP.is_file():
        print(f"没有快照: {SNAP}", file=sys.stderr)
        return 2
    snap = json.loads(SNAP.read_text())
    book = a.book or snap.get("book")
    day = a.day or snap.get("day") or date.today().isoformat()
    path = db_path(a.db)
    c = _conn(path)
    removed_sessions = 0
    if a.sessions:
        removed_sessions = c.execute(
            "DELETE FROM rsession WHERE book=? AND id>?", (book, int(snap["rsession_max_id"]))
        ).rowcount
    row = snap.get("rtime")
    if row:
        c.execute("""INSERT INTO rtime(book,day,bitmap,minutes,first_at,updated_at,
                                       cloud_minutes,cloud_at)
                     VALUES(?,?,?,?,?,?,?,?)
                     ON CONFLICT(book,day) DO UPDATE SET bitmap=excluded.bitmap,
                       minutes=excluded.minutes, cloud_minutes=excluded.cloud_minutes""",
                  (row["book"], row["day"], row["bitmap"], row["minutes"],
                   int(time.time()), int(time.time()),
                   int(row.get("cloud_minutes") or 0), int(time.time())))
        action = f"恢复为 {row['minutes']} 分钟"
    else:
        c.execute("DELETE FROM rtime WHERE book=? AND day=?", (book, day))
        action = "原本没有 → 删除该行 (0 分钟)"
    c.commit()
    print(f"restored: book={book} day={day} {action}; 删除新增会话 {removed_sessions} 条")
    return 0


def cmd_clear(a) -> int:
    if SNAP.is_file():
        SNAP.unlink()
        print(f"cleared: {SNAP}")
    else:
        print("没有快照")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="阅读时长快照/还原 (探针卫生)")
    p.add_argument("cmd", choices=["save", "restore", "clear"])
    p.add_argument("--book", default="")
    p.add_argument("--day", default="")
    p.add_argument("--db", default="")
    p.add_argument("--keep-sessions", dest="sessions", action="store_false",
                   help="只还原分钟, 保留 rsession 行 (默认一起删)")
    p.set_defaults(sessions=True)
    a = p.parse_args(argv)
    if a.cmd in ("save", "restore") and not a.book:
        print("需要 --book <bookKey> (restore 可省略, 用快照里的)", file=sys.stderr)
        return 2
    return {"save": cmd_save, "restore": cmd_restore, "clear": cmd_clear}[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
