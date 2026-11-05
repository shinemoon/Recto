#!/usr/bin/env python3
"""PaperEcho receiver — passive HTTP server on port 8080 (LAN).

Subcommands:
    start       daemonize, write pid, return immediately
    stop        SIGTERM the daemon, wait up to 5 s, remove pid
    status      running? pid? last deck?
    tail        last 20 log lines + last 3 deck summaries
    foreground  run in this terminal (Ctrl-C to stop)

Wire format: see ../references/protocol.md
Storage:     see ../references/storage.md
Ops:         see ../references/operations.md

Stdlib only. No Flask, no FastAPI. The receiver is a dumb pipe —
it persists what the device sends and does no validation beyond
the hard limits in protocol.md.
"""

from __future__ import annotations

import argparse
import datetime as dt
import errno
import fcntl
import json
import os
import shutil
import signal
import socket
import socketserver
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

# ---------- Constants (overridable via .env) ----------

from recto.core import legacy, paths  # noqa: E402

DEFAULT_DATA_DIR = paths.DATA_ROOT
DEFAULT_BIND = "0.0.0.0"
DEFAULT_PORT = 8080
# Admin UI listener (only /admin/* routes). 0 = disabled.
# Overridable via RECTO_DEVICE_ADMIN_PORT in .env.
DEFAULT_ADMIN_PORT = 8081
BATCH_HARD_LIMIT = 200
CONTENT_BYTE_CAP = 64 * 1024
EVENTS_FILENAME = "events.jsonl"
CURSOR_FILENAME = "events.cursor"


def load_env(data_dir: Path) -> None:
    """Read .env in the data dir (KEY=VALUE per line, # comments)."""
    env_path = data_dir / ".env"
    if not env_path.is_file():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


def resolve_config() -> tuple[Path, str, int, int]:
    load_env(DEFAULT_DATA_DIR)
    data_dir = Path(legacy.env("DATA_DIR", DEFAULT_DATA_DIR))
    bind = legacy.env("BIND", DEFAULT_BIND)
    port = int(legacy.env("PORT", DEFAULT_PORT))
    admin_port = int(legacy.env("ADMIN_PORT", DEFAULT_ADMIN_PORT))
    return data_dir, bind, port, admin_port


# ---------- Port bind probe ----------

def _port_listening(port: int, host: str = "127.0.0.1", timeout: float = 0.3) -> bool:
    """Probe whether (host, port) accepts TCP connections.

    Used by restart to wait for the port to go free after stop. Also used
    by status-style checks. Cheap (sub-second) and side-effect-free.
    """
    try:
        s = socket.create_connection((host, port), timeout=timeout)
    except (OSError, socket.timeout):
        return False
    finally:
        try:
            s.close()  # type: ignore[union-attr]
        except Exception:
            pass
    return True


def probe_bind(port: int = DEFAULT_PORT) -> tuple[bool, str]:
    """Try binding to (0.0.0.0, port) and immediately close. Returns (ok, error)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("0.0.0.0", port))
    except PermissionError as e:
        return False, f"PermissionError: {e}"
    except OSError as e:
        return False, f"OSError: {e}"
    finally:
        s.close()
    return True, ""


# ---------- Logging ----------

def _log_path(data_dir: Path) -> Path:
    return data_dir / "recto.log"


def _events_path(data_dir: Path) -> Path:
    return data_dir / EVENTS_FILENAME


def _cursor_path(data_dir: Path) -> Path:
    return data_dir / CURSOR_FILENAME


def _pid_path(data_dir: Path) -> Path:
    return data_dir / "recto.pid"


def _admin_pid_path(data_dir: Path) -> Path:
    return data_dir / "admin.pid"


def _read_admin_pid(data_dir: Path) -> int | None:
    pp = _admin_pid_path(data_dir)
    if not pp.is_file():
        return None
    try:
        return int(pp.read_text().strip())
    except (OSError, ValueError):
        return None


def _config_path(data_dir: Path) -> Path:
    return data_dir / "recto.config.json"


def _read_config(data_dir: Path) -> dict:
    cp = _config_path(data_dir)
    if not cp.is_file():
        return {}
    try:
        return json.loads(cp.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def log(data_dir: Path, level: str, msg: str) -> None:
    line = f"{dt.datetime.utcnow().isoformat(timespec='seconds')}Z {level} {msg}\n"
    lp = _log_path(data_dir)
    try:
        with open(lp, "a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass


def _safe_repr(obj, limit: int = 500) -> str:
    try:
        s = repr(obj)
    except Exception as e:
        return f"<repr-fail: {type(e).__name__}>"
    if len(s) > limit:
        return s[:limit] + f"...<+{len(s)-limit}>"
    return s


# ---------- Daemon lifecycle ----------

def _read_pid(data_dir: Path) -> int | None:
    pp = _pid_path(data_dir)
    if not pp.is_file():
        return None
    try:
        return int(pp.read_text().strip())
    except (OSError, ValueError):
        return None


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return False
    return True


def _find_orphan_on_port(port: int) -> int | None:
    """Find a device.py pid listening on (0.0.0.0, port).

    Walks /proc only — no extra deps. Returns pid or None.

    Steps:
      1. List all pids whose /proc/<pid>/cmdline mentions device.py.
      2. Read /proc/net/tcp, find LISTEN entries on `port` (hex).
      3. For each candidate pid, scan /proc/<pid>/fd symlinks — match the
         socket inode from (2). The first hit is our orphan.
    """
    port_hex = f"{port:04X}"
    candidates: list[int] = []
    for fd in os.listdir("/proc"):
        if not fd.isdigit():
            continue
        pid = int(fd)
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                cmdline = f.read().decode("utf-8", errors="ignore")
        except OSError:
            continue
        if "device.py" in cmdline:
            candidates.append(pid)
    if not candidates:
        return None
    try:
        with open("/proc/net/tcp", "r") as f:
            tcp_lines = f.read().splitlines()
    except OSError:
        return None
    listen_inodes: set[str] = set()
    for ln in tcp_lines[1:]:
        parts = ln.split()
        if len(parts) < 10 or parts[3] != "0A":
            continue
        if not parts[1].endswith(":" + port_hex):
            continue
        listen_inodes.add(parts[9])
    if not listen_inodes:
        return None
    for pid in candidates:
        fd_dir = f"/proc/{pid}/fd"
        try:
            fds = os.listdir(fd_dir)
        except OSError:
            continue
        for fdent in fds:
            try:
                target = os.readlink(f"{fd_dir}/{fdent}")
            except OSError:
                continue
            for inode in listen_inodes:
                if target == f"socket:[{inode}]":
                    return pid
    return None


def cmd_status(data_dir: Path) -> int:
    pid = _read_pid(data_dir)
    if pid is None:
        print("not running")
        return 0
    if not _pid_alive(pid):
        print(f"stale pid file (pid {pid} not alive)")
        return 0
    cfg = _read_config(data_dir)
    if cfg:
        port_str = f"{cfg.get('bind', '?')}:{cfg.get('port', '?')}"
    else:
        port_str = f"port {DEFAULT_PORT}"
    admin_pid = _read_admin_pid(data_dir)
    admin_suffix = ""
    if admin_pid and _pid_alive(admin_pid):
        admin_suffix = f" + admin :{DEFAULT_ADMIN_PORT} (pid {admin_pid})"
    last = _last_deck_summary(data_dir)
    suffix = f", last deck: {last}" if last else ""
    print(f"running (pid {pid}, {port_str}{admin_suffix}{suffix})")
    return 0


def cmd_start(data_dir: Path, bind: str, port: int, foreground: bool = False,
              admin_port: int | None = None) -> int:
    data_dir.mkdir(parents=True, exist_ok=True)

    ok, err = probe_bind(port)
    if not ok:
        print(f"bind to {bind}:{port} failed: {err}", file=sys.stderr)
        print("(is the port already in use?)", file=sys.stderr)
        return 2

    existing = _read_pid(data_dir)
    if existing and _pid_alive(existing):
        print(f"already running (pid {existing})", file=sys.stderr)
        return 1

    if foreground:
        # foreground keeps a single port: run device only, admin via separate cmd
        _run_server(data_dir, bind, port)
        return 0

    # daemonize: double-fork
    if os.fork() > 0:
        for _ in range(50):
            time.sleep(0.05)
            if _read_pid(data_dir):
                break
        print(f"started (pid {_read_pid(data_dir)})")
        return 0

    os.setsid()
    if os.fork() > 0:
        os._exit(0)

    lp = _log_path(data_dir)
    lp.touch()
    log_fh = open(lp, "a", encoding="utf-8")
    os.dup2(log_fh.fileno(), sys.stdout.fileno())
    os.dup2(log_fh.fileno(), sys.stderr.fileno())
    os.dup2(log_fh.fileno(), sys.stdin.fileno())

    _pid_path(data_dir).write_text(str(os.getpid()), encoding="utf-8")
    _config_path(data_dir).write_text(
        json.dumps({"bind": bind, "port": port, "pid": os.getpid()}),
        encoding="utf-8",
    )
    log(data_dir, "INFO", f"daemon started (pid {os.getpid()}, {bind}:{port})")

    # Spawn admin listener as a child of this daemon (separate process, separate pid file)
    admin_proc: subprocess.Popen | None = None
    if admin_port and admin_port > 0:
        admin_proc = _spawn_admin_listener(data_dir, bind, admin_port)

    try:
        _run_server(data_dir, bind, port)
    except Exception as e:
        log(data_dir, "ERROR", f"server crashed: {e!r}")
    finally:
        if admin_proc and admin_proc.poll() is None:
            try:
                admin_proc.terminate()
                admin_proc.wait(timeout=2)
            except Exception:
                pass
        _pid_path(data_dir).unlink(missing_ok=True)
        _config_path(data_dir).unlink(missing_ok=True)
    return 0


def _spawn_admin_listener(data_dir: Path, bind: str, admin_port: int) -> subprocess.Popen:
    """Fork a child process that runs only the admin listener on admin_port.

    The child writes its own pid file and config file so the parent stop can clean it up.
    """
    admin_log = data_dir / "admin.log"
    admin_log.touch()
    lf = open(admin_log, "a", encoding="utf-8")
    # re-exec self with "admin" subcommand (placeholder; we will add it below)
    cmd = [sys.executable, os.path.abspath(__file__), "admin",
           "--bind", bind, "--port", str(admin_port),
           "--data-dir", str(data_dir)]
    return subprocess.Popen(
        cmd,
        stdin=lf, stdout=lf, stderr=lf,
        start_new_session=True,
        close_fds=True,
    )


def cmd_stop(data_dir: Path) -> int:
    pid = _read_pid(data_dir)
    if pid is not None and not _pid_alive(pid):
        _pid_path(data_dir).unlink(missing_ok=True)
        _config_path(data_dir).unlink(missing_ok=True)
        pid = None

    # Kill the admin listener FIRST so it can't spawn a new device request in flight
    admin_pid = _read_admin_pid(data_dir)
    if admin_pid is not None and _pid_alive(admin_pid):
        try:
            os.kill(admin_pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        for _ in range(20):
            time.sleep(0.1)
            if not _pid_alive(admin_pid):
                break
        if _pid_alive(admin_pid):
            try:
                os.kill(admin_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        log(data_dir, "INFO", f"admin stopped (pid {admin_pid})")

    if pid is None:
        cfg = _read_config(data_dir)
        port = cfg.get("port") if cfg else DEFAULT_PORT
        orphan = _find_orphan_on_port(port)
        if orphan is not None:
            print(f"no pid file, but device.py is on port {port} (pid {orphan})", file=sys.stderr)
            try:
                os.kill(orphan, signal.SIGTERM)
            except ProcessLookupError:
                pass
            for _ in range(50):
                time.sleep(0.1)
                if not _pid_alive(orphan):
                    break
            if _pid_alive(orphan):
                os.kill(orphan, signal.SIGKILL)
            log(data_dir, "INFO", f"daemon stopped (orphan pid {orphan})")
            print("stopped (orphan)")
            return 0
        print("not running")
        return 0
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        _pid_path(data_dir).unlink(missing_ok=True)
        _config_path(data_dir).unlink(missing_ok=True)
        return 0
    for _ in range(50):
        time.sleep(0.1)
        if not _pid_alive(pid):
            break
    if _pid_alive(pid):
        os.kill(pid, signal.SIGKILL)
    _pid_path(data_dir).unlink(missing_ok=True)
    _config_path(data_dir).unlink(missing_ok=True)
    log(data_dir, "INFO", f"daemon stopped (pid {pid})")
    print("stopped")
    return 0


def cmd_restart(data_dir: Path, bind: str, port: int, admin_port: int) -> int:
    """Stop → wait for the port to actually go free → start again.

    Re-execs the same script as a child so the SIGTERM/SIGKILL happens in a
    separate process — keeps the parent (the agent's CLI) free of `os.kill`
    in its own runtime, and means the daemon fully releases the socket
    before the new bind races in.

    Stops the admin listener first (so it can't spawn new device requests
    while device is dying), then the device. After stop returns we poll the
    device port for up to 3 s (typical TIME_WAIT drain is sub-second); if
    it's still bound we proceed anyway and let probe_bind() in cmd_start
    report the conflict.
    """
    print(f"restarting ({bind}:{port} + admin :{admin_port})")
    # 1. stop
    rc = subprocess.run(
        [sys.executable, os.path.abspath(__file__), "stop", "--data-dir", str(data_dir)],
        capture_output=True, text=True,
    )
    sys.stdout.write(rc.stdout)
    if rc.stderr:
        sys.stderr.write(rc.stderr)
    if rc.returncode not in (0,):
        # stop already exited non-zero — print and try start anyway; user
        # likely wants the server up even if it was already dead
        print(f"stop returned {rc.returncode}, attempting start anyway", file=sys.stderr)

    # 2. wait for the device port to free up (poll, max 3 s)
    deadline = time.time() + 3.0
    while time.time() < deadline:
        if not _port_listening(port):
            break
        time.sleep(0.1)
    # admin port too
    deadline2 = time.time() + 2.0
    while time.time() < deadline2:
        if not _port_listening(admin_port):
            break
        time.sleep(0.1)

    # 3. start
    rc = subprocess.run(
        [sys.executable, os.path.abspath(__file__), "start",
         "--data-dir", str(data_dir),
         "--bind", bind, "--port", str(port)],
        capture_output=True, text=True,
    )
    sys.stdout.write(rc.stdout)
    if rc.stderr:
        sys.stderr.write(rc.stderr)
    return rc.returncode


def cmd_tail(data_dir: Path) -> int:
    lp = _log_path(data_dir)
    if lp.is_file():
        lines = lp.read_text(encoding="utf-8").splitlines()[-20:]
        for ln in lines:
            print(ln)
    else:
        print("(no log yet)")
    print("---")
    decks = _recent_decks(data_dir, n=3)
    if not decks:
        print("(no decks)")
        return 0
    for d in decks:
        meta_p = d / "meta.json"
        if meta_p.is_file():
            m = json.loads(meta_p.read_text(encoding="utf-8"))
            print(f"{d.name}: {m.get('count', '?')} excerpts, "
                  f"received {m.get('receivedAt', '?')}")
    # Show aggregate stats
    agg = _aggregate_path(data_dir)
    if agg.exists():
        n = sum(1 for _ in open(agg, encoding="utf-8"))
        print(f"---\naggregate: {n} excerpt(s) cumulative")
    return 0


def cmd_backfill(data_dir: Path) -> int:
    """One-shot: scan all existing snapshot dirs, append their excerpts to
    the cumulative aggregate (dedup by bookId+content). Safe to re-run."""
    decks = _recent_decks(data_dir, n=10_000)
    if not decks:
        print("(no snapshots found, nothing to backfill)")
        return 0
    agg = _aggregate_path(data_dir)
    agg.parent.mkdir(parents=True, exist_ok=True)
    # Pre-load existing keys
    existing: set[tuple] = set()
    if agg.exists():
        with open(agg, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                existing.add((rec.get("bookId"), rec.get("content")))
    # Walk oldest-first: the FIRST deck to carry an excerpt wins _firstSeenDeck
    decks_asc = list(reversed(decks))
    added = 0
    scanned = 0
    for d in decks_asc:
        ex_path = d / "excerpts.jsonl"
        if not ex_path.is_file():
            continue
        with open(ex_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                scanned += 1
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                key = (rec.get("bookId"), rec.get("content"))
                if key in existing:
                    continue
                _cap_content(rec)
                # Derive deckId from path: <HHMMSS>_<deckId>
                deck_id = d.name.split("_", 1)[-1] if "_" in d.name else d.name
                rec["_firstSeenDeck"] = deck_id
                with open(agg, "a", encoding="utf-8") as out:
                    out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                existing.add(key)
                added += 1
    print(f"backfill: scanned {scanned} excerpt(s) across {len(decks)} snapshot(s), "
          f"added {added} new (rest already in aggregate)")
    return 0


# ---------- Storage helpers ----------

def _deck_id_now() -> str:
    now = dt.datetime.now()
    return now.strftime("%Y%m%d-%H%M%S") + "-" + os.urandom(2).hex()


def _last_deck_summary(data_dir: Path) -> str | None:
    decks = _recent_decks(data_dir, n=1)
    if not decks:
        return None
    return decks[0].name


def _recent_decks(data_dir: Path, n: int) -> list[Path]:
    if not data_dir.is_dir():
        return []
    out: list[Path] = []
    for day in sorted((data_dir).iterdir(), reverse=True):
        if not day.is_dir() or day.name.startswith("."):
            continue
        for deck in sorted(day.iterdir(), reverse=True):
            if deck.is_dir():
                out.append(deck)
                if len(out) >= n:
                    return out
    return out


# Cap on retained snapshot dirs. 0 disables GC.
# 2026-06-12 23:50: set back to 3. The 230518 deck (2576 excerpts) was lost
# to an earlier over-zealous `find -delete`, and M5Paper has re-synced the
# full deck — aggregate.jsonl now holds 2507 unique excerpts and the device
# is the source of truth. GC can run again; aggregate is the safety net.
MAX_DECK_SNAPSHOTS = 3


def _gc_old_decks(data_dir: Path, keep: int = MAX_DECK_SNAPSHOTS) -> list[str]:
    """Delete oldest snapshot dirs across all days, keeping newest `keep`.

    Returns the relative deck dirs that were removed (for logging).
    """
    if keep <= 0 or not data_dir.is_dir():
        return []
    decks = _recent_decks(data_dir, n=10_000)  # all
    if len(decks) <= keep:
        return []
    to_remove = decks[keep:]  # newest first; tail = oldest
    removed: list[str] = []
    for d in to_remove:
        try:
            shutil.rmtree(d)
            removed.append(str(d.relative_to(data_dir)))
        except OSError as e:
            log(data_dir, "ERROR", f"gc failed for {d}: {e!r}")
    return removed


AGGREGATE_FILENAME = "aggregate.jsonl"
BOOKS_AGGREGATE_FILENAME = "books.jsonl"


def _aggregate_path(data_dir: Path) -> Path:
    """Path to the ever-growing cumulative excerpts log.

    Lives at data_dir/aggregate/aggregate.jsonl. Survives snapshot GC.
    Dedup key = (bookId, content) — re-syncing the same excerpt is a no-op.
    """
    return data_dir / "aggregate" / AGGREGATE_FILENAME


def _books_aggregate_path(data_dir: Path) -> Path:
    """Path to the ever-growing books catalog.

    Lives at data_dir/aggregate/books.jsonl. Survives snapshot GC.
    Dedup key = book.id — first push wins, later pushes don't overwrite
    name/author. Mirrors the "first push wins" semantics of excerpts
    aggregate (re-sync doesn't overwrite metadata).
    """
    return data_dir / "aggregate" / BOOKS_AGGREGATE_FILENAME


def _load_books_aggregate(data_dir: Path) -> dict:
    """Return {book_id_str: book_record} from the cumulative books log.

    Missing file → empty dict. Corrupt lines are skipped (logged in caller).
    """
    path = _books_aggregate_path(data_dir)
    out: dict[str, dict] = {}
    if not path.exists():
        return out
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            bid = rec.get("id")
            if bid is None:
                continue
            key = str(bid)
            if key in out:
                # "first push wins" — do not overwrite name/author
                continue
            out[key] = rec
    return out


def _append_books_aggregate(
    data_dir: Path, books: list[dict], deck_id: str
) -> tuple[int, int]:
    """Append books to the cumulative catalog.

    Dedup key = id (str-coerced). First push wins — if a book id is already
    in the catalog, we keep the original record (name/author) and don't
    overwrite. This protects against later payloads that might carry empty
    or stub fields.

    Returns (added, skipped_existing).
    """
    path = _books_aggregate_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = _load_books_aggregate(data_dir)
    added = 0
    skipped = 0
    with open(path, "a", encoding="utf-8") as f:
        for b in books:
            bid = b.get("id")
            if bid is None:
                continue
            key = str(bid)
            if key in existing:
                skipped += 1
                continue
            tagged = dict(b)
            tagged["_firstSeenDeck"] = deck_id
            f.write(json.dumps(tagged, ensure_ascii=False) + "\n")
            existing[key] = tagged
            added += 1
    return added, skipped


def _append_aggregate(data_dir: Path, excerpts: list[dict], deck_id: str) -> int:
    """Append new excerpts to the cumulative log. Returns # actually added."""
    path = _aggregate_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Load existing keys for dedup
    existing: set[tuple] = set()
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                key = (rec.get("bookId"), rec.get("content"))
                existing.add(key)
    added = 0
    with open(path, "a", encoding="utf-8") as f:
        for ex in excerpts:
            key = (ex.get("bookId"), ex.get("content"))
            if key in existing:
                continue
            _cap_content(ex)
            tagged = dict(ex)
            tagged["_firstSeenDeck"] = deck_id
            f.write(json.dumps(tagged, ensure_ascii=False) + "\n")
            existing.add(key)
            added += 1
    return added


def _write_deck(
    data_dir: Path,
    payload: dict,
    batch_count: int = 0,
) -> tuple[bool, str]:
    """Write a full /api/sync payload. Returns (ok, deck_id_or_err)."""
    books = payload.get("books", [])
    excerpts = payload.get("excerpts", [])
    settings = payload.get("reviewSettings", {})

    now = dt.datetime.now()
    deck_id = _deck_id_now()
    deck_dir = data_dir / now.strftime("%Y-%m-%d") / f"{now.strftime('%H%M%S')}_{deck_id}"
    try:
        deck_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        deck_id = deck_id + "-" + os.urandom(1).hex()
        deck_dir = deck_dir.parent / f"{now.strftime('%H%M%S')}_{deck_id}"
        deck_dir.mkdir(parents=True, exist_ok=False)

    try:
        (deck_dir / "books.json").write_text(
            json.dumps(books, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (deck_dir / "review_settings.json").write_text(
            json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        with open(deck_dir / "excerpts.jsonl", "w", encoding="utf-8") as f:
            for ex in excerpts:
                _cap_content(ex)
                f.write(json.dumps(ex, ensure_ascii=False) + "\n")
        _write_index(deck_dir, books, excerpts)
        meta = {
            "deckId": deck_id,
            "receivedAt": dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "source": "single" if batch_count == 0 else "batched",
            "count": len(excerpts),
            "bookCount": len(books),
            "batchCount": batch_count,
        }
        (deck_dir / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError as e:
        log(data_dir, "ERROR", f"deck write failed: {e!r} (deck {deck_id})")
        return False, "SD write failed"
    # Append to the cumulative aggregate (survives snapshot GC, dedup'd)
    try:
        added = _append_aggregate(data_dir, excerpts, deck_id)
        if added != len(excerpts):
            log(data_dir, "INFO", f"aggregate: +{added}/{len(excerpts)} new (rest dup)")
    except OSError as e:
        log(data_dir, "ERROR", f"aggregate append failed: {e!r}")
    # Append to the cumulative books catalog (survives snapshot GC, first-push-wins)
    try:
        b_added, b_skipped = _append_books_aggregate(data_dir, books, deck_id)
        log(data_dir, "INFO",
            f"books aggregate: +{b_added} new, {b_skipped} already known")
    except OSError as e:
        log(data_dir, "ERROR", f"books aggregate append failed: {e!r}")
    log(data_dir, "INFO", f"deck written: {deck_id} ({len(excerpts)} excerpts)")
    _append_event(data_dir, {
        "kind": "oneshot" if batch_count == 0 else "session_end",
        "deckId": deck_id,
        "deckDir": str(deck_dir.relative_to(data_dir)),
        "bookCount": len(books),
        "excerptCount": len(excerpts),
        "batchCount": batch_count,
        "bytes": _dir_size_bytes(deck_dir),
    })
    # GC old snapshots (keep newest MAX_DECK_SNAPSHOTS globally)
    removed = _gc_old_decks(data_dir)
    if removed:
        log(data_dir, "INFO", f"gc removed {len(removed)} old deck(s): {removed}")
    return True, deck_id


def _cap_content(excerpt: dict) -> None:
    c = excerpt.get("content", "")
    if isinstance(c, str) and len(c.encode("utf-8")) > CONTENT_BYTE_CAP:
        excerpt["content"] = c.encode("utf-8")[:CONTENT_BYTE_CAP].decode("utf-8", errors="ignore")


def _append_event(data_dir: Path, record: dict) -> None:
    """Append one line to events.jsonl (atomic for small writes on POSIX).

    Caller is responsible for shape; we just stamp ts and JSON-dump.
    ts is in local time with UTC offset (e.g. 2026-06-12T22:46:15+08:00)
    so the agent can slice HH:MM and show the wall-clock time the user
    actually sees.
    """
    record = {"ts": dt.datetime.now().astimezone().isoformat(timespec="seconds"), **record}
    line = json.dumps(record, ensure_ascii=False) + "\n"
    try:
        with open(_events_path(data_dir), "a", encoding="utf-8") as f:
            f.write(line)
    except OSError as e:
        log(data_dir, "WARN", f"event write failed: {e!r}")


def _dir_size_bytes(d: Path) -> int:
    total = 0
    try:
        for root, _, files in os.walk(d):
            for fn in files:
                try:
                    total += os.path.getsize(Path(root) / fn)
                except OSError:
                    pass
    except OSError:
        pass
    return total


def read_unpushed_events(data_dir: Path) -> tuple[list[dict], int]:
    """Read events.jsonl lines past the cursor; bump cursor to current total.

    Returns (new_lines, total_lines). If cursor is missing or corrupt,
    treats it as 0 (caller should be prepared for full replay on first
    use). Cursor is bumped only when the read succeeds.
    """
    ep = _events_path(data_dir)
    cp = _cursor_path(data_dir)
    try:
        cursor = int(cp.read_text(encoding="utf-8").strip()) if cp.is_file() else 0
    except (OSError, ValueError):
        cursor = 0
    if not ep.is_file():
        return [], cursor
    try:
        all_lines = ep.read_text(encoding="utf-8").splitlines()
    except OSError:
        return [], cursor
    total = len(all_lines)
    if cursor < 0 or cursor > total:
        cursor = 0
    new_lines: list[dict] = []
    for ln in all_lines[cursor:]:
        ln = ln.strip()
        if not ln:
            continue
        try:
            new_lines.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    try:
        cp.write_text(str(total), encoding="utf-8")
    except OSError:
        pass
    return new_lines, total


def _write_index(deck_dir: Path, books: list, excerpts: list) -> None:
    name_by_id = {b.get("id"): b.get("name", "") for b in books}
    with open(deck_dir / "index.jsonl", "w", encoding="utf-8") as f:
        for ex in excerpts:
            joined = {**ex, "bookName": name_by_id.get(ex.get("bookId", ""), "")}
            f.write(json.dumps(joined, ensure_ascii=False) + "\n")


def _session_dir(data_dir: Path, session_id: str) -> Path:
    return data_dir / ".sessions" / session_id


# module-level cursor: tracks the most recent begin() so batch can
# resolve implicit sessionId from clients (e.g. M5Paper) that don't echo it back.
_current_session: dict[str, tuple[Path, str]] = {}  # data_dir -> (data_dir, sessionId)


def _set_current(data_dir: Path, sid: str) -> None:
    _current_session[str(data_dir.resolve())] = (data_dir, sid)


def _get_current(data_dir: Path) -> str | None:
    entry = _current_session.get(str(data_dir.resolve()))
    return entry[1] if entry else None


def _begin_session(data_dir: Path, session_id: str, payload: dict) -> bool:
    sd = _session_dir(data_dir, session_id)
    try:
        sd.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        return True
    (sd / "books.json").write_text(
        json.dumps(payload.get("books", []), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (sd / "review_settings.json").write_text(
        json.dumps(payload.get("reviewSettings", {}), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (sd / "excerpts.jsonl").touch()
    meta = {
        "sessionId": session_id,
        "startedAt": dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "lastBatchAt": None,
        "batchCount": 0,
        "excerptCount": 0,
    }
    (sd / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    _append_event(data_dir, {
        "kind": "session_begin",
        "sessionId": session_id,
        "bookCount": len(payload.get("books", [])),
    })
    _set_current(data_dir, session_id)
    return True


def _append_batch(data_dir: Path, session_id: str, excerpts: list) -> int:
    sd = _session_dir(data_dir, session_id)
    if not sd.is_dir():
        return 0
    lock = sd / ".lock"
    with open(lock, "w") as lf:
        fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
        try:
            with open(sd / "excerpts.jsonl", "a", encoding="utf-8") as f:
                for ex in excerpts:
                    _cap_content(ex)
                    f.write(json.dumps(ex, ensure_ascii=False) + "\n")
        finally:
            fcntl.flock(lf.fileno(), fcntl.LOCK_UN)
    meta_p = sd / "meta.json"
    meta = json.loads(meta_p.read_text(encoding="utf-8"))
    meta["batchCount"] += 1
    meta["excerptCount"] += len(excerpts)
    meta["lastBatchAt"] = dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"
    meta_p.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return len(excerpts)


def _end_session(data_dir: Path, session_id: str) -> tuple[bool, str]:
    sd = _session_dir(data_dir, session_id)
    if not sd.is_dir():
        return False, "not found"
    books = json.loads((sd / "books.json").read_text(encoding="utf-8"))
    settings = json.loads((sd / "review_settings.json").read_text(encoding="utf-8"))
    excerpts = []
    ep = sd / "excerpts.jsonl"
    if ep.is_file():
        for ln in ep.read_text(encoding="utf-8").splitlines():
            if ln.strip():
                excerpts.append(json.loads(ln))
    meta_sess = json.loads((sd / "meta.json").read_text(encoding="utf-8"))
    batch_count = meta_sess.get("batchCount", 0)

    ok, deck_id = _write_deck(
        data_dir,
        {"books": books, "excerpts": excerpts, "reviewSettings": settings},
        batch_count=batch_count,
    )
    if not ok:
        return False, deck_id
    import shutil
    shutil.rmtree(sd, ignore_errors=True)
    if _get_current(data_dir) == session_id:
        _current_session.pop(str(data_dir.resolve()), None)
    return True, deck_id


# ---------- HTTP server ----------

class PaperEchoHandler(BaseHTTPRequestHandler):
    server_version = "PaperEchoReceiver/1.0"
    data_dir: Path = DEFAULT_DATA_DIR  # injected by _make_server

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        log(self.data_dir, "INFO", f"{self.address_string()} {format % args}")

    def do_GET(self) -> None:
        from urllib.parse import urlsplit
        from recto.app import excerpts as admin_ui  # 每请求 import 以便 reload
        parts = urlsplit(self.path)
        if self.path == "/api/status":
            self._send_json(_status_payload(self.data_dir))
            return
        if parts.path.startswith("/admin"):
            admin_ui.handle_admin(self, self.data_dir, parts.path, parts.query)
            return
        self._send_error(404, "not found")

    def do_DELETE(self) -> None:
        if self.path == "/api/data":
            self._send_json({"ok": True})
            return
        self._send_error(404, "not found")

    def do_POST(self) -> None:  # noqa: C901
        from urllib.parse import urlsplit
        from recto.app import excerpts as admin_ui
        parts = urlsplit(self.path)
        if parts.path.startswith("/admin"):
            admin_ui.handle_admin(self, self.data_dir, parts.path, parts.query)
            return
        length = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(length) if length else b""
        if not body:
            self._send_error(400, "empty body")
            return
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_error(400, "invalid JSON")
            return

        if self.path == "/api/sync":
            self._handle_sync(payload)
        elif self.path == "/api/sync/begin":
            self._handle_begin(payload)
        elif self.path == "/api/sync/batch":
            log(self.data_dir, "INFO", f"batch recv: {len(body)}B body={_safe_repr(body)}")
            self._handle_batch(payload)
        elif self.path == "/api/sync/end":
            self._handle_end(payload)
        else:
            self._send_error(404, "not found")

    def _handle_sync(self, payload: dict) -> None:
        if not isinstance(payload, dict):
            self._send_error(400, "invalid JSON")
            return
        ok, result = _write_deck(self.data_dir, payload, batch_count=0)
        if not ok:
            self._send_error(500, result)
            return
        self._send_json({
            "ok": True,
            "deckId": result,
            "excerptCount": len(payload.get("excerpts", [])),
            "bookCount": len(payload.get("books", [])),
        })

    def _handle_begin(self, payload: dict) -> None:
        if not isinstance(payload, dict):
            self._send_error(400, "invalid JSON")
            return
        sid = str(payload.get("sessionId") or payload.get("id") or f"sess-{int(time.time())}")
        _begin_session(self.data_dir, sid, payload)
        self._send_json({"ok": True, "sessionId": sid})

    def _handle_batch(self, payload: dict) -> None:
        if not isinstance(payload, dict):
            log(self.data_dir, "WARN", f"batch reject: not dict, payload={_safe_repr(payload)}")
            self._send_error(400, "invalid JSON")
            return
        excerpts = payload.get("excerpts", [])
        if not isinstance(excerpts, list):
            log(self.data_dir, "WARN", f"batch reject: excerpts_type={type(excerpts).__name__} keys={sorted(payload.keys())}")
            self._send_error(400, "invalid JSON")
            return
        if len(excerpts) > BATCH_HARD_LIMIT:
            self._send_error(400, "batch too large")
            return
        # sessionId: client-supplied > cursor (last begin) > 400
        sid_raw = payload.get("sessionId")
        if isinstance(sid_raw, str) and sid_raw:
            sid = sid_raw
        else:
            sid = _get_current(self.data_dir)
            if not sid:
                log(self.data_dir, "WARN", f"batch reject: no sessionId and no cursor keys={sorted(payload.keys())}")
                self._send_error(400, "no active session; call /api/sync/begin first or include sessionId")
                return
        accepted = _append_batch(self.data_dir, sid, excerpts)
        if accepted == 0 and not _session_dir(self.data_dir, sid).is_dir():
            self._send_error(404, "not found")
            return
        self._send_json({"ok": True, "accepted": accepted, "sessionId": sid})

    def _handle_end(self, payload: dict) -> None:
        if not isinstance(payload, dict):
            self._send_error(400, "invalid JSON")
            return
        sid_raw = payload.get("sessionId")
        if isinstance(sid_raw, str) and sid_raw:
            sid = sid_raw
        else:
            sid = _get_current(self.data_dir)
            if not sid:
                self._send_error(400, "no active session; include sessionId or call /api/sync/begin first")
                return
        ok, result = _end_session(self.data_dir, sid)
        if not ok:
            self._send_error(404 if result == "not found" else 500, result)
            return
        self._send_json({"ok": True, "deckId": result})

    def _send_json(self, obj: dict) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, code: int, msg: str) -> None:
        body = json.dumps({"error": msg}, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _status_payload(data_dir: Path) -> dict:
    decks = _recent_decks(data_dir, n=1)
    excerpt_count = 0
    book_count = 0
    if decks:
        meta_p = decks[0] / "meta.json"
        if meta_p.is_file():
            m = json.loads(meta_p.read_text(encoding="utf-8"))
            excerpt_count = m.get("count", 0)
            book_count = m.get("bookCount", 0)
    try:
        st = os.statvfs(str(data_dir))
        sd_free_kb = (st.f_bavail * st.f_frsize) // 1024
    except OSError:
        sd_free_kb = 0
    pending = 0
    sess_dir = data_dir / ".sessions"
    if sess_dir.is_dir():
        pending = sum(1 for _ in sess_dir.iterdir() if _.is_dir())
    return {
        "excerptCount": excerpt_count,
        "bookCount": book_count,
        "sdFreeKB": sd_free_kb,
        "version": "1.0",
        "pendingSessions": pending,
    }


def _make_server(bind: str, port: int, data_dir: Path):
    class BoundHandler(PaperEchoHandler):
        pass
    BoundHandler.data_dir = data_dir
    ThreadingHTTPServer.allow_reuse_address = True
    return ThreadingHTTPServer((bind, port), BoundHandler)


def _make_admin_server(bind: str, port: int, data_dir: Path):
    """Server that ONLY serves /admin/* routes. /api/sync and /api/status return 404.

    Used on the dedicated admin port (default 8081), so M5Paper devices on 8080
    stay physically isolated from the web admin UI.
    """
    class AdminOnlyHandler(PaperEchoHandler):
        pass

    AdminOnlyHandler.data_dir = data_dir

    original_do_GET = AdminOnlyHandler.do_GET
    original_do_POST = AdminOnlyHandler.do_POST
    original_do_DELETE = AdminOnlyHandler.do_DELETE

    def do_GET(self) -> None:  # type: ignore[no-redef]
        from urllib.parse import urlsplit
        parts = urlsplit(self.path)
        if parts.path.startswith("/admin"):
            original_do_GET(self)
            return
        self._send_error(404, "admin port: /admin/* only")

    def do_POST(self) -> None:  # type: ignore[no-redef]
        from urllib.parse import urlsplit
        parts = urlsplit(self.path)
        if parts.path.startswith("/admin"):
            original_do_POST(self)
            return
        self._send_error(404, "admin port: /admin/* only")

    def do_DELETE(self) -> None:  # type: ignore[no-redef]
        self._send_error(404, "admin port: /admin/* only")

    AdminOnlyHandler.do_GET = do_GET  # type: ignore[assignment]
    AdminOnlyHandler.do_POST = do_POST  # type: ignore[assignment]
    AdminOnlyHandler.do_DELETE = do_DELETE  # type: ignore[assignment]

    ThreadingHTTPServer.allow_reuse_address = True
    return ThreadingHTTPServer((bind, port), AdminOnlyHandler)


def _run_server(data_dir: Path, bind: str, port: int) -> None:
    srv = _make_server(bind, port, data_dir)
    log(data_dir, "INFO", f"listening on {bind}:{port}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
        _pid_path(data_dir).unlink(missing_ok=True)
        _config_path(data_dir).unlink(missing_ok=True)
        log(data_dir, "INFO", "server closed")


def cmd_admin(data_dir: Path, bind: str, admin_port: int) -> int:
    """Foreground admin-only listener. Spawned as a child of cmd_start's daemon.

    No daemonize: parent already did the double-fork. We just bind, log, serve_forever.
    Writes its own pid file so the parent's stop can clean it up.
    """
    if admin_port <= 0:
        log(data_dir, "ERROR", "admin port is 0; refusing to start admin listener")
        return 2

    ok, err = probe_bind(admin_port)
    if not ok:
        log(data_dir, "ERROR", f"admin bind to {bind}:{admin_port} failed: {err}")
        return 2

    admin_pid_path = _admin_pid_path(data_dir)
    admin_pid_path.write_text(str(os.getpid()), encoding="utf-8")
    log(data_dir, "INFO", f"admin listener started (pid {os.getpid()}, {bind}:{admin_port})")

    srv = _make_admin_server(bind, admin_port, data_dir)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
        _admin_pid_path(data_dir).unlink(missing_ok=True)
        log(data_dir, "INFO", "admin listener closed")
    return 0


# ---------- CLI ----------

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="device", description=__doc__)
    p.add_argument("cmd", choices=["start", "stop", "restart", "status", "tail", "foreground", "backfill", "admin"])
    p.add_argument("--data-dir", type=Path, default=None)
    p.add_argument("--bind", type=str, default=None)
    p.add_argument("--port", type=int, default=None)
    args = p.parse_args(argv)

    data_dir, bind, port, admin_port = resolve_config()
    if args.data_dir is not None:
        data_dir = args.data_dir
    if args.bind is not None:
        bind = args.bind
    if args.port is not None:
        port = args.port

    if args.cmd == "start":
        return cmd_start(data_dir, bind, port, foreground=False, admin_port=admin_port)
    if args.cmd == "foreground":
        return cmd_start(data_dir, bind, port, foreground=True, admin_port=None)
    if args.cmd == "stop":
        return cmd_stop(data_dir)
    if args.cmd == "restart":
        return cmd_restart(data_dir, bind, port, admin_port)
    if args.cmd == "status":
        return cmd_status(data_dir)
    if args.cmd == "tail":
        return cmd_tail(data_dir)
    if args.cmd == "backfill":
        return cmd_backfill(data_dir)
    if args.cmd == "admin":
        return cmd_admin(data_dir, bind, admin_port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
