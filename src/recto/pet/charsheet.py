"""书宠的人物体系 —— 江湖上的那一套 (取法参考 MUD《侠客行》)。

**这一套是武侠世界的, 不是阅读的比喻。** 书宠靠陪读攒下来的东西, 长成的
是**拳脚、兵刃、内功、绝招**: 六项根骨决定它交手时有多重、多快、多耐;
技能决定它用哪一路打法; 绝招是它在遭遇里真能打出去的那几手, 有内力开销、
有成功率、有反噬。读书只是**来路**, 不是**内容**。

**借了什么**
  · 六项根骨 (臂力 / 根骨 / 悟性 / 身法 / 定力 / 耐力): 初始 10、每项上限 30
  · 潜能加点: 升级与陪读得潜能点, 自由分配
  · 状态值: 气血 / 精神 / 内力 / 精力 (当前 + 上限; 气血与内力会被交手吃掉)
  · 技能等级上限 ``(exp*10)^(1/3)+1`` (以 30 悟性为基准, 悟性越低打折)
  · 内功 (exert) 与绝招 (pfm) 分开; 内功吃内力上限
  · 绝招三要素: 成功率 / 定身 (busy) / 反噬 (fail), 另加内力消耗
  · 经脉十层, 通脉给五行亲和
  · 天生禀赋 (侠客行叫"性情") 四色点数: 黄 40 / 红 30 / 蓝 20 / 绿 10, 最多 6 条
  · 门派 / 声望 / 门忠 / 心性 / 觉醒点

**在哪用得上**: ``recto.pet.duel`` —— 每回游历会先结算一次遭遇 (交手),
它读的就是这里的 ``derive()["combat"]``、技能等级与绝招参数, 打完把气血与
内力写回存档。故事只负责把那一场照实写出来。

**存档**: ``<pet_dir>/char.json`` (权限 600)。

**派生值一律实时算, 不入库** —— 免得公式改过以后旧存档里的数字对不上。
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

# 六项根骨 —— 侠客行的老六样。读书只是书宠攒本事的**来路**, 长出来的
# 一律是江湖上的本事: 出手、挨打、起落、稳心。所以每一项都直接对应
# 交手时会用到的那个数 (命中 / 闪避 / 伤害 / 气血 / 内力)。
ATTRS: list[dict[str, Any]] = [
    {
        "key": "li", "name": "臂力", "alias": "劲道", "init": 10, "cap": 30,
        "desc": "拳、刀、枪上的劲。交手时决定伤害轻重, 也决定托不托得住重兵器。",
    },
    {
        "key": "gen", "name": "根骨", "alias": "筋骨", "init": 10, "cap": 30,
        "desc": "气血底子与筋骨强度。气血上限、挨打的耐受、重伤还能撑几回合都看它。",
    },
    {
        "key": "wu", "name": "悟性", "alias": "悟性", "init": 10, "cap": 30,
        "desc": "一点就透。学招快、招式上限高, 也更容易看出对手招式里的破绽。",
    },
    {
        "key": "shen", "name": "身法", "alias": "身法", "init": 10, "cap": 30,
        "desc": "起落进退。先手、闪避、追人、脱身都靠它。",
    },
    {
        "key": "ding", "name": "定力", "alias": "定力", "init": 10, "cap": 30,
        "desc": "心神不乱。中招之后还能稳住招式, 抗摄魂、抗迷药、抗人激将。",
    },
    {
        "key": "na", "name": "耐力", "alias": "耐力", "init": 10, "cap": 30,
        "desc": "久战不疲。内力回得快, 长途赶路不趴下, 一夜未睡也还能出手。",
    },
]

ATTR_KEYS = [a["key"] for a in ATTRS]
ATTR_BY_KEY = {a["key"]: a for a in ATTRS}

# 品质: 侠客行里的名号 (黄 40 / 红 30 / 蓝 20 / 绿 10)
TRAIT_COLORS: dict[str, dict[str, Any]] = {
    "gold": {"name": "上品", "points": 40, "desc": "罕见。一条抵十条。"},
    "red": {"name": "良品", "points": 30, "desc": "难得。天生的筋骨。"},
    "blue": {"name": "常品", "points": 20, "desc": "常见。也能改命。"},
    "green": {"name": "下品", "points": 10, "desc": "锦上添花。"},
}

# 天生禀赋 (侠客行叫"性情")。每条都带 ``mods`` —— 这些不是好看的形容词,
# 直接进 ``derive()["combat"]``: 命中 / 闪避 / 伤害 / 减伤 / 气血 / 内力。
# mods 的键:
#   attr {项: +n}   直接加在根骨上 (与门派加成同层, 派生不入库)
#   hit/dodge/speed/parry/damage/crit   交手数值
#   take_pct        受到的伤害 ±% (负=减伤)
#   neili_pct/hp_pct 内力 / 气血上限 ±%
#   skill_cap_pct   技能上限 ±%
#   fail            绝招反噬 ±级
TRAITS: list[dict[str, Any]] = [
    # 上品 (40)
    {"id": "gugqingqi", "name": "骨骼清奇", "color": "gold",
     "desc": "筋络天生顺, 学招比别人快一截 (技能上限 +12%)。",
     "mods": {"skill_cap_pct": 12}},
    {"id": "tianshengshenli", "name": "天生神力", "color": "gold",
     "desc": "生下来手上有桩。臂力 +3, 出手就是重的。",
     "mods": {"attr": {"li": 3}}},
    {"id": "tieguqingpi", "name": "铁骨铜皮", "color": "gold",
     "desc": "皮肉筋骨像熟过的铁。挨打减伤一成半。",
     "mods": {"take_pct": -15}},
    {"id": "xinruzhishui", "name": "心如止水", "color": "gold",
     "desc": "心不动。定力 +3, 绝招反噬还轻一级。",
     "mods": {"attr": {"ding": 3}, "fail": -1}},
    # 良品 (30)
    {"id": "muliguoren", "name": "目力过人", "color": "red",
     "desc": "十丈外看得见剑尖的毛刺。命中 +6。",
     "mods": {"hit": 6}},
    {"id": "shenqingruyan", "name": "身轻如燕", "color": "red",
     "desc": "落脚没声。闪避 +6。",
     "mods": {"dodge": 6}},
    {"id": "neiximianchang", "name": "内息绵长", "color": "red",
     "desc": "一口气能转很久。内力上限 +20%。",
     "mods": {"neili_pct": 20}},
    {"id": "chushourudian", "name": "出手如电", "color": "red",
     "desc": "刀出鞘比人眨眼还快。先手 +8。",
     "mods": {"speed": 8}},
    # 常品 (20)
    {"id": "wenzhawenda", "name": "稳扎稳打", "color": "blue",
     "desc": "下盘沉, 招架不散。招架 +8。",
     "mods": {"parry": 8}},
    {"id": "jinguqiangjian", "name": "筋骨强健", "color": "blue",
     "desc": "经得住摔打。气血上限 +12%。",
     "mods": {"hp_pct": 12}},
    {"id": "wuxingtiancheng", "name": "悟性天成", "color": "blue",
     "desc": "听一遍就会。悟性 +2。",
     "mods": {"attr": {"wu": 2}}},
    {"id": "ercongmuming", "name": "耳聪目明", "color": "blue",
     "desc": "背后有人摸上来也听得见。命中 +3、闪避 +3。",
     "mods": {"hit": 3, "dodge": 3}},
    # 下品 (10)
    {"id": "dandaxixin", "name": "胆大心细", "color": "green",
     "desc": "敢下手, 也看得准。暴击 +4。",
     "mods": {"crit": 4}},
    {"id": "sichanlanda", "name": "死缠烂打", "color": "green",
     "desc": "见血之后反而更狠。气血低于三成时伤害 +12%。",
     "mods": {"low_hp_damage": 12}},
    {"id": "yanjishoukuai", "name": "眼疾手快", "color": "green",
     "desc": "手比眼快半步。先手 +3。",
     "mods": {"speed": 3}},
    {"id": "haoyonghaodou", "name": "好勇斗狠", "color": "green",
     "desc": "下手不留余力。伤害 +4, 却也容易结仇。",
     "mods": {"damage": 4}},
]

TRAIT_BY_ID = {t["id"]: t for t in TRAITS}

# 门派 —— 自创, 各有本门内功与一路绝招。``attrs`` 是长项, ``bonus`` 是入门加成
# (派生值: 叛门即刻收回)。
SCHOOLS: list[dict[str, Any]] = [
    {
        "id": "mochi", "name": "墨池剑庐", "sect": "外门",
        "attrs": ["li", "shen"], "bonus": {"li": 2},
        "neigong": "guanhai", "perform": "yiqihcheng", "weapon": "jianfa",
        "desc": "以剑入道的剑庐。出手快、剑走轻灵, 一手快剑正面难挡。",
    },
    {
        "id": "tieyan", "name": "铁砚堂", "sect": "外门",
        "attrs": ["li", "na"], "bonus": {"na": 2},
        "neigong": "monengong", "perform": "tiehuayingou", "weapon": "daofa",
        "desc": "苦功路数的刀堂。刀沉势大, 挨得住也砍得实。",
    },
    {
        "id": "jingxin", "name": "静心斋", "sect": "内门",
        "attrs": ["ding", "gen"], "bonus": {"ding": 2},
        "neigong": "yangqijue", "perform": "budongrushan", "weapon": "zhangfa",
        "desc": "坐得住的功夫。掌法守多攻少, 心神厚, 久战不散。",
    },
    {
        "id": "fengxue", "name": "风雪阁", "sect": "内门",
        "attrs": ["shen", "na"], "bonus": {"shen": 2},
        "neigong": "taxuegong", "perform": "taxuewuchen", "weapon": "tuifa",
        "extras": ["qinggong"],
        "desc": "轻灵一路。踏雪无痕, 追得上也逃得掉。",
    },
    {
        "id": "lingxi", "name": "灵犀谷", "sect": "内门",
        "attrs": ["wu", "shen"], "bonus": {"wu": 2},
        "neigong": "tongmingjue", "perform": "lingxizhetong", "weapon": "zhifa",
        "desc": "以指代剑。指法认穴极准, 一手点穴就能让对手动不了。",
    },
    {
        "id": "tingsong", "name": "听松府", "sect": "内门",
        "attrs": ["gen", "ding"], "bonus": {"gen": 2},
        "neigong": "woyungong", "perform": "songfengchangxiao", "weapon": "quanfa",
        "desc": "养气为主的拳门。气血厚、内息长, 能熬到对手先喘。",
    },
]

SCHOOL_BY_ID = {s["id"]: s for s in SCHOOLS}

# 技能表。
#   type: basic 基本功 / weapon 兵器 / fist 拳脚 / qinggong 轻功 /
#         neigong 内功 / perform 绝招 / art 杂学
#   attr: 决定上限与成功率的那项根骨
#   use : 它在**交手或赶路**时具体干什么 —— 写故事与结算都按这一句来
#
# 绝招 (perform) 的三要素照侠客行: ``rate`` 基础成功率 / ``busy`` 成功让对方
# 定身几回合 / ``fail`` 失败反噬几级; ``cost`` 是内力开销; ``effect`` 决定它
# 在交手里的作用 (damage 伤害 / control 控住 / defend 守 / evade 身法 /
# heal 回气血 / buff 涨自己)。
SKILLS: list[dict[str, Any]] = [
    # ── 基本功
    {"id": "jibenneigong", "name": "基本内功", "type": "basic", "attr": "gen", "cap": 20,
     "desc": "吐纳的底子。内力上限与回气速度的根。",
     "use": "打坐回气; 内力上限的基数。"},
    {"id": "jibenquanjiao", "name": "基本拳脚", "type": "basic", "attr": "li", "cap": 20,
     "desc": "空手过招的底子。手里没家伙时全靠它。",
     "use": "空手交手不落下风; 被缴了兵器也有得打。"},
    {"id": "jibenzhaojia", "name": "基本招架", "type": "basic", "attr": "gen", "cap": 20,
     "desc": "卸力、格挡、不让人打实。",
     "use": "交手时卸掉一部分来势, 减伤。"},
    {"id": "jibenshenfa", "name": "基本身法", "type": "basic", "attr": "shen", "cap": 20,
     "desc": "起落、转身、错步的底子。",
     "use": "闪避与先手的底子。"},
    {"id": "jibenyinggong", "name": "基本硬功", "type": "basic", "attr": "gen", "cap": 20,
     "desc": "挨打不伤筋骨的功夫。",
     "use": "硬吃一击也不倒; 减伤。"},
    # ── 兵器 (武学)
    {"id": "jianfa", "name": "剑法", "type": "weapon", "attr": "shen", "cap": 30,
     "desc": "轻灵一路的兵刃功夫, 走的是快与准。",
     "use": "剑在手时的主攻手段, 出手快、破绽小。"},
    {"id": "daofa", "name": "刀法", "type": "weapon", "attr": "li", "cap": 30,
     "desc": "沉、猛、直来直去, 一刀是一刀。",
     "use": "刀在手时的主攻手段, 伤害最重。"},
    {"id": "gunfa", "name": "棍法", "type": "weapon", "attr": "li", "cap": 30,
     "desc": "长兵扫打, 一丈之内把人逼开。",
     "use": "以长打短, 让对手近不了身。"},
    # ── 拳脚
    {"id": "quanfa", "name": "拳法", "type": "fist", "attr": "li", "cap": 30,
     "desc": "近身硬打, 拳拳到肉。",
     "use": "贴身缠斗时的主攻手段。"},
    {"id": "zhangfa", "name": "掌法", "type": "fist", "attr": "wu", "cap": 30,
     "desc": "以掌代兵, 推、按、劈、切, 讲的是听劲。",
     "use": "化解来势、推开对手, 攻守都成。"},
    {"id": "zhifa", "name": "指法", "type": "fist", "attr": "wu", "cap": 30,
     "desc": "认穴打点, 一指下去对手半边就麻。",
     "use": "点穴与破招, 常用来控住对手。"},
    {"id": "tuifa", "name": "腿法", "type": "fist", "attr": "shen", "cap": 30,
     "desc": "下三路最难防。踢、扫、蹬、撩。",
     "use": "破人下盘、绕后偷袭。"},
    # ── 轻功
    {"id": "qinggong", "name": "轻功", "type": "qinggong", "attr": "shen", "cap": 30,
     "desc": "登高、越墙、踏水、追人。",
     "use": "追上逃的人、甩掉追的人; 交手时抢身位。"},
    # ── 内功 (通用 + 门派)
    {"id": "guanhai", "name": "观海诀", "type": "neigong", "attr": "wu", "cap": 40,
     "desc": "墨池剑庐内功。气走轻灵, 剑上更快一分。",
     "use": "回内力快; 剑法出手加成。"},
    {"id": "monengong", "name": "磨墨功", "type": "neigong", "attr": "li", "cap": 40,
     "desc": "铁砚堂内功。一层一层压下力来, 越练手越沉。",
     "use": "内力上限厚; 刀法伤害加成。"},
    {"id": "yangqijue", "name": "养气诀", "type": "neigong", "attr": "ding", "cap": 40,
     "desc": "静心斋内功。气息绵长, 心神不散。",
     "use": "抗乱神; 挨打后不易被破招。"},
    {"id": "taxuegong", "name": "踏雪功", "type": "neigong", "attr": "shen", "cap": 40,
     "desc": "风雪阁内功。落雪不陷, 一身轻。",
     "use": "闪避与先手加成; 赶路快。"},
    {"id": "tongmingjue", "name": "通明诀", "type": "neigong", "attr": "wu", "cap": 40,
     "desc": "灵犀谷内功。心如明镜, 照见对手招式来路。",
     "use": "识破破绽, 提高命中。"},
    {"id": "woyungong", "name": "卧云功", "type": "neigong", "attr": "gen", "cap": 40,
     "desc": "听松府内功。气血厚, 躺云上一样能撑。",
     "use": "气血上限与恢复加成。"},
    # ── 绝招 (通用)
    {"id": "yizhenjianxue", "name": "一针见血", "type": "perform", "attr": "wu",
     "cost": 8, "rate": 62, "busy": 2, "fail": 1, "effect": "damage",
     "desc": "指上找破绽, 一点就透。耗得少、准头高。",
     "use": "低耗快招, 专门戳破绽。"},
    {"id": "dianxuejiemai", "name": "点穴截脉", "type": "perform", "attr": "shen",
     "cost": 14, "rate": 46, "busy": 4, "fail": 2, "effect": "control",
     "desc": "点中穴道, 对手一时动不了。成败全看身法准不准。",
     "use": "控住对手 4 回合 —— 打不过时用来脱身。"},
    {"id": "tieqiaolianjiang", "name": "铁桥拦江", "type": "perform", "attr": "ding",
     "cost": 10, "rate": 66, "busy": 2, "fail": 1, "effect": "defend",
     "desc": "横身一挡, 硬吃一击也不退。",
     "use": "守势: 这一回合减伤, 撑住等援手。"},
    {"id": "huifengfuliu", "name": "回风拂柳", "type": "perform", "attr": "shen",
     "cost": 12, "rate": 56, "busy": 2, "fail": 1, "effect": "evade",
     "desc": "侧身让过来势, 顺手还一下。",
     "use": "闪身避开, 同时回敬一记。"},
    {"id": "bafangfengyu", "name": "八方风雨", "type": "perform", "attr": "li",
     "cost": 18, "rate": 50, "busy": 2, "fail": 2, "effect": "damage",
     "desc": "一口气连打数下, 四面八方都是招。",
     "use": "被围住时开道; 消耗大, 一次定胜负。"},
    {"id": "lipihuashan", "name": "力劈华山", "type": "perform", "attr": "li",
     "cost": 16, "rate": 42, "busy": 3, "fail": 3, "effect": "damage",
     "desc": "全身之力压在一击上。中了就结束, 不中就空门大开。",
     "use": "拼命的一击: 高伤害, 反噬也重。"},
    # ── 绝招 (门派)
    {"id": "yiqihcheng", "name": "一气呵成", "type": "perform", "attr": "shen",
     "cost": 12, "rate": 58, "busy": 3, "fail": 1, "effect": "damage",
     "desc": "墨池剑庐剑招。七剑连成一线, 中间不留缝。",
     "use": "快剑连击, 让对方喘不上气。"},
    {"id": "tiehuayingou", "name": "铁画银钩", "type": "perform", "attr": "li",
     "cost": 16, "rate": 52, "busy": 3, "fail": 2, "effect": "damage",
     "desc": "铁砚堂刀招。一横一竖, 断人兵刃。",
     "use": "重刀硬劈, 常能震脱对手兵器。"},
    {"id": "budongrushan", "name": "不动如山", "type": "perform", "attr": "ding",
     "cost": 12, "rate": 62, "busy": 3, "fail": 1, "effect": "defend",
     "desc": "静心斋守招。往那一站, 谁也推不动。",
     "use": "守到底: 减伤并把对手逼退半步。"},
    {"id": "taxuewuchen", "name": "踏雪无痕", "type": "perform", "attr": "shen",
     "cost": 10, "rate": 60, "busy": 3, "fail": 1, "effect": "evade",
     "desc": "风雪阁身法。一步出去, 雪上不留印。",
     "use": "抽身、绕后、抢身位, 也能干脆走掉。"},
    {"id": "lingxizhetong", "name": "灵犀一指", "type": "perform", "attr": "wu",
     "cost": 15, "rate": 50, "busy": 4, "fail": 2, "effect": "control",
     "desc": "灵犀谷指招。看准一处, 一指点下去。",
     "use": "认穴极准, 控住对手的招。"},
    {"id": "songfengchangxiao", "name": "松风长吟", "type": "perform", "attr": "gen",
     "cost": 18, "rate": 48, "busy": 4, "fail": 2, "effect": "control",
     "desc": "听松府拳掌。掌风里带着内息, 打得人胸口发闷。",
     "use": "以内力压人, 让对手一两回合用不上内力。"},
    # ── 杂学
    {"id": "yishu", "name": "医术", "type": "art", "attr": "wu", "cap": 25,
     "desc": "接骨、止血、认药。江湖上走远路得会一点。",
     "use": "交手之后疗伤, 多回一点气血; 也认得出毒。"},
    {"id": "guanqi", "name": "观气", "type": "art", "attr": "wu", "cap": 25,
     "desc": "看人下盘、听人呼吸, 就知道他下一步往哪走。",
     "use": "交手前看破对手虚实: 提高命中、少挨打。"},
    {"id": "zhuizong", "name": "追踪", "type": "art", "attr": "shen", "cap": 25,
     "desc": "看脚印、看草倒的方向、看灰落在哪。",
     "use": "追人、找人、不被甩掉。"},
]

SKILL_BY_ID = {s["id"]: s for s in SKILLS}

# 老存档里的旧技能 id (读书路数) → 新体系。只保留能对上的, 其余直接退休。
# 为什么不一并删掉: 用户那只书宠的「读书悟道」已经陪读养到 10 级,
# 那是它靠时间攒下来的东西, 不能因为改名就清零。
LEGACY_SKILL_MAP: dict[str, str] = {
    "dushuwudao": "jibenneigong",   # 读书悟道 (陪读主路) → 吐纳的底子
    "jingzuo": "jibenneigong",
    "tuna": "jibenneigong",
    "ningshen": "jibenneigong",
    "yanli": "guanqi",
    "erli": "guanqi",
    "jingxinli": "jibenzhaojia",
    "shouji": "jibenzhaojia",
    "chaojing": "jibenzhaojia",
    "pianzhuodianjing": "guanqi",
    "zhuiqinsuyuan": "zhuizong",
    "huifushidian": "zhuizong",
    "miaoyujieyi": "yizhenjianxue",
    "yinjingjudian": "yizhenjianxue",
    "wenguzhixinpfm": "huifengfuliu",
    "pangzhengboyin": "huifengfuliu",
    "huihaopomo": "bafangfengyu",
}

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
        # 复位/转生那一刻, 阅读器那边的累计时长是多少 —— 作为新的零点。
        # 没有它, 复位后下一轮心跳会立刻把**全部历史时长**重新折算回来,
        # 复位等于没发生 (真踩过: 756 分钟立刻回到 12 岁第 127 日)。
        "reading_baseline": 0,
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
    # 老存档里的"读书路数"技能迁到新体系 (见 LEGACY_SKILL_MAP);
    # 对不上的直接退休 —— 它们本来就不属于这个江湖。
    if any(str(k) in LEGACY_SKILL_MAP for k in ch["skills"]):
        moved: dict[str, int] = {}
        for sid, lv in ch["skills"].items():
            sid = str(sid)
            tgt = sid if sid in SKILL_BY_ID else LEGACY_SKILL_MAP.get(sid)
            if not tgt:
                continue
            moved[tgt] = max(int(moved.get(tgt, 0)), int(lv or 0))
        ch["skills"] = moved
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
        ch["reading_baseline"] = max(0, int(ch.get("reading_baseline") or 0))
    except (TypeError, ValueError):
        ch["generation"], ch["last_skill_day"], ch["reading_baseline"] = 1, 0, 0
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
    # 有门派却没有本门兵器 (老存档入门时没发) → 补一级, 与"入门即得"同口径
    _sch = SCHOOL_BY_ID.get(str(ch.get("school") or ""))
    if _sch:
        for _sid in (_sch.get("weapon"), *(_sch.get("extras") or [])):
            if _sid in SKILL_BY_ID:
                ch["skills"].setdefault(str(_sid), 1)
    # 气血 / 内力 / 精神 / 精力 夹到上限 (老存档里的 100 可能超过上限)
    r = resources(ch)
    for key in ("hp", "neili", "spirit", "jingli"):
        ch[key] = r[key]["cur"]
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
    """六项根骨的**实际值** = 基础 + 先天 + 门派加成 + 禀赋加成 (每项封顶 30)。"""
    base = _base_attrs(ch)
    born = ch.get("born") or {}
    school = SCHOOL_BY_ID.get(ch.get("school") or "")
    bonus = (school.get("bonus") or {}) if school else {}
    tattr = (trait_mods(ch).get("attr") or {})
    out: dict[str, int] = {}
    for a in ATTRS:
        k = a["key"]
        v = (base.get(k, a["init"]) + int(born.get(k, 0)) + int(bonus.get(k, 0))
             + int(tattr.get(k, 0)))
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


# 交手时算"主修"的几类技能 (兵器 / 拳脚)。轻功与内功单独加成。
COMBAT_TYPES = ("weapon", "fist")

#: 各门兵器 / 拳脚使的伤害骰 —— 剑快、刀重、指轻、棍沉。
#: 这是 d20 那一层的口径 (交手时真掷), 与 ``combat["damage"]`` 那个旧数并存:
#: 旧数是给人看的"份量", 这一张是掷骰用的。
WEAPON_DICE: dict[str, tuple[int, int]] = {
    "jianfa": (1, 8), "daofa": (1, 10), "gunfa": (1, 10),
    "quanfa": (1, 6), "zhangfa": (1, 6), "zhifa": (1, 4), "tuifa": (1, 8),
}
DEFAULT_DICE = (1, 6)

#: 一回合 (约十来个故事日) 打坐能回的气血 / 内力比例 —— 上限封在 100%
REST_HP_PCT = 0.18
REST_NEILI_PCT = 0.45


def trait_mods(ch: dict[str, Any]) -> dict[str, Any]:
    """禀赋带来的数值加成 (派生, 不入库)。

    禀赋不是好看的形容词: ``mods`` 里的每一项都真的进 ``derive()["combat"]``
    或上限公式。这里是唯一的汇总点 —— 别在别处再各算一遍。
    """
    out: dict[str, Any] = {
        "attr": {}, "hit": 0, "dodge": 0, "speed": 0, "parry": 0,
        "damage": 0, "crit": 0, "take_pct": 0, "neili_pct": 0, "hp_pct": 0,
        "skill_cap_pct": 0, "fail": 0, "low_hp_damage": 0,
    }
    for tid in (ch.get("traits") or []):
        t = TRAIT_BY_ID.get(str(tid))
        if not t:
            continue
        for k, v in (t.get("mods") or {}).items():
            if k == "attr":
                for ak, av in (v or {}).items():
                    out["attr"][ak] = int(out["attr"].get(ak, 0)) + int(av)
            elif k in out:
                out[k] = int(out[k]) + int(v)
    return out


def skill_level(ch: dict[str, Any], sid: str) -> int:
    return int((ch.get("skills") or {}).get(sid) or 0)


def main_skill(ch: dict[str, Any]) -> dict[str, Any] | None:
    """主修的那一路 —— 等级最高的兵器 / 拳脚。交手时用它判定命中与伤害。"""
    best: dict[str, Any] | None = None
    for sid, lv in (ch.get("skills") or {}).items():
        sk = SKILL_BY_ID.get(str(sid))
        if not sk or sk.get("type") not in COMBAT_TYPES:
            continue
        if best is None or int(lv) > int(best["level"]):
            best = {"id": str(sid), "name": sk["name"], "type": sk["type"],
                    "level": int(lv), "attr": sk.get("attr") or "li"}
    return best


def _attr_name(key: str) -> str:
    return str((ATTR_BY_KEY.get(key) or {}).get("name") or key)


def d20_stats(ch: dict[str, Any]) -> dict[str, Any]:
    """交手用的 d20 数值 —— 护体 / 命中加值 / 伤害骰 / 先攻 / 豁免 / 绝招加值。

    **全部实时算, 不入库。** 每一项都带拆账 (名字 + 分值 + 来源), 战报与页面
    都照它展示 —— 这样"为什么这一下中了"能追溯到根骨与技能。

    口径 (与 ``derive()`` 那套数并存, 各管各的):
      · 护体 AC = 10 + 根骨加值 + 基本招架 + 轻功的一小截 + 禀赋招架
      · 命中加值 = 主修对应根骨的加值 + 主修技能加值 + 禀赋命中
      · 伤害 = 兵刃骰 + 臂力加值×2 + 主修技能加值×2 + 8; 卸力 = 防御 // 3
      · 先攻 = 身法加值 + 轻功的一半 + 禀赋先手
      · 豁免 = 根骨 / 定力 / 身法 (各带对应基本功)
      · 绝招加值 = 绝招对应根骨的加值 + 招式等级 + 内力档次 (最多 +3)
    """
    from recto.pet import d20

    a = effective_attrs(ch)
    tm = trait_mods(ch)
    main = main_skill(ch)
    derived = derive(ch)
    combat = derived["combat"]

    zhaojia = skill_level(ch, "jibenzhaojia")
    qg = skill_level(ch, "qinggong") + skill_level(ch, "jibenshenfa")
    parry_mod = d20.attr_mod(a["gen"]) + d20.skill_mod(zhaojia) + int(tm.get("parry") or 0) // 4
    ac = 10 + parry_mod + d20.skill_mod(qg) // 3
    ac_parts = d20.mods_of(
        ("根骨 " + str(a["gen"]), d20.attr_mod(a["gen"]), "attr"),
        ("基本招架 " + str(zhaojia) + " 级", d20.skill_mod(zhaojia), "skill"),
        ("禀赋招架", int(tm.get("parry") or 0) // 4, "trait"),
        ("轻功 " + str(qg) + " 级", d20.skill_mod(qg) // 3, "skill"),
    )

    main_lv = int(main["level"]) if main else 0
    main_attr = str(main.get("attr") or "li") if main else "li"
    attack_parts = d20.mods_of(
        (_attr_name(main_attr) + " " + str(a[main_attr]), d20.attr_mod(a[main_attr]), "attr"),
        ((main["name"] + " " + str(main_lv) + " 级") if main else "主修 无",
         d20.skill_mod(main_lv), "skill"),
        ("禀赋命中", int(tm.get("hit") or 0) // 3, "trait"),
    )
    attack = sum(int(m["value"]) for m in attack_parts)

    dice = WEAPON_DICE.get(str(main["id"]) if main else "", DEFAULT_DICE)
    flat = (8 + d20.attr_mod(a["li"]) * 2 + d20.skill_mod(main_lv) * 2
            + int(tm.get("damage") or 0) // 2)
    pierce = int(combat.get("defense") or 0) // 3

    init_parts = d20.mods_of(
        ("身法 " + str(a["shen"]), d20.attr_mod(a["shen"]), "attr"),
        ("轻功 " + str(qg) + " 级", d20.skill_mod(qg) // 2, "skill"),
        ("禀赋先手", int(tm.get("speed") or 0) // 4, "trait"),
    )
    neili_tier = min(3, int(derived["neili_max"]) // 40)

    def save(attr_key: str, sid: str) -> dict[str, Any]:
        lv = skill_level(ch, sid)
        sname = str((SKILL_BY_ID.get(sid) or {}).get("name") or sid)
        parts = d20.mods_of(
            (_attr_name(attr_key) + " " + str(a[attr_key]), d20.attr_mod(a[attr_key]), "attr"),
            (sname + " " + str(lv) + " 级", d20.skill_mod(lv) // 2, "skill"),
        )
        return {"total": sum(int(m["value"]) for m in parts), "parts": parts}

    return {
        "ac": int(ac),
        "ac_parts": ac_parts,
        "attack": int(attack),
        "attack_parts": attack_parts,
        "damage": {"count": int(dice[0]), "sides": int(dice[1]), "flat": int(flat),
                   "text": str(dice[0]) + "d" + str(dice[1]) + "+" + str(flat),
                   "weapon": (main["name"] if main else "空手")},
        "pierce": int(pierce),
        "init": {"total": sum(int(m["value"]) for m in init_parts), "parts": init_parts},
        "saves": {
            "gen": save("gen", "jibenneigong"),
            "ding": save("ding", "jibenzhaojia"),
            "shen": save("shen", "jibenshenfa"),
        },
        "perform": {"neili_tier": int(neili_tier),
                    "note": "绝招加值 = 招式对应根骨 + 招式等级 + 内力档次"},
        "crit_range": int(20 - min(3, int(combat.get("crit") or 0) // 5)),
        "main": dict(main) if main else None,
    }


def cap_for(ch: dict[str, Any], sk: dict[str, Any]) -> int:
    """某门功夫的等级上限 —— 修为/悟性决定基础值, 禀赋再加减百分比。"""
    base = skill_cap(int(ch.get("exp") or 0),
                     int(effective_attrs(ch).get(sk.get("attr") or "wu", 10)))
    pct = int(trait_mods(ch).get("skill_cap_pct") or 0)
    if pct:
        base = int(round(base * (100 + pct) / 100.0))
    if sk.get("cap"):
        base = min(base, int(sk["cap"]))
    return max(1, base)


def resources(ch: dict[str, Any]) -> dict[str, dict[str, int]]:
    """气血 / 内力 / 精神 / 精力 的当前与上限。

    **当前值会被交手吃掉**, 所以存在存档里; 上限永远实时算。
    存档里没有这一栏 (老存档 / 新人物) → 当作满值。
    """
    d = derive(ch)
    out: dict[str, dict[str, int]] = {}
    for key, mx in (("hp", d["hp_max"]), ("neili", d["neili_max"]),
                    ("spirit", d["spirit_max"]), ("jingli", d["jingli_max"])):
        raw = ch.get(key)
        cur = mx if raw is None else int(raw)
        out[key] = {"cur": max(0, min(int(mx), cur)), "max": int(mx)}
    return out


def restore(ch: dict[str, Any], days: float = 1.0, *, hp_pct: float = REST_HP_PCT,
            neili_pct: float = REST_NEILI_PCT) -> dict[str, int]:
    """打坐 / 过夜 —— 按比例回气血与内力 (就地改 ``ch`` 的当前值)。"""
    r = resources(ch)
    hp_max, neili_max = r["hp"]["max"], r["neili"]["max"]
    hp = min(hp_max, r["hp"]["cur"] + int(round(hp_max * hp_pct * max(0.0, days))))
    neili = min(neili_max, r["neili"]["cur"] + int(round(neili_max * neili_pct * max(0.0, days))))
    ch["hp"], ch["neili"] = hp, neili
    ch.setdefault("spirit", r["spirit"]["cur"])
    ch.setdefault("jingli", r["jingli"]["cur"])
    return {"hp": hp - r["hp"]["cur"], "neili": neili - r["neili"]["cur"]}


def derive(ch: dict[str, Any]) -> dict[str, Any]:
    """派生值 —— 全部实时算, 不入库。"""
    a = effective_attrs(ch)
    level = int(ch.get("level") or 1)
    exp = int(ch.get("exp") or 0)
    ng = _neigong_level(ch)
    vein = int(ch.get("vein") or 0)
    vein_bonus = sum(1 for v in VEINS[:vein] if v["idx"] == 3)  # 第 3 层加六项

    tm = trait_mods(ch)
    hp_max = 20 + a["gen"] * 3 + level * 2 + ng + vein_bonus * 2
    spirit_max = 20 + a["ding"] * 3 + a["wu"] * 2
    jingli_max = a["gen"] * 5 + a["na"] * 5
    neili_max = ng * 10 + a["gen"] * 2
    if tm.get("hp_pct"):
        hp_max = hp_max * (100 + int(tm["hp_pct"])) / 100.0
    if tm.get("neili_pct"):
        neili_max = neili_max * (100 + int(tm["neili_pct"])) / 100.0

    # ── 交手数值 (故事里的"遭遇"读的就是这几个数)
    main = main_skill(ch)
    main_lv = int(main["level"]) if main else 0
    qg_lv = skill_level(ch, "qinggong") + skill_level(ch, "jibenshenfa")
    hard_lv = skill_level(ch, "jibenyinggong")
    parry_lv = skill_level(ch, "jibenzhaojia") + skill_level(ch, "jibenneigong")
    guanqi_lv = skill_level(ch, "guanqi")
    hit = 40 + a["shen"] * 1.2 + main_lv * 1.6 + guanqi_lv * 0.8 + tm.get("hit", 0)
    dodge = 30 + a["shen"] * 1.2 + qg_lv * 1.1 + tm.get("dodge", 0)
    parry = 20 + a["gen"] * 1.0 + parry_lv * 1.2 + tm.get("parry", 0)
    damage = 8 + a["li"] * 1.2 + main_lv * 2.0 + tm.get("damage", 0)
    defense = 4 + a["gen"] * 0.8 + hard_lv * 1.2
    speed = a["shen"] * 1.0 + qg_lv * 0.5 + tm.get("speed", 0)
    crit = 5 + a["shen"] * 0.25 + tm.get("crit", 0)
    take_pct = max(-40, min(40, int(tm.get("take_pct", 0))))

    # 经脉的五行亲和 (通一层 +1, 顺次轮转)
    wuxing = {w: 0 for w in WUXING}
    for i in range(vein):
        wuxing[WUXING[i % len(WUXING)]] += 1

    # 门派加成
    school = SCHOOL_BY_ID.get(ch.get("school") or "")
    bonus = dict(school.get("bonus") or {}) if school else {}

    return {
        "hp_max": int(round(hp_max)),
        "spirit_max": int(round(spirit_max)),
        "jingli_max": int(round(jingli_max)),
        "neili_max": int(round(neili_max)),
        "combat": {
            "hit": int(round(hit)), "dodge": int(round(dodge)),
            "parry": int(round(parry)), "damage": int(round(damage)),
            "defense": int(round(defense)), "speed": int(round(speed)),
            "crit": int(round(crit)), "take_pct": take_pct,
            "low_hp_damage": int(tm.get("low_hp_damage") or 0),
            "main": dict(main) if main else None,
            "qinggong": skill_level(ch, "qinggong"),
            "neigong": ng,
        },
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


def perform_fail(ch: dict[str, Any], sk: dict[str, Any]) -> int:
    """绝招失败时的反噬级数 (禀赋可以让它轻一级, 最少 0)。"""
    return max(0, int(sk.get("fail") or 0) + int(trait_mods(ch).get("fail") or 0))


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
    # 门派内功 / 本门兵器 / 绝招 入门即得 1 级
    skills = ch.get("skills") or {}
    for sid in (school.get("neigong"), school.get("weapon"), school.get("perform"),
                *(school.get("extras") or [])):
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
    res = resources(ch)
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
        row["use"] = sk.get("use", "")
        if sk["type"] == "perform":
            row.update({
                "cost": sk.get("cost", 0),
                "rate": perform_rate(ch, sk, lv),
                "base_rate": sk.get("rate", 0),
                "busy": sk.get("busy", 0) + (1 if int(ch.get("vein") or 0) >= 10 else 0),
                "fail": perform_fail(ch, sk),
                "effect": sk.get("effect", "damage"),
            })
        else:
            row["cap"] = cap_for(ch, sk)
        skills.append(row)
    order = {"basic": 0, "weapon": 1, "fist": 2, "qinggong": 3,
             "neigong": 4, "perform": 5, "art": 6}
    skills.sort(key=lambda r: (order.get(r["type"], 9), -r["level"], r["name"]))

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
        "school": {
            "id": school["id"], "name": school["name"], "sect": school["sect"],
            "desc": school["desc"], "attrs": school["attrs"],
            # 本门给的三样东西 (入门即得 1 级) —— 门派卡上要看得见
            "neigong": (SKILL_BY_ID.get(school.get("neigong") or "") or {}).get("name", ""),
            "weapon": (SKILL_BY_ID.get(school.get("weapon") or "") or {}).get("name", ""),
            "perform": (SKILL_BY_ID.get(school.get("perform") or "") or {}).get("name", ""),
        } if school else None,
        "attrs": attr_rows,
        "attrs_total": d["attrs_total"],
        "attrs_cap_total": d["attrs_cap_total"],
        "stats": {
            key: {"label": STAT_LABELS[key][1], "mud": STAT_LABELS[key][0],
                  "cur": res[key]["cur"], "max": res[key]["max"]}
            for key in ("hp", "spirit", "neili", "jingli")
        },
        "combat": d["combat"],
        "d20": d20_stats(ch),
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
        "schools": [dict(x, neigong_name=(SKILL_BY_ID.get(x.get("neigong") or "") or {}).get("name", ""),
                         weapon_name=(SKILL_BY_ID.get(x.get("weapon") or "") or {}).get("name", ""),
                         perform_name=(SKILL_BY_ID.get(x.get("perform") or "") or {}).get("name", ""))
                    for x in SCHOOLS],
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
            "绝招三要素: 成功率 / 定身 (busy) / 反噬 (fail), 另加内力消耗。",
            "交手的命中 / 闪避 / 伤害 / 减伤, 都由根骨 + 对应技能 + 禀赋实时算出。",
            "气血与内力会在交手里被吃掉, 打坐 (每故事日) 回一部分。",
            "门派入门给本门内功、本门兵器与一路绝招各 1 级。",
            "禀赋 (性情) 最多 6 条, 总分 ≤ 100; 四色点数 黄 40 / 红 30 / 蓝 20 / 绿 10。",
            "经脉十层, 通脉给五行亲和与属性加成。",
        ],
        "skill_types": {
            "basic": "基本功", "weapon": "兵器", "fist": "拳脚",
            "qinggong": "轻功", "neigong": "内功", "perform": "绝招", "art": "杂学",
        },
        "perform_effects": {
            "damage": "伤害", "control": "控住", "defend": "守势",
            "evade": "身法", "heal": "疗伤", "buff": "涨己",
        },
        "dc_tiers": __import__("recto.pet.d20", fromlist=["x"]).dc_table(),
        "d20_rules": __import__("recto.pet.d20", fromlist=["x"]).rulebook(),
        "combat_labels": {
            "hit": "命中", "dodge": "闪避", "parry": "招架", "damage": "伤害",
            "defense": "防御", "speed": "先手", "crit": "暴击", "take_pct": "减伤",
        },
    }
