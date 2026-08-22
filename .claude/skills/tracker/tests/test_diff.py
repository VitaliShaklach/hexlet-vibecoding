#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Офлайн-проверка сравнения прогонов: правила значимости из KNOWLEDGE.md.

Сеть не нужна — прогоны собираются прямо здесь. Проверяется ровно зона
ответственности diff_runs.py: товары сопоставлены по URL, поля сравнены,
незначимое отброшено, недобранные строки не выданы за движение цены.

    python3 .claude/skills/tracker/tests/test_diff.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
DIFFER = SKILL_DIR / "scripts" / "diff_runs.py"


def row(url, regular=None, sale=None, credit=False, status="ok", note=""):
    return {"url": url, "regular_price": regular, "sale_price": sale,
            "has_credit": credit, "status": status, "note": note}


def run_file(tmp: Path, name: str, date: str, rows: list[dict]) -> Path:
    path = tmp / name
    payload = {
        "date": date,
        "started_at": f"{date} 09:00 UTC",
        "total": len(rows),
        "ok": sum(1 for r in rows if r["status"] == "ok"),
        "failed": sum(1 for r in rows if r["status"] != "ok"),
        "rows": rows,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def diff(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(DIFFER), *args],
                          capture_output=True, text=True, timeout=60)


def lines_for(result: dict, key: str, url: str) -> list[str]:
    for item in result[key]:
        if item["url"] == url:
            return item["lines"]
    return []


def main() -> int:
    failures = []

    def check(condition, message):
        if not condition:
            failures.append(message)

    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)

        prev = run_file(tmp, "prev.json", "2026-08-21", [
            row("u/noise",      regular=100.00),                 # шум: +0.03 %
            row("u/edge",       regular=100.00),                 # ровно +5 %
            row("u/edge-float", regular=479.49),                 # ровно +5 %, но в double неточно
            row("u/jump-up",    regular=300.00),                 # +20 %
            row("u/jump-down",  regular=500.00),                 # −20 %
            row("u/sale-gone",  regular=200.00, sale=180.00),    # скидка исчезнет
            row("u/sale-new",   regular=250.00),                 # скидка появится
            row("u/sale-moved", regular=400.00, sale=300.00),    # скидка −10 %
            row("u/sale-noise", regular=400.00, sale=300.00),    # скидка −1 %
            row("u/credit-on",  regular=90.00,  credit=False),   # рассрочка появится
            row("u/credit-off", regular=90.00,  credit=True),    # рассрочка пропадёт
            row("u/broken-now", regular=150.00),                 # сломается в текущем
            row("u/was-broken", status="no_price", note="цена не найдена"),
            row("u/gone",       regular=50.00),                  # пропадёт из списка
        ])

        curr = run_file(tmp, "curr.json", "2026-08-22", [
            # Порядок нарочно другой: сопоставление идёт по URL, не по позиции.
            row("u/credit-on",  regular=90.00,  credit=True),
            row("u/noise",      regular=100.03),
            row("u/edge",       regular=105.00),
            row("u/edge-float", regular=503.4645),
            row("u/jump-up",    regular=360.00),
            row("u/jump-down",  regular=400.00),
            row("u/sale-gone",  regular=200.00, sale=None),
            row("u/sale-new",   regular=250.00, sale=225.00),
            row("u/sale-moved", regular=400.00, sale=270.00),
            row("u/sale-noise", regular=400.00, sale=297.00),
            row("u/credit-off", regular=90.00,  credit=False),
            row("u/broken-now", status="fetch_failed", note="HTTP Error 498: "),
            row("u/was-broken", regular=310.00),
            row("u/new",        regular=77.00),
        ])

        completed = diff(str(prev), str(curr), "--format", "json")
        check(completed.returncode == 0, f"сравнение упало: {completed.stderr.strip()[:200]}")
        result = json.loads(completed.stdout)

        changed = {item["url"] for item in result["changes"]}
        skipped = {item["url"] for item in result["skipped"]}
        no_data = {item["url"] for item in result["no_data"]}

        # 1. Порог цены: строго больше 5 % значимо, 5 % и меньше — нет.
        check("u/jump-up" in changed, "рост цены на 20 % не признан значимым")
        check("u/jump-down" in changed, "падение цены на 20 % не признано значимым")
        check("u/noise" not in changed, "колебание цены на копейки попало в значимые")
        check("u/noise" in skipped, "копеечное колебание не отмечено как отброшенное")
        check("u/edge" not in changed, "ровно 5 % не должны быть значимыми — порог строгий")
        check("u/edge" in skipped, "граничные 5 % не отмечены как отброшенное")
        check("u/edge-float" not in changed,
              "ровно 5 % пролезли в значимые из-за погрешности double")

        # Направление и величина видны в самой формулировке.
        up = " ".join(lines_for(result, "changes", "u/jump-up"))
        check("300.00" in up and "360.00" in up and "+20.0%" in up,
              f"в строке роста цены нет было/стало/процента: {up!r}")
        down = " ".join(lines_for(result, "changes", "u/jump-down"))
        check("-20.0%" in down, f"падение цены без знака минус: {down!r}")

        # 2. Скидка: появление и исчезновение значимы всегда, порог — только к величине.
        check("Скидка пропала" in " ".join(lines_for(result, "changes", "u/sale-gone")),
              "исчезнувшая скидка не попала в значимые")
        check("Появилась скидка" in " ".join(lines_for(result, "changes", "u/sale-new")),
              "появившаяся скидка не попала в значимые")
        check("u/sale-moved" in changed, "изменение скидки на 10 % не признано значимым")
        check("u/sale-noise" not in changed, "изменение скидки на 1 % попало в значимые")
        check("u/sale-noise" in skipped, "мелкое изменение скидки не отмечено как отброшенное")

        # 3. Рассрочка: булево поле, порога нет — значимо любое изменение.
        check("Появилась рассрочка" in " ".join(lines_for(result, "changes", "u/credit-on")),
              "появление рассрочки не попало в значимые")
        check("Рассрочка пропала" in " ".join(lines_for(result, "changes", "u/credit-off")),
              "пропажа рассрочки не попала в значимые")

        # 4. Состав списка — отдельной строкой на товар.
        check("Новый товар в списке" in " ".join(lines_for(result, "changes", "u/new")),
              "новый URL не отмечен как новый товар")
        check("Товар пропал из списка" in " ".join(lines_for(result, "changes", "u/gone")),
              "исчезнувший URL не отмечен как пропавший товар")

        # 5. Недобранная строка — не изменение: сбой парсера не выдаём за цену.
        check("u/broken-now" in no_data and "u/broken-now" not in changed,
              "недоступная страница выдана за пропажу цены")
        check("u/was-broken" in no_data and "u/was-broken" not in changed,
              "первое удачное измерение выдано за появление цены")

        # 6. Товары без изменений в вывод не идут вовсе.
        check(len(result["changes"]) == 9,
              f"значимых товаров {len(result['changes'])}, ожидалось 9: {sorted(changed)}")

        # 7. Текстовый вывод: только значимое, но со счётчиком отброшенного.
        text = diff(str(prev), str(curr)).stdout
        check("u/jump-up" in text, "значимый товар не попал в текстовый вывод")
        check("u/noise" not in text, "отброшенный товар протёк в текстовый вывод")
        check("отброшено незначимых: 4" in text,  # noise, edge, edge-float, sale-noise
              f"в сводке неверное число отброшенных:\n{text}")

        # 8. Прошлого прогона нет — это первый запуск, а не «всё новое».
        first = json.loads(diff(str(curr), "--format", "json").stdout)
        check(first["first_run"] is True, "одиночный прогон не признан первым запуском")
        check(first["changes"] == [], "у первого запуска появились изменения")
        check("Первый прогон" in diff(str(curr)).stdout,
              "первый запуск не объявлен в текстовом выводе")

        # 9. Битый файл — понятная ошибка, а не трейсбек.
        garbage = tmp / "garbage.json"
        garbage.write_text("{не json", encoding="utf-8")
        broken = diff(str(garbage), str(curr))
        check(broken.returncode == 2,
              f"нечитаемый прогон дал код {broken.returncode}, ожидался 2")
        check("Traceback" not in broken.stderr, "нечитаемый прогон уронил скрипт трейсбеком")

    for line in failures:
        print(f"FAIL {line}")
    print("OK все проверки пройдены" if not failures else f"\n{len(failures)} проблем(ы)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
