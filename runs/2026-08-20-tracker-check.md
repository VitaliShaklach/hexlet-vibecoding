# Контроль качества скилла tracker

Прогон: 2026-08-20, окружение — удалённая сессия Claude Code.
Полная таблица прогона: [`2026-08-20-tracker.md`](2026-08-20-tracker.md).

## 1. Все ссылки отработал — да, 7 из 7

Скилл прошёл по зашитому списку `.claude/skills/tracker/references/urls.txt`
целиком, за один запуск, ни одна ссылка не пропущена и не продублирована:

```
[1/7] extract-price → https://catalog.onliner.by/pump/jileks/6550prof
[2/7] extract-price → https://nasosov.by/catalog/vodomet_prof_55_35_a_df_dzhileks_6535/
[3/7] extract-price → https://www.teplodvor.by/shop/nasosy/kolodeznye-nasosy/nasos-pogruzhnoj-vibraczionnyj-aqualink-vp-d-6518-25/
[4/7] extract-price → https://larek.by/catalog/vodosnabzhenie/nasosnoe_oborudovanie/kolodeznye_nasosy/aquario_kolodeznyy_nasos_asp6_35_100w/
[5/7] extract-price → https://5element.by/products/772895-nasos-vibracionnyy-stavr-npv-300n25-st300n25npv
[6/7] extract-price → https://progreem.by/catalog/vodosnabzhenie/nasosy/nasosnye-stantsii/ibo-jet100a-tf-50-suh-hod/
[7/7] extract-price → https://www.wildberries.by/catalog/109676366/detail.aspx
```

Порядок строк в таблице совпадает с порядком в списке — это проверяется тестом.

## 2. Скилл extract-price вызван — да, 7 раз, по разу на URL

Своей логики разбора цен в tracker нет: `run_tracker.py` на каждом URL
запускает `.claude/skills/extract-price/scripts/extract_price.py` подпроцессом и
кладёт его объект `{regular_price, sale_price, has_credit}` в строку таблицы.
В коде tracker нет ни одного регулярного выражения по ценам — только вызов
соседнего скилла и сборка результатов.

Что вернул extract-price в этом прогоне: на всех 7 URL — `fetch_failed`,
`Tunnel connection failed: 403 Forbidden`.

**Цены в этом прогоне собрать не удалось, и это не дефект скилла.** Все 7
доменов закрыты политикой сетевого доступа этой удалённой сессии: и прямой
запрос из песочницы, и запасной путь через WebFetch (шаг 3 инструкции
extract-price) отвечают `EGRESS_BLOCKED` / 403 от egress-прокси. Обходить
запрет политики нельзя, поэтому строки остались с прочерками, а причина
записана в сводке прогона. Из окружения с доступом к этим магазинам та же
команда заполнит цены — механика прогона от сети не зависит и проверена
офлайн (пункт 3).

## 3. Таблица составлена — да, одна на весь прогон

Одна таблица, строка на товар, четыре колонки: URL, regular_price, sale_price,
has_credit. Семь отдельных ответов вместо таблицы скилл не выдаёт.

Боевой прогон (цены пустые из-за блокировки сети):

| # | URL | regular_price | sale_price | has_credit |
|---|-----|---------------|------------|------------|
| 1 | catalog.onliner.by/pump/jileks/6550prof | — | — | — |
| 2 | nasosov.by/…/vodomet_prof_55_35_a_df_dzhileks_6535/ | — | — | — |
| 3 | www.teplodvor.by/…/nasos-pogruzhnoj-vibraczionnyj-aqualink-vp-d-6518-25/ | — | — | — |
| 4 | larek.by/…/aquario_kolodeznyy_nasos_asp6_35_100w/ | — | — | — |
| 5 | 5element.by/products/772895-nasos-vibracionnyy-stavr-npv-300n25-st300n25npv | — | — | — |
| 6 | progreem.by/…/ibo-jet100a-tf-50-suh-hod/ | — | — | — |
| 7 | www.wildberries.by/catalog/109676366/detail.aspx | — | — | — |

Что сборка таблицы работает и цены в неё действительно попадают, показывает
офлайн-прогон по фикстурам extract-price — тот же скилл, тот же код, вместо
магазинов локальные страницы:

| # | URL | regular_price | sale_price | has_credit |
|---|-----|---------------|------------|------------|
| 1 | fixture: sale_jsonld.html | 1349.00 | 1199.99 | да |
| 2 | fixture: sale_jsstate.html | 2450.00 | 2199.00 | да |
| 3 | fixture: plain_microdata.html | 289.50 | — | нет |

Форматы на выбор: `--format markdown` (по умолчанию), `json`, `csv`;
`--out <файл>` пишет таблицу в файл — это и есть запись прогона.

## Итог

| Пункт | Статус |
|---|---|
| Все ссылки отработал | ✅ 7 из 7, за один запуск |
| Скилл extract-price вызван | ✅ 7 вызовов, логика не продублирована |
| Таблица составлена | ✅ одна таблица прогона, 7 строк, 4 колонки |
| Цены получены | ⚠️ 0 из 7 — все домены закрыты egress-политикой сессии |

Автотесты: `python3 .claude/skills/tracker/tests/test_tracker.py` → `OK все
проверки пройдены`; `python3 .claude/skills/extract-price/tests/test_extract_price.py`
→ `3/3 проверок пройдено`.
