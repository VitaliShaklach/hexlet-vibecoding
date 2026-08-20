#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Офлайн-проверка tracker: обход списка, вызов extract-price, сборка таблицы.

Сеть не нужна — вместо магазинов берутся фикстуры скилла extract-price,
подставленные как file://-адреса. Проверяется ровно то, за что отвечает
tracker: все URL списка обойдены, для каждого вызван extract-price,
из ответов собрана одна таблица прогона.

    python3 .claude/skills/tracker/tests/test_tracker.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
RUNNER = SKILL_DIR / "scripts" / "run_tracker.py"
FIXTURES = SKILL_DIR.parent / "extract-price" / "tests" / "fixtures"

# Фикстура → ожидаемая строка таблицы (regular, sale, credit).
CASES = [
    ("sale_jsonld.html", 1349.0, 1199.99, True),
    ("sale_jsstate.html", 2450.0, 2199.0, True),
    ("plain_microdata.html", 289.5, None, False),
]


def run(urls_file: Path, fmt: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(RUNNER), "--urls", str(urls_file), "--format", fmt],
        capture_output=True, text=True, timeout=120,
    )


def main() -> int:
    failures = []
    with tempfile.TemporaryDirectory() as tmp:
        urls_file = Path(tmp) / "urls.txt"
        urls_file.write_text(
            "# тестовый список\n"
            + "\n".join(f"file://{FIXTURES / name}" for name, *_ in CASES) + "\n",
            encoding="utf-8",
        )

        completed = run(urls_file, "json")
        run_data = json.loads(completed.stdout)
        rows = run_data["rows"]

        # 1. Все ссылки списка отработаны — по строке на каждую, в том же порядке.
        if len(rows) != len(CASES):
            failures.append(f"строк в таблице {len(rows)}, ожидалось {len(CASES)}")
        for row, (name, regular, sale, credit) in zip(rows, CASES):
            if not row["url"].endswith(name):
                failures.append(f"порядок строк нарушен: {row['url']} вместо {name}")
                continue
            # 2. extract-price отработал и вернул свой объект цены.
            got = (row["regular_price"], row["sale_price"], row["has_credit"])
            if got != (regular, sale, credit):
                failures.append(f"{name}: получено {got}, ожидалось {(regular, sale, credit)}")
            if row["status"] != "ok":
                failures.append(f"{name}: статус {row['status']} ({row['note']})")

        if run_data["total"] != len(CASES) or run_data["ok"] != len(CASES):
            failures.append(f"итоги прогона: {run_data['total']}/{run_data['ok']}")

        # 3. Таблица собирается в markdown: шапка на месте, строк столько же.
        markdown = run(urls_file, "markdown").stdout
        if "| URL | regular_price | sale_price | has_credit |" not in markdown:
            failures.append("в markdown-таблице нет шапки с полями цены")
        if markdown.count("| file://") != len(CASES):
            failures.append("в markdown-таблице не все строки товаров")

        # Битый URL не должен ронять прогон — строка остаётся, статус не ok.
        broken = Path(tmp) / "broken.txt"
        broken.write_text(f"file://{FIXTURES / 'no-such-file.html'}\n", encoding="utf-8")
        result = run(broken, "json")
        broken_rows = json.loads(result.stdout)["rows"]
        if len(broken_rows) != 1 or broken_rows[0]["status"] == "ok":
            failures.append("недоступная страница не превратилась в строку с ошибкой")
        elif result.returncode != 1:
            failures.append(f"код возврата при неполном прогоне {result.returncode}, ожидался 1")

    for line in failures:
        print(f"FAIL {line}")
    print("OK все проверки пройдены" if not failures else f"\n{len(failures)} проблем(ы)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
