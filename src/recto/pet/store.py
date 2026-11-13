"""书宠自己的库 —— 书籍知识 / 语料遭遇 / 游历回合 (pet.sqlite)。

**为什么单独一个 sqlite 而不是塞进 reader.sqlite**:
`reader.sqlite` 是"人读过的痕迹"(进度 / 划线 / 时长), 属 `services` 数据层;
这里存的是**书宠自己攒下来的东西**(它对一本书的理解、它遇到过哪些词、
它走过哪些路)。书宠被重置 (::func:`recto.pet.charsheet.reset_char`) 时
这些也不该跟着消失 —— 一个角色换了, 江湖还在。

表的四组:

  ``lore_book``     一本书的"底细" (主题 / 世界设定 / 一句话概要)
  ``lore_chapter``  章节级的"典故储备" (读到哪章, 那章讲了什么)
  ``chapter_seen``  人打开过哪些章 (给知识储备排队, 顺带留一小段原文做摘要依据)
  ``encounter``     语料遭遇计数 (书 × 天 × 词 → 次数) —— 书宠联想的燃料
  ``cycle``         每次后台推演的留痕 (证据 / 判定 / 成败)
  ``adventure``     书宠的游历故事 (一回一条)

时间口径与全站一致: 一律**本地时间**字符串 (`YYYY-MM-DD` / ISO 本地),
由 :mod:`recto.core.clock` 提供 —— 时区换算只在展示层做。
"""
from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS lore_book (
    book        TEXT PRIMARY KEY,
    title       TEXT DEFAULT '',
    author      TEXT DEFAULT '',
    identity    TEXT DEFAULT '',     -- 这本书在书宠眼里"是什么" (一句定位)
    themes      TEXT DEFAULT '[]',   -- JSON 数组: 主题词
    setting     TEXT DEFAULT '',     -- 世界设定 / 时代 / 地点
    summary     TEXT DEFAULT '',     -- 概要 (不剧透结局)
    seeds       TEXT DEFAULT '{}',    -- JSON: 去专名的"种子" (母题/意象/气韵/质地)
    source      TEXT DEFAULT '',     -- search / model / manual
    searched    TEXT DEFAULT '',     -- 网络检索状态: ok / off / empty
    confidence  REAL DEFAULT 0,
    fetched_at  TEXT DEFAULT '',
    updated_at  TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS lore_chapter (
    book        TEXT NOT NULL,
    chapter     INTEGER NOT NULL,
    title       TEXT DEFAULT '',
    gist        TEXT DEFAULT '',     -- 这章讲了什么 (2-3 句)
    motifs      TEXT DEFAULT '[]',   -- JSON 数组: 意象 / 典故
    fetched_at  TEXT DEFAULT '',
    PRIMARY KEY (book, chapter)
);

CREATE TABLE IF NOT EXISTS chapter_seen (
    book        TEXT NOT NULL,
    chapter     INTEGER NOT NULL,
    title       TEXT DEFAULT '',
    chars       INTEGER DEFAULT 0,
    excerpt     TEXT DEFAULT '',     -- 开头一小段 (给摘要当依据, 不存全文)
    first_at    TEXT DEFAULT '',
    last_at     TEXT DEFAULT '',
    PRIMARY KEY (book, chapter)
);

CREATE TABLE IF NOT EXISTS encounter (
    book        TEXT NOT NULL,
    day         TEXT NOT NULL,       -- 本地日期
    term        TEXT NOT NULL,
    cat         TEXT DEFAULT '',
    count       INTEGER NOT NULL DEFAULT 0,
    first_at    TEXT DEFAULT '',
    last_at     TEXT DEFAULT '',
    PRIMARY KEY (book, day, term)
);

CREATE TABLE IF NOT EXISTS cycle (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    at          TEXT NOT NULL,
    from_at     TEXT DEFAULT '',
    to_at       TEXT DEFAULT '',
    trigger     TEXT DEFAULT 'tick', -- tick / manual
    status      TEXT DEFAULT 'ok',   -- ok / idle / dry / error
    minutes     INTEGER DEFAULT 0,
    evidence    TEXT DEFAULT '{}',
    judgement   TEXT DEFAULT '{}',
    error       TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS adventure (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle       INTEGER DEFAULT 0,
    at          TEXT DEFAULT '',
    no          INTEGER DEFAULT 0,   -- 第几回
    title       TEXT DEFAULT '',
    place       TEXT DEFAULT '',
    body        TEXT DEFAULT '',
    themes      TEXT DEFAULT '[]',
    terms       TEXT DEFAULT '[]',   -- 用到的语料词
    growth      TEXT DEFAULT '{}',
    created_at  TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS ix_encounter_day ON encounter(day);
CREATE INDEX IF NOT EXISTS ix_encounter_book ON encounter(book);
CREATE INDEX IF NOT EXISTS ix_adventure_no ON adventure(no);
"""

#: 后加的列 (老库靠 ALTER 补齐, 不重建表)。加列必须同时改这里的清单与
#: 测试里手工建的 DDL —— 漏一处就会让读取报 "no such column"。
ALTERS: list[tuple[str, str, str]] = [
    # 2026-11-13: 故事要"自我架空", 书只提供去专名的种子
    ("lore_book", "seeds", "TEXT DEFAULT '{}'"),
    # 2026-11-13: 这一回用到/新造的世界元素 (给了哪几处、新记了哪些)
    ("adventure", "world", "TEXT DEFAULT '{}'"),
]


# ------------------------------------------------------------------ 路径

def pet_dir() -> Path:
    """书宠数据根 (与 config / char.json 同一处)。"""
    try:
        from recto.core import paths as _paths

        return Path(_paths.PATHS.pet_dir)
    except Exception:  # noqa: BLE001 — 单测没有 Recto 配置也要能用
        env = os.environ.get("RECTO_PET_DIR")
        if env:
            return Path(env)
        return Path(__file__).resolve().parents[2] / "data" / "pet"


def db_path() -> Path:
    return pet_dir() / "pet.sqlite"


# ------------------------------------------------------------------ 连接

def conn() -> sqlite3.Connection:
    """取连接 (每次调用新建, WAL + 忙等 5s)。

    书宠的写入频率很低 (每回合一次 / 每章一次), 不值得维护连接池;
    用新连接换"绝不跨线程复用"的简单性。
    """
    p = Path(db_path())
    p.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(p), timeout=5.0)
    c.row_factory = sqlite3.Row
    try:
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
    except sqlite3.Error:
        pass
    with _LOCK:
        c.executescript(SCHEMA)
        _migrate(c)
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    return c


def _migrate(c: sqlite3.Connection) -> None:
    """老库补列 (幂等)。``ALTER TABLE`` 加列失败 (已存在) 一律忽略。"""
    for table, col, decl in ALTERS:
        try:
            cols = {r[1] for r in c.execute(f"PRAGMA table_info({table})").fetchall()}
        except sqlite3.Error:
            continue
        if col in cols:
            continue
        try:
            c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
            c.commit()
        except sqlite3.Error:
            pass


# ------------------------------------------------------------------ 工具

def rows(c: sqlite3.Connection, sql: str, args: tuple = ()) -> list[dict[str, Any]]:
    return [dict(r) for r in c.execute(sql, args).fetchall()]


def one(c: sqlite3.Connection, sql: str, args: tuple = ()) -> dict[str, Any] | None:
    r = c.execute(sql, args).fetchone()
    return dict(r) if r else None


def reset() -> None:
    """清空书宠的库 (只在测试 / 手工重置时用)。"""
    try:
        Path(db_path()).unlink()
    except OSError:
        pass
