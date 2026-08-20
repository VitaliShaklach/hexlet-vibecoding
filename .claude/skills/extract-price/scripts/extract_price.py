#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Извлечение цены товара со страницы интернет-магазина (белорусские рубли).

Использование:
    python3 extract_price.py <URL>
    python3 extract_price.py --file page.html
    python3 extract_price.py <URL> --debug     # разбор источников в stderr

Вывод в stdout — ровно один JSON-объект:
    {"regular_price": 1349.0, "sale_price": 1199.99, "has_credit": true}

Коды возврата: 0 — цена найдена, 2 — цена не найдена, 3 — страницу не скачать.
Зависимостей нет, только стандартная библиотека.
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
import urllib.error
import urllib.request
from html.parser import HTMLParser

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)
TIMEOUT = 25

# Разделители разрядов, которые реально встречаются в вёрстке.
SPACES = "\u0020\u00a0\u202f\u2009\u2007"  # space, nbsp, narrow nbsp, thin, figure
_SP = "[" + SPACES + "]"

MIN_PRICE = 0.5
MAX_PRICE = 1_000_000.0

CURRENCY_HINT = re.compile(r"BYN|Br\b|руб|р\.|бел", re.I)

NUM_RE = re.compile(r"(?<!\d)(\d{1,3}(?:" + _SP + r"\d{3})+|\d+)(?:[.,](\d{1,2}))?(?!\d)")

# Ключи цены в JSON-LD и во встроенном состоянии страницы.
KEYS_CURRENT = (
    "price", "lowprice", "saleprice", "sale_price", "discountprice",
    "discount_price", "finalprice", "final_price", "currentprice",
    "current_price", "pricevalue",
)
KEYS_OLD = (
    "oldprice", "old_price", "priceold", "price_old", "regularprice",
    "regular_price", "listprice", "list_price", "baseprice", "base_price",
    "strikeprice", "pricewithoutdiscount", "price_without_discount",
)

# Число обязано заканчиваться цифрой — иначе в значение затягивается запятая-разделитель.
_NUM_TOKEN = r"\d(?:[\d" + SPACES + r".,]{0,18}\d)?"
SCRIPT_PRICE_RE = re.compile(
    r"[\"'](" + "|".join(KEYS_CURRENT + KEYS_OLD) + r")[\"']\s*:\s*"
    r"(?:\"(" + _NUM_TOKEN + r")\"|'(" + _NUM_TOKEN + r")'|(" + _NUM_TOKEN + r"))",
    re.I,
)

# Рассрочка / кредит. Белорусские сервисы названы явно — по ним меньше ложных срабатываний.
CREDIT_RE = re.compile(
    r"рассрочк|в\s+рассрочку|оплат\w*\s+частями|частями\s+без\s+переплат|"
    r"кредит|halva|халва|карт\w*\s+покупок|черепаха|смарт[\s-]?карт|"
    r"магнит\w*\s+плюс|ползёт\s+в\s+рассрочку",
    re.I,
)


def parse_price_string(raw) -> float | None:
    """Разобрать цену из строки JSON/атрибута: '2 450,00' / '1199.99' -> float."""
    s = re.sub(_SP, "", str(raw).strip())
    if not s:
        return None
    if "," in s and "." in s:
        # Десятичный разделитель — тот, что стоит последним.
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        parts = s.split(",")
        s = parts[0] + "." + parts[1] if len(parts) == 2 and len(parts[1]) in (1, 2) \
            else "".join(parts)
    # Артикул вида 0838728: ведущий ноль без дробной части — это код, а не сумма.
    whole = s.split(".")[0]
    if len(whole) > 1 and whole.startswith("0") and "." not in s:
        return None
    try:
        value = float(s)
    except ValueError:
        return None
    if not (MIN_PRICE <= value <= MAX_PRICE):
        return None
    return round(value, 2)


def scan_prices(text: str, require_currency: bool) -> list[float]:
    """Найти в тексте суммы. require_currency — требовать рядом BYN/руб/р."""
    found = []
    for m in NUM_RE.finditer(text):
        # "1.234.567" — не цена: за совпадением снова идёт разделитель с цифрой.
        if re.match(r"[.,]\d", text[m.end():m.end() + 2]):
            continue
        # "−11%" — размер скидки, а не сумма.
        if re.match(r"\s*%", text[m.end():m.end() + 3]):
            continue
        int_part, frac = m.group(1), m.group(2)
        digits = re.sub(_SP, "", int_part)
        if len(digits) > 1 and digits[0] == "0":
            continue  # артикул вида 0838728, а не сумма
        value = float(digits)
        if frac:
            value += int(frac.ljust(2, "0")) / 100.0
        if not (MIN_PRICE <= value <= MAX_PRICE):
            continue
        if require_currency and not CURRENCY_HINT.search(text[m.end():m.end() + 14]):
            continue
        found.append(round(value, 2))
    return found


VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
}


class PriceParser(HTMLParser):
    """Собирает кандидатов на цену, текст без «обвязки» и содержимое скриптов."""

    SKIP_TAGS = {"script", "style", "noscript", "template", "svg"}
    STRUCK_TAGS = {"del", "s", "strike"}

    PRICE_CLS = re.compile(r"price|cost|amount|цена|стоим", re.I)
    OLD_CLS = re.compile(r"old|was|strike|through|crossed|prev|before|бы(?:ла|ло)", re.I)
    # Блоки, чьи цены не относятся к этому товару.
    CHROME_CLS = re.compile(
        r"header|footer|nav|menu|breadcrumb|cookie|subscri|banner|"
        r"recommend|similar|related|analog|accessor|viewed|carousel|карусел|"
        r"также|похож|сопутств",
        re.I,
    )
    # Блоки рассрочки: там платёж за месяц, а не цена товара.
    CREDIT_CLS = re.compile(
        r"credit|kredit|rassroch|рассроч|installment|halva|monthly|"
        r"per[\s_-]?month|mesyac|мес",
        re.I,
    )
    CHROME_TAGS = {"header", "footer", "nav"}
    PRICE_ITEMPROPS = {"price", "lowprice"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[dict] = []
        self.candidates: list[dict] = []
        self.body_chunks: list[str] = []
        self.scripts: list[tuple[str, str]] = []
        self.meta_prices: list[tuple[str, float]] = []
        self._script_open = False
        self._script_type = ""
        self._script_buf: list[str] = []
        self._in_skip = 0
        self._order = 0

    # --- служебное -----------------------------------------------------

    def _attrs(self, attrs) -> dict:
        return {k.lower(): (v or "") for k, v in attrs}

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        a = self._attrs(attrs)

        if tag == "script":
            self._script_open = True
            self._script_type = a.get("type", "").lower()
            self._script_buf = []
            self._in_skip += 1
            return
        if tag in self.SKIP_TAGS:
            self._in_skip += 1
            return

        itemprop = a.get("itemprop", "").lower()
        content = a.get("content", "")
        if itemprop in self.PRICE_ITEMPROPS and content:
            value = parse_price_string(content)
            if value is not None:
                self.meta_prices.append(("itemprop:" + itemprop, value))
        prop = a.get("property", "").lower() or a.get("name", "").lower()
        if prop in ("og:price:amount", "product:price:amount", "price") and content:
            value = parse_price_string(content)
            if value is not None:
                self.meta_prices.append(("meta:" + prop, value))

        if tag in VOID_TAGS:
            return

        parent = self.stack[-1] if self.stack else None
        cls = a.get("class", "")
        self.stack.append({
            "tag": tag,
            "class": cls,
            "itemprop": itemprop,
            "content": content,
            "text": [],
            "struck": bool(parent and parent["struck"]) or tag in self.STRUCK_TAGS,
            "chrome": bool(parent and parent["chrome"]) or tag in self.CHROME_TAGS
                      or bool(self.CHROME_CLS.search(cls)),
            "credit": bool(parent and parent["credit"]) or bool(self.CREDIT_CLS.search(cls)),
        })

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == "script":
            if self._script_open:
                self.scripts.append((self._script_type, "".join(self._script_buf)))
                self._script_open = False
                self._in_skip = max(0, self._in_skip - 1)
            return
        if tag in self.SKIP_TAGS:
            self._in_skip = max(0, self._in_skip - 1)
            return
        if tag in VOID_TAGS:
            return

        # Незакрытые теги: закрываем всё до ближайшего совпадения.
        index = None
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i]["tag"] == tag:
                index = i
                break
        if index is None:
            return
        while len(self.stack) > index:
            self._close(self.stack.pop())

    def handle_data(self, data):
        if self._script_open:
            self._script_buf.append(data)
            return
        if self._in_skip:
            return
        if self.stack:
            self.stack[-1]["text"].append(data)
            if not self.stack[-1]["chrome"]:
                self.body_chunks.append(data)
        else:
            self.body_chunks.append(data)

    def close(self):
        while self.stack:
            self._close(self.stack.pop())
        super().close()

    # --- сбор кандидатов ------------------------------------------------

    def _close(self, frame: dict) -> None:
        text = "".join(frame["text"])
        if self.stack:
            self.stack[-1]["text"].append(text)
        if frame["chrome"] or frame["credit"]:
            return

        has_hint = bool(self.PRICE_CLS.search(frame["class"])) \
            or frame["itemprop"] in self.PRICE_ITEMPROPS
        is_old = frame["struck"] or bool(self.OLD_CLS.search(frame["class"]))
        if not (has_hint or frame["struck"]):
            return

        depth = len(self.stack) + 1
        for value in scan_prices(text, require_currency=not has_hint):
            self._order += 1
            self.candidates.append({
                "value": value,
                "old": is_old,
                "depth": depth,
                "order": self._order,
                "source": "dom:" + (frame["class"] or frame["tag"]),
            })

    # --- итоги ----------------------------------------------------------

    def dom_prices(self) -> list[dict]:
        """Дедупликация: для каждой суммы оставляем самый глубокий (точный) элемент."""
        best: dict[float, dict] = {}
        for cand in self.candidates:
            prev = best.get(cand["value"])
            if prev is None or cand["depth"] > prev["depth"]:
                best[cand["value"]] = cand
        return sorted(best.values(), key=lambda c: c["order"])

    def body_text(self) -> str:
        return re.sub(r"\s+", " ", " ".join(self.body_chunks))


def walk_json(node, out: dict) -> None:
    """Рекурсивно вытащить из JSON значения по ключам цены."""
    if isinstance(node, dict):
        for key, value in node.items():
            low = key.lower()
            if isinstance(value, (str, int, float)) and not isinstance(value, bool):
                parsed = parse_price_string(value)
                if parsed is not None:
                    if low in KEYS_OLD:
                        out.setdefault("old", []).append((key, parsed))
                    elif low in KEYS_CURRENT:
                        out.setdefault("current", []).append((key, parsed))
            else:
                walk_json(value, out)
    elif isinstance(node, list):
        for item in node:
            walk_json(item, out)


def prices_from_scripts(scripts: list[tuple[str, str]]) -> dict:
    out: dict = {}
    for stype, body in scripts:
        if "ld+json" in stype or "application/json" in stype:
            try:
                walk_json(json.loads(body.strip()), out)
                continue
            except (ValueError, TypeError):
                pass  # битый JSON — ниже пройдёмся регуляркой
        for key, quoted, single, bare in SCRIPT_PRICE_RE.findall(body):
            parsed = parse_price_string(quoted or single or bare)
            if parsed is None:
                continue
            bucket = "old" if key.lower() in KEYS_OLD else "current"
            out.setdefault(bucket, []).append((key, parsed))
    return out


def pick_current(items: list[tuple[str, float]]) -> float:
    """Из найденных «текущих» цен выбрать по приоритету ключа, а не по порядку в JSON."""
    for name in ("price", "saleprice", "sale_price", "discountprice", "discount_price",
                 "finalprice", "final_price", "currentprice", "current_price",
                 "pricevalue", "lowprice"):
        for key, value in items:
            if key.lower() == name:
                return value
    return items[0][1]


def script_current_is_ambiguous(items: list[tuple[str, float]]) -> bool:
    """Под выбранным ключом лежит несколько разных цен — значит, порядок решает, а не смысл.

    Так выглядит страница, где в JS-состоянии рядом с товаром едут соседние карточки:
    три разных `price` подряд, и какой окажется первым — дело случая. Цену с такой
    страницы брать из скрипта нельзя, разбор будет прыгать от запроса к запросу.
    """
    if not items:
        return False
    chosen = pick_current(items)
    keys = [key.lower() for key, value in items if value == chosen]
    if not keys:
        return False
    same_key = {value for key, value in items if key.lower() == keys[0]}
    return len(same_key) > 1


def extract(html: str) -> tuple[dict, dict]:
    """Вернуть (результат, разбор источников)."""
    parser = PriceParser()
    parser.feed(html)
    parser.close()

    script_prices = prices_from_scripts(parser.scripts)
    dom = parser.dom_prices()
    dom_old = [c["value"] for c in dom if c["old"]]
    dom_new = [c for c in dom if not c["old"]]

    evidence = {
        "script_prices": script_prices,
        "meta_prices": parser.meta_prices,
        "dom_candidates": dom,
    }

    # Текущая цена: сначала структурированные источники, потом вёрстка.
    current = None
    if script_prices.get("current"):
        current = pick_current(script_prices["current"])
        evidence["current_from"] = "script/json-ld"
        # Скрипт даёт несколько разных цен под одним ключом — доверяем вёрстке,
        # если она показывает ровно одну цену: это то, что видит покупатель.
        if script_current_is_ambiguous(script_prices["current"]):
            dom_values = {c["value"] for c in dom_new}
            if len(dom_values) == 1:
                current = dom_new[0]["value"]
                evidence["current_from"] = dom_new[0]["source"]
                evidence["note_current"] = (
                    "в скрипте несколько разных цен, взята однозначная цена из вёрстки"
                )
            else:
                evidence["note_current"] = (
                    "в скрипте несколько разных цен, вёрстка их не разрешает — цена ненадёжна"
                )
    elif parser.meta_prices:
        current = parser.meta_prices[0][1]
        evidence["current_from"] = "microdata/meta"
    elif dom_new:
        current = dom_new[0]["value"]
        evidence["current_from"] = dom_new[0]["source"]

    # Старая цена: явные ключи или зачёркнутая вёрстка.
    old = None
    if script_prices.get("old"):
        old = max(v for _, v in script_prices["old"])
        evidence["old_from"] = "script/json-ld"
    elif dom_old:
        old = max(dom_old)
        evidence["old_from"] = "dom:struck"

    # Последний шанс: ни один источник не сработал — ищем по всему тексту.
    if current is None and old is None:
        fallback = scan_prices(parser.body_text(), require_currency=True)
        if fallback:
            current = fallback[0]
            evidence["current_from"] = "fallback:text"

    if old is not None and current is not None:
        if old > current:
            regular, sale = old, current
        else:
            # Скидки нет: «старая» цена не больше текущей.
            regular, sale = max(old, current), None
            evidence["note"] = "old <= current, скидка не подтверждена"
    else:
        regular, sale = (current if current is not None else old), None

    body = parser.body_text()
    credit_match = CREDIT_RE.search(body)
    evidence["credit_match"] = credit_match.group(0) if credit_match else None

    result = {
        "regular_price": regular,
        "sale_price": sale,
        "has_credit": credit_match is not None,
    }
    return result, evidence


def fetch_html(url: str) -> str:
    request = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,be;q=0.8",
        "Accept-Encoding": "gzip, identity",
    })
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        raw = response.read()
        if (response.headers.get("Content-Encoding") or "").lower() == "gzip":
            raw = gzip.decompress(raw)
        charset = response.headers.get_content_charset()
    if not charset:
        match = re.search(rb"charset=[\"']?([\w-]+)", raw[:4096], re.I)
        charset = match.group(1).decode("ascii", "ignore") if match else "utf-8"
    return raw.decode(charset, errors="replace")


def main(argv=None) -> int:
    argparser = argparse.ArgumentParser(
        description="Извлечь цену товара со страницы магазина (BYN).")
    argparser.add_argument("url", nargs="?", help="URL страницы товара")
    argparser.add_argument("--file", help="локальный HTML вместо загрузки по сети")
    argparser.add_argument("--debug", action="store_true",
                           help="вывести разбор источников в stderr")
    args = argparser.parse_args(argv)

    if not args.url and not args.file:
        argparser.error("нужен URL или --file")

    if args.file:
        with open(args.file, encoding="utf-8", errors="replace") as handle:
            html = handle.read()
    else:
        try:
            html = fetch_html(args.url)
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as error:
            print(json.dumps({"error": "fetch_failed", "detail": str(error)},
                             ensure_ascii=False), file=sys.stderr)
            return 3

    result, evidence = extract(html)
    if args.debug:
        print(json.dumps(evidence, ensure_ascii=False, indent=2, default=str),
              file=sys.stderr)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["regular_price"] is not None else 2


if __name__ == "__main__":
    sys.exit(main())
