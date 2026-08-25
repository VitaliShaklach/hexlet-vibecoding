#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Проверка YAML-шапок скиллов и агентов в .claude/.

Ловит ошибку, из-за которой GitHub показывает «Error in user YAML: mapping
values are not allowed in this context»: двоеточие с пробелом внутри
некавыченного значения. Для YAML `: ` — разделитель «ключ: значение», поэтому
разбор обрывается на середине описания.

    description: Только оформление: считать и сравнивать   ← ломается
    description: "Только оформление: считать и сравнивать"  ← в порядке

Загрузчик скиллов Claude Code разбирает шапку мягче и такой файл проглатывает,
поэтому ошибка живёт незамеченной, пока файл не откроют на GitHub. Отсюда и
тест: сломанную шапку должно быть видно до пуша.

    python3 test_frontmatter.py

Коды возврата: 0 — все шапки в порядке, 1 — есть сломанные.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# PyYAML есть не везде, а зависимостей у проекта нет. Полный разбор — когда
# библиотека доступна; проверка на `: ` работает в любом случае, и именно она
# ловит ту самую ошибку.
try:
    import yaml
except ImportError:
    yaml = None

ROOT = Path(__file__).resolve().parent
CLAUDE_DIR = ROOT / ".claude"
FRONTMATTER = re.compile(r"^---\n(.*?)\n---\n", re.S)
KEY_LINE = re.compile(r"^(\w[\w-]*):\s+(.*)$")
REQUIRED = ("name", "description")


def find_files() -> list[Path]:
    """Файлы, у которых шапка обязана быть: SKILL.md скиллов и файлы агентов."""
    files = sorted(CLAUDE_DIR.glob("agents/*.md"))
    files += sorted(CLAUDE_DIR.glob("skills/*/SKILL.md"))
    return files


def is_quoted(value: str) -> bool:
    return len(value) > 1 and value[0] == value[-1] and value[0] in "\"'"


def check_file(path: Path) -> list[str]:
    """Список проблем в шапке одного файла; пустой — всё в порядке."""
    problems: list[str] = []
    text = path.read_text(encoding="utf-8")

    match = FRONTMATTER.match(text)
    if not match:
        return ["нет YAML-шапки в начале файла (--- ... ---)"]
    head = match.group(1)

    # Нумерация от начала файла: так строку видно в редакторе сразу. GitHub в
    # своём сообщении считает строки внутри шапки и показывает на единицу меньше.
    keys = {}
    for number, line in enumerate(head.split("\n"), start=2):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key_match = KEY_LINE.match(line)
        if not key_match:
            continue
        key, value = key_match.groups()
        keys[key] = value
        # Та самая ошибка: `: ` в некавыченном значении обрывает разбор.
        if ": " in value and not is_quoted(value):
            column = line.index(": ", len(key) + 1) + 1
            problems.append(
                f"строка файла {number}, колонка {column}: «{key}» содержит "
                f"двоеточие с пробелом и не взято в кавычки")

    for key in REQUIRED:
        if key not in keys:
            problems.append(f"нет обязательного поля «{key}»")

    if yaml is not None:
        try:
            yaml.safe_load(head)
        except yaml.YAMLError as error:
            mark = getattr(error, "problem_mark", None)
            where = (f"строка файла {mark.line + 2}, колонка {mark.column + 1}: "
                     if mark else "")
            problems.append(f"{where}{getattr(error, 'problem', error)}")

    return problems


def main() -> int:
    files = find_files()
    if not files:
        print("не найдено ни одного файла со шапкой — проверять нечего", file=sys.stderr)
        return 1

    broken = 0
    for path in files:
        problems = check_file(path)
        shown = path.relative_to(ROOT)
        if problems:
            broken += 1
            print(f"FAIL {shown}")
            for problem in problems:
                print(f"     {problem}")
        else:
            print(f"OK   {shown}")

    print()
    if broken:
        print(f"сломанных шапок: {broken} из {len(files)}")
        print("починка: взять значение в кавычки — description: \"текст: с двоеточием\"")
        return 1

    note = "" if yaml is not None else "  (PyYAML нет — полный разбор пропущен)"
    print(f"OK все проверки пройдены{note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
