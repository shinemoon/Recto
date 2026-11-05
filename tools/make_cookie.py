"""签一个控制台会话 cookie —— 探针脚本用 (playwright 里塞 cookie 免登录)。

用法:  python3 tools/make_cookie.py  [用户名]
输出:  cookie 值 (单行)
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from recto.app import webauth  # noqa: E402

user = (sys.argv[1] if len(sys.argv) > 1 else "") or os.environ.get("RV_USER") or webauth.login_user()
cookie = webauth.set_cookie(user)
print(cookie.split(";")[0].split("=", 1)[1])
