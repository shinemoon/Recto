"""查词服务 (`recto.services.dict`) —— 解析 / 降级 / 缓存 / 口径。

真网络不参与: `_get_json` 全部打桩。验的是"词典返回的各种形态我们怎么读",
不是"有道今天通不通"。
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from recto.services import dict as D  # noqa: E402

checks = []


def ok(name, cond, extra=""):
    checks.append((name, bool(cond), str(extra)))


ZH = {
    "newhh": {
        "dataList": [{
            "word": "蹉跎",
            "pinyin": "cuōtuó",
            "cat": "动词",
            "sense": [{
                "cat": "动词",
                "def": ["虚度（光阴）；耽误（时光）"],
                "examples": ["<self>蹉跎</self>岁月。"],
            }],
        }],
        "source": {"name": "《现代汉语规范词典》"},
    }
}

EN = {
    "ec": {
        "source": {"name": "有道词典"},
        "word": [{
            "ukphone": "ˌserənˈdɪpəti",
            "usphone": "ˌserənˈdɪpəti",
            "return-phrase": {"l": {"i": "serendipity"}},
            "trs": [{"tr": [{"l": {"i": ["n. <b>意外</b>发现美好事物的运气"]}}]}],
        }],
    },
    "blng_sents_part": {"sentence-pair": [
        {"sentence": "Pure <self>serendipity</self>.", "sentence-translation": "纯粹的机缘。"},
    ]},
}

SUGGEST = {"result": {"code": 200}, "data": {"entries": [
    {"entry": "蹉跎", "explain": "waste time; idle away"},
]}}


class Fake:
    """按 URL 形态选择返回值的桩。"""

    def __init__(self, mapping, delay=0):
        self.mapping = mapping
        self.calls = []

    def __call__(self, url, timeout):
        self.calls.append(url)
        for k, v in self.mapping.items():
            if k in url:
                return v
        return None


def with_stub(mapping):
    fake = Fake(mapping)
    D._get_json = fake          # type: ignore[assignment]
    D.clear_cache()
    return fake


# ---------------------------------------------------------------- 文本清洗
ok("剥标签", D._clean("n. <b>意外</b>发现") == "n. 意外发现", D._clean("n. <b>意外</b>发现"))
ok("压空白", D._clean("  a\t\tb  ") == "a b", D._clean("  a\t\tb  "))
ok("例句里 <self> 换成「」", D._example("<self>蹉跎</self>岁月。") == "「蹉跎」岁月。",
   D._example("<self>蹉跎</self>岁月。"))
ok("实体解码", D._clean("a&amp;b") == "a&b", D._clean("a&amp;b"))

# ---------------------------------------------------------------- 语言的判断
ok("有汉字 = zh", D._lang_of("蹉跎") == "zh", D._lang_of("蹉跎"))
ok("纯英文 = en", D._lang_of("serendipity") == "en", D._lang_of("serendipity"))
ok("带连字符/撇号仍是 en", D._lang_of("self-made") == "en" and D._lang_of("don't") == "en")

# ---------------------------------------------------------------- 查询的清洗
ok("去首尾标点", D._norm_query("「蹉跎，」") == "蹉跎", D._norm_query("「蹉跎，」"))
ok("压空格", D._norm_query("  a   b ") == "a b", D._norm_query("  a   b "))
ok("清洗时先留余量 (≤80)", len(D._norm_query("字" * 200)) == 80, len(D._norm_query("字" * 200)))

# ---------------------------------------------------------------- 汉语
f = with_stub({"jsonapi": ZH})
r = D.lookup("蹉跎")
ok("汉语: 查到", r["ok"])
ok("汉语: 拼音", (r["entries"][0] or {}).get("phone") == "cuōtuó", r["entries"][0].get("phone"))
ok("汉语: 释义带词性", r["entries"][0]["defs"][0].startswith("动词"), r["entries"][0]["defs"][0])
ok("汉语: 例句转写", r["entries"][0]["examples"][0] == "「蹉跎」岁月。", r["entries"][0]["examples"][0])
ok("汉语: 出处是词典名", r["source"] == "《现代汉语规范词典》", r["source"])
ok("汉语: href 指向该词", "word=%E8%B9%89%E8%B7%8E" in r["href"], r["href"])

# ---------------------------------------------------------------- 英汉
with_stub({"jsonapi": EN})
r = D.lookup("serendipity")
ok("英文: 查到", r["ok"] and r["lang"] == "en")
ok("英文: 音标", r["entries"][0]["phone"] == "ˌserənˈdɪpəti", r["entries"][0]["phone"])
ok("英文: 释义剥了标签", "<b>" not in r["entries"][0]["defs"][0], r["entries"][0]["defs"][0])
ok("英文: 带双语例句", len(r["entries"][0]["examples"]) >= 1, r["entries"][0]["examples"][:1])

# ---------------------------------------------------------------- 降级
with_stub({"jsonapi": {"nope": 1}, "suggest": SUGGEST})
r = D.lookup("蹉跎")
ok("jsonapi 空 → 退回 suggest", r["ok"] and r["entries"][0]["defs"][0].startswith("waste time"),
   r["entries"][0]["defs"])

with_stub({})
r = D.lookup("找不到的词")
ok("两个接口都空 → ok=False", not r["ok"])
ok("ok=False 时给一句人话", "没查到" in (r.get("error") or ""), r.get("error"))
ok("ok=False 时仍有可用链接 (网页搜索出口)", r["href"].startswith("https://dict.youdao.com/"), r["href"])

# 汉语词没有 newhh 时, 退一步用英汉节解析 (外来词/新词常只有这个)
with_stub({"jsonapi": {"ec": EN["ec"]}})
r = D.lookup("赛伦迪皮蒂")
ok("汉语查不到中文释义时退用英汉节", r["ok"] and r["entries"][0]["defs"],
   (r["entries"] or [{}])[0].get("defs"))

# ---------------------------------------------------------------- 缓存
f = with_stub({"jsonapi": ZH})
D.lookup("蹉跎")
D.lookup("蹉跎")
ok("同一个词第二次不再打网络", len(f.calls) == 1, len(f.calls))
D.lookup("蹉跎，")
ok("清洗后同词仍命中缓存", len(f.calls) == 1, len(f.calls))
D.clear_cache()
D.lookup("蹉跎")
ok("clear_cache 之后重新查", len(f.calls) == 2, len(f.calls))

# 缓存不缓存"没查到"(下次也许网络好了)
f = with_stub({})
D.lookup("x")
D.lookup("x")
# 一次查不到 = jsonapi + suggest 各一次; 第二次仍要重新打 (没查到的不进缓存)
ok("没查到的结果不进缓存", len([c for c in f.calls if "jsonapi" in c]) == 2,
   len([c for c in f.calls if "jsonapi" in c]))

# ---------------------------------------------------------------- 边界
with_stub({"jsonapi": ZH})
ok("空词直接拒", D.lookup("")["ok"] is False)
ok("纯标点也算空", D.lookup("，。？！")["ok"] is False)
ok("超长不查词典", "太长" in (D.lookup("字" * 60).get("error") or ""), D.lookup("字" * 60).get("error"))
r = D.lookup("蹉跎")
ok("返回值可 json 序列化 (前端直接吃)", isinstance(r, dict) and isinstance(r["entries"], list))

# 不改全局状态
D._get_json = __import__("urllib.request", fromlist=["urlopen"]).urlopen  # 恢复成真实现不必, 但别留桩
D.clear_cache()

failed = [c for c in checks if not c[1]]
for name, good, extra in checks:
    print(("  ok   " if good else "  FAIL ") + name + (f"   [{extra}]" if extra and not good else ""))
print(f"\n{len(checks) - len(failed)} passed / {len(failed)} failed")
sys.exit(1 if failed else 0)
