#!/usr/bin/env bash
# Recto 启停 —— 一个进程, 一个端口 (默认 5015)
#
#   bash run.sh start|stop|restart|status
#
# 端口/地址来自 config.toml 的 [recto], 可用 RECTO_HOST / RECTO_PORT 覆盖。
set -uo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
DATA="${RECTO_DATA_DIR:-$DIR/data}"
PIDFILE="$DATA/recto.pid"
LOG="$DATA/recto.log"
PORT="${RECTO_PORT:-5015}"

mkdir -p "$DATA"

running() { [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; }

start() {
  if running; then
    echo "已在运行 (pid $(cat "$PIDFILE")) → http://0.0.0.0:$PORT"
    return 0
  fi
  cd "$DIR" || exit 1
  PYTHONPATH="$DIR/src" nohup python3 -m recto.cli serve >> "$LOG" 2>&1 &
  echo $! > "$PIDFILE"
  sleep 1.5
  if running; then
    echo "已启动 (pid $(cat "$PIDFILE")) → http://0.0.0.0:$PORT"
  else
    echo "启动失败, 看 $LOG"
    tail -20 "$LOG"
    rm -f "$PIDFILE"
    return 1
  fi
}

stop() {
  if [ -f "$PIDFILE" ]; then
    PID="$(cat "$PIDFILE")"
    if kill -0 "$PID" 2>/dev/null; then
      kill "$PID"
      for _ in 1 2 3 4 5 6 7 8 9 10; do
        kill -0 "$PID" 2>/dev/null || break
        sleep 0.3
      done
      kill -0 "$PID" 2>/dev/null && kill -9 "$PID" 2>/dev/null
      echo "已停止 (pid $PID)"
    else
      echo "pid 文件残留, 进程已不在"
    fi
    rm -f "$PIDFILE"
    return 0
  fi
  # 没有 pid 文件: 按端口找 (只杀 cmdline 里带 recto.cli 的那个)
  echo "没有 pid 文件; 端口 $PORT 上的进程请手动确认"
  return 0
}

status() {
  if running; then
    echo "running (pid $(cat "$PIDFILE"), :$PORT)"
  else
    echo "not running"
  fi
}

case "${1:-status}" in
  start)   start ;;
  stop)    stop ;;
  restart) stop; sleep 1; start ;;
  status)  status ;;
  *) echo "用法: bash run.sh start|stop|restart|status"; exit 2 ;;
esac
