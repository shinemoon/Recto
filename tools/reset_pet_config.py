"""清掉探针写进生产的书宠配置 (把 llm 段与模型库还原成空)。

探针 (tools/probe_pet_flow.mjs) 会真的往 data/pet/config.json 写配置。
跑完探针后跑这个, 把假端点 / 假 key / 探针人格清干净。

用法: python3 tools/reset_pet_config.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from recto.pet import config as petcfg  # noqa: E402

cfg = petcfg.load_config()
cfg["llm"]["base_url"] = ""
cfg["llm"]["model"] = ""
cfg["llm"]["api_key"] = ""
cfg["models"] = []
cfg["active_model_id"] = ""
cfg["persona"] = dict(petcfg.DEFAULT_CONFIG["persona"])
petcfg.save_config(cfg)
print("书宠配置已还原:", petcfg.CONFIG_PATH)
