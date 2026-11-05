"""外部世界适配器 —— 每个对端一个模块。

    library.py  本地书库 (REST + SQLite 直写)
    cloud.py    云端阅读服务 (API)
    device.py   墨水屏设备接入 (PaperEcho 协议, :8080)
    corpus.py   术语语料 (只读)
"""
