"""阅读高亮 (语料滤镜) 测试 —— 配置 / 语料 / 挑语料 / 词表预算。

跑法: python3 tests/test_reading_mark.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

TMP = Path(tempfile.mkdtemp(prefix="recto-mark-test-"))
CORPUS = TMP / "corpus"
CORPUS.mkdir(parents=True, exist_ok=True)
BOOKS = TMP / "books"
BOOKS.mkdir(parents=True, exist_ok=True)

os.environ["RECTO_DATA_DIR"] = str(TMP / "data")
os.environ["RECTO_CORPUS_DIR"] = str(CORPUS)
os.environ["RECTO_LIBRARY_DIR"] = str(BOOKS)

from recto.core import config as cfgmod          # noqa: E402
from recto.core import reading_mark as rm        # noqa: E402

cfgmod.get_config(reload=True)

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {extra}")


def write_corpus(name, title, cats, **meta):
    d = {"meta": dict({"title": title}, **meta),
         "categories": {cid: {"name": nm, "items": [{"term": t} for t in words]}
                        for cid, (nm, words) in cats.items()}}
    p = CORPUS / name
    p.write_text(json.dumps(d, ensure_ascii=False), "utf-8")
    return p


def book_row(key="k1", title="示例之书", author="某作者", path=""):
    return {"key": key, "title": title, "author": author, "path": path}


# ---------------------------------------------------------------- 配置

print("[1] 配置默认值 / 归一化")
conf0 = rm.load()
check("默认启用", conf0["enabled"] is True)
check("默认 highlight 模式", conf0["mode"] == "highlight")
check("语料目录取自 [corpus] dir", conf0["dirs"] == [str(CORPUS)], conf0["dirs"])
check("8 类样式齐备", len(conf0["styles"]) == 8)
check("默认只给半透明底色 (不设文字色)",
      conf0["styles"]["person"]["color"] == "" and
      conf0["styles"]["person"]["bg"].startswith("rgba("))

bad = rm.normalize({"enabled": "no", "mode": "nope", "dirs": ["相对路径", "/tmp"],
                    "styles": {"person": {"bg": "red;}body{x:", "color": "url(x)"}},
                    "min_len": 999, "max_terms": 1, "bind": {"": "x", "k": ""}})
check("坏模式退回 highlight", bad["mode"] == "highlight", bad["mode"])
check("相对路径丢掉, 绝对路径保留", bad["dirs"] == ["/tmp"], bad["dirs"])
check("注入字符的颜色被丢掉", bad["styles"]["person"]["bg"] == "" and
      bad["styles"]["person"]["color"] == "", bad["styles"]["person"])
check("min_len / max_terms 夹到区间", bad["min_len"] == 12 and bad["max_terms"] == 50,
      f'{bad["min_len"]}/{bad["max_terms"]}')
check("空键 / 空值的绑定丢掉", bad["bind"] == {}, bad["bind"])

print("[2] 保存 / 版本 / 恢复默认")
saved = rm.save({**conf0, "mode": "span"})
check("保存后落盘", rm.conf_path().exists(), str(rm.conf_path()))
check("版本自增", saved["version"] == conf0["version"] + 1, str(saved["version"]))
check("模式被记住", rm.load()["mode"] == "span")
rm.reset()
check("恢复默认删掉覆盖文件", not rm.conf_path().exists())
check("恢复默认模式回到 highlight", rm.load()["mode"] == "highlight")

# ---------------------------------------------------------------- 语料

print("[3] 语料解析 / 扫描")
c1 = write_corpus("示例之书-abc123.json", "示例之书",
                  {"person": ("人名", ["阿蒙", "Amon"]),
                   "place": ("地点", ["雾都"])})
c2 = write_corpus("别的书-def456.json", "别的书", {"term": ("术语", ["量子"])})
(CORPUS / "index.json").write_text("{}", "utf-8")
(CORPUS / "latest.json").write_text("{}", "utf-8")
(CORPUS / "broken.json").write_text("{ nope", "utf-8")

rows = rm.scan_corpora([str(CORPUS)])
names = sorted(r["file"] for r in rows)
check("扫描跳过 index/latest/坏文件", names == ["别的书-def456.json", "示例之书-abc123.json"], names)
check("扫到词数与分类", rows[1]["words"] == 3 and len(rows[1]["categories"]) == 2, rows[1])

mappings = CORPUS / "terms-map.json"
mappings.write_text(json.dumps({"mappings": [
    {"source": "Mum", "target": "妈妈", "tags": ["person"]},
    {"source": "Nuneaton", "target": "努尼顿", "tags": ["place"]}]}), "utf-8")
mc = rm.load_corpus(mappings)
check("认术语表格式 (mappings)",
      mc and set(mc["categories"]) == {"person", "place"} and mc["words_total"] == 4,
      mc and mc["words_total"])

# ---------------------------------------------------------------- 挑语料

print("[4] 挑语料: 绑定 / 边车 / 同名 / 无匹配")
conf = rm.load()
corpora = rm.scan_corpora(conf["dirs"])

c, why = rm.resolve(book_row(title="示例之书"), conf, corpora)
check("书名完全相等 → name", why["how"] == "name" and why["score"] == 100, why)

c, why = rm.resolve(book_row(title="完全不相干"), conf, corpora)
check("不像的书不硬配", c is None and why["how"] == "none", why)

c, why = rm.resolve(book_row(title="作者的另一本", author="某作者"), conf, corpora)
check("同作者的另一本不会被错配", c is None, why)

rm.set_bind("k1", "别的书-def456.json")
conf = rm.load()
c, why = rm.resolve(book_row(title="示例之书"), conf, corpora)
check("显式绑定优先于书名匹配", why["how"] == "bind" and c["file"] == "别的书-def456.json", why)

rm.set_bind("k9", "不存在的语料.json")
conf = rm.load()
c, why = rm.resolve(book_row("k9", "示例之书"), conf, corpora)
check("绑定指向不存在的文件 → bind_missing (不退回模糊匹配)",
      c is None and why["how"] == "bind_missing", why)
rm.set_bind("k9", "")
rm.set_bind("k1", "")

book_path = BOOKS / "边车之书.epub"
book_path.write_bytes(b"x")
(book_path.parent / "边车之书.keywords.json").write_text(json.dumps({
    "meta": {"title": "边车语料"},
    "categories": {"person": {"name": "人名", "items": [{"term": "边车人"}]}}}), "utf-8")
c, why = rm.resolve(book_row(title="边车之书", path=str(book_path)), rm.load(), corpora)
check("书库里的 <书名>.keywords.json 被认出来", why["how"] == "sidecar" and c["title"] == "边车语料", why)

src = write_corpus("同源文件-xyz.json", "完全另一个书名",
                   {"term": ("术语", ["同源"])}, source_epub=str(book_path))
c, why = rm.resolve(book_row(title="边车之书", path=str(book_path)), rm.load(), corpora)
check("source_epub 指向同一份书也算命中", why["how"] == "sidecar", why)   # 边车优先
c2_, why2 = rm.resolve(book_row(title="没有任何边车的书名", path=str(BOOKS / "无.epub")),
                       rm.load(), corpora)
check("无书无绑定 → 不命中", c2_ is None, why2)

# ---------------------------------------------------------------- 词表

print("[5] 词表: 预算公平 / 过滤 / 去重")
big = CORPUS / "big.json"
big.write_text(json.dumps({
    "meta": {"title": "大书"},
    "categories": {
        "person": {"name": "人名", "items": [{"term": f"人名{i}"} for i in range(400)]},
        "place": {"name": "地点", "items": [{"term": f"地点{i}"} for i in range(10)]}},
}), "utf-8")
conf = rm.load()
conf["max_terms"] = 100
conf["dirs"] = [str(CORPUS)]
rm.save(conf)
rm.set_bind("big", "big.json")

orig = rm._book_row
rm._book_row = lambda k: book_row(k, "大书")      # 不碰真实书库
pay = rm.build_payload("big")
cats = {c["id"]: len(c["words"]) for c in pay["cats"]}
check("预算不会让人名吃光", cats.get("place", 0) > 0 and cats.get("person", 0) > 0, cats)
check("总词数不超过上限", pay["total"] <= 100, str(pay["total"]))
check("payload 带语料名与来源", pay["file"] == "big.json" and pay["pick"]["how"] == "bind", pay["pick"])

conf = rm.load()
conf["enabled"] = False
rm.save(conf)
pay = rm.build_payload("big")
check("关掉后 payload 只带开关", pay["enabled"] is False and not pay.get("cats"), str(pay)[:80])
conf["enabled"] = True
conf["skip_digits"] = True
conf["min_len"] = 2
rm.save(conf)

stopc = CORPUS / "stop.json"
stopc.write_text(json.dumps({
    "meta": {"title": "过滤之书"},
    "categories": {"person": {"name": "人名",
                              "items": [{"term": t} for t in ["他", "the", "1981", "阿蒙", "蒙", "阿蒙"]]}},
}), "utf-8")
rm.set_bind("stop", "stop.json")
rm._book_row = lambda k: book_row(k, "过滤之书")
pay = rm.build_payload("stop")
words = pay["cats"][0]["words"] if pay["cats"] else []
check("停用词的 '他' / 'the' 被挡", "他" not in words and "the" not in words, words)
check("纯数字 1981 被挡", "1981" not in words, words)
check("短词 '蒙' 被 min_len 挡", "蒙" not in words, words)
check("重复词只留一个", words.count("阿蒙") == 1, words)

rm._book_row = orig

# ---------------------------------------------------------------- 概览

print("[6] 设置页概览 / 绑定 / 清理")
rm.set_bind("k1", "")
rm.reset()
summ = rm.summary()
check("summary 含语料清单", len(summ["corpora"]) >= 4, str(len(summ["corpora"])))
check("summary 含分类名", any(c["id"] == "person" and c["name"] == "人名"
                              for c in summ["categories"]), summ["categories"])
check("summary 含配置文件路径", str(rm.conf_path()) in summ["path"], summ["path"])
check("每本书都有匹配说明", all("how" in b for b in summ["books"]), summ["books"])

print()
print(f"{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
