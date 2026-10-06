#!/usr/bin/env python3
"""Overnight read-only stability probe for the mirroir fork.

Every INTERVAL seconds it runs ONE describe_screen (read-only — no taps, no typing, nothing is
changed on the phone) against a fresh mirroir-fork process and records:
  - ocr_time_ms and element_count (from the _meta line),
  - wall time of the whole call,
  - outcome: ok | ocr_timeout | error | hang | no_window.

Purpose: validate the OCR-timeout fix in the wild (a wedged Vision must now surface as a bounded
error, never an infinite hang) and gather long-run stability data. Pings Ruslan on Telegram on the
first anomaly and once per hour with a summary.

Each probe runs in its own subprocess with a hard WALL cap, so the monitor itself can never hang.
Run: ~/MyProjects/telegram-notify/.venv/bin/python -u stress/stress_describe.py
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
WALL_CAP = 60           # kill a probe process after this many seconds (fix should bound OCR to ~15s)
MAX_HOURS = 9
META = re.compile(r"ocr_time_ms=(\d+).*?element_count=(\d+)")


def probe() -> dict:
    """One describe_screen against a fresh fork process. Never raises; never hangs past WALL_CAP."""
    reqs = "\n".join([
        '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05",'
        '"capabilities":{},"clientInfo":{"name":"stress","version":"1"}}}',
        '{"jsonrpc":"2.0","method":"notifications/initialized"}',
        '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"describe_screen",'
        '"arguments":{"omit_screenshot":true}}}',
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
    text = ""
    for line in out.splitlines():
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if msg.get("id") == 2:
            r = msg.get("result") or msg.get("error") or {}
            if isinstance(r, dict) and "content" in r:
                text = "\n".join(c.get("text", "") for c in r["content"] if c.get("type") == "text")
            else:
                text = json.dumps(r)
    low = text.lower()
    if "timed out" in low:
        return {"outcome": "ocr_timeout", "wall_ms": wall_ms, "detail": text[:120]}
    if "no window" in low or "not connected" in low or "is the 'iphone' window" in low:
        return {"outcome": "no_window", "wall_ms": wall_ms}
    m = META.search(text)
    if m:
        return {"outcome": "ok", "wall_ms": wall_ms,
                "ocr_ms": int(m.group(1)), "elements": int(m.group(2))}
    return {"outcome": "error", "wall_ms": wall_ms, "detail": text[:120]}


def main() -> int:
    deadline = time.time() + MAX_HOURS * 3600
    log_path = "/Users/ruslan/MyProjects/mirroir-mcp-fork/stress/stress.log"
    log = open(log_path, "a")
    n = 0
    anomalies = 0
    hangs = 0
    ok_ms: list[int] = []
    last_summary = time.time()
    notified_anomaly = False
    send_telegram_message("🌙 mirroir stress-probe запущен: read-only describe_screen каждые 5 мин, "
                          "проверяю фикс OCR-таймаута. Напишу при аномалии и раз в час.")
    while time.time() < deadline:
        r = probe()
        n += 1
        line = f"{time.strftime('%H:%M:%S')}\t{r.get('outcome')}\twall={r.get('wall_ms')}ms\t" \
               f"ocr={r.get('ocr_ms','-')}\tel={r.get('elements','-')}\t{r.get('detail','')}"
        log.write(line + "\n"); log.flush()
        print(line, flush=True)
        if r["outcome"] == "ok":
            ok_ms.append(r["wall_ms"])
        else:
            anomalies += 1
            if r["outcome"] == "hang":
                hangs += 1
            if not notified_anomaly:
                notified_anomaly = True
                send_telegram_message(
                    f"⚠️ mirroir probe #{n}: {r['outcome']} (wall {r.get('wall_ms')}ms). "
                    + ("ВАЖНО: это полное зависание >60с — фикс таймаута не сработал."
                       if r["outcome"] == "hang"
                       else "Это ОГРАНИЧЕННАЯ ошибка (фикс сработал — виса нет)."
                       if r["outcome"] == "ocr_timeout"
                       else "Похоже, Mirroring отвалился (ожидаемо из-за биометрии).")
                    + f"\n{r.get('detail','')}")
        if time.time() - last_summary >= 3600:
            last_summary = time.time()
            avg = sum(ok_ms) / len(ok_ms) if ok_ms else 0
            send_telegram_message(
                f"⏱ mirroir probe за час: {n} проб, ok {len(ok_ms)}, аномалий {anomalies} "
                f"(зависаний >60с: {hangs}). Средний describe_screen {avg:.0f}ms.")
            ok_ms = ok_ms[-1:]
        time.sleep(INTERVAL)
    send_telegram_message(f"✅ mirroir stress-probe завершён: {n} проб, аномалий {anomalies}, "
                          f"полных зависаний {hangs}. Лог: stress/stress.log")
    return 0


if __name__ == "__main__":
    sys.exit(main())
