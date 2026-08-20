#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Обход зашитого списка URL и сборка единой таблицы прогона.

Своей логики разбора цен здесь нет и быть не должно: за каждый URL отвечает
скилл extract-price — скрипт вызывает его CLI как подпроцесс и только
складывает ответы в таблицу.

Использование:
    python3 run_tracker.py                    # markdown-таблица в stdout
    python3 run_tracker.py --format json      # тот же прогон в JSON
    python3 run_tracker.py --format csv
    python3 run_tracker.py --urls my.txt      # другой список вместо зашитого
    python3 run_tracker.py --out runs/2026-08-20.md

Коды возврата: 0 — все URL отработали, 1 — часть строк осталась без цены
(их нужно добрать вручную по инструкции extract-price).
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
DEFAULT_URLS = SKILL_DIR / "references" / "urls.txt"
EXTRACT_PRICE = SKILL_DIR.parent / "extract-price" / "scripts" / "extract_price.py"

# Запас на медленные магазины: extract-price сам ждёт страницу 25 секунд.
PER_URL_TIMEOUT = 60

COLUMNS = ("url", "regular_price", "sale_price", "has_credit")


def read_urls(path: Path) -> list[str]:
    urls = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            urls.append(line)
    return urls


def call_extract_price(url: str) -> dict:
    """Один вызов скилла extract-price. Возвращает строку будущей таблицы."""
    row = {
        "url": url,
        "regular_price": None,
        "sale_price": None,
        "has_credit": None,
        "status": "error",
        "note": "",
    }
    try:
        completed = subprocess.run(
            [sys.executable, str(EXTRACT_PRICE), url],
            capture_output=True, text=True, timeout=PER_URL_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        row["note"] = f"таймаут {PER_URL_TIMEOUT} c"
        return row

    stdout = completed.stdout.strip()
    if completed.returncode == 3:
        row["status"] = "fetch_failed"
        row["note"] = _detail(completed.stderr) or "страница не открылась"
        return row

    if not stdout:
        row["note"] = _detail(completed.stderr) or "пустой ответ extract-price"
        return row

    try:
        payload = json.loads(stdout.splitlines()[-1])
    except json.JSONDecodeError:
        row["note"] = "ответ extract-price не разобрался как JSON"
        return row

    row.update({key: payload.get(key) for key in COLUMNS[1:]})
    if row["regular_price"] is None:
        row["status"] = "no_price"
        row["note"] = "цена не найдена, нужен ручной проход по extract-price"
    else:
        row["status"] = "ok"
    return row


def _detail(stderr: str) -> str:
    """Достать короткое пояснение из stderr extract-price."""
    for line in reversed(stderr.strip().splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            return line[:160]
        if isinstance(parsed, dict):
            return str(parsed.get("detail") or parsed.get("error") or line)[:160]
    return ""


def fmt(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "да" if value else "нет"
    return f"{value:.2f}" if isinstance(value, float) else str(value)


def to_markdown(run: dict) -> str:
    out = [
        f"# Прогон tracker — {run['started_at']}",
        "",
        f"Товаров в списке: {run['total']} · с ценой: {run['ok']} · без цены: {run['failed']}",
        "",
        "| # | URL | regular_price | sale_price | has_credit |",
        "|---|-----|---------------|------------|------------|",
    ]
    for index, row in enumerate(run["rows"], 1):
        out.append(
            f"| {index} | {row['url']} | {fmt(row['regular_price'])} | "
            f"{fmt(row['sale_price'])} | {fmt(row['has_credit'])} |"
        )
    problems = [r for r in run["rows"] if r["status"] != "ok"]
    if problems:
        out += ["", "## Строки без цены", ""]
        out += [f"- {r['url']} — {r['status']}: {r['note']}" for r in problems]
    return "\n".join(out) + "\n"


def to_csv(run: dict) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for row in run["rows"]:
        writer.writerow(row)
    return buffer.getvalue()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Обойти список URL через extract-price и собрать таблицу прогона.")
    parser.add_argument("--urls", default=str(DEFAULT_URLS),
                        help="файл со списком URL (по умолчанию — зашитый)")
    parser.add_argument("--format", choices=("markdown", "json", "csv"),
                        default="markdown", help="формат таблицы прогона")
    parser.add_argument("--out", help="записать таблицу в файл, а не в stdout")
    args = parser.parse_args(argv)

    urls = read_urls(Path(args.urls))
    rows = []
    for index, url in enumerate(urls, 1):
        print(f"[{index}/{len(urls)}] extract-price → {url}", file=sys.stderr)
        row = call_extract_price(url)
        print(f"    {row['status']}: regular={row['regular_price']} "
              f"sale={row['sale_price']} credit={row['has_credit']}", file=sys.stderr)
        rows.append(row)

    run = {
        "started_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "total": len(rows),
        "ok": sum(1 for r in rows if r["status"] == "ok"),
        "failed": sum(1 for r in rows if r["status"] != "ok"),
        "rows": rows,
    }

    renderers = {
        "markdown": to_markdown,
        "csv": to_csv,
        "json": lambda r: json.dumps(r, ensure_ascii=False, indent=2) + "\n",
    }
    text = renderers[args.format](run)

    if args.out:
        path = Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        print(f"таблица прогона записана: {path}", file=sys.stderr)
    else:
        sys.stdout.write(text)

    return 0 if run["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
