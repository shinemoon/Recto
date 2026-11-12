"""后台 skill 的装载器 —— 把 `pet/skills/*.md` 变成能塞进提示词的正文。

**为什么把规矩放在 markdown 而不是字符串常量里**:
这些文件是给模型看的"作业指导", 会反复改 (判分口径 / 文风 / 上限)。
写成 .py 里的三引号字符串, 改一句就得动代码; 写成 md, 可以直接读、可以 diff、
也可以让用户自己拿去改 —— 代码只负责"装载 + 填变量 + 记版本"。

三份:

  ``lore.md``       给一本书立档案卡 (网络检索 + 章节储备)
  ``chronicle.md``  从读书记录判断成长数值
  ``adventure.md``  用母题与语料写书宠的游历故事

**版本号进留痕**: `cycle` 表会记下这次用的是哪一版 (sha1 前 8 位),
出问题时能对上"当时那套规矩长什么样"。
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

DIR = Path(__file__).resolve().parent / "skills"

NAMES = ("lore", "chronicle", "adventure", "legacy")


def path_of(name: str) -> Path:
    if name not in NAMES:
        raise KeyError(f"未知 skill: {name}")
    return DIR / f"{name}.md"


def load(name: str) -> str:
    """读一份 skill 的全文。文件缺失时返回一句能自解释的空壳 (不炸)。"""
    try:
        return path_of(name).read_text("utf-8")
    except OSError:
        return f"# {name}\n(缺失: {path_of(name)})"


def version(name: str = "") -> str:
    """skill 的版本指纹 (单份文件 / 三份合起来)。"""
    parts = [name] if name else list(NAMES)
    h = hashlib.sha1()
    for n in parts:
        h.update(n.encode())
        h.update(load(n).encode("utf-8"))
    return h.hexdigest()[:8]


def render(name: str, **vars: Any) -> str:
    """装载并替换 ``{{var}}`` 占位符。

    故意只做"整词替换": 不想在这里引入模板引擎 (Jinja 会把故事里的大括号吃掉,
    而这些文本里确实可能出现散文式的大括号)。
    """
    txt = load(name)
    for k, v in vars.items():
        txt = txt.replace("{{" + k + "}}", str(v))
    return txt


def spec(name: str) -> dict[str, Any]:
    """给页面看的元信息 (前端不用读全文)。"""
    p = path_of(name)
    try:
        st = p.stat()
        size, mtime = st.st_size, st.st_mtime
    except OSError:
        size, mtime = 0, 0
    txt = load(name)
    head = ""
    m = re.search(r"^#\s+(.+)$", txt, re.M)
    if m:
        head = m.group(1).strip()
    return {"name": name, "title": head, "bytes": size, "mtime": mtime,
            "version": version(name)}


def all_specs() -> list[dict[str, Any]]:
    return [spec(n) for n in NAMES]
