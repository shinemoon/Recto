"""统一数据模型 —— Recto 的地基。

四套系统 (云端阅读服务 / 本地书库 / 墨水屏设备 / 纸间) 说的其实是同四件事,
只是坐标不同。Recto 内部统一用**原书字节坐标**。

见 docs/PLAN.md §2.3。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Literal

# --- 留痕种类 ---------------------------------------------------------------
Kind = Literal["excerpt", "highlight", "bookmark", "session"]
# --- 留痕来源 ---------------------------------------------------------------
Origin = Literal["cloud", "library", "device", "zhijian"]


@dataclass(frozen=True)
class Anchor:
    """跨系统位置锚点 —— 全系统的核心。

    云端阅读服务用的是字节坐标, 本地书库用的是「页 + xpath」,
    纸间用的是「书内百分比」。Recto 内部统一存**原书字节坐标**,
    对外再按目标系统换算。

    字节口径跟云端 readPercent 同源 (readPercent = 字节位置 / 全书字节)。
    """

    byte_start: int
    """原书全局字节偏移 (含)"""

    byte_end: int
    """原书全局字节偏移 (闭区间) —— 与云端 positionend 口径一致"""

    chapter_id: int = 0
    """spine 下标。注意: 位置落在 spine[chapter_id + 1], 因为 spine[0] 是封面。"""

    chapter_label: str = ""
    """目录里的「卷名 章名」。取不到时留空, 不要拿文件名凑。"""

    @property
    def length(self) -> int:
        return max(0, self.byte_end - self.byte_start)

    @property
    def is_empty(self) -> bool:
        return self.byte_end <= self.byte_start

    def __contains__(self, other: "Anchor") -> bool:
        return (self.byte_start <= other.byte_start
                and other.byte_end <= self.byte_end)


@dataclass
class Trace:
    """一条「阅读留痕」在 Recto 里的统一对象。"""

    book_id: str
    """Recto 自己的稳定书 id (sha1(book_key))"""

    kind: Kind
    """excerpt=摘录 / highlight=划线 / bookmark=书签 / session=时长段"""

    text: str = ""
    """正文 / 划线原文"""

    note: str = ""
    """批注"""

    anchor: Anchor | None = None
    """位置。session 类没有位置。"""

    created_at: int = 0
    """Unix 秒"""

    source: Origin = "library"
    """这条留痕从哪来"""

    external_id: str = ""
    """各源自己的主键, 用于回写与去重"""

    meta: dict = field(default_factory=dict)
    """额外字段 (颜色、时长分钟数、章节名等)"""

    def fingerprint(self) -> str:
        """内容指纹 —— 用于判断「同一条留痕是否变了」。

        刻意不包含 created_at: 同一条划线重新同步时
        时间戳会变, 但内容没变, 不该被当成新条目推上去。
        """
        raw = "|".join([
            self.kind,
            self.book_id,
            self.external_id,
            self.text,
            self.note,
            f"{self.anchor.byte_start}" if self.anchor else "",
            f"{self.anchor.byte_end}" if self.anchor else "",
        ])
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()


@dataclass
class Book:
    """一本书 —— Recto 视角的 (本地书库, 云端) 配对。"""

    book_id: str
    """Recto 稳定 id"""

    title: str = ""
    author: str = ""

    library_series_id: int | None = None
    """本地书库系列 id (整本级)"""

    library_chapter_ids: list[int] = field(default_factory=list)
    """本地书库章 id (卷级映射时多个)"""

    cloud_book_id: str = ""
    """云端书身份 (文件内容 MD5 派生, 64 hex 大写)"""

    epub_path: str = ""
    """原书归档路径 —— 字节换算的唯一权威"""

    total_bytes: int = 0
    """原书总字节数"""

    @property
    def is_volume(self) -> bool:
        """是否卷级映射 (多卷系列)。"""
        return len(self.library_chapter_ids) > 1


@dataclass
class Session:
    """一段阅读时长。"""

    book_id: str
    start_at: int
    """Unix 秒"""

    end_at: int
    """Unix 秒。end <= start 的段一律丢弃 —— 还没结束的阅读不算时长。"""

    minutes: int = 0
    chapter_id: int = -1
    source: Origin = "library"
    generated: bool = False
    """True = 由同步写入的 (本地书库侧的 IsGenerated=1)"""

    def __post_init__(self):
        if self.minutes <= 0 and self.end_at > self.start_at:
            self.minutes = int((self.end_at - self.start_at) // 60)


@dataclass
class Progress:
    """阅读进度 —— 双向同步的核心对象。"""

    book_id: str
    anchor: Anchor
    """当前所在位置"""

    percent: float = 0.0
    """0.0 ~ 1.0。全书加权比例 (按字节, 不是章号)。"""

    chapter_count: int = 0
    """目录章数 (不含封面 / 制作信息)"""

    chapter_id: int = 0
    """目录下标 (0 基)"""

    finished: bool = False
    """是否读完"""

    def __post_init__(self):
        if not 0.0 <= self.percent <= 1.0:
            raise ValueError(f"percent out of range: {self.percent}")
