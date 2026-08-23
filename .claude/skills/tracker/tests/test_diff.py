#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Офлайн-проверка сравнения прогонов: правила значимости из KNOWLEDGE.md.

Сеть не нужна — прогоны собираются прямо здесь. Проверяется ровно зона
ответственности diff_runs.py: товары сопоставлены по URL, поля сравнены,
незначимое отброшено, недобранные строки не выданы за движение цены,
а сводка для Telegram собрана по строке на изменение.

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


def row(url, regular=None, sale=None, credit=False, status="ok", note="", title=None):
    return {"url": url, "title": title, "regular_price": regular, "sale_price": sale,
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
            row("u/gone",       regular=50.00, title="Насос Ручеек-1"),  # пропадёт из списка
        ])

        curr = run_file(tmp, "curr.json", "2026-08-22", [
            # Порядок нарочно другой: сопоставление идёт по URL, не по позиции.
            row("u/credit-on",  regular=90.00,  credit=True),
            row("u/noise",      regular=100.03),
            row("u/edge",       regular=105.00),
            row("u/edge-float", regular=503.4645),
            row("u/jump-up",    regular=360.00, title="Насос Джилекс Водомет 55/50"),
            row("u/jump-down",  regular=400.00),
            row("u/sale-gone",  regular=200.00, sale=None),
            row("u/sale-new",   regular=250.00, sale=225.00),
            row("u/sale-moved", regular=400.00, sale=270.00),
            row("u/sale-noise", regular=400.00, sale=297.00),
            row("u/credit-off", regular=90.00,  credit=False),
            row("u/broken-now", status="fetch_failed", note="HTTP Error 498: "),
            row("u/was-broken", regular=310.00),
            row("u/new",        regular=77.00, title="Насос вибрационный STAVR"),
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

        # 8. Вывод по пунктам, под ссылкой — название товара.
        check("1. " in text and "2. " in text, f"вывод не пронумерован:\n{text}")
        check("\n   - Цена: 300.00 → 360.00 BYN (+20.0%)" in text,
              f"изменения не оформлены пунктами под товаром:\n{text}")
        check("\n   Насос Джилекс Водомет 55/50\n" in text,
              f"название товара не встало под ссылкой:\n{text}")
        # Название пропавшего товара берётся из прошлого прогона — в текущем его нет.
        check("Насос Ручеек-1" in text, "у пропавшего товара потерялось название")
        # Старый файл прогона названий не содержит: строка без title не должна ломаться.
        check("u/sale-gone" in text, "товар без названия выпал из вывода")

        # 9. Прошлого прогона нет — это первый запуск, а не «всё новое».
        first = json.loads(diff(str(curr), "--format", "json").stdout)
        check(first["first_run"] is True, "одиночный прогон не признан первым запуском")
        check(first["changes"] == [], "у первого запуска появились изменения")
        check("Первый прогон" in diff(str(curr)).stdout,
              "первый запуск не объявлен в текстовом выводе")

        # 10. Битый файл — понятная ошибка, а не трейсбек.
        garbage = tmp / "garbage.json"
        garbage.write_text("{не json", encoding="utf-8")
        broken = diff(str(garbage), str(curr))
        check(broken.returncode == 2,
              f"нечитаемый прогон дал код {broken.returncode}, ожидался 2")
        check("Traceback" not in broken.stderr, "нечитаемый прогон уронил скрипт трейсбеком")

        # 11. Сводка для Telegram: пункт на товар, строка на изменение, ссылка внутри.
        tg = diff(str(prev), str(curr), "--format", "telegram")
        check(tg.returncode == 0, f"сводка упала: {tg.stderr.strip()[:200]}")
        tg_lines = tg.stdout.splitlines()
        bullets = [l.strip() for l in tg_lines if l.strip().startswith("• ")]
        headers = [l for l in tg_lines if l and l[0].isdigit() and ". " in l[:4]]

        expected_changes = sum(len(item["lines"]) for item in result["changes"])
        check(len(bullets) == expected_changes,
              f"строк в сводке {len(bullets)}, изменений {expected_changes}")
        check(len(headers) == len(result["changes"]),
              f"пунктов {len(headers)}, товаров с изменениями {len(result['changes'])}")
        check(f"Изменения цен {result['curr_date']}" in tg.stdout,
              "в заголовке сводки нет даты текущего прогона")

        # Товар — заголовок пункта, а не приписка к каждой строке.
        titled = [h for h in headers if "Насос Джилекс Водомет 55/50" in h]
        check(len(titled) == 1, f"товар с названием дал {len(titled)} заголовков, нужен один")
        check(not any("Насос Джилекс" in b for b in bullets),
              "название товара повторяется в строках изменений — группировка не сработала")

        # У каждого пункта есть ссылка, по которой можно перейти.
        for item in result["changes"]:
            check(item["url"] in tg.stdout, f"в сводке нет ссылки на {item['url']}")

        # Названия нет — заголовком идёт ссылка, и второй раз она не печатается.
        check(any(h.endswith("u/sale-gone") for h in headers),
              "товар без названия не стал заголовком со ссылкой")
        check(tg.stdout.count("u/sale-gone") == 1,
              "у безымянного товара ссылка напечатана дважды")

        # Суть изменения не потерялась при перегруппировке.
        price_line = [b for b in bullets if "360.00" in b]
        check(len(price_line) == 1 and "+20.0%" in price_line[0],
              f"в строке изменения нет было/стало/процента: {price_line!r}")
        check(price_line and price_line[0].startswith("• цена:"),
              f"строка изменения не начинается со строчной буквы: {price_line!r}")

        # Итоговая строка — общая с текстовым форматом, цифры не расходятся.
        text_tail = [l for l in diff(str(prev), str(curr)).stdout.splitlines()
                     if l.startswith("Итого:")]
        tg_tail = [l for l in tg_lines if l.startswith("Итого:")]
        check(tg_tail == text_tail,
              f"итоговые строки разъехались: {tg_tail} против {text_tail}")

        # Сводка — обычный текст: без разметки её не испортит отправка как есть.
        check("<a href" not in tg.stdout and "](" not in tg.stdout,
              "в сводку просочилась разметка — она уйдёт в Telegram сырыми тегами")

        # 12. Значимого нет — сообщение всё равно уходит, с датой и счётчиком.
        quiet_prev = run_file(tmp, "quiet_prev.json", "2026-08-21", [row("u/noise", regular=100.00)])
        quiet_curr = run_file(tmp, "quiet_curr.json", "2026-08-22", [row("u/noise", regular=100.03)])
        quiet = diff(str(quiet_prev), str(quiet_curr), "--format", "telegram")
        check("Значимых изменений цен на 2026-08-22 нет." in quiet.stdout,
              f"нет точной формулировки про отсутствие изменений: {quiet.stdout!r}")
        check("отброшено незначимых: 1" in quiet.stdout,
              f"в пустой сводке нет счётчика отброшенных: {quiet.stdout!r}")
        check(quiet.stdout.strip(), "пустой прогон дал пустое сообщение — бот промолчит")

        # 13. Первый прогон — одна строка, считать нечего.
        first_tg = diff(str(curr), "--format", "telegram").stdout
        check("Первый прогон 2026-08-22" in first_tg,
              f"первый прогон не объявлен в сводке: {first_tg!r}")
        check("Итого:" not in first_tg, "у первого прогона появилась итоговая строка")

    for line in failures:
        print(f"FAIL {line}")
    print("OK все проверки пройдены" if not failures else f"\n{len(failures)} проблем(ы)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
