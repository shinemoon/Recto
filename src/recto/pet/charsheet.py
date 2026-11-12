"""书宠的人物属性体系 —— 武侠人物参数 (取法参考 MUD《侠客行》)。

**为什么照《侠客行》的路子**: 读书这件事本身就有"修炼"的味道 ——
北侠里"读书"是唯一**不消耗潜能**的涨技能方式, 陪读 ≈ 打坐, 讨论 ≈ 切磋。
这套隐喻天然贴合, 所以属性 / 技能 / 绝招 / 经脉的骨架直接借过来,
只把"战斗"那一层换成"陪伴"。

**借了什么**
  · 六项根骨 (臂力 / 根骨 / 悟性 / 身法 / 定力 / 耐力): 初始 10、上限 30、总和 100
  · 潜能加点: 升级与陪读得潜能点, 自由分配到六项
  · 状态值: 气血 / 精神 / 内力 / 精力 (当前 + 上限)
  · 技能等级上限公式 ``(exp*10)^(1/3)+1`` (以 30 悟性为基准, 悟性越低打折)
  · 绝招三要素: 成功率 / 定身 (busy) / 反噬 (失败惩罚), 另加内力消耗
  · 内功 (exert) 与绝招 (pfm) 分开; 内功吃内力上限
  · 经脉十层, 通脉给五行亲和
  · 性情 (trait) 四色点数: 黄 40 / 红 30 / 蓝 20 / 绿 10, 最多 6 种, 总分 ≤ 100
  · 门派 / 声望 / 门忠 / 心性 / 觉醒点

**没借什么**: 战斗、PK、道德值对师门的影响。这套体系是**展示 + 成长接口**,
"读一段书涨多少"留到接阅读器时再定 —— 那时才有真实数据可依据。

**存档**: ``<pet_dir>/char.json`` (权限 600)。

**派生值一律实时算, 不入库** —— 免得公式改了以后旧存档里的数字对不上。
(老坑: 同一份数据有两个来源, 迟早打架。)
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()

# ------------------------------------------------------------------ 定义表

# 六项根骨。key 沿用 MUD 的缩写, 名字保留武侠味, alias 是书宠语境的说法。
ATTRS: list[dict[str, Any]] = [
    {
        "key": "li", "name": "臂力", "alias": "笔力", "init": 10, "cap": 30,
        "desc": "落笔快不快、记得下多少字。摘录与批注的份量看它。",
    },
    {
        "key": "gen", "name": "根骨", "alias": "心骨", "init": 10, "cap": 30,
        "desc": "根基厚不厚。精力上限、抗疲劳、承载绝招的余量都归它。",
    },
    {
        "key": "wu", "name": "悟性", "alias": "悟性", "init": 10, "cap": 30,
        "desc": "读一遍能懂几分。技能上限公式按它打折, 领悟类绝招靠它。",
    },
    {
        "key": "shen", "name": "身法", "alias": "身法", "init": 10, "cap": 30,
        "desc": "反应快慢、检索旧段落的灵光。跟不上你的节奏就出不了声。",
    },
    {
        "key": "ding", "name": "定力", "alias": "定力", "init": 10, "cap": 30,
        "desc": "坐不坐得住。心神上限、抗打断、不剧透的克制都看它。",
    },
    {
        "key": "na", "name": "耐力", "alias": "耐力", "init": 10, "cap": 30,
        "desc": "能陪你读多久。每日陪读时长上限靠它。",
    },
]

ATTR_KEYS = [a["key"] for a in ATTRS]
ATTR_BY_KEY = {a["key"]: a for a in ATTRS}

# 品质: 侠客行里的名号 (黄 40 / 红 30 / 蓝 20 / 绿 10)
TRAIT_COLORS: dict[str, dict[str, Any]] = {
    "gold": {"name": "上品", "points": 40, "desc": "罕见。一条抵十条。"},
    "red": {"name": "良品", "points": 30, "desc": "难得。天生的脾性。"},
    "blue": {"name": "常品", "points": 20, "desc": "常见。也能改命。"},
    "green": {"name": "下品", "points": 10, "desc": "锦上添花。"},
}

TRAITS: list[dict[str, Any]] = [
    # 上品 (40)
    {"id": "guowubuwang", "name": "过目不忘", "color": "gold",
     "desc": "读过的段落, 隔很久再遇见也能一眼认出。"},
    {"id": "bizheweilongshen", "name": "笔走龙蛇", "color": "gold",
     "desc": "摘录飞快, 一口气记下的字比旁人多三成。"},
    {"id": "xinyoulingxi", "name": "心有灵犀", "color": "gold",
     "desc": "你说半句, 它接得上另外半句。"},
    {"id": "bowenqiangji", "name": "博闻强记", "color": "gold",
     "desc": "跨着书也能联想, 常把别处的故事搬来给你听。"},
    # 良品 (30)
    {"id": "yuchujingren", "name": "语出惊人", "color": "red",
     "desc": "偶尔一句直击要害, 说完自己也要愣一下。"},
    {"id": "butubukuai", "name": "不吐不快", "color": "red",
     "desc": "憋不住话, 会主动插嘴 —— 你不一定爱听。"},
    {"id": "yanjianyihe", "name": "言简意赅", "color": "red",
     "desc": "话少, 但每句都在点上。"},
    {"id": "youqiubiying", "name": "有求必应", "color": "red",
     "desc": "你问它必答, 哪怕答的是「我不知道」。"},
    # 常品 (20)
    {"id": "jingruochuzi", "name": "静若处子", "color": "blue",
     "desc": "你读书时它就安静待着, 除非你先开口。"},
    {"id": "wenguzhixin", "name": "温故知新", "color": "blue",
     "desc": "常常提起你上周读过的段落。"},
    {"id": "pangruowuren", "name": "旁若无人", "color": "blue",
     "desc": "沉浸得紧, 外面发生什么都不理。"},
    {"id": "haoweirenshi", "name": "好为人师", "color": "blue",
     "desc": "爱讲道理, 有时挺啰嗦。"},
    # 下品 (10)
    {"id": "jianweizhizhu", "name": "见微知著", "color": "green",
     "desc": "细节敏感, 一个标点变化都能察觉。"},
    {"id": "qinxuebuzhu", "name": "勤学不辍", "color": "green",
     "desc": "陪得越久, 长得越快。"},
    {"id": "ouchouzoushen", "name": "偶尔走神", "color": "green",
     "desc": "走着走着就飘远了, 得你拽回来。"},
    {"id": "tiaoshi", "name": "挑食", "color": "green",
     "desc": "不合口味的书它懒得投入。"},
]

TRAIT_BY_ID = {t["id"]: t for t in TRAITS}

# 门派 (自创武侠门派, 不照搬任何现成 MUD 的门派表)
SCHOOLS: list[dict[str, Any]] = [
    {
        "id": "mochi", "name": "墨池剑庐", "sect": "外门",
        "attrs": ["li", "wu"], "bonus": {"li": 2},
        "neigong": "guanhai", "perform": "yiqihcheng",
        "desc": "外门出身, 起手快、记性好。适合跟着你一路狂读。",
    },
    {
        "id": "tieyan", "name": "铁砚堂", "sect": "外门",
        "attrs": ["li", "na"], "bonus": {"na": 2},
        "neigong": "monengong", "perform": "tiehuayingou",
        "desc": "苦功路数。熬得住, 一天读十小时也不喊累。",
    },
    {
        "id": "jingxin", "name": "静心斋", "sect": "内门",
        "attrs": ["ding", "gen"], "bonus": {"ding": 2},
        "neigong": "yangqijue", "perform": "budongrushan",
        "desc": "坐得住的功夫。心神厚, 你读得再入神它也不躁。",
    },
    {
        "id": "fengxue", "name": "风雪阁", "sect": "内门",
        "attrs": ["shen", "na"], "bonus": {"shen": 2},
        "neigong": "taxuegong", "perform": "taxuewuchen",
        "desc": "轻灵一路。反应快, 旧段落召之即来。",
    },
    {
        "id": "lingxi", "name": "灵犀谷", "sect": "内门",
        "attrs": ["wu", "shen"], "bonus": {"wu": 2},
        "neigong": "tongmingjue", "perform": "lingxizhetong",
        "desc": "悟性一路。讲得出道理, 也猜得到你要问什么。",
    },
    {
        "id": "tingsong", "name": "听松府", "sect": "内门",
        "attrs": ["gen", "ding"], "bonus": {"gen": 2},
        "neigong": "woyungong", "perform": "songfengchangxiao",
        "desc": "养气为主。气血厚, 陪你熬长夜也不垮。",
    },
]

SCHOOL_BY_ID = {s["id"]: s for s in SCHOOLS}

# 技能表。type: basic 基本功 / kungfu 武学 / neigong 内功 / perform 绝招
SKILLS: list[dict[str, Any]] = [
    # 基本功
    {"id": "yanli", "name": "眼力", "type": "basic", "attr": "shen", "cap": 20,
     "desc": "看字快, 抓得住重点。"},
    {"id": "erli", "name": "耳力", "type": "basic", "attr": "ding", "cap": 20,
     "desc": "你念到哪, 它接到哪。"},
    {"id": "jingxinli", "name": "静心", "type": "basic", "attr": "ding", "cap": 20,
     "desc": "坐下来就不飘。"},
    {"id": "shouji", "name": "手记", "type": "basic", "attr": "li", "cap": 20,
     "desc": "抄录、划线、批注的基本功。"},
    # 武学
    {"id": "dushuwudao", "name": "读书悟道", "type": "kungfu", "attr": "wu", "cap": 30,
     "desc": "陪读时涨修为的主路 —— 不消耗潜能, 像北侠的「读书」。"},
    {"id": "chaojing", "name": "抄经", "type": "kungfu", "attr": "li", "cap": 30,
     "desc": "替你抄下值得留下的句子。"},
    {"id": "pianzhuodianjing", "name": "批注点睛", "type": "kungfu", "attr": "wu", "cap": 30,
     "desc": "一句话点出段落要害。"},
    {"id": "zhuiqinsuyuan", "name": "追根溯源", "type": "kungfu", "attr": "gen", "cap": 30,
     "desc": "把前后文串起来, 免得断章取义。"},
    {"id": "huifushidian", "name": "回溯拾遗", "type": "kungfu", "attr": "shen", "cap": 30,
     "desc": "从旧段落里翻出你要的那句。"},
    # 内功 (exert)
    {"id": "jingzuo", "name": "静坐", "type": "neigong", "attr": "gen", "cap": 30,
     "desc": "吐纳心神。恢复精神。"},
    {"id": "tuna", "name": "吐纳", "type": "neigong", "attr": "gen", "cap": 30,
     "desc": "调匀气息。恢复精力, 也抬内力上限。"},
    {"id": "ningshen", "name": "凝神", "type": "neigong", "attr": "wu", "cap": 30,
     "desc": "收摄心神。恢复内力。"},
    # 内功 (门派)
    {"id": "guanhai", "name": "观海诀", "type": "neigong", "attr": "wu", "cap": 40,
     "desc": "墨池剑庐内功。心神涨得快。"},
    {"id": "monengong", "name": "磨墨功", "type": "neigong", "attr": "li", "cap": 40,
     "desc": "铁砚堂内功。笔力与耐力长。"},
    {"id": "yangqijue", "name": "养气诀", "type": "neigong", "attr": "ding", "cap": 40,
     "desc": "静心斋内功。定力长, 少受打断。"},
    {"id": "taxuegong", "name": "踏雪功", "type": "neigong", "attr": "shen", "cap": 40,
     "desc": "风雪阁内功。身法长, 反应快。"},
    {"id": "tongmingjue", "name": "通明诀", "type": "neigong", "attr": "wu", "cap": 40,
     "desc": "灵犀谷内功。悟性长。"},
    {"id": "woyungong", "name": "卧云功", "type": "neigong", "attr": "gen", "cap": 40,
     "desc": "听松府内功。根骨长, 气血厚。"},
    # 绝招 (perform) —— 成功率 / 定身 / 反噬
    {"id": "yizhenjianxue", "name": "一针见血", "type": "perform", "attr": "wu",
     "cost": 8, "rate": 60, "busy": 3, "fail": 1,
     "desc": "精准点出一句关键。定身短, 反噬轻 —— 性价比最高的一招。"},
    {"id": "miaoyujieyi", "name": "妙语解颐", "type": "perform", "attr": "wu",
     "cost": 20, "rate": 45, "busy": 2, "fail": 2,
     "desc": "把难懂的一段讲得透。费内力, 但讲完能安静一阵子。"},
    {"id": "yinjingjudian", "name": "引经据典", "type": "perform", "attr": "wu",
     "cost": 15, "rate": 40, "busy": 2, "fail": 2,
     "desc": "拿别处的道理来佐证。悟性越高越稳。"},
    {"id": "wenguzhixinpfm", "name": "旧知引新", "type": "perform", "attr": "gen",
     "cost": 18, "rate": 30, "busy": 2, "fail": 3,
     "desc": "把旧段落和新的一处连起来。成了很妙, 败了很伤。"},
    {"id": "pangzhengboyin", "name": "旁征博引", "type": "perform", "attr": "shen",
     "cost": 12, "rate": 50, "busy": 3, "fail": 1,
     "desc": "举别处的例子类比。轻巧, 定身也久。"},
    {"id": "huihaopomo", "name": "挥毫泼墨", "type": "perform", "attr": "li",
     "cost": 10, "rate": 55, "busy": 4, "fail": 1,
     "desc": "一口气记下大段。笔力越足越顺手。"},
    # 门派绝招
    {"id": "yiqihcheng", "name": "一气呵成", "type": "perform", "attr": "li",
     "cost": 12, "rate": 58, "busy": 4, "fail": 1,
     "desc": "墨池剑庐绝招。长句不断, 落笔如飞。"},
    {"id": "tiehuayingou", "name": "铁画银钩", "type": "perform", "attr": "li",
     "cost": 16, "rate": 50, "busy": 3, "fail": 2,
     "desc": "铁砚堂绝招。字字见骨, 费劲但扎实。"},
    {"id": "budongrushan", "name": "不动如山", "type": "perform", "attr": "ding",
     "cost": 14, "rate": 55, "busy": 5, "fail": 1,
     "desc": "静心斋绝招。你读多久它都不催。"},
    {"id": "taxuewuchen", "name": "踏雪无痕", "type": "perform", "attr": "shen",
     "cost": 10, "rate": 60, "busy": 3, "fail": 1,
     "desc": "风雪阁绝招。旧段落召之即来。"},
    {"id": "lingxizhetong", "name": "灵犀一点通", "type": "perform", "attr": "wu",
     "cost": 15, "rate": 52, "busy": 3, "fail": 2,
     "desc": "灵犀谷绝招。接话总能接上你想说的。"},
    {"id": "songfengchangxiao", "name": "松风长啸", "type": "perform", "attr": "gen",
     "cost": 18, "rate": 48, "busy": 4, "fail": 2,
     "desc": "听松府绝招。气沉丹田, 一次能陪你很久。"},
]

SKILL_BY_ID = {s["id"]: s for s in SKILLS}

# 经脉十层 (侠客行有十二正经 + 奇经八脉; 这里取前十层, 名字沿用)
VEINS: list[dict[str, Any]] = [
    {"idx": 1, "name": "手太阴肺经", "level": 30, "minutes": 120,
     "effect": "气血上限 +5"},
    {"idx": 2, "name": "手阳明大肠经", "level": 45, "minutes": 300,
     "effect": "精力上限 +5"},
    {"idx": 3, "name": "足阳明胃经", "level": 60, "minutes": 540,
     "effect": "六项根骨各 +1"},
    {"idx": 4, "name": "足太阴脾经", "level": 75, "minutes": 840,
     "effect": "悟性 +2"},
    {"idx": 5, "name": "手少阴心经", "level": 90, "minutes": 1200,
     "effect": "心神上限 +8"},
    {"idx": 6, "name": "手太阳小肠经", "level": 105, "minutes": 1620,
     "effect": "身法 +2"},
    {"idx": 7, "name": "手少阳三焦经", "level": 125, "minutes": 2100,
     "effect": "所有绝招成功率 +3"},
    {"idx": 8, "name": "足少阳胆经", "level": 145, "minutes": 2640,
     "effect": "定力 +2"},
    {"idx": 9, "name": "冲脉", "level": 165, "minutes": 3240,
     "effect": "根骨 +2"},
    {"idx": 10, "name": "任脉", "level": 190, "minutes": 3900,
     "effect": "绝招定身 +1"},
]

# 品级 (等级 → 名号)
RANKS: list[dict[str, Any]] = [
    {"min": 1, "name": "初入江湖"},
    {"min": 5, "name": "崭露头角"},
    {"min": 10, "name": "小有名气"},
    {"min": 20, "name": "江湖新秀"},
    {"min": 35, "name": "一方好手"},
    {"min": 50, "name": "名动江湖"},
    {"min": 70, "name": "武林高手"},
    {"min": 100, "name": "一代宗师"},
    {"min": 140, "name": "绝世高手"},
    {"min": 200, "name": "陆地神仙"},
]

# 五行 (经脉通完后累积的亲和)
WUXING = ("金", "木", "水", "火", "土")

# 状态文案 (MUD 里的叫法 → 书宠语境的说法)
STAT_LABELS = {
    "hp": ("气血", "精元"),
    "spirit": ("精神", "心神"),
    "neili": ("内力", "内力"),
    "jingli": ("精力", "精力"),
}


# ------------------------------------------------------------------ 存档

def _pet_root() -> Path:
    try:
        from recto.core import paths as _paths

        return Path(_paths.PATHS.pet_dir)
    except Exception:  # noqa: BLE001 — 单独跑测试时没有 Recto 配置也要能用
        env = os.environ.get("RECTO_PET_DIR")
        if env:
            return Path(env)
        return Path(__file__).resolve().parents[2] / "data" / "pet"


CHAR_PATH = _pet_root() / "char.json"


def new_char(name: str = "小墨") -> dict[str, Any]:
    """一只刚出生的书宠 (12 岁出江湖)。"""
    return {
        "version": 1,
        "name": name or "小墨",
        "generation": 1,               # 第几代 (传承 +1)
        "lineage": [],                 # 上一代们的记录 (退隐时追加)
        "life": {"stage": "growing"},  # growing | retiring (已退隐, 等传承)
        "school": "",
        "level": 1,
        "exp": 0,
        "pot": 0,                       # 潜能点 (可分配到六项根骨)
        "attrs": {a["key"]: a["init"] for a in ATTRS},
        "born": {},                    # 先天属性 (升级偶得)
        "hp": 100, "spirit": 100, "neili": 100, "jingli": 100,
        "fame": 0,                     # 声望
        "morality": 0,                 # 心性 (-100 ~ +100)
        "loyalty": 0,                  # 门忠
        "wish": 0,                     # 觉醒点
        "minutes": 0,                  # 累计陪读分钟 (已折算进修为的那部分)
        "credited_minutes": 0,         # 已从阅读时长折算过的分钟 (幂等锚点)
        "skills": {},                  # 技能 id → 等级
        "last_skill_day": 0,           # 上次技能长进时的故事内天数 (限速用)
        "traits": [],                  # 已获性情 id
        "vein": 0,                     # 已通经脉层数
        "vein_minutes": 0,             # 本层已积累的陪读分钟
        "created_at": "",
        "updated_at": "",
    }


def load_char() -> dict[str, Any]:
    """读存档。坏文件 / 老文件 → 兜底成新人物 (不炸)。"""
    try:
        raw = json.loads(CHAR_PATH.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return new_char()
    if not isinstance(raw, dict):
        return new_char()
    ch = new_char(str(raw.get("name") or "小墨"))
    ch.update({k: v for k, v in raw.items() if k in ch})
    # 六项根骨补齐 (老存档可能缺字段)
    attrs = ch.get("attrs")
    if not isinstance(attrs, dict):
        attrs = {}
    ch["attrs"] = {a["key"]: int(attrs.get(a["key"], a["init"])) for a in ATTRS}
    if not isinstance(ch.get("skills"), dict):
        ch["skills"] = {}
    if not isinstance(ch.get("traits"), list):
        ch["traits"] = []
    if not isinstance(ch.get("born"), dict):
        ch["born"] = {}
    # 时序 / 传承 (老存档没有这几栏)
    if not isinstance(ch.get("life"), dict):
        ch["life"] = {"stage": "growing"}
    if not isinstance(ch.get("lineage"), list):
        ch["lineage"] = []
    try:
        ch["generation"] = max(1, int(ch.get("generation") or 1))
        ch["last_skill_day"] = max(0, int(ch.get("last_skill_day") or 0))
    except (TypeError, ValueError):
        ch["generation"], ch["last_skill_day"] = 1, 0
    # 自愈: credited_minutes 与 minutes 是同一个数的两个名字, 必须始终相等。
    # (第一版折算 724 分钟时, award_minutes 的单次封顶截掉了 244 分钟,
    #  但锚点已经推到 724 → 那 244 分钟永远补不回来。)
    # 这里以**小的那份**为准拉齐 (两份不等 = 有过截断或数据源被清理过)。
    #
    # 但字段缺失 ≠ 锚点是 0: 老存档 (接阅读器之前) 只有 minutes 没有锚点,
    # 若按"缺了就当 0"拉齐, 累计陪读会被清零 → 下次折算把历史重喂一遍,
    # 修为翻倍。所以缺失时**以 minutes 为锚点** (那份历史已经折算过了)。
    # 注意判的是 **raw** 里有没有: ``ch`` 是 new_char() 铺过默认值的, 永远有这一栏。
    credited = (int(ch["credited_minutes"]) if "credited_minutes" in raw
                else int(ch.get("minutes") or 0))
    actual = int(ch.get("minutes") or 0)
    same = min(credited, actual)
    ch["credited_minutes"] = same
    ch["minutes"] = same
    return ch


def save_char(ch: dict[str, Any]) -> None:
    """原子写存档 (权限 600)。"""
    from recto.core import clock

    ch = dict(ch)
    ch["updated_at"] = clock.iso_local()
    ch.setdefault("created_at", ch["updated_at"])
    p = Path(CHAR_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
    with _LOCK:
        tmp.write_text(json.dumps(ch, ensure_ascii=False, indent=2), "utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, p)
        try:
            os.chmod(p, 0o600)
        except OSError:
            pass


# ------------------------------------------------------------------ 公式

def exp_needed(level: int) -> int:
    """升到下一级需要的修为。MUD 那边是经验 + 声望 + 存款三样, 这里留两样。"""
    return max(100, int(level * level * 100))


def fame_needed(level: int) -> int:
    return int(level * 10)


def skill_cap(exp: int, attr_value: int) -> int:
    """技能等级上限 —— 侠客行原式 ``(EXP*10)^(1/3)+1`` (以 30 悟性为基准)。

    悟性不足 30 就按比例打折 (30 悟性 = 原值)。
    """
    base = int((max(0, exp) * 10) ** (1.0 / 3.0)) + 1
    return max(1, base * max(0, int(attr_value)) // 30)


def _base_attrs(ch: dict[str, Any]) -> dict[str, int]:
    """六项根骨的**基础值** —— 初始 10 + 玩家分配的点。

    门派加成不在这里: 它是**派生值**, 叛门就得立刻消失。
    (老坑: 派生值一旦写进存档, 换个门派就叠成两份 —— 第一次实现就是这么错的。)
    """
    raw = ch.get("attrs") or {}
    return {a["key"]: int(raw.get(a["key"], a["init"])) for a in ATTRS}


def effective_attrs(ch: dict[str, Any]) -> dict[str, int]:
    """六项根骨的**实际值** = 基础 + 先天 + 门派加成 (每项封顶 30)。"""
    base = _base_attrs(ch)
    born = ch.get("born") or {}
    school = SCHOOL_BY_ID.get(ch.get("school") or "")
    bonus = (school.get("bonus") or {}) if school else {}
    out: dict[str, int] = {}
    for a in ATTRS:
        k = a["key"]
        v = base.get(k, a["init"]) + int(born.get(k, 0)) + int(bonus.get(k, 0))
        out[k] = max(0, min(a["cap"], v))
    return out


def _neigong_level(ch: dict[str, Any]) -> int:
    """最高内功等级 —— 内力上限按它算 (侠客行: 内功等级 × 10)。"""
    best = 0
    for sid, lv in (ch.get("skills") or {}).items():
        sk = SKILL_BY_ID.get(sid)
        if sk and sk["type"] == "neigong":
            best = max(best, int(lv))
    return best


def derive(ch: dict[str, Any]) -> dict[str, Any]:
    """派生值 —— 全部实时算, 不入库。"""
    a = effective_attrs(ch)
    level = int(ch.get("level") or 1)
    exp = int(ch.get("exp") or 0)
    ng = _neigong_level(ch)
    vein = int(ch.get("vein") or 0)
    vein_bonus = sum(1 for v in VEINS[:vein] if v["idx"] == 3)  # 第 3 层加六项

    hp_max = 20 + a["gen"] * 3 + level * 2 + ng + vein_bonus * 2
    spirit_max = 20 + a["ding"] * 3 + a["wu"] * 2
    jingli_max = a["gen"] * 5 + a["na"] * 5
    neili_max = ng * 10 + a["gen"] * 2

    # 经脉的五行亲和 (通一层 +1, 顺次轮转)
    wuxing = {w: 0 for w in WUXING}
    for i in range(vein):
        wuxing[WUXING[i % len(WUXING)]] += 1

    # 门派加成
    school = SCHOOL_BY_ID.get(ch.get("school") or "")
    bonus = dict(school.get("bonus") or {}) if school else {}

    return {
        "hp_max": hp_max,
        "spirit_max": spirit_max,
        "jingli_max": jingli_max,
        "neili_max": neili_max,
        "neigong_level": ng,
        "wuxing": wuxing,
        "school_bonus": bonus,
        "exp_needed": exp_needed(level),
        "fame_needed": fame_needed(level),
        "attrs_total": sum(a.values()),
        "attrs_cap_total": sum(x["cap"] for x in ATTRS),
    }


def rank_of(level: int) -> dict[str, Any]:
    cur = RANKS[0]
    for r in RANKS:
        if level >= r["min"]:
            cur = r
    return cur


def perform_rate(ch: dict[str, Any], sk: dict[str, Any], skill_lv: int) -> int:
    """绝招成功率 —— 基础值 + 对应根骨 + 绝招等级, 再加经脉加成。"""
    attrs = effective_attrs(ch)
    base = int(sk.get("rate") or 40)
    v = base + int(attrs.get(sk.get("attr") or "wu", 10)) + skill_lv * 2
    if int(ch.get("vein") or 0) >= 7:
        v += 3
    return max(0, min(95, v))


def traits_info(ch: dict[str, Any]) -> dict[str, Any]:
    """性情: 已获 / 未获, 点数占用。侠客行规矩: 最多 6 种, 总分 ≤ 100。"""
    got = [t for t in (ch.get("traits") or []) if t in TRAIT_BY_ID]
    spent = sum(TRAIT_COLORS[TRAIT_BY_ID[t]["color"]]["points"] for t in got)
    return {
        "owned": got,
        "spent": spent,
        "max_count": 6,
        "max_points": 100,
        "left_count": max(0, 6 - len(got)),
        "left_points": max(0, 100 - spent),
    }


# ------------------------------------------------------------------ 操作

def alloc_point(ch: dict[str, Any], key: str, n: int = 1) -> tuple[int, dict]:
    """分配潜能点到六项根骨。

    规矩照侠客行: 每项封顶 30, 总和封顶 180 (6×30)。

    潜能点只能加在**基础值**上 —— 门派加成是派生的, 不占玩家自己的额度
    (否则入门就等于白送 2 点, 叛门又还回去, 来回刷)。
    """
    if key not in ATTR_BY_KEY:
        return 400, {"error": f"未知属性: {key}"}
    n = int(n or 1)
    if n <= 0:
        return 400, {"error": "点数必须为正"}
    if int(ch.get("pot") or 0) < n:
        return 400, {"error": "潜能点不够"}
    base = _base_attrs(ch)
    cur = base.get(key, 0)
    cap = ATTR_BY_KEY[key]["cap"]
    if cur + n > cap:
        return 400, {"error": f"{ATTR_BY_KEY[key]['name']} 已达上限 {cap}"}
    total = sum(base.values())
    if total + n > sum(x["cap"] for x in ATTRS):
        return 400, {"error": "六项总和已满"}
    base[key] = cur + n
    ch["attrs"] = base
    ch["pot"] = int(ch.get("pot") or 0) - n
    save_char(ch)
    return 200, {"ok": True, "char": view(ch)}


def choose_school(ch: dict[str, Any], school_id: str) -> tuple[int, dict]:
    """入门 / 叛门。

    规矩照侠客行: 入门给门派内功与绝招、属性加成; **叛师有代价** ——
    这里用"声望清零 + 门忠清零"表示 (真要狠一点可以扣属性, 但那太狠)。

    门派属性加成是**派生值** (见 ``effective_attrs``) —— 叛门即刻消失, 不留残根。
    但门派内功与绝招是**学过的招**, 叛门不收回 (侠客行里本门绝招是要自己废掉的,
    这里简化成"留着也能用", 免得用户一换门派就丢一整套技能)。
    """
    school = SCHOOL_BY_ID.get(school_id or "")
    if not school:
        return 400, {"error": f"未知门派: {school_id}"}
    old = ch.get("school") or ""
    if old == school_id:
        return 200, {"ok": True, "char": view(ch)}
    if old:
        ch["fame"] = 0
        ch["loyalty"] = 0
    ch["school"] = school_id
    # 门派内功与绝招入门即得 1 级
    skills = ch.get("skills") or {}
    for sid in (school.get("neigong"), school.get("perform")):
        if sid and sid in SKILL_BY_ID:
            skills.setdefault(sid, 1)
    ch["skills"] = skills
    save_char(ch)
    return 200, {"ok": True, "char": view(ch)}


def grant_trait(ch: dict[str, Any], trait_id: str) -> tuple[int, dict]:
    """获得一条性情 (觉醒)。点数/条数上限照侠客行。"""
    t = TRAIT_BY_ID.get(trait_id or "")
    if not t:
        return 400, {"error": f"未知性情: {trait_id}"}
    info = traits_info(ch)
    if trait_id in info["owned"]:
        return 200, {"ok": True, "char": view(ch)}
    pts = TRAIT_COLORS[t["color"]]["points"]
    if info["left_count"] <= 0:
        return 400, {"error": f"性情最多 {info['max_count']} 条"}
    if info["spent"] + pts > info["max_points"]:
        return 400, {"error": f"性情总分不能超过 {info['max_points']}"}
    ch["traits"] = list(info["owned"]) + [trait_id]
    save_char(ch)
    return 200, {"ok": True, "char": view(ch)}


def reset_char() -> tuple[int, dict]:
    """重开一只书宠 (存档推倒重来)。"""
    ch = new_char()
    save_char(ch)
    return 200, {"ok": True, "char": view(ch)}


def set_name(ch: dict[str, Any], name: str) -> tuple[int, dict]:
    """改名字。

    页面上的「名字」只有一处输入 —— 人格那边存的也是同一个名字。
    两处都写, 免得出现"人物卡叫 A、人格卡叫 B"这种分裂。
    """
    n = (name or "").strip()
    if not n:
        return 400, {"error": "名字不能为空"}
    if len(n) > 24:
        return 400, {"error": "名字太长"}
    ch["name"] = n
    save_char(ch)
    return 200, {"ok": True, "char": view(ch)}


# ------------------------------------------------------------------ 对外视图

def view(ch: dict[str, Any]) -> dict[str, Any]:
    """给前端 / 阅读器用的完整人物面板。"""
    d = derive(ch)
    eff = effective_attrs(ch)
    base = _base_attrs(ch)
    school = SCHOOL_BY_ID.get(ch.get("school") or "")
    rank = rank_of(int(ch.get("level") or 1))
    try:
        from recto.pet import life as _life

        life_view = _life.view(ch)
    except Exception:  # noqa: BLE001 — 时序算不出来不该拖垮人物面板
        life_view = {}

    attr_rows = []
    for a in ATTRS:
        k = a["key"]
        val = int(eff.get(k, 0))
        attr_rows.append({
            "key": k,
            "name": a["name"],
            "alias": a["alias"],
            "desc": a["desc"],
            "value": val,
            "base": int(base.get(k, 0)),
            "cap": a["cap"],
            "born": int((ch.get("born") or {}).get(k, 0)),
            "bonus": int((school.get("bonus") or {}).get(k, 0)) if school else 0,
            "can_add": int(ch.get("pot") or 0) > 0 and int(base.get(k, 0)) < a["cap"],
        })

    skills = []
    eff_attrs = effective_attrs(ch)
    for sid, lv in (ch.get("skills") or {}).items():
        sk = SKILL_BY_ID.get(sid)
        if not sk:
            continue
        lv = int(lv)
        row = {
            "id": sid,
            "name": sk["name"],
            "type": sk["type"],
            "attr": sk.get("attr", ""),
            "level": lv,
            "desc": sk.get("desc", ""),
        }
        if sk["type"] == "perform":
            row.update({
                "cost": sk.get("cost", 0),
                "rate": perform_rate(ch, sk, lv),
                "base_rate": sk.get("rate", 0),
                "busy": sk.get("busy", 0) + (1 if int(ch.get("vein") or 0) >= 10 else 0),
                "fail": sk.get("fail", 0),
            })
        else:
            row["cap"] = min(sk.get("cap", 30),
                             skill_cap(int(ch.get("exp") or 0),
                                       int(eff_attrs.get(sk.get("attr", "wu"), 10))))
        skills.append(row)
    skills.sort(key=lambda r: ({"basic": 0, "kungfu": 1, "neigong": 2, "perform": 3}.get(r["type"], 9),
                               -r["level"], r["name"]))

    trait_rows = []
    for t in TRAITS:
        owned = t["id"] in (ch.get("traits") or [])
        trait_rows.append({
            "id": t["id"], "name": t["name"], "color": t["color"],
            "color_name": TRAIT_COLORS[t["color"]]["name"],
            "points": TRAIT_COLORS[t["color"]]["points"],
            "desc": t["desc"], "owned": owned,
        })

    vein_rows = []
    minutes = int(ch.get("minutes") or 0)
    for v in VEINS:
        done = int(ch.get("vein") or 0) >= v["idx"]
        if done:
            progress = 100
        elif int(ch.get("vein") or 0) + 1 == v["idx"]:
            span = max(1, v["minutes"] - VEINS[v["idx"] - 2]["minutes"] if v["idx"] >= 2 else v["minutes"])
            base = VEINS[v["idx"] - 2]["minutes"] if v["idx"] >= 2 else 0
            progress = max(0, min(100, int((minutes - base) * 100 / span)))
        else:
            progress = 0
        vein_rows.append({
            "idx": v["idx"], "name": v["name"], "level": v["level"],
            "minutes": v["minutes"], "effect": v["effect"],
            "done": done, "progress": progress,
        })

    return {
        "name": ch.get("name") or "小墨",
        "level": int(ch.get("level") or 1),
        "exp": int(ch.get("exp") or 0),
        "exp_needed": d["exp_needed"],
        "pot": int(ch.get("pot") or 0),
        "rank": rank["name"],
        "school": {"id": school["id"], "name": school["name"], "sect": school["sect"],
                   "desc": school["desc"], "attrs": school["attrs"]} if school else None,
        "attrs": attr_rows,
        "attrs_total": d["attrs_total"],
        "attrs_cap_total": d["attrs_cap_total"],
        "stats": {
            "hp": {"label": STAT_LABELS["hp"][1], "mud": STAT_LABELS["hp"][0],
                   "cur": int(ch.get("hp") or 0), "max": d["hp_max"]},
            "spirit": {"label": STAT_LABELS["spirit"][1], "mud": STAT_LABELS["spirit"][0],
                       "cur": int(ch.get("spirit") or 0), "max": d["spirit_max"]},
            "neili": {"label": STAT_LABELS["neili"][1], "mud": STAT_LABELS["neili"][0],
                      "cur": int(ch.get("neili") or 0), "max": d["neili_max"]},
            "jingli": {"label": STAT_LABELS["jingli"][1], "mud": STAT_LABELS["jingli"][0],
                       "cur": int(ch.get("jingli") or 0), "max": d["jingli_max"]},
        },
        "fame": int(ch.get("fame") or 0),
        "fame_needed": d["fame_needed"],
        "morality": int(ch.get("morality") or 0),
        "loyalty": int(ch.get("loyalty") or 0),
        "wish": int(ch.get("wish") or 0),
        "minutes": minutes,
        "skills": skills,
        "traits": trait_rows,
        "traits_info": traits_info(ch),
        "veins": vein_rows,
        "vein": int(ch.get("vein") or 0),
        "neigong_level": d["neigong_level"],
        "wuxing": d["wuxing"],
        "school_bonus": d["school_bonus"],
        "generation": int(ch.get("generation") or 1),
        "lineage": list(ch.get("lineage") or []),
        "life": life_view,
        "created_at": ch.get("created_at") or "",
        "updated_at": ch.get("updated_at") or "",
    }


def schema() -> dict[str, Any]:
    """体系定义 —— 前端用它渲染表格 (服务端与前端**同一份表**, 免得两处对不上)。"""
    return {
        "attrs": ATTRS,
        "trait_colors": TRAIT_COLORS,
        "traits": TRAITS,
        "schools": SCHOOLS,
        "skills": SKILLS,
        "veins": VEINS,
        "ranks": RANKS,
        "wuxing": list(WUXING),
        "stat_labels": {k: {"mud": v[0], "label": v[1]} for k, v in STAT_LABELS.items()},
        "rules": [
            "六项根骨初始 10, 每项封顶 30, 总和封顶 180 —— 侠客行的老规矩。",
            "潜能点来自升级与陪读, 自由分配到六项根骨。",
            "技能上限 = (修为×10)^(1/3)+1, 按对应根骨与 30 的比例打折 (悟性 30 = 原值)。",
            "内力上限 = 最高内功等级 × 10 + 根骨 × 2 —— 内功练不上去, 内力就上不去。",
            "绝招三要素: 成功率 / 定身 (busy) / 反噬 (失败惩罚), 另加内力消耗。",
            "性情最多 6 条, 总分 ≤ 100; 四色点数 黄 40 / 红 30 / 蓝 20 / 绿 10。",
            "经脉十层, 通脉给五行亲和与属性加成。",
        ],
    }
