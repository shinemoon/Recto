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

    恢复 = **整份照快照重建**, 再把"只增不减"的成长项与当前取大。

    为什么以快照为底: 探针开跑时会 ``reset`` 重开一只, 重置之后存档里
    等级 / 修为 / 声望 / 技能 / 门派 全没了 —— 只挑几个字段回填是补不回来的
    (真踩过: 用户的书宠被探针留成 1 级无门派)。

    为什么还要与当前取大: 这期间陪读心跳 (pet/companion.py) 可能又涨了分钟 / 修为,
    整份覆盖会把刚写进去的盖掉。
    """
    if not SNAP.exists():
        print("没有快照可还原:", SNAP)
        return 1
    try:
        snap = json.loads(SNAP.read_text("utf-8"))
        cur = json.loads(CHAR.read_text("utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"读存档失败 ({e}) —— 没还原")
        return 1

    def _i(v, d=0):
        try:
            return int(v or 0)
        except (TypeError, ValueError):
            return d

    # 以**快照为准**重建整份存档 (探针开跑时会 reset, 只有快照里还有玩家原本的一切),
    # 再把"只增不减"的成长项与当前取大 (这期间陪读心跳可能又涨了)。
    out = dict(snap)
    for k in ("fame", "minutes", "credited_minutes", "vein", "generation",
              "last_skill_day", "wish"):
        if k in snap or k in cur:
            out[k] = max(_i(snap.get(k)), _i(cur.get(k)))
    sl, cl = _i(snap.get("level"), 1), _i(cur.get("level"), 1)
    if cl > sl or (cl == sl and _i(cur.get("exp")) > _i(snap.get("exp"))):
        out["level"] = cl
        out["exp"] = _i(cur.get("exp"))
    else:
        out["level"] = sl
        out["exp"] = _i(snap.get("exp"))
    ss, cs2 = snap.get("skills") or {}, cur.get("skills") or {}
    merged = {str(k): max(_i(ss.get(k)), _i(cs2.get(k))) for k in set(ss) | set(cs2)}
    out["skills"] = {k: v for k, v in merged.items() if v > 0}
    # 气血 / 内力取当前那份 (探针不打架, 但心跳不改它; 当前更低说明确实被打过)
    for k in ("hp", "neili"):
        if k in cur:
            out[k] = cur[k]

    tmp = CHAR.with_name(f".{CHAR.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(out, ensure_ascii=False, indent=2), "utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, CHAR)
    print("已还原 (整份照快照 / 成长项取大):", CHAR)
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
