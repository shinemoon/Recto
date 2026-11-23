"""行囊 · 银钱 · 伤药 —— 它在江湖上靠什么活下去。

**为什么要有这一层**: 交手是拿气血换的 (一场输下来能掉到 1/97), 而"打坐回一点"
永远追不上"一刀砍掉半条命" —— 实测那只书宠卡在 20/97 整整两天, 故事里就成了
"永远带伤"。江湖上的人不是这么活的: 伤了要上药, 伤重了要找郎中、住客栈,
这些都要钱; 钱是路上挣来的 (打赢了有赏钱, 赶脚有脚钱), 也是路上花掉的。
有了这一层, 气血才会在 60~95% 之间起伏, 而不是单调往下掉。

**三件事, 一条账**
  · **银钱** (文): 挣钱的路子与花法都写在这里, 不另设"经济系统"
  · **行囊**: 药 / 吃食 / 杂用 (火折子、绳子) —— 有量, 有价钱, 用掉就少一份
  · **装具**: 兵器 / 护具 / 佩物 三个槽 —— 直接进 ``charsheet.d20_stats()`` 的
    拆账 (护体、命中、伤害骰、先攻、暴击), 所以"换一把好刀"是真看得见的

**口径**: 与别处一样 —— 派生值实时算 (装具加成不入库), 消耗与账目才落盘
(``char.json`` 的 ``money`` / ``bag`` / ``equip``)。所有随机都先掷定、再由故事照写,
故事没有"回血"或"捡钱"的自由。
"""
from __future__ import annotations

import random
from typing import Any

from recto.pet import charsheet as cs

# ------------------------------------------------------------------ 定义表

SLOTS: tuple[str, ...] = ("weapon", "armor", "charm")
SLOT_CN = {"weapon": "兵器", "armor": "护具", "charm": "佩物"}

KIND_CN = {
    "medicine": "药", "food": "吃食", "weapon": "兵器",
    "armor": "护具", "charm": "佩物", "kit": "杂用",
}

#: 出门时兜里的钱与行囊 (新人物、以及没有这些栏位的老存档补的都是这一份)
START_MONEY = 60
START_BAG = {"jinchuangyao": 2, "ganliang": 3}
START_EQUIP = {"weapon": "tiejian", "armor": "buyi", "charm": ""}

MAX_MONEY = 99999

#: 医馆 / 客栈 —— 花钱买时间的两条路 (打坐不花钱, 但慢)
CLINIC = {"name": "医馆", "price": 45,
          "heal": {"hp": 0.55, "neili": 0.30},
          "note": "上了药、包了伤, 郎中又多给了一小包药末"}
INN = {"name": "客栈", "price": 18,
       "heal": {"hp": 0.30, "spirit": 0.30, "neili": 0.20},
       "note": "在客栈歇了一夜, 热水泡过脚"}

#: 打坐一次给内功多少熟练度 —— 打坐就是运功, 这是内功最稳的进账
#: (与growth.PRACTICE["rest"] 同值; 那个表管"一趟里记多少", 这里管"当场加多少")。
REST_PRACTICE = 4


def _w(iid: str, name: str, price: int, dice: tuple[int, int], *,
       attack: int = 0, desc: str = "") -> dict[str, Any]:
    return {"id": iid, "name": name, "kind": "weapon", "slot": "weapon",
            "price": price, "dice": list(dice),
            "mods": {"attack": int(attack)}, "desc": desc}


def _a(iid: str, name: str, price: int, *, ac: int = 0, pierce: int = 0,
       init: int = 0, desc: str = "") -> dict[str, Any]:
    return {"id": iid, "name": name, "kind": "armor", "slot": "armor",
            "price": price, "mods": {"ac": int(ac), "pierce": int(pierce),
                                     "init": int(init)}, "desc": desc}


def _c(iid: str, name: str, price: int, mods: dict[str, int], desc: str = "") -> dict[str, Any]:
    return {"id": iid, "name": name, "kind": "charm", "slot": "charm",
            "price": price, "mods": dict(mods), "desc": desc}


def _m(iid: str, name: str, price: int, heal: dict[str, float],
       kind: str = "medicine", desc: str = "") -> dict[str, Any]:
    return {"id": iid, "name": name, "kind": kind, "price": price,
            "heal": dict(heal), "desc": desc}


#: 全部物件。``price`` 是买价, 卖价一律 ``max(1, price // 3)``。
ITEMS: list[dict[str, Any]] = [
    # —— 药 (敷的、服的)
    _m("jinchuangyao", "金创药", 24, {"hp": 0.35},
       desc="一包褐色粉末, 敷上去火辣辣地疼, 血却止得快。"),
    _m("shengjisan", "生肌散", 58, {"hp": 0.60},
       desc="贵, 但伤口收得快 —— 走远路的人身上都该带一包。"),
    _m("huiqidan", "回气丹", 32, {"neili": 0.70},
       desc="一粒下去, 丹田里那股空掉的劲慢慢回来。"),
    _m("anshensan", "安神散", 26, {"spirit": 0.55},
       desc="夜里睡不实的人用它, 第二天眼睛是亮的。"),
    _m("dahuandan", "大还丹", 140, {"hp": 1.0, "neili": 0.5, "spirit": 0.4},
       desc="压箱底的东西。真到要命的时候才服。"),
    # —— 吃食
    _m("ganliang", "干粮", 6, {"hp": 0.08, "jingli": 0.30}, kind="food",
       desc="硬得硌牙, 但顶饿。"),
    _m("tangbing", "汤饼", 10, {"hp": 0.15, "jingli": 0.30}, kind="food",
       desc="一碗热的, 吃完脚上有劲。"),
    _m("shaojiudao", "烧刀子", 12, {"spirit": 0.30, "hp": 0.05}, kind="food",
       desc="烈。伤口上浇一口, 疼得清醒。"),
    # —— 兵器 (兵刃骰与命中)
    _w("mujian", "木剑", 14, (1, 6), desc="练手用的, 轻, 也砍不动东西。"),
    _w("tiejian", "铁剑", 88, (1, 8), attack=1, desc="寻常铁匠铺里打得出的剑。"),
    _w("liuyedao", "柳叶刀", 96, (1, 8), attack=1, desc="刀身薄, 走的是快。"),
    _w("qimeigun", "齐眉棍", 70, (1, 8), desc="乡下人也会使的兵器, 打人不打骨。"),
    _w("yanlingdao", "雁翎刀", 240, (1, 10), attack=1, desc="刀背压手, 一刀下去沉。"),
    _w("qingfengjian", "青锋剑", 280, (1, 10), attack=2, desc="刃口收得极细, 出手快半拍。"),
    _w("panguanbi", "判官笔", 200, (1, 4), attack=3, desc="短, 准, 专往穴上点。"),
    # —— 护具 (护体 / 卸力)
    _a("buyi", "布衣", 8, desc="两件换着穿的粗布衣裳。"),
    _a("duanhe", "短褐", 40, ac=1, desc="袖口扎紧, 挨打时多少挡一下。"),
    _a("ruanjia", "软甲", 120, ac=1, pierce=1, desc="贴身的一层, 藏在衣裳里看不出来。"),
    _a("pijia", "皮甲", 180, ac=2, desc="沉, 但胸口那一块是真的硬。"),
    _a("huxinjing", "护心镜", 160, ac=1, pierce=2, desc="铜镜一面, 缝在衣里正对心口。"),
    _a("suojia", "锁子甲", 380, ac=3, init=-1, desc="重甲。穿上它, 起落都慢半拍。"),
    # —— 佩物 (暴击 / 先攻 / 上限)
    _c("pinganfu", "平安符", 45, {"hp_pct": 5}, desc="庙里求的, 黄纸一张。"),
    _c("qingshenfu", "轻身符", 70, {"init": 2}, desc="画得潦草, 但真觉得脚下轻些。"),
    _c("tiepai", "铁牌", 120, {"crit": 5}, desc="不知谁留下的旧铁牌, 握在手里心定。"),
    _c("yupai", "玉佩", 90, {"neili_pct": 8}, desc="温的。坐久了, 气息比平时长。"),
    # —— 杂用 (给路上的坎加值, 不进交手)
    {"id": "huozhezi", "name": "火折子", "kind": "kit", "price": 15,
     "kit": {"road": 1}, "desc": "夜里照得见脚底下那三尺。"},
    {"id": "shengzi", "name": "麻绳", "kind": "kit", "price": 12,
     "kit": {"road": 1}, "desc": "一丈二, 渡水、探路、捆人都用得上。"},
    {"id": "mingtie", "name": "名帖", "kind": "kit", "price": 30,
     "kit": {"person": 1}, "desc": "上头写着它的名字 —— 有些门是要递帖才进的。"},
]

ITEM_BY_ID: dict[str, dict[str, Any]] = {str(i["id"]): i for i in ITEMS}

#: 打斗收获的兵刃 (按对手使的那一路)
FOE_WEAPON_LOOT = {
    "jianfa": ["tiejian", "qingfengjian"],
    "daofa": ["liuyedao", "yanlingdao"],
    "gunfa": ["qimeigun"],
    "zhifa": ["panguanbi"],
}

#: 路上随手能捡到 / 从人身上摸到的东西
COMMON_FINDS = ["jinchuangyao", "ganliang", "shaojiudao", "huozhezi",
                "shengzi", "huiqidan"]

#: 各处手艺上的开销 (学艺要买纸墨)
LEARN_COST = (6, 14)


# ------------------------------------------------------------------ 基础读数

def money(ch: dict[str, Any]) -> int:
    try:
        return max(0, int(ch.get("money") or 0))
    except (TypeError, ValueError):
        return 0


def bag(ch: dict[str, Any]) -> dict[str, int]:
    raw = ch.get("bag")
    if not isinstance(raw, dict):
        raw = {}
    out: dict[str, int] = {}
    for k, v in raw.items():
        iid = str(k)
        if iid not in ITEM_BY_ID:
            continue
        try:
            n = int(v or 0)
        except (TypeError, ValueError):
            n = 0
        if n > 0:
            out[iid] = n
    return out


def equip(ch: dict[str, Any]) -> dict[str, str]:
    raw = ch.get("equip")
    if not isinstance(raw, dict):
        raw = {}
    out: dict[str, str] = {}
    for slot in SLOTS:
        iid = str(raw.get(slot) or "")
        it = ITEM_BY_ID.get(iid)
        out[slot] = iid if (it and it.get("slot") == slot) else ""
    return out


def _set_bag(ch: dict[str, Any], b: dict[str, int]) -> None:
    ch["bag"] = {str(k): int(v) for k, v in b.items() if int(v) > 0}


def give(ch: dict[str, Any], item_id: str, count: int = 1) -> int:
    """往行囊里放东西 (返回实际放入的件数)。"""
    iid = str(item_id or "")
    if iid not in ITEM_BY_ID or int(count) <= 0:
        return 0
    b = bag(ch)
    b[iid] = b.get(iid, 0) + int(count)
    _set_bag(ch, b)
    return int(count)


def take(ch: dict[str, Any], item_id: str, count: int = 1) -> bool:
    """从行囊里拿走东西 (不够就拿不动, 返回 False)。"""
    iid = str(item_id or "")
    b = bag(ch)
    n = b.get(iid, 0)
    if n < int(count):
        return False
    if n == int(count):
        b.pop(iid, None)
    else:
        b[iid] = n - int(count)
    _set_bag(ch, b)
    return True


def add_money(ch: dict[str, Any], delta: int) -> int:
    """加减钱 (就地, 返回实际变化量)。"""
    before = money(ch)
    after = max(0, min(MAX_MONEY, before + int(delta)))
    ch["money"] = after
    return after - before


# ------------------------------------------------------------------ 装具加成

def _blank_mods() -> dict[str, Any]:
    return {"attack": 0, "ac": 0, "init": 0, "crit": 0, "pierce": 0,
            "damage": 0, "hp_pct": 0, "neili_pct": 0, "dice": None,
            "parts": []}


def mods(ch: dict[str, Any]) -> dict[str, Any]:
    """身上三件东西带来的加值 —— **派生, 不入库**。

    ``charsheet.d20_stats()`` 与 ``derive()`` 都读这一份, 所以面板上"护体 13"
    与战报里那一记"骰点 + 命中加值"用的是同一套数。
    """
    out = _blank_mods()
    eq = equip(ch)
    for slot in SLOTS:
        it = ITEM_BY_ID.get(eq.get(slot) or "")
        if not it:
            continue
        m = it.get("mods") or {}
        for key in ("attack", "ac", "init", "crit", "pierce", "damage",
                    "hp_pct", "neili_pct"):
            if m.get(key):
                out[key] = int(out[key]) + int(m[key])
        if slot == "weapon":
            d = it.get("dice")
            if isinstance(d, (list, tuple)) and len(d) == 2:
                out["dice"] = (int(d[0]), int(d[1]))
            out["weapon"] = {"id": it["id"], "name": it["name"]}
        else:
            out[slot] = {"id": it["id"], "name": it["name"]}
        if m:
            out["parts"].append({
                "slot": slot, "slot_cn": SLOT_CN[slot],
                "name": it["name"], "mods": dict(m), "dice": it.get("dice"),
            })
    return out


def check_bonus(ch: dict[str, Any], cat: str = "road") -> int:
    """行囊里的杂用物件给某一类坎的加值 (封顶 +2)。

    只认"带在身上"的: 火折子照夜路, 麻绳渡水, 名帖递门 —— 都是走一趟能用上的。
    """
    total = 0
    for iid, n in bag(ch).items():
        if n <= 0:
            continue
        it = ITEM_BY_ID.get(iid) or {}
        kit = it.get("kit") or {}
        if int(kit.get(cat) or 0):
            total += int(kit.get(cat))
    return max(0, min(2, total))


# ------------------------------------------------------------------ 消耗与买卖

def use(ch: dict[str, Any], item_id: str) -> dict[str, Any]:
    """用掉一件 (药 / 吃食)。返回恢复了多少。"""
    it = ITEM_BY_ID.get(str(item_id or ""))
    if not it:
        return {"ok": False, "error": "没有这样东西"}
    if not (it.get("heal") or {}):
        return {"ok": False, "error": f"{it['name']}不是吃用的东西"}
    if not take(ch, it["id"], 1):
        return {"ok": False, "error": f"行囊里没有{it['name']}"}
    healed = _apply_heal(ch, it.get("heal") or {})
    return {"ok": True, "item": it["id"], "name": it["name"], "healed": healed}


def _apply_heal(ch: dict[str, Any], heal: dict[str, float]) -> dict[str, int]:
    r = cs.resources(ch)
    out: dict[str, int] = {}
    for key, frac in (heal or {}).items():
        if key not in r:
            continue
        cur, mx = int(r[key]["cur"]), int(r[key]["max"])
        add = int(round(mx * float(frac)))
        new = max(0, min(mx, cur + add))
        if new != cur:
            ch[key] = new
            out[key] = new - cur
    return out


def sell_price(item_id: str) -> int:
    it = ITEM_BY_ID.get(str(item_id or "")) or {}
    return max(1, int(it.get("price") or 0) // 3)


def buy(ch: dict[str, Any], item_id: str, count: int = 1) -> dict[str, Any]:
    it = ITEM_BY_ID.get(str(item_id or ""))
    if not it:
        return {"ok": False, "error": "没有这样东西可买"}
    n = max(1, int(count or 1))
    cost = int(it["price"]) * n
    if money(ch) < cost:
        return {"ok": False, "error": f"钱不够 ({money(ch)} / {cost} 文)"}
    add_money(ch, -cost)
    give(ch, it["id"], n)
    return {"ok": True, "item": it["id"], "name": it["name"], "count": n,
            "cost": cost, "money": money(ch)}


def sell(ch: dict[str, Any], item_id: str, count: int = 1) -> dict[str, Any]:
    iid = str(item_id or "")
    it = ITEM_BY_ID.get(iid)
    if not it:
        return {"ok": False, "error": "行囊里没有这样东西"}
    if equip(ch).get(str(it.get("slot") or "")) == iid:
        return {"ok": False, "error": f"{it['name']}还带在身上, 先卸下"}
    n = max(1, int(count or 1))
    if not take(ch, iid, n):
        return {"ok": False, "error": f"行囊里的{it['name']}不够"}
    gain = sell_price(iid) * n
    add_money(ch, gain)
    return {"ok": True, "item": iid, "name": it["name"], "count": n,
            "gain": gain, "money": money(ch)}


def wear(ch: dict[str, Any], item_id: str) -> dict[str, Any]:
    """装备 / 卸下 (``item_id`` 为空 = 卸下该槽)。"""
    iid = str(item_id or "")
    if not iid:
        return {"ok": False, "error": "要装哪一件?"}
    it = ITEM_BY_ID.get(iid)
    if not it or not it.get("slot"):
        return {"ok": False, "error": "这件东西没法带在身上"}
    slot = str(it["slot"])
    eq = equip(ch)
    if eq.get(slot) != iid:
        if not take(ch, iid, 1):
            return {"ok": False, "error": f"行囊里没有{it['name']}"}
        if eq.get(slot):
            give(ch, eq[slot], 1)      # 换下来的回行囊
        eq[slot] = iid
    ch["equip"] = eq
    return {"ok": True, "slot": slot, "item": iid, "name": it["name"],
            "equip": eq}


def unwear(ch: dict[str, Any], slot: str) -> dict[str, Any]:
    s = str(slot or "")
    if s not in SLOTS:
        return {"ok": False, "error": "没有这个位置"}
    eq = equip(ch)
    iid = eq.get(s) or ""
    if not iid:
        return {"ok": True, "slot": s, "equip": eq}
    give(ch, iid, 1)
    eq[s] = ""
    ch["equip"] = eq
    name = (ITEM_BY_ID.get(iid) or {}).get("name") or iid
    return {"ok": True, "slot": s, "item": iid, "name": name, "equip": eq}


# ------------------------------------------------------------------ 自理 (出门前 / 战后)

def _pick_medicine(ch: dict[str, Any]) -> dict[str, Any] | None:
    """挑一颗最该吃的: 缺口最大的那一项, 取"够用且便宜"的。"""
    r = cs.resources(ch)
    gaps = {
        "hp": max(0.0, 0.80 - r["hp"]["cur"] / max(1, r["hp"]["max"])),
        "neili": max(0.0, 0.80 - r["neili"]["cur"] / max(1, r["neili"]["max"])),
        "spirit": max(0.0, 0.75 - r["spirit"]["cur"] / max(1, r["spirit"]["max"])),
    }
    key = max(gaps, key=lambda k: gaps[k] * (2.0 if k == "hp" else 1.0))
    if gaps[key] <= 0.06:
        return None
    cands: list[tuple[int, int, int, dict[str, Any]]] = []
    for iid in bag(ch):
        it = ITEM_BY_ID.get(iid) or {}
        val = float((it.get("heal") or {}).get(key) or 0)
        if val <= 0:
            continue
        enough = 0 if val >= gaps[key] * 0.7 else 1
        # 先药后食: 有药就先上药, 干粮只是"垫一口" (它治不了伤)
        is_food = 0 if str(it.get("kind")) == "medicine" else 1
        cands.append((enough, is_food, int(it.get("price") or 0), it))
    if not cands:
        return None
    cands.sort(key=lambda t: (t[0], t[1], t[2]))
    return cands[0][3]


def selfcare(ch: dict[str, Any], *, seed: int | None = None,
             when: str = "出门前") -> dict[str, Any]:
    """伤了自己料理 —— 先吃药, 再花钱, 最后打坐。**就地改 ch**。

    顺序是有讲究的 (也是这层存在的理由):
      1. 行囊里有药 → 先吃 (钱要留着, 药本来就是为这一刻备的)
      2. 伤得重而没有药 → 找医馆; 伤得不轻 → 客栈歇一夜
      3. 什么都做不了 → 找个背风处打坐 (只回一点点, 但不要钱)
    返回的 ``acts`` / ``notes`` 直接进故事的素材: 这一趟它花了什么、敷了什么。
    """
    rng = random.Random(int(seed or 0) ^ 0x2545F491)
    r0 = cs.resources(ch)
    hp0 = float(r0["hp"]["cur"]) / max(1, r0["hp"]["max"])
    money0 = money(ch)
    acts: list[dict[str, Any]] = []
    notes: list[str] = []

    def pct(key: str) -> float:
        r = cs.resources(ch)
        return float(r[key]["cur"]) / max(1, r[key]["max"])

    # 1) 药
    for _ in range(3):
        if pct("hp") >= 0.72 and pct("neili") >= 0.55 and pct("spirit") >= 0.55:
            break
        it = _pick_medicine(ch)
        if not it:
            break
        res = use(ch, str(it["id"]))
        if not res.get("ok"):
            break
        acts.append({"kind": "use", "name": res["name"],
                     "healed": res.get("healed") or {}})
        heal_cn = _heal_cn(res.get("healed") or {})
        _it = ITEM_BY_ID.get(str(res["item"])) or {}
        verb = "吃了" if _it.get("kind") == "food" else "用了"
        notes.append(f"{verb}{res['name']}" + (f", {heal_cn}" if heal_cn else ""))

    # 2) 花钱 (医馆 / 客栈)
    if pct("hp") < 0.42 and money(ch) >= int(CLINIC["price"]):
        add_money(ch, -int(CLINIC["price"]))
        healed = _apply_heal(ch, CLINIC["heal"])
        acts.append({"kind": "pay", "name": CLINIC["name"],
                     "cost": int(CLINIC["price"]), "healed": healed})
        notes.append(f"去医馆花了 {CLINIC['price']} 文, {CLINIC['note']}")
    elif pct("hp") < 0.62 and money(ch) >= int(INN["price"]):
        add_money(ch, -int(INN["price"]))
        healed = _apply_heal(ch, INN["heal"])
        acts.append({"kind": "pay", "name": INN["name"],
                     "cost": int(INN["price"]), "healed": healed})
        notes.append(f"花了 {INN['price']} 文住店, {INN['note']}")

    # 3) 打坐 (不花钱, 只回一点点)
    if pct("hp") < 0.55 or pct("neili") < 0.4:
        got = cs.restore(ch, 0.45 + 0.15 * rng.random())
        if got.get("hp") or got.get("neili"):
            act = {"kind": "rest", "name": "打坐", "healed": got}
            # 打坐就是运功 —— 内功能从这里攒熟练度 (否则它一辈子停在入门那一格)
            ng = cs.sect_neigong(ch)
            sid = str(ng.get("sid") or "jibenneigong")
            prac = cs.add_practice(ch, sid, REST_PRACTICE)
            if int(prac.get("added") or 0):
                act["practice"] = {"id": sid, "name": str(ng.get("name") or "基本内功"),
                                   "gain": int(prac["added"]),
                                   "leveled": bool(prac.get("leveled"))}
            acts.append(act)
            notes.append("找了个背风处打坐, 气回了些")

    r1 = cs.resources(ch)
    return {
        "when": when,
        "money": {"from": money0, "to": money(ch)},
        "hp": {"from": int(r0["hp"]["cur"]), "to": int(r1["hp"]["cur"]),
               "max": int(r1["hp"]["max"]), "pct_from": round(hp0, 4)},
        "acts": acts,
        "notes": notes,
        "note": "；".join(notes),
    }


_HEAL_CN = {"hp": "气血", "neili": "内力", "spirit": "精神", "jingli": "精力"}


def _heal_cn(healed: dict[str, int]) -> str:
    parts = [f"{_HEAL_CN.get(k, k)} +{int(v)}" for k, v in (healed or {}).items()
             if int(v)]
    return " · ".join(parts)


# ------------------------------------------------------------------ 路上的账 (挣钱 / 捡东西)

def _loot_item(rng: random.Random, ch: dict[str, Any],
               duel: dict[str, Any] | None) -> str:
    foe = (duel or {}).get("foe") or {}
    weapon = str(foe.get("weapon") or "")
    if duel and weapon in FOE_WEAPON_LOOT and rng.random() < 0.55:
        pool = FOE_WEAPON_LOOT[weapon]
        return pool[rng.randrange(len(pool))]
    pool = [i for i in COMMON_FINDS if bag(ch).get(i, 0) < 3] or list(COMMON_FINDS)
    return pool[rng.randrange(len(pool))]


def aftermath(ch: dict[str, Any], *, kind: str = "", duel: dict[str, Any] | None = None,
              seed: int | None = None) -> dict[str, Any]:
    """走完这一趟之后: 赏钱 / 脚钱 / 谢礼 / 花销, 以及路上捡到的物件。**就地改 ch**。

    规矩 (都按这一趟是什么事):
      · 斗 赢了有赏钱与战利品, 输了会被摸走一点, 平手两不相欠
      · 救 有人给谢礼; 行 有脚钱; 观 随手挣几个; 会 两清; 学 要买纸墨 (往外花)
    钱与东西都是**掷定之后落账**的, 故事只负责照写 —— 不许自己在正文里加钱。
    """
    rng = random.Random((int(seed or 0) ^ 0x5DEECE66D) & 0x7FFFFFFF)
    money0 = money(ch)
    result = str((duel or {}).get("result") or "")
    gained: list[dict[str, Any]] = []
    spends: list[dict[str, Any]] = []
    notes: list[str] = []
    delta = 0

    def earn(n: int, why: str) -> None:
        nonlocal delta
        n = int(n)
        if n <= 0:
            return
        real = add_money(ch, n)
        delta += real
        if real:
            gained.append({"money": real, "why": why})
            notes.append(f"{why} {real} 文")

    def spend(n: int, why: str) -> None:
        nonlocal delta
        n = min(int(n), money(ch))
        if n <= 0:
            return
        real = add_money(ch, -n)
        delta += real
        spends.append({"money": -real, "why": why})
        notes.append(f"{why} 花了 {abs(real)} 文")

    if kind == "fight" and duel:
        if result == "win":
            earn(18 + rng.randrange(38), "对手身上摸出的钱")
            if rng.random() < 0.30:
                iid = _loot_item(rng, ch, duel)
                give(ch, iid, 1)
                gained.append({"item": iid,
                               "name": (ITEM_BY_ID.get(iid) or {}).get("name", iid),
                               "count": 1, "why": "从对手身上取下"})
        elif result == "lose":
            spend(6 + rng.randrange(13), "钱袋被人摸了一下")
            if rng.random() < 0.15:
                b = bag(ch)
                meds = [i for i in b if (ITEM_BY_ID.get(i) or {}).get("kind") == "medicine"]
                if meds:
                    lost = meds[rng.randrange(len(meds))]
                    take(ch, lost, 1)
                    notes.append(f"还丢了一包{(ITEM_BY_ID.get(lost) or {}).get('name', lost)}")
        else:
            earn(rng.randrange(12), "散场时有人塞了几个钱")
    elif kind == "rescue":
        earn(16 + rng.randrange(30), "受助的人给的谢礼")
        if rng.random() < 0.20:
            iid = _loot_item(rng, ch, None)
            give(ch, iid, 1)
            gained.append({"item": iid, "name": (ITEM_BY_ID.get(iid) or {}).get("name", iid),
                           "count": 1, "why": "人家塞给它的"})
    elif kind == "travel":
        earn(5 + rng.randrange(12), "替人捎带东西的脚钱")
        if rng.random() < 0.15:
            iid = _loot_item(rng, ch, None)
            give(ch, iid, 1)
            gained.append({"item": iid, "name": (ITEM_BY_ID.get(iid) or {}).get("name", iid),
                           "count": 1, "why": "路边捡到的"})
    elif kind == "observe":
        earn(rng.randrange(9), "替人看了一桩事, 收了几个钱")
    elif kind == "learn":
        spend(LEARN_COST[0] + rng.randrange(LEARN_COST[1] - LEARN_COST[0] + 1),
              "买纸墨")
    # 会 (talk) 两不相欠

    item_rows = [g for g in gained if g.get("item")]
    return {
        "kind": kind,
        "result": result,
        "money": {"from": money0, "to": money(ch), "delta": int(delta)},
        "gained": gained,
        "spends": spends,
        "items": item_rows,
        "notes": notes,
        "note": "；".join(notes),
    }


# ------------------------------------------------------------------ 对外视图

def _mods_cn(it: dict[str, Any]) -> str:
    bits: list[str] = []
    d = it.get("dice")
    if isinstance(d, (list, tuple)) and len(d) == 2:
        bits.append(f"兵刃骰 {d[0]}d{d[1]}")
    m = it.get("mods") or {}
    for key, cn in (("attack", "命中"), ("ac", "护体"), ("pierce", "卸力"),
                    ("init", "先攻"), ("crit", "暴击"), ("damage", "伤害"),
                    ("hp_pct", "气血上限"), ("neili_pct", "内力上限")):
        v = int(m.get(key) or 0)
        if v:
            bits.append(f"{cn} {'+' if v > 0 else ''}{v}" + ("%" if key.endswith("_pct") else ""))
    return " · ".join(bits)


def _heal_text(it: dict[str, Any]) -> str:
    h = it.get("heal") or {}
    bits = []
    for key, cn in (("hp", "气血"), ("neili", "内力"), ("spirit", "精神"), ("jingli", "精力")):
        v = float(h.get(key) or 0)
        if v:
            bits.append(f"{cn} +{int(round(v * 100))}%")
    return " · ".join(bits)


def _row(it: dict[str, Any], count: int = 0) -> dict[str, Any]:
    return {
        "id": it["id"], "name": it["name"], "kind": it["kind"],
        "kind_cn": KIND_CN.get(str(it["kind"]), str(it["kind"])),
        "price": int(it.get("price") or 0),
        "sell": sell_price(it["id"]),
        "count": int(count),
        "desc": it.get("desc") or "",
        "slot": it.get("slot") or "",
        "slot_cn": SLOT_CN.get(str(it.get("slot") or ""), ""),
        "effect": _mods_cn(it) or _heal_text(it),
        "usable": bool(it.get("heal")),
        "wearable": bool(it.get("slot")),
        "kit": dict(it.get("kit") or {}),
    }


def view(ch: dict[str, Any]) -> dict[str, Any]:
    """行囊 / 装具 / 铺子的完整视图 (给人物面板)。"""
    b = bag(ch)
    eq = equip(ch)
    slots = []
    for slot in SLOTS:
        iid = eq.get(slot) or ""
        it = ITEM_BY_ID.get(iid) or {}
        slots.append({
            "slot": slot, "slot_cn": SLOT_CN[slot],
            "item": iid, "name": it.get("name") or "",
            "effect": _mods_cn(it) if it else "",
            "desc": it.get("desc") or "",
        })
    rows = [_row(ITEM_BY_ID[i], n) for i, n in
            sorted(b.items(), key=lambda kv: (KIND_CN.get(ITEM_BY_ID[kv[0]]["kind"], ""),
                                              ITEM_BY_ID[kv[0]]["price"]))]
    shop = []
    cash = money(ch)
    for it in sorted(ITEMS, key=lambda x: (x["kind"], int(x["price"]))):
        row = _row(it, b.get(it["id"], 0))
        row["afford"] = cash >= int(it["price"])
        shop.append(row)
    m = mods(ch)
    return {
        "money": cash,
        "money_text": f"{cash} 文",
        "slots": slots,
        "bag": rows,
        "bag_count": sum(int(r["count"]) for r in rows),
        "shop": shop,
        "mods": {"attack": m["attack"], "ac": m["ac"], "init": m["init"],
                 "crit": m["crit"], "pierce": m["pierce"], "damage": m["damage"]},
        "mods_text": " · ".join(x for x in [
            f"命中 +{m['attack']}" if m["attack"] else "",
            f"护体 +{m['ac']}" if m["ac"] else "",
            f"卸力 +{m['pierce']}" if m["pierce"] else "",
            f"先攻 +{m['init']}" if m["init"] else "",
            f"暴击 +{m['crit']}" if m["crit"] else "",
        ] if x) or "（赤手空拳，布衣一身）",
        "clinic": {"name": CLINIC["name"], "price": int(CLINIC["price"]),
                   "heal": _heal_text({"heal": CLINIC["heal"]})},
        "inn": {"name": INN["name"], "price": int(INN["price"]),
                "heal": _heal_text({"heal": INN["heal"]})},
        "rule": "伤了先吃行囊里的药, 没钱也没药就打坐 —— 这条账由后端结, 故事只照写。",
    }
