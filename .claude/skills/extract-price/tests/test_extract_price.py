#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Офлайн-проверка extract_price.py на фикстурах.

Запуск:  python3 .claude/skills/extract-price/tests/test_extract_price.py
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
SCRIPT = HERE.parent / "scripts" / "extract_price.py"
FIXTURES = HERE / "fixtures"

CASES = [
    # (файл, ожидаемый результат, что именно проверяем)
    (
        "sale_jsonld.html",
        {"regular_price": 1349.0, "sale_price": 1199.99, "has_credit": True},
        "JSON-LD + зачёркнутая старая цена; цены похожих товаров и меню игнорируются",
    ),
    (
        "sale_jsstate.html",
        {"regular_price": 2450.0, "sale_price": 2199.0, "has_credit": True},
        "цена во встроенном JS-состоянии (oldPrice/price), рассрочка через Halva",
    ),
    (
        "plain_microdata.html",
        {"regular_price": 289.5, "sale_price": None, "has_credit": False},
        "микроразметка без скидки; «Рассрочка» только в подвале — не считается",
    ),
]


def run(fixture: str) -> dict:
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--file", str(FIXTURES / fixture)],
        capture_output=True, text=True, check=False,
    )
    if completed.returncode not in (0, 2):
        raise AssertionError(f"{fixture}: код возврата {completed.returncode}\n{completed.stderr}")
    return json.loads(completed.stdout)


def main() -> int:
    failures = 0
    for fixture, expected, what in CASES:
        actual = run(fixture)
        if actual == expected:
            print(f"OK   {fixture}: {what}")
        else:
            failures += 1
            print(f"FAIL {fixture}: {what}")
            print(f"     ожидалось: {json.dumps(expected, ensure_ascii=False)}")
            print(f"     получено:  {json.dumps(actual, ensure_ascii=False)}")

    print(f"\n{len(CASES) - failures}/{len(CASES)} проверок пройдено")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
