#!/usr/bin/env python3
"""Overnight keep-alive + stability probe for the mirroir fork, on the iPhone HOME screen only.

Every INTERVAL seconds it runs, against a fresh fork process:
  1. describe_screen  (read-only; records ocr_time_ms, element_count, detects hang/no-window)
  2. press_home       (one neutral Home press — a real input event so Mirroring does not idle out;
                       it only ever returns to the home screen, changes nothing, opens nothing)

It NEVER launches apps, taps arbitrary coordinates, types, or touches anything but Home. Purpose:
keep the Mirroring session warm while Ruslan sleeps AND validate the OCR-timeout fix over many hours.
Pings Ruslan on Telegram on the first anomaly, on recovery, and once per hour with a summary.

Each phone call runs in its own subprocess with a hard WALL cap, so this monitor can never hang.
Run: ~/MyProjects/telegram-notify/.venv/bin/python -u stress/keepalive.py
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time

sys.path.insert(0, "/Users/ruslan/MyProjects/telegram-notify")
from server import send_telegram_message  # noqa: E402

FORK_BIN = "/Users/ruslan/.local/bin/mirroir-mcp-fork"
INTERVAL = 300
WALL_CAP = 60
MAX_HOURS = 9
LOG = "/Users/ruslan/MyProjects/mirroir-mcp-fork/stress/keepalive.log"
META = re.compile(r"ocr_time_ms=(\d+).*?element_count=(\d+)")


def run_calls() -> dict:
    """describe_screen then press_home against a fresh fork process. Never raises, never hangs."""
    reqs = "\n".join([
        '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05",'
        '"capabilities":{},"clientInfo":{"name":"keepalive","version":"1"}}}',
        '{"jsonrpc":"2.0","method":"notifications/initialized"}',
        '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"describe_screen",'
        '"arguments":{"omit_screenshot":true}}}',
        '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"press_home","arguments":{}}}',
    ]) + "\n"
    started = time.perf_counter()
    try:
        out = subprocess.run(
            [FORK_BIN], input=reqs, capture_output=True, text=True, timeout=WALL_CAP,
            env={"PATH": "/usr/bin:/bin", "HOME": "/Users/ruslan",
                 "MIRROIR_SCREEN_DESCRIBER_MODE": "ocr"},
        ).stdout
    except subprocess.TimeoutExpired:
        return {"outcome": "hang", "wall_ms": int((time.perf_counter() - started) * 1000)}
    wall_ms = int((time.perf_counter() - started) * 1000)
    desc = ""
    home_ok = False
    for line in out.splitlines():
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        r = msg.get("result") or msg.get("error") or {}
        text = ""
        if isinstance(r, dict) and "content" in r:
            text = "\n".join(c.get("text", "") for c in r["content"] if c.get("type") == "text")
        if msg.get("id") == 2:
            desc = text
        elif msg.get("id") == 3:
            home_ok = "home screen" in text.lower()
    low = desc.lower()
    if "timed out" in low:
        return {"outcome": "ocr_timeout", "wall_ms": wall_ms, "detail": desc[:120]}
    if "no window" in low or "not connected" in low or "is the 'iphone' window" in low or not desc:
        return {"outcome": "no_window", "wall_ms": wall_ms}
    m = META.search(desc)
    return {"outcome": "ok" if home_ok else "home_failed", "wall_ms": wall_ms,
            "ocr_ms": int(m.group(1)) if m else None, "elements": int(m.group(2)) if m else None}


def main() -> int:
    deadline = time.time() + MAX_HOURS * 3600
    log = open(LOG, "a")
    n = anomalies = hangs = 0
    ok_ms: list[int] = []
    last_summary = time.time()
    down = False
    send_telegram_message("🌙 mirroir keep-alive запущен: каждые 5 мин читаю домашний экран и жму Home "
                          "(ничего не открываю), держу связь и проверяю фикс. Напишу при сбое и раз в час.")
    while time.time() < deadline:
        r = run_calls()
        n += 1
        line = (f"{time.strftime('%H:%M:%S')}\t{r['outcome']}\twall={r.get('wall_ms')}ms"
                f"\tocr={r.get('ocr_ms','-')}\tel={r.get('elements','-')}\t{r.get('detail','')}")
        log.write(line + "\n"); log.flush(); print(line, flush=True)
        if r["outcome"] == "ok":
            ok_ms.append(r["wall_ms"])
            if down:
                down = False
                send_telegram_message(f"✅ Связь с iPhone восстановилась (проба #{n}).")
        else:
            anomalies += 1
            if r["outcome"] == "hang":
                hangs += 1
            if not down:
                down = True
                tag = {"hang": "ПОЛНОЕ зависание >60с — фикс таймаута НЕ сработал",
                       "ocr_timeout": "ограниченная ошибка — фикс сработал, виса нет",
                       "no_window": "Mirroring отвалился (вероятно биометрия/блокировка)",
                       "home_failed": "Home не нажался"}.get(r["outcome"], r["outcome"])
                send_telegram_message(f"⚠️ iPhone keep-alive, проба #{n}: {tag}. "
                                      "Связь потеряна — возможно, нужен твой Touch ID на Mac.")
        if time.time() - last_summary >= 3600:
            last_summary = time.time()
            avg = sum(ok_ms) / len(ok_ms) if ok_ms else 0
            send_telegram_message(f"⏱ За час: {n} проб, ok {len(ok_ms)}, сбоев {anomalies} "
                                  f"(зависаний >60с: {hangs}). Средний describe_screen {avg:.0f}ms.")
            ok_ms = ok_ms[-1:]
        time.sleep(INTERVAL)
    send_telegram_message(f"✅ keep-alive завершён: {n} проб, сбоев {anomalies}, зависаний {hangs}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
