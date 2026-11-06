"""设备接入口 (墨水屏 /api/sync) 与主服务同进程。

跑法: cd <仓库根> && PYTHONPATH=src python3 tests/test_device_intake.py

验的是 app/server.py 的 _start_device_listener:
  * device.port <= 0            -> 不起监听 (返回 None)
  * 端口被占                    -> 不抛异常, 返回 None (主服务照常起)
  * 正常                        -> /api/status 200 + 一次性 /api/sync 落盘到 device.data_dir
  * 收尾                        -> shutdown 后端口释放
"""
from __future__ import annotations

import http.client
import json
import socket
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

TMP = Path(tempfile.mkdtemp())

from recto.app import server  # noqa: E402


class _Cfg:
    """只回 device.port 的配置桩 (真配置不动)。"""

    def __init__(self, port):
        self.port = port

    def get(self, section, key, default=None):
        if section == "device" and key == "port":
            return self.port
        return default


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _patch(port: int) -> None:
    server._cfg = _Cfg(port)
    server.paths = SimpleNamespace(PATHS=SimpleNamespace(device_data_dir=TMP))


def _req(port: int, method: str, path: str, body: dict | None = None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    payload = None
    headers = {}
    if body is not None:
        payload = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    c.request(method, path, payload, headers)
    r = c.getresponse()
    data = r.read()
    c.close()
    return r.status, data


def test_disabled_when_port_zero():
    _patch(0)
    assert server._start_device_listener() is None
    _patch(-1)
    assert server._start_device_listener() is None


def test_port_busy_is_not_fatal():
    busy = socket.socket()
    busy.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    busy.bind(("0.0.0.0", 0))
    busy.listen(1)
    port = busy.getsockname()[1]
    _patch(port)
    assert server._start_device_listener() is None, "端口被占时应返回 None 而不是抛异常"
    busy.close()


def test_listener_serves_status_and_accepts_sync():
    port = _free_port()
    _patch(port)
    srv = server._start_device_listener()
    assert srv is not None
    try:
        st, body = _req(port, "GET", "/api/status")
        assert st == 200, st
        info = json.loads(body)
        assert "excerptCount" in info and "bookCount" in info, info

        payload = {
            "books": [{"id": "b1", "name": "设备书", "author": ""}],
            "excerpts": [{"id": "e1", "bookId": "b1", "content": "设备摘录", "createdTime": 1762000000}],
        }
        st, body = _req(port, "POST", "/api/sync", payload)
        assert st == 200, st
        res = json.loads(body)
        assert res.get("ok") is True, res
        assert res.get("excerptCount") == 1 and res.get("bookCount") == 1, res

        # 落进 device.data_dir (不再有第二个进程写别处)
        agg = TMP / "aggregate" / "aggregate.jsonl"
        assert agg.exists(), "aggregate.jsonl 应落在 device.data_dir"
        assert "设备摘录" in agg.read_text(encoding="utf-8")
        books = TMP / "aggregate" / "books.jsonl"
        assert "设备书" in books.read_text(encoding="utf-8")
        decks = list(TMP.glob("*/*"))
        assert decks, "deck 目录应落在 device.data_dir"
    finally:
        srv.shutdown()
        srv.server_close()


def test_port_released_after_shutdown():
    port = _free_port()
    _patch(port)
    srv = server._start_device_listener()
    assert srv is not None
    srv.shutdown()
    srv.server_close()
    probe = socket.socket()
    probe.settimeout(1)
    rc = probe.connect_ex(("127.0.0.1", port))
    probe.close()
    assert rc != 0, "shutdown 后端口应释放"


if __name__ == "__main__":
    import traceback

    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    ok = 0
    for fn in fns:
        try:
            fn()
            ok += 1
            print(f"  ok   {fn.__name__}")
        except Exception:
            print(f"  FAIL {fn.__name__}")
            traceback.print_exc()
    print(f"\n{ok}/{len(fns)} passed")
    sys.exit(0 if ok == len(fns) else 1)
