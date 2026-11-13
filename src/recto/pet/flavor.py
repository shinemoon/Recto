"""交手的文辞 —— 把 d20 的数值写成像样的一场打斗。

**为什么单独一层**: ``duel`` 管"中了没有、掉多少血", 它算出来的东西适合进表格,
不适合进故事。战报上也一样 —— 一行 "命中, 伤 12" 读起来像日志, 不像江湖。

**照谁写**: 一路 MUD (侠客行这一脉) 的战报文法 ——

  · 先起身形与兵刃的势 (足尖一点 / 剑走轻灵 / 沉肩坠肘)
  · 招式名引在「」里, 是"用什么招", 不是"造成多少伤害"
  · 劲力要有形 (剑光如一线秋水 / 掌风卷起地上尘土 / 棍影如山压顶)
  · 对手必须有反应 (忙侧身让过 / 闷哼一声, 退了半步 / 刀背被震得发麻)
  · 数字只落一处 (末尾给一行"伤 N"), 文气不被数字打断

**不重样**: 一场十几拍, 同一句"嘴角见血"说三遍就露馅。所有取词都过
``Voice`` —— 它记着这一场已经用过的句子, 同一句不会连着再用。

**只做一件事**: 给定这一拍的机械结果, 返回一句话。不掷骰、不改状态、
不碰存档 —— 所以它可以被单独测, 也可以被关掉 (故事那层自己会写)。
"""
from __future__ import annotations

import random
from typing import Any

# ------------------------------------------------------------------ 词库

#: 兵刃归属: 技能 id → 词库键
SKILL_KEY = {
    "jianfa": "jian", "daofa": "dao", "gunfa": "gun",
    "quanfa": "quan", "zhangfa": "zhang", "zhifa": "zhi", "tuifa": "tui",
}

#: 兵刃名 (写进句子用)
WEAPON_CN = {"jian": "剑", "dao": "刀", "gun": "棍", "quan": "拳",
             "zhang": "掌", "zhi": "指", "tui": "腿"}

#: 每一路兵刃的词: 起手 / 无招的劲势 / 有招的劲势 / 中招 / 落空 / 要害
#: ``{th}`` = 对手的称呼 (由调用方填), ``{me}`` = 出手的人
POOLS: dict[str, dict[str, list[str]]] = {
    "jian": {
        "hand": ["手腕一抖, 剑走轻灵", "足尖轻点, 剑诀一引", "肩头微沉, 剑锋斜指",
                 "衣袖一振, 长剑出鞘三寸", "踏步上前, 剑随身走"],
        "plain": ["长剑一引, 剑势走中宫", "剑锋平平递出", "一式极朴的直刺",
                  "剑随身进, 不绕不避", "剑尖一颤, 分不清要走哪边",
                  "长剑斜挑, 只求一个准"],
        "force": ["剑光如一线秋水", "剑气森然, 逼人中宫", "剑锋带起一道银弧",
                  "剑影忽左忽右, 虚实难辨", "剑势轻得像没使力, 快到却在眼前"],
        "hit": ["衣襟裂开一道口子, 血跟着渗出来", "闷哼一声, 退了半步",
                "兵刃相接, 火星四溅, 手腕都震麻了", "那一下透得极深, 半天没回过气来",
                "脸色一白, 硬生生把这口气咽了下去"],
        "miss": ["剑锋擦着衣角掠过, 只削下一片布", "{th}一个铁板桥让开, 这一剑落了个空",
                 "招式走到尽头, 剑尖点在了地上", "{th}侧身一转, 剑走空了半尺",
                 "剑到近前, {th}忽然后仰, 只挑落了头上斗笠",
                 "{th}只退开半步, 剑势便全落了空", "剑尖在他身侧一尺处趔了开去",
                 "这一剑递得太正, 被{th}抬手拨到一边"],
        "crit": ["来势太急, 躲无可躲", "血几乎同时涌了出来",
                 "一瞬竟像有两道剑影, 分不清哪个是真的"],
        "target": ["咽喉", "眉心", "左肩", "右肋", "手腕", "心口", "膝弯", "后心"],
    },
    "dao": {
        "hand": ["刀锋斜引, 沉肩坠肘", "跨步上前, 刀背朝下", "腕上一翻, 刀走弧线",
                 "双臂一沉, 刀势先起", "半侧着身, 刀口迎着风"],
        "plain": ["一刀平砍过去", "刀锋自上而下, 走了个正", "沉腰递出一刀",
                  "刀势不花, 只往人身上去", "刀背一转, 刀口反撩上来",
                  "半旋着身, 一刀横切"],
        "force": ["刀风卷起地上一蓬尘土", "刀光如半月劈落", "刀势沉猛, 破空有声",
                  "一道白亮直劈下来, 空气都被压得发闷", "刀气横着扫过来, 不收不敛"],
        "hit": ["闷响一声, 半边身子都歪了", "衣襟裂开, 血顺着刃口淌下",
                "被这一刀劈得踉跄两步", "刀锋啃进肉里, 又硬生生拖出来",
                "闷哼一声, 咬牙没让痛叫出来"],
        "miss": ["刀劈在地上, 溅起一蓬碎土", "{th}闪开, 刀锋从肩头三寸处掠过",
                 "刀势用老, 收回来时慢了半拍", "只劈断了{th}半片衣袖",
                 "{th}一矮身, 刀锋贴着头皮过去", "这一刀重是重, 慢得被{th}看穿了",
                 "刀口擦过{th}的腰带, 却没吃着肉", "刀刃砍在石阶上, 崩了个小口"],
        "crit": ["退无可退, 硬吃了一记", "连骨头都在响",
                 "一刀未尽, 第二道刀光已到眼前"],
        "target": ["左肩", "腰肋", "后颈", "手臂", "胸口", "胯侧", "膝头", "背脊"],
    },
    "gun": {
        "hand": ["双手一进, 棍梢点地", "腰马一沉, 棍走直线", "棍尾一挑, 棍头压下来",
                 "退半步再进, 借了身子的势", "两手一错, 棍身嗡嗡作响"],
        "plain": ["一棍从中路压进来", "棍梢点过去", "棍身平平推出",
                  "一棍矮身扫进来", "棍尾一送, 忽然改点", "抡圆了半圈, 砸下来"],
        "force": ["棍影如山压顶", "棍风横扫, 封死退路", "一根棍使得像活物, 忽长忽短",
                  "棍梢抽得空气呼呼响", "棍势横着荡开, 把地上的落叶卷成一层"],
        "hit": ["被棍梢扫中, 整个人横着退了两步", "肩头一麻, 手都抬不起来了",
                "结结实实吃了一记, 眼前发花", "闷响一声, 肋骨像断了一根",
                "被打得矮了半身, 半天直不起腰"],
        "miss": ["棍梢抽空, 打在树干上, 抖下一片叶子", "{th}矮身钻过, 棍势落空",
                 "棍风扫过, 只掀了{th}的衣角", "棍到中途被{th}一掌拨开",
                 "棍身贴着{th}的肩头滑过去, 没吃着力",
                 "棍长了一寸, 恰好被{th}抢进里圈", "这一棍铺得太开, 收不回来",
                 "棍梢从{th}腰侧滑过, 打了个空"],
        "crit": ["收势都来不及", "要害上结结实实吃了一记",
                 "门户被一棍荡开, 第二下已经跟上了"],
        "target": ["肩头", "腰肋", "膝弯", "后心", "手腕", "下盘", "背脊", "颈侧"],
    },
    "quan": {
        "hand": ["五指一收, 拳走中宫", "吐气开声, 一步踏进来", "上身微侧, 拳藏肘后",
                 "肩背一耸, 力道从脚底起来", "两手一开一合, 先抢了半步"],
        "plain": ["一拳直进", "拳从肋下翻出来", "一记沉实的直拳",
                  "半个身子压上去, 一拳递到近前", "上步贴身, 短拳快打",
                  "两拳交替, 一记跟着一记"],
        "force": ["一拳带着风声过来", "拳势沉实, 像一记闷雷", "拳影连成一片",
                  "这一拳不花哨, 却重得吓人", "拳风先到, 人还没到"],
        "hit": ["被打得眼前一黑", "胸口一闷, 半天提不上气", "退了两步, 撞在墙上",
                "闷响一声, 脸上血色全退了", "嘴角见了血, 却没吭声"],
        "miss": ["{th}一让, 这一拳走了空", "一拳打在{th}臂上, 反震得自己手麻",
                 "{th}只偏了半寸, 拳锋贴着面皮过去", "招式用尽, 收手时门户大开",
                 "被{th}一掌拨开, 拳势散了", "这一拳差着一指, 全打在空处",
                 "{th}退得快, 拳锋只扫到衣襟", "力沉得太早, 到近前已是强弩之末"],
        "crit": ["正中要害", "力道全灌了进来, 眼前发黑",
                 "两拳连出, 第二下才落在身上"],
        "target": ["脸侧", "下巴", "胸口", "小腹", "腰眼", "太阳穴", "肩窝", "肋下"],
    },
    "zhang": {
        "hand": ["掌心向外一翻, 掌势先起", "五指微张, 一掌推出来", "沉腕塌腰, 掌走弧线",
                 "脚步一换, 抢到侧面", "袖口一拂, 掌已经到了近前"],
        "plain": ["一掌按过去", "掌缘横切", "平平一掌推出",
                  "掌心向下一沉", "双掌一错, 中路直进", "腕子一翻, 掌背反打"],
        "force": ["掌风挟着一股劲气", "掌影重重, 虚实难辨", "掌势看着慢, 压下来却沉",
                  "一掌拍出, 空气像被推了一记", "掌缘带着风声切过来"],
        "hit": ["被拍得闷哼一声, 退开半步", "胸口一陷, 好一阵喘不上气",
                "脸上一热, 半边脸立时肿了", "被打得一个趔趄, 抱住了胳膊",
                "背上挨了一记, 五脏都晃了晃"],
        "miss": ["掌风拂过, 只把{th}衣摆掀起来", "被{th}侧身卸开, 力道全卸到空处",
                 "掌到近前, {th}仰头让过", "一掌拍在墙上, 震落一层灰",
                 "{th}矮身钻到侧面, 掌势落空", "这一掌推得太正, 被{th}抬手截住",
                 "掌心擦过{th}的肩, 力道散在半路", "掌影落了空, 只带起一阵风"],
        "crit": ["透体而入, 整个人都软了一下", "一掌既中, 又跟了一掌",
                 "这一掌不重, 却正打在要害上"],
        "target": ["胸口", "肩井", "后心", "肋下", "面门", "小腹", "颈侧", "琵琶骨"],
    },
    "zhi": {
        "hand": ["食指一弹, 指风先至", "身形一矮, 指尖朝前", "两指并拢, 直点过来",
                 "肩不动, 手已经递出去了", "侧身半步, 指影一闪"],
        "plain": ["两指直点", "指风一线射过去", "指尖一送",
                  "腕子一翻, 点向近处", "食指屈伸, 只点一处", "指影一分, 难辨真假"],
        "force": ["指风细细一线, 却专往要害钻", "一缕劲气直刺过来",
                  "指尖带着破空的轻响", "指势不快, 落的却是死处",
                  "看似随手一点, 后头还藏着三分力"],
        "hit": ["被点得半边身子一麻", "闷哼一声, 一条胳膊垂了下来",
                "脸色一变, 退了半步又退半步", "那处穴道一麻, 半天抬不起手",
                "身子晃了晃, 咬牙站住"],
        "miss": ["指风擦过, 只点破了{th}的衣领", "被{th}抬手格开, 指势偏了",
                 "一点落空, 指尖点在木柱上, 木屑簌簌落下", "{th}侧头让过, 指风落了空",
                 "被{th}抢了半步, 指劲再递不出去", "这一指差着分寸, 点在了空处",
                 "{th}缩肩卸力, 指劲顺着骨头滑开", "指风从{th}耳侧溜过, 点了个空",
                 "两指擦着{th}的衣袖滑开, 没落到实处"],
        "crit": ["要穴上正着, 半边身子都麻了", "指劲透进去, 连话都说不完整了",
                 "两指连点, 前后差不到半息"],
        "target": ["膻中", "肩井", "曲池", "太阳穴", "章门", "胸前要穴", "腕脉", "风池"],
    },
    "tui": {
        "hand": ["身形一矮, 腿先抢了半步", "腰腹一拧, 一腿扫出", "足尖点地, 借力转身",
                 "半步换步, 膝盖先顶上来", "上身一压, 腿势已起"],
        "plain": ["一腿横扫", "膝盖顶上来", "抬腿就踢",
                  "半个身子的分量压在一腿上", "旋身半圈, 后跟扫出",
                  "矮身贴地, 一脚勾进来"],
        "force": ["腿风横扫, 带着半圈尘土", "一腿沉得像一根柱子压下来",
                  "腿势快, 快到只看见影子", "这一腿带着整个身子的分量",
                  "脚风扫过, 把{th}的衣摆卷起来"],
        "hit": ["被扫得整个人横飞出去", "膝盖一软, 半跪在地上",
                "腹上挨了一记, 弓着身子退开", "闷响一声, 连退三步才站住",
                "被打得仰倒, 又硬撑着翻起来"],
        "miss": ["扫了个空, 收腿时身法一滞", "被{th}跳过, 腿风只扫着靴子",
                 "一脚踢在墙上, 砖屑簌簌", "{th}贴地一滚, 腿势落空",
                 "被{th}一掌托住小腿, 力道卸了", "这一腿高了些, 从{th}肩头掠过",
                 "{th}退了半步, 一脚便只踢着风", "腿势起得急, 被{th}顺手一推带偏",
                 "这一脚落了空, 反把地上的土扬起来"],
        "crit": ["正踢在要害上", "一脚未收, 另一脚已经跟上",
                 "力道全灌进来, 连哼都没哼出来"],
        "target": ["膝弯", "小腹", "腰肋", "下颌", "胸口", "大腿", "背脊", "脚踝"],
    },
}

PLAIN_FORCE = ["这一下没什么花哨, 只是快", "招式使老了, 靠的是身上的力",
               "平平常常一记, 落点却准", "没起手, 直接递过来的",
               "这一记算不上妙招, 沉倒是真沉"]

FUMBLE = ["力道用岔, 招式散在半路", "内息没跟上, 手上一滞",
          "脚步一乱, 收势不及", "力用过头, 整个人往前一个踉跄",
          "腕子一翻, 兵刃差点脱手"]

BACKFIRE = ["运到七成, 内息忽然乱了", "一口真气走岔, 逆着往上冲",
            "丹田猛地一沉, 力道反噬回来", "内息在经脉里撞了一下, 走岔了",
            "劲还没吐出去, 先被自己震了一记"]

HEAL = ["退开半步, 借此调了一口气", "按住胸口, 把翻涌的气血压下去",
        "拇指扣住脉门, 内息绕行一周", "背过身去调息, 肩头起伏几下"]

DEFEND = ["横兵当胸, 门户收得极紧", "脚下不丁不八, 把手抬到眉高",
          "半侧着身, 只留一线进路", "退了半步, 稳稳站住, 先不抢攻"]

EVADE = ["身影一晃, 像从原处滑开", "贴地疾退, 只留一道残影",
         "脚下换了个方位, 站到了侧后", "身子一矮, 从势里钻出去"]

BUFF = ["缓缓吐出一口长气, 掌心隐隐发热", "肩背一松, 力道重新提起来",
        "把内息理顺一遍, 眼神定下来", "脚下站稳, 连呼吸都慢了半拍"]

STUNNED = ["那处穴道还麻着, 一动就抽痛", "半边身子不听使唤, 只能干看着",
           "试着挣了一下, 内息反倒滞住", "劲提不起来, 脚下像钉住了"]


# ------------------------------------------------------------------ 取词

class Voice:
    """一场打斗里的"嘴" —— 记住这一场已经说过的句子, 不重样。

    用法: ``v = Voice(random.Random(seed))``; 之后所有取词都过它。
    换一场就打一张新嘴 (``new_voice``)。
    """

    def __init__(self, rng: random.Random, memory: int = 7) -> None:
        self.rng = rng
        self.memory = max(1, int(memory))
        self.used: list[str] = []

    def pick(self, seq: list[str] | tuple[str, ...]) -> str:
        if not seq:
            return ""
        # 先把没说过挑出来; 全说过就退回整池 (这一场说得路数太多)
        fresh = [x for x in seq if x not in self.used]
        pool = fresh or list(seq)
        # fresh 里优先"前半段"的, 尾部留着 —— 让同类句子不至于挤在相邻两拍
        cut = max(1, int(len(pool) * 0.6))
        x = pool[self.rng.randrange(cut)]
        self.used.append(x)
        if len(self.used) > self.memory * 6:
            del self.used[: len(self.used) - self.memory * 6]
        return x


#: 中文名 → 词库键 (d20_stats 的 damage.weapon 给的是"刀法"这种)
NAME_KEY = {"剑法": "jian", "刀法": "dao", "棍法": "gun", "拳法": "quan",
            "掌法": "zhang", "指法": "zhi", "腿法": "tui",
            "剑": "jian", "刀": "dao", "棍": "gun", "拳": "quan",
            "掌": "zhang", "指": "zhi", "腿": "tui", "空手": "quan"}


def _key(weapon: str | None) -> str:
    w = str(weapon or "")
    if w in POOLS:
        return w
    if w in NAME_KEY:
        return NAME_KEY[w]
    return SKILL_KEY.get(w, "quan")


def _pool(k: str, slot: str) -> list[str]:
    return POOLS.get(k, POOLS["quan"]).get(slot) or POOLS["quan"][slot]


def _fill(text: str, *, who: str, whom: str) -> str:
    return text.replace("{th}", whom or "他").replace("{me}", who or "他")


def _move(move: str) -> str:
    return f"「{move}」" if move else ""


# ------------------------------------------------------------------ 句式

def hit(v: Voice, *, who: str, whom: str, weapon: str,
        move: str, crit: bool, dmg: int, guard: float = 0.0,
        stun: int = 0, note: str = "") -> str:
    """打中那一拍。"""
    k = _key(weapon)
    hand = _fill(v.pick(_pool(k, "hand")), who=who, whom=whom) if move else ""
    if move:
        force = _fill(v.pick(_pool(k, "force")), who=who, whom=whom)
    else:
        force = _fill(v.pick(_pool(k, "plain")), who=who, whom=whom)
    place = v.pick(_pool(k, "target"))
    react = _fill(v.pick(_pool(k, "hit")), who=who, whom=whom)
    if crit:
        react = _fill(v.pick(_pool(k, "crit")), who=who, whom=whom) + ", " + react
    head = f"{who}{hand}, " if hand else f"{who}"
    tail = ((f"使一招{_move(move)}, " if move else "") + f"{force}, 直取{place}")
    line = f"{head}{tail} —— {whom}{react}"
    if guard:
        line += f", 被卸去 {int(guard * 100)}% 力"
    if stun:
        line += f", 这一下把他钉在原地 {stun} 拍"
    if note:
        line += f"; {note}"
    return f"{line}。(伤 {dmg})"


def miss(v: Voice, *, who: str, whom: str, weapon: str,
         move: str, exposed: bool = False) -> str:
    """落空那一拍。"""
    k = _key(weapon)
    hand = _fill(v.pick(_pool(k, "hand")), who=who, whom=whom) if move else ""
    if move:
        force = _fill(v.pick(_pool(k, "force")), who=who, whom=whom)
    else:
        force = _fill(v.pick(_pool(k, "plain")), who=who, whom=whom)
    dodge = _fill(v.pick(_pool(k, "miss")), who=who, whom=whom)
    head = f"{who}{hand}, " if hand else f"{who}"
    open_move = f"{_move(move)}起处, " if move else ""
    line = f"{head}{open_move}{force} —— {dodge}"
    if exposed:
        line += ", 自己门户大开"
    return line + "。"


def fumble(v: Voice, *, who: str, weapon: str, move: str = "") -> str:
    """大失败: 自己散了架。"""
    head = f"{who}{_move(move)}才使到一半, " if move else f"{who}"
    return f"{head}{v.pick(FUMBLE)} —— 这一记没递到人身上。"


def backfire(v: Voice, *, who: str, move: str, weapon: str) -> str:
    """绝招运功失败且骰出 1: 内息倒冲。"""
    k = _key(weapon)
    return (f"{who}把{_move(move)}运到七成, {v.pick(BACKFIRE)}, "
            f"整个人一震, 眼前发黑 —— {v.pick(_pool(k, 'hand'))}, "
            f"却全用在了自己身上。")


def pfm_lost(v: Voice, *, who: str, move: str) -> str:
    """招式没运成 (不是大失败), 手还照常递出去。"""
    return (f"{who}想把{_move(move)}运起来, 内息却慢了半拍, 花样散了 —— "
            f"只好照着老路数, 平平递出一招。")


def effect(v: Voice, *, who: str, move: str, kind: str, amount: int = 0) -> str:
    """疗伤 / 守势 / 身法 / 涨己 这四种不必打到人的绝招。"""
    if kind == "heal":
        return (f"{who}{v.pick(HEAL)}, 借着 {_move(move)} 把气归了位"
                f" —— 回了 {amount} 分气血。")
    if kind == "defend":
        return f"{who}摆出 {_move(move)}, {v.pick(DEFEND)} —— 卸力三成。"
    if kind == "evade":
        return f"{who}依着 {_move(move)}, {v.pick(EVADE)} —— 护体加三。"
    return f"{who}运 {_move(move)} 通身, {v.pick(BUFF)} —— 下一拍更沉。"


def stunned(v: Voice, *, who: str) -> str:
    return f"{who}{v.pick(STUNNED)}, 这一拍只好错过。"


# ------------------------------------------------------------------ 一句话入口

def new_voice(rng: random.Random) -> Voice:
    """开一场新的打斗就开一张新嘴。"""
    return Voice(rng)


def beat(v: Voice, *, kind: str, who: str = "", whom: str = "",
         weapon: str = "", move: str = "", crit: bool = False, dmg: int = 0,
         guard: float = 0.0, stun: int = 0, amount: int = 0,
         exposed: bool = False, effect_kind: str = "") -> str:
    """按 ``kind`` 分派。``duel`` 只调这一个入口。"""
    if kind == "hit":
        return hit(v, who=who, whom=whom, weapon=weapon, move=move,
                   crit=crit, dmg=dmg, guard=guard, stun=stun)
    if kind == "miss":
        return miss(v, who=who, whom=whom, weapon=weapon, move=move,
                    exposed=exposed)
    if kind == "fumble":
        return fumble(v, who=who, weapon=weapon, move=move)
    if kind == "backfire":
        return backfire(v, who=who, move=move, weapon=weapon)
    if kind == "gain":
        return effect(v, who=who, move=move, kind=effect_kind, amount=amount)
    if kind == "stun":
        return stunned(v, who=who)
    return ""


def rulebook() -> list[str]:
    """写进「交手数据与规则」折叠区的文辞口径 (给读者看, 不是给模型)。"""
    return [
        "战报的文辞走 MUD 一路: 先起身形与兵刃的势, 招式引在「」里, 劲力有形, 对手有反应。",
        "数字只落一处 —— 句子末尾那一行「伤 N」, 中间不打断文气。",
        "同一场里不重样: 说过的那句不会再连着用第二遍。",
    ]


def dump() -> dict[str, Any]:
    """词库规模 (测试与页面统计用)。"""
    return {k: {slot: len(v) for slot, v in pools.items()} for k, pools in POOLS.items()}
