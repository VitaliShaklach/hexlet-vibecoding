#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Отправка сообщения в Telegram. Только стандартная библиотека.

Токен и chat id берутся из окружения — в коде их нет и быть не должно:

    TELEGRAM_BOT_TOKEN   токен бота от @BotFather
    TELEGRAM_CHAT_ID     кому слать: id пользователя, группы или канала

Использование:
    python send.py "текст"
    python send.py -                       # текст со stdin
    echo "отчёт" | python send.py -
    python send.py --parse-mode HTML "<b>жирный</b>"

Коды возврата: 0 — отправлено, 1 — Telegram отказал или сеть недоступна,
2 — не заданы переменные окружения или пустой текст.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

API_BASE = "https://api.telegram.org"
TIMEOUT = 20

# Предел Telegram на текст сообщения — 4096, берём с запасом: считаем символы,
# а Telegram считает единицы UTF-16, и эмодзи в его счёте идут за два.
CHUNK_LIMIT = 4000


def split_message(text: str, limit: int = CHUNK_LIMIT) -> list[str]:
    """Разбить длинный текст на части, по возможности по границе строк."""
    chunks: list[str] = []
    rest = text
    while len(rest) > limit:
        head = rest[:limit]
        cut = head.rfind("\n")
        if cut <= 0:  # одна сплошная строка — режем по пределу
            cut = limit
        chunks.append(rest[:cut].rstrip("\n"))
        rest = rest[cut:].lstrip("\n")
    if rest:
        chunks.append(rest)
    return chunks


def send_message(token: str, chat_id: str, text: str, parse_mode: str | None = None) -> dict:
    """Один вызов sendMessage. Возвращает разобранный ответ Telegram."""
    payload = {
        "chat_id": chat_id,
        "text": text,
        # Ссылок в отчёте много, и превью первой из них раздувает сообщение.
        "disable_web_page_preview": True,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode

    request = urllib.request.Request(
        f"{API_BASE}/bot{token}/sendMessage",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8"))


def _fail(message: str, code: int) -> int:
    print(message, file=sys.stderr)
    return code


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Отправить сообщение в Telegram через бота.")
    parser.add_argument("text", help='текст сообщения; "-" — прочитать со stdin')
    parser.add_argument("--parse-mode", choices=("HTML", "Markdown", "MarkdownV2"),
                        help="разметка текста; по умолчанию текст уходит как есть")
    args = parser.parse_args(argv)

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    missing = [name for name, value in
               (("TELEGRAM_BOT_TOKEN", token), ("TELEGRAM_CHAT_ID", chat_id)) if not value]
    if missing:
        return _fail(f"не заданы переменные окружения: {', '.join(missing)}", 2)

    text = sys.stdin.read() if args.text == "-" else args.text
    text = text.strip()
    if not text:
        return _fail("пустой текст: отправлять нечего", 2)

    chunks = split_message(text)
    for index, chunk in enumerate(chunks, 1):
        try:
            result = send_message(token, chat_id, chunk, args.parse_mode)
        except urllib.error.HTTPError as error:
            # У Telegram причина отказа лежит в теле ответа, а не в статусе.
            detail = error.read().decode("utf-8", "replace").strip()
            try:
                detail = json.loads(detail).get("description", detail)
            except json.JSONDecodeError:
                pass
            return _fail(f"Telegram отказал ({error.code}): {detail}", 1)
        except urllib.error.URLError as error:
            return _fail(f"сеть недоступна: {error.reason}", 1)

        if not result.get("ok"):
            return _fail(f"Telegram вернул ok=false: {result}", 1)

        part = f" (часть {index} из {len(chunks)})" if len(chunks) > 1 else ""
        print(f"отправлено{part}: message_id={result['result']['message_id']}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
