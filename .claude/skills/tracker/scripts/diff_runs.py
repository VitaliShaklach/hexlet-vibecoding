#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Сравнение двух прогонов tracker и отбор значимых изменений.

Товары сопоставляются по URL, сравниваются regular_price, sale_price и
has_credit. Пороги и формулировки — из KNOWLEDGE.md рядом с этим скиллом,
здесь они только запрограммированы; расходиться эти два файла не должны.

Использование:
    python3 diff_runs.py prev.json curr.json                 # текст для человека
    python3 diff_runs.py prev.json curr.json --format json   # то же машинно
    python3 diff_runs.py --curr curr.json                    # прошлого нет: первый прогон
    python3 diff_runs.py prev.json curr.json --all           # + отброшенные незначимые

Коды возврата: 0 — сравнение прошло (значимые изменения могли найтись и нет),
2 — файл прогона не читается.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Порог значимости для цен: строго больше 5 % отклонения от прошлого значения.
# Ровно 5 % и меньше — колебание, о котором не уведомляем (KNOWLEDGE.md).
THRESHOLD_PCT = 5.0

# Ровно пороговое отклонение считается в double неточно: 479.49 → 503.4645 даёт
# 5.000000000000007 и без допуска пролезло бы в значимые. Допуск держит границу
# на стороне молчания — как и требует правило «выше 5 %».
THRESHOLD_EPS = 1e-9

# Строка прогона участвует в сравнении, только если её удалось заполнить.
OK_STATUS = "ok"


def load_run(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("rows"), list):
        raise ValueError(f"{path}: не похоже на файл прогона (нет списка rows)")
    return data


def index_rows(run: dict) -> dict[str, dict]:
    """Строки прогона по URL — URL и есть ключ сопоставления товаров."""
    return {row["url"]: row for row in run["rows"] if row.get("url")}


def pick_title(*rows) -> str | None:
    """Название товара из первой строки, где оно есть.

    Берём из текущего прогона, а для пропавшего товара — из прошлого. Старые
    файлы прогонов названий не содержат вовсе: тогда останется одна ссылка.
    """
    for row in rows:
        if row and row.get("title"):
            return row["title"]
    return None


def pct_change(old: float, new: float) -> float | None:
    """Отклонение нового значения от старого в процентах, со знаком."""
    if not old:  # от нуля процент не считается
        return None
    return (new - old) / old * 100


def is_significant(old: float, new: float) -> bool:
    """Значимо ли изменение величины: строго больше порога в любую сторону."""
    delta = pct_change(old, new)
    if delta is None:
        return old != new  # было 0, стало не 0 — это изменение, а не шум
    return abs(delta) > THRESHOLD_PCT + THRESHOLD_EPS


def money(value) -> str:
    return f"{value:.2f}"


def signed(delta: float | None) -> str:
    return "" if delta is None else f" ({delta:+.1f}%)"


def compare_row(prev: dict, curr: dict) -> tuple[list[str], list[str]]:
    """Изменения по одному товару: (значимые, отброшенные как незначимые)."""
    significant: list[str] = []
    skipped: list[str] = []

    # --- обычная цена -------------------------------------------------
    old_price, new_price = prev.get("regular_price"), curr.get("regular_price")
    if old_price is not None and new_price is not None and old_price != new_price:
        delta = pct_change(old_price, new_price)
        text = f"Цена: {money(old_price)} → {money(new_price)} BYN{signed(delta)}"
        (significant if is_significant(old_price, new_price) else skipped).append(text)

    # --- скидка -------------------------------------------------------
    # Появление и исчезновение скидки значимы всегда: это смена состояния,
    # а не колебание величины, и порог к ним не применяется.
    old_sale, new_sale = prev.get("sale_price"), curr.get("sale_price")
    if old_sale is None and new_sale is not None:
        off = pct_change(new_price, new_sale) if new_price else None
        tail = f"{signed(off)[:-1]} от цены)" if off is not None else ""
        significant.append(f"Появилась скидка: {money(new_sale)} BYN{tail}")
    elif old_sale is not None and new_sale is None:
        significant.append(f"Скидка пропала (была {money(old_sale)} BYN)")
    elif old_sale is not None and new_sale is not None and old_sale != new_sale:
        delta = pct_change(old_sale, new_sale)
        text = f"Скидка: {money(old_sale)} → {money(new_sale)} BYN{signed(delta)}"
        (significant if is_significant(old_sale, new_sale) else skipped).append(text)

    # --- рассрочка ----------------------------------------------------
    # Поле булево, порога нет: любое изменение условий значимо.
    old_credit, new_credit = prev.get("has_credit"), curr.get("has_credit")
    if old_credit is not None and new_credit is not None and old_credit != new_credit:
        significant.append("Появилась рассрочка" if new_credit else "Рассрочка пропала")

    return significant, skipped


def diff_runs(prev: dict | None, curr: dict) -> dict:
    """Полный разбор пары прогонов: значимое, отброшенное и то, что не сравнить."""
    result = {
        "prev_date": prev.get("date") if prev else None,
        "curr_date": curr.get("date"),
        "first_run": prev is None,
        "watched": len(index_rows(curr)),
        "changes": [],      # значимое — только это идёт пользователю
        "skipped": [],      # отброшенные незначимые: нужны для сводки
        "no_data": [],      # строки, которые нечем сравнивать
    }
    if prev is None:
        return result

    prev_rows, curr_rows = index_rows(prev), index_rows(curr)
    # Под наблюдением — все товары обоих прогонов: пропавший из списка тоже считается.
    result["watched"] = len(set(prev_rows) | set(curr_rows))

    # Порядок вывода — как в текущем прогоне, чтобы сходился с таблицей.
    for url, curr_row in curr_rows.items():
        prev_row = prev_rows.get(url)

        # Состав списка: нового товара в прошлом прогоне просто нет.
        if prev_row is None:
            result["changes"].append({"url": url, "title": pick_title(curr_row),
                                      "lines": ["Новый товар в списке"]})
            continue

        # Недобранная строка значения не даёт: сбой парсера — не движение цены.
        if prev_row.get("status") != OK_STATUS or curr_row.get("status") != OK_STATUS:
            broken = curr_row if curr_row.get("status") != OK_STATUS else prev_row
            result["no_data"].append({
                "url": url,
                "title": pick_title(curr_row, prev_row),
                "reason": broken.get("note") or broken.get("status") or "нет данных",
            })
            continue

        significant, skipped = compare_row(prev_row, curr_row)
        title = pick_title(curr_row, prev_row)
        if significant:
            result["changes"].append({"url": url, "title": title, "lines": significant})
        if skipped:
            result["skipped"].append({"url": url, "title": title, "lines": skipped})

    # Пропавшие из списка — в конце: в текущем прогоне их строк уже нет.
    for url in prev_rows:
        if url not in curr_rows:
            result["changes"].append({"url": url, "title": pick_title(prev_rows[url]),
                                      "lines": ["Товар пропал из списка"]})

    return result


def to_text(result: dict, show_all: bool = False) -> str:
    if result["first_run"]:
        return "Первый прогон, сравнивать не с чем.\n"

    out = [f"Значимые изменения: {result['prev_date']} → {result['curr_date']}", ""]

    if result["changes"]:
        # По пунктам: номер, ссылка, под ней название товара, дальше сами изменения.
        for index, item in enumerate(result["changes"], 1):
            out.append(f"{index}. {item['url']}")
            if item.get("title"):
                out.append(f"   {item['title']}")
            out += [f"   - {line}" for line in item["lines"]]
            out.append("")
    else:
        out += ["Значимых изменений нет.", ""]

    skipped_count = sum(len(i["lines"]) for i in result["skipped"])
    out.append(
        f"Итого: товаров с изменениями {len(result['changes'])} из {result['watched']} · "
        f"отброшено незначимых: {skipped_count} · "
        f"нет данных: {len(result['no_data'])}"
    )

    if show_all and result["skipped"]:
        out += ["", "Отброшено как незначимое (≤ 5 %):"]
        for index, item in enumerate(result["skipped"], 1):
            out.append(f"{index}. {item['url']}")
            if item.get("title"):
                out.append(f"   {item['title']}")
            out += [f"   - {line}" for line in item["lines"]]

    if result["no_data"]:
        out += ["", "Нет данных для сравнения:"]
        for index, item in enumerate(result["no_data"], 1):
            out.append(f"{index}. {item['url']}")
            if item.get("title"):
                out.append(f"   {item['title']}")
            out.append(f"   - {item['reason']}")

    return "\n".join(out) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Сравнить два прогона tracker и показать только значимые изменения.")
    parser.add_argument("runs", nargs="*", metavar="ПРОГОН",
                        help="два файла — прошлый и текущий прогон; "
                             "один файл — текущий, прошлого ещё нет")
    parser.add_argument("--curr", help="текущий прогон явно (равнозначно одному "
                                       "позиционному файлу)")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--all", action="store_true",
                        help="показать и отброшенные незначимые изменения")
    args = parser.parse_args(argv)

    # Прошлого прогона может не быть — тогда задан только текущий.
    if args.curr and args.runs:
        parser.error("текущий прогон задан дважды: позиционно и через --curr")
    if args.curr:
        prev_path, curr_path = None, args.curr
    elif len(args.runs) == 2:
        prev_path, curr_path = args.runs
    elif len(args.runs) == 1:
        prev_path, curr_path = None, args.runs[0]
    else:
        parser.error("нужен хотя бы файл текущего прогона")

    try:
        curr = load_run(Path(curr_path))
        prev = load_run(Path(prev_path)) if prev_path else None
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"не читается файл прогона: {exc}", file=sys.stderr)
        return 2

    result = diff_runs(prev, curr)

    if args.format == "json":
        sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    else:
        sys.stdout.write(to_text(result, show_all=args.all))
    return 0


if __name__ == "__main__":
    sys.exit(main())
