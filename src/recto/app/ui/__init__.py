"""Recto 外观层 —— 皮肤 token + 共享外壳。

对外只用这三个名字:
    from recto.app.ui import shell, skins
    shell.document(...)      # 整页
    shell.auth_page(...)     # 登录页
    skins.skin_css()         # /assets/skin.css
"""
from . import shell, skins  # noqa: F401

__all__ = ["shell", "skins"]
