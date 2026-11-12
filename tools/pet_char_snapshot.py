"""书宠人物存档的快照 / 还原 —— 给探针用。

    python3 tools/pet_char_snapshot.py save     # 存到 data/pet/.char.snapshot.json
    python3 tools/pet_char_snapshot.py restore  # 还原
    python3 tools/pet_char_snapshot.py reset    # 重开一只 (存档推倒)
    python3 tools/pet_char_snapshot.py clear    # 删掉快照

为什么要有: 探针 (tools/probe_pet_char.mjs) 会真的入门派、觉醒性情 —— 这些都落盘。
不还原的话, 用户下次打开书宠页发现自己已经入了门派、多了两条性情, 却不知是谁干的。
(老坑: 探针碰用户状态必须快照 + 还原, 见 tests/test_probe_hygiene.py。)
"""
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def _pet_dir() -> Path:
    env = os.environ.get("RECTO_PET_DIR")
    if env:
        return Path(env)
    try:
        from recto.core import paths as _paths

        return Path(_paths.PATHS.pet_dir)
    except Exception:  # noqa: BLE001
        return ROOT / "data" / "pet"


CHAR = _pet_dir() / "char.json"
SNAP = _pet_dir() / ".char.snapshot.json"


def save() -> int:
    if not CHAR.exists():
        print("没有存档可存:", CHAR)
        return 1
    SNAP.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(CHAR, SNAP)
    try:
        os.chmod(SNAP, 0o600)
    except OSError:
        pass
    print("已快照:", SNAP)
    return 0


def restore() -> int:
    """还原探针动过的那几项 —— **不是整份覆盖**。

    为什么不能整份覆盖: 陪读心跳 (pet/companion.py) 每分钟都在写人物存档
    (修为 / 等级 / 累计分钟 / 锚点)。整份 copyfile 会把心跳刚写的东西盖掉 ——
    真踩过: 探针跑完, 等级/修为/累计分钟 全退回快照那一刻的值。
    探针动的只有"玩家操作"那几项, 恢复它们就够。

    动的字段: 门派 / 性情 / 根骨 / 潜能点 (探针会入门派、觉醒性情、分配潜能点)
    不动的字段: 修为 / 等级 / 声望 / 累计分钟 / 锚点 / 经脉 / 技能 —— 那些是"自然增长"
    """
    if not SNAP.exists():
        print("没有快照可还原:", SNAP)
        return 1
    try:
        snap = json.loads(SNAP.read_text("utf-8"))
        cur = json.loads(CHAR.read_text("utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        # 读不出来就**不动** —— 整份覆盖会把心跳写的修为盖掉, 那比不还原更糟。
        print(f"读存档失败 ({e}) —— 没还原 (整份覆盖会盖掉陪读心跳写的修为)")
        return 1
    for k in ("school", "traits", "attrs", "pot", "morality", "loyalty"):
        if k in snap:
            cur[k] = snap[k]
    tmp = CHAR.with_name(f".{CHAR.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(cur, ensure_ascii=False, indent=2), "utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, CHAR)
    print("已还原 (门派/性情/根骨/潜能):", CHAR)
    return 0


def reset() -> int:
    from recto.pet import charsheet as cs

    cs.reset_char()
    print("已重开一只书宠:", CHAR)
    return 0


def clear() -> int:
    if SNAP.exists():
        SNAP.unlink()
        print("已删快照")
    else:
        print("没有快照")
    return 0


CMDS = {"save": save, "restore": restore, "reset": reset, "clear": clear}

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    fn = CMDS.get(cmd)
    if not fn:
        print("用法: python3 tools/pet_char_snapshot.py {save|restore|reset|clear}")
        raise SystemExit(2)
    raise SystemExit(fn())
