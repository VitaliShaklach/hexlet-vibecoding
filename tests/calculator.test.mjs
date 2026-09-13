// Браузерные тесты калькулятора: страница открывается в Chromium,
// значения вводятся как это делал бы человек, проверяется то, что видно на экране.
import { test, before, after, afterEach, describe } from 'node:test';
import assert from 'node:assert/strict';
import { chromium } from 'playwright';

const PAGE_URL = new URL('../index.html', import.meta.url).href;

let browser;
let page;
const consoleErrors = [];

before(async () => {
  browser = await chromium.launch();
  page = await browser.newPage();
  page.on('pageerror', (error) => consoleErrors.push(String(error)));
  page.on('console', (msg) => {
    if (msg.type() === 'error') consoleErrors.push(msg.text());
  });
  await page.goto(PAGE_URL);
});

after(async () => {
  await browser?.close();
});

/** Читает число из блока результатов, отбрасывая единицы измерения и пробелы-разделители. */
async function num(id) {
  const text = await page.textContent(`#${id}`);
  return Number(text.replace(/[^\d]/g, ''));
}

async function visible(id) {
  return !(await page.locator(`#${id}`).evaluate((el) => el.hidden));
}

/** Заполняет форму так же, как это делает пользователь. */
async function fill({ sex, age, height, weight, activity, goal }) {
  await page.click(`label[for="sex-${sex === 'male' ? 'm' : 'f'}"]`);
  await page.fill('#age', String(age));
  await page.fill('#height', String(height));
  await page.fill('#weight', String(weight));
  await page.selectOption('#activity', String(activity));
  await page.selectOption('#goal', goal);
}

describe('расчёт нормы калорий', () => {
  test('женщина 30 лет, 170 см, 65 кг, средняя активность, удержание веса', async () => {
    await fill({ sex: 'female', age: 30, height: 170, weight: 65, activity: 1.55, goal: 'keep' });

    // BMR = 10*65 + 6.25*170 - 5*30 - 161 = 1401.5
    assert.equal(await num('bmr'), 1402);
    // TDEE = 1401.5 * 1.55 = 2172.3
    assert.equal(await num('tdee'), 2172);
    assert.equal(await num('target'), 2172);
    assert.equal(await num('delta'), 0, 'при удержании веса поправки быть не должно');
  });

  test('мужчина 30 лет, 180 см, 80 кг, средняя активность, удержание веса', async () => {
    await fill({ sex: 'male', age: 30, height: 180, weight: 80, activity: 1.55, goal: 'keep' });

    // BMR = 10*80 + 6.25*180 - 5*30 + 5 = 1780
    assert.equal(await num('bmr'), 1780);
    assert.equal(await num('tdee'), 2759);
  });

  test('цель меняет норму, не трогая базовый обмен', async () => {
    await fill({ sex: 'female', age: 30, height: 170, weight: 65, activity: 1.55, goal: 'keep' });
    const bmr = await num('bmr');
    const maintain = await num('target');

    await page.selectOption('#goal', 'lose');
    assert.equal(await num('bmr'), bmr, 'базовый обмен не зависит от цели');
    assert.ok(await num('target') < maintain, 'дефицит должен снижать норму');

    await page.selectOption('#goal', 'gain');
    assert.ok(await num('target') > maintain, 'профицит должен повышать норму');
  });
});

describe('баланс БЖУ', () => {
  // Главное свойство расчёта: сумма калорий из белков, жиров и углеводов
  // обязана сходиться с целевой калорийностью при любых входных данных.
  const profiles = [
    { name: 'женщина, удержание', sex: 'female', age: 30, height: 170, weight: 65, activity: 1.55, goal: 'keep' },
    { name: 'женщина, похудение', sex: 'female', age: 60, height: 150, weight: 95, activity: 1.2, goal: 'lose' },
    { name: 'мужчина, набор массы', sex: 'male', age: 25, height: 190, weight: 110, activity: 1.9, goal: 'gain' },
    { name: 'минимальные значения', sex: 'female', age: 14, height: 120, weight: 30, activity: 1.2, goal: 'lose' },
    { name: 'максимальные значения', sex: 'male', age: 100, height: 230, weight: 300, activity: 1.9, goal: 'gain' },
  ];

  for (const profile of profiles) {
    test(`сумма БЖУ сходится с нормой: ${profile.name}`, async () => {
      await fill(profile);

      const target = await num('target');
      const fromMacros = (await num('protein-kcal')) + (await num('fat-kcal')) + (await num('carbs-kcal'));

      // Допуск в 3 ккал — накопленная погрешность округления трёх слагаемых.
      assert.ok(
        Math.abs(fromMacros - target) <= 3,
        `сумма БЖУ ${fromMacros} ккал расходится с нормой ${target} ккал`,
      );

      for (const macro of ['protein', 'fat', 'carbs']) {
        assert.ok(await num(macro) >= 0, `${macro} не может быть отрицательным`);
      }
    });
  }
});

describe('валидация ввода', () => {
  test('значение вне диапазона показывает ошибку вместо результата', async () => {
    await fill({ sex: 'female', age: 30, height: 170, weight: 65, activity: 1.55, goal: 'keep' });
    await page.fill('#age', '5');

    assert.equal(await visible('error'), true, 'ошибка должна быть видна');
    assert.equal(await visible('result'), false, 'результат должен быть скрыт');
    assert.match(await page.textContent('#error'), /Возраст/);
  });

  test('пустое поле показывает ошибку', async () => {
    // Форму сначала приводим в валидное состояние: предыдущий тест оставил
    // некорректный возраст, и ошибка про него перекрыла бы ошибку про вес.
    await fill({ sex: 'female', age: 30, height: 170, weight: 65, activity: 1.55, goal: 'keep' });
    await page.fill('#weight', '');

    assert.equal(await visible('error'), true);
    assert.match(await page.textContent('#error'), /укажите значение/);
  });

  test('после исправления расчёт возвращается', async () => {
    await fill({ sex: 'female', age: 30, height: 170, weight: 65, activity: 1.55, goal: 'keep' });

    assert.equal(await visible('error'), false, 'ошибка должна исчезнуть');
    assert.equal(await visible('result'), true, 'результат должен снова появиться');
    assert.equal(await num('target'), 2172);
  });
});

describe('доступность и стабильность', () => {
  test('переключатель пола доступен с клавиатуры', async () => {
    await page.locator('#sex-f').focus();
    await page.keyboard.press('ArrowRight');

    const checked = await page.locator('#sex-m').isChecked();
    assert.equal(checked, true, 'стрелка должна переключать радиокнопку');
  });

  test('страница работает без ошибок в консоли', () => {
    assert.deepEqual(consoleErrors, []);
  });
});

describe('кнопка «Рассчитать»', () => {
  test('пересчитывает и уводит фокус на результат', async () => {
    await fill({ sex: 'female', age: 30, height: 170, weight: 65, activity: 1.55, goal: 'keep' });
    await page.click('button[type="submit"]');

    assert.equal(await num('target'), 2172);
    assert.equal(await page.evaluate(() => document.activeElement.id), 'result');
  });

  test('при ошибке уводит фокус на сообщение', async () => {
    await page.fill('#height', '999');
    await page.click('button[type="submit"]');

    assert.equal(await page.evaluate(() => document.activeElement.id), 'error');
  });
});

describe('индекс массы тела', () => {
  /** ИМТ выводится с запятой как десятичным разделителем. */
  async function bmi() {
    const text = await page.textContent('#bmi-value');
    return Number(text.replace(',', '.'));
  }

  const cases = [
    { name: 'нормальный вес', height: 170, weight: 65, expected: 22.5, category: 'Нормальный вес', level: 'ok' },
    { name: 'недостаточный вес', height: 180, weight: 55, expected: 17.0, category: 'Недостаточный вес', level: 'warn' },
    { name: 'избыточный вес', height: 170, weight: 78, expected: 27.0, category: 'Избыточный вес', level: 'warn' },
    { name: 'ожирение I степени', height: 170, weight: 90, expected: 31.1, category: 'Ожирение I степени', level: 'alert' },
    { name: 'ожирение II степени', height: 170, weight: 102, expected: 35.3, category: 'Ожирение II степени', level: 'alert' },
    { name: 'ожирение III степени', height: 150, weight: 95, expected: 42.2, category: 'Ожирение III степени', level: 'alert' },
  ];

  for (const { name, height, weight, expected, category, level } of cases) {
    test(`${name}: ${weight} кг при ${height} см`, async () => {
      await fill({ sex: 'female', age: 30, height, weight, activity: 1.55, goal: 'keep' });

      assert.ok(
        Math.abs((await bmi()) - expected) < 0.1,
        `ИМТ ${await bmi()} вместо ожидаемого ${expected}`,
      );
      assert.equal(await page.textContent('#bmi-category'), category);
      assert.equal(await page.getAttribute('#bmi-category', 'data-level'), level);
    });
  }

  test('границы категорий по классификации ВОЗ', async () => {
    // Ровно 18.5 — уже норма, ровно 25 — уже избыточный вес.
    await fill({ sex: 'female', age: 30, height: 200, weight: 74, activity: 1.55, goal: 'keep' });
    assert.equal(await bmi(), 18.5);
    assert.equal(await page.textContent('#bmi-category'), 'Нормальный вес');

    await fill({ sex: 'female', age: 30, height: 200, weight: 100, activity: 1.55, goal: 'keep' });
    assert.equal(await bmi(), 25.0);
    assert.equal(await page.textContent('#bmi-category'), 'Избыточный вес');
  });

  test('не зависит от возраста, пола, активности и цели', async () => {
    await fill({ sex: 'female', age: 30, height: 170, weight: 65, activity: 1.55, goal: 'keep' });
    const before = await bmi();

    await fill({ sex: 'male', age: 55, height: 170, weight: 65, activity: 1.2, goal: 'gain' });
    assert.equal(await bmi(), before, 'ИМТ считается только по росту и весу');
  });
});

describe('рекомендации по физической активности', () => {
  /** Возвращает пары «занятие — расход в ккал» из таблицы. */
  async function burnRows() {
    return page.$$eval('#burn-list .burn-row', (rows) =>
      rows.map((row) => ({
        name: row.querySelector('.burn-name').textContent,
        kcal: Number(row.querySelector('.burn-kcal').textContent.replace(/[^\d]/g, '')),
      })));
  }

  test('расход считается по MET-формуле', async () => {
    await fill({ sex: 'female', age: 30, height: 170, weight: 65, activity: 1.55, goal: 'keep' });
    const rows = await burnRows();

    // Ходьба быстрым шагом, MET 5.0: 5 * 3.5 * 65 / 200 * 30 = 170.6
    const walk = rows.find((row) => row.name.startsWith('Ходьба быстрым шагом'));
    assert.equal(walk.kcal, 171);

    // Бег 8 км/ч, MET 8.3: 8.3 * 3.5 * 65 / 200 * 30 = 283.2
    const run = rows.find((row) => row.name.startsWith('Бег'));
    assert.equal(run.kcal, 283);
  });

  test('расход пропорционален весу', async () => {
    await fill({ sex: 'female', age: 30, height: 170, weight: 65, activity: 1.55, goal: 'keep' });
    const light = (await burnRows()).map((row) => row.kcal);

    await page.fill('#weight', '130');
    const heavy = (await burnRows()).map((row) => row.kcal);

    heavy.forEach((kcal, i) => {
      assert.ok(Math.abs(kcal - light[i] * 2) <= 1, `${kcal} вместо удвоенного ${light[i]}`);
    });
  });

  test('таблица содержит десять занятий по возрастанию расхода', async () => {
    await fill({ sex: 'female', age: 30, height: 170, weight: 65, activity: 1.55, goal: 'keep' });
    const rows = await burnRows();

    assert.equal(rows.length, 10);
    const sorted = [...rows].sort((a, b) => a.kcal - b.kcal).map((row) => row.name);
    assert.deepEqual(rows.map((row) => row.name), sorted);
  });

  test('подсказка своя у каждого уровня активности', async () => {
    const seen = new Set();

    for (const level of ['1.2', '1.375', '1.55', '1.725', '1.9']) {
      await page.selectOption('#activity', level);
      const hint = await page.textContent('#activity-hint');

      assert.ok(hint.trim().length > 0, `пустая подсказка для уровня ${level}`);
      assert.ok(!seen.has(hint), `подсказка для уровня ${level} повторяет предыдущую`);
      seen.add(hint);
    }
  });

  test('рекомендация своя у каждой цели', async () => {
    const seen = new Set();

    for (const goal of ['lose', 'keep', 'gain']) {
      await page.selectOption('#goal', goal);
      const advice = await page.textContent('#goal-advice');

      assert.ok(advice.trim().length > 0, `пустая рекомендация для цели ${goal}`);
      assert.ok(!seen.has(advice), `рекомендация для цели ${goal} повторяет предыдущую`);
      seen.add(advice);
    }
  });

  test('вес в заголовке совпадает с введённым', async () => {
    await fill({ sex: 'female', age: 30, height: 170, weight: 82, activity: 1.55, goal: 'keep' });
    assert.equal(await page.textContent('#burn-weight'), '82 кг');
  });

  test('при ошибке ввода карточка скрывается вместе с результатом', async () => {
    await fill({ sex: 'female', age: 30, height: 170, weight: 65, activity: 1.55, goal: 'keep' });
    assert.equal(await visible('advice'), true);

    await page.fill('#weight', '');
    assert.equal(await visible('advice'), false, 'иначе останутся числа от прошлого веса');
    assert.equal(await visible('result'), false);

    await page.fill('#weight', '65');
    assert.equal(await visible('advice'), true, 'после исправления карточка должна вернуться');
  });
});

describe('рекомендации при заболеваниях', () => {
  /** Пункты одного из трёх списков карточки. */
  async function items(listId) {
    return page.$$eval(`#${listId} li`, (nodes) => nodes.map((node) => node.textContent));
  }

  async function chooseCondition(value) {
    await page.selectOption('#condition', value);
  }

  test('в списке пятнадцать заболеваний плюс пункт «не указывать»', async () => {
    const values = await page.$$eval('#condition option', (nodes) => nodes.map((n) => n.value));

    assert.equal(values[0], 'none', 'первым идёт пункт «не указывать»');
    assert.equal(values.length - 1, 15);
    assert.equal(new Set(values).size, values.length, 'значения не должны повторяться');
  });

  test('заболевания, названные заказчиком, присутствуют', async () => {
    const labels = await page.$$eval('#condition option', (nodes) => nodes.map((n) => n.textContent));
    const joined = labels.join(' | ');

    for (const required of ['Тромбофлебит', 'Варикозное расширение вен', 'инфаркт']) {
      assert.ok(joined.includes(required), `в списке нет пункта «${required}»`);
    }
  });

  test('без выбранного диагноза карточки нет', async () => {
    await fill({ sex: 'female', age: 30, height: 170, weight: 65, activity: 1.55, goal: 'keep' });
    await chooseCondition('none');

    assert.equal(await visible('condition-card'), false);
  });

  test('выбор диагноза показывает все три списка', async () => {
    await chooseCondition('varicose');

    assert.equal(await visible('condition-card'), true);
    assert.equal(await page.textContent('#condition-name'), 'Варикозное расширение вен');

    for (const list of ['condition-diet', 'condition-activity', 'condition-caution']) {
      assert.ok((await items(list)).length > 0, `список ${list} пуст`);
    }
  });

  test('смена диагноза полностью заменяет содержимое', async () => {
    await chooseCondition('varicose');
    const varicose = await items('condition-diet');

    await chooseCondition('aneurysm');
    const aneurysm = await items('condition-diet');

    assert.equal(await page.textContent('#condition-name'), 'Аневризма аорты');
    assert.notDeepEqual(aneurysm, varicose);
    // Старые пункты не должны остаться в списке.
    assert.equal(aneurysm.some((text) => varicose.includes(text)), false);
  });

  test('у каждого из пятнадцати диагнозов заполнены все три списка', async () => {
    const values = await page.$$eval('#condition option', (nodes) =>
      nodes.map((n) => n.value).filter((value) => value !== 'none'));

    for (const value of values) {
      await chooseCondition(value);

      assert.ok((await page.textContent('#condition-name')).trim().length > 0, `${value}: нет названия`);
      assert.ok((await items('condition-diet')).length > 0, `${value}: пустое питание`);
      assert.ok((await items('condition-activity')).length > 0, `${value}: пустая активность`);
      assert.ok((await items('condition-caution')).length > 0, `${value}: нет предостережений`);
    }
  });

  test('при тромбофлебите есть предупреждение про варфарин и витамин K', async () => {
    await chooseCondition('thrombophlebitis');
    const caution = (await items('condition-caution')).join(' ');

    // Совет «ешьте больше зелени» без этой оговорки опасен для тех, кто на варфарине.
    assert.match(caution, /варфарин/i);
    assert.match(caution, /витамин K/i);
  });

  test('карточка скрывается при ошибке ввода и возвращается после исправления', async () => {
    await fill({ sex: 'female', age: 30, height: 170, weight: 65, activity: 1.55, goal: 'keep' });
    await chooseCondition('infarction');
    assert.equal(await visible('condition-card'), true);

    await page.fill('#weight', '');
    assert.equal(await visible('condition-card'), false);

    await page.fill('#weight', '65');
    assert.equal(await visible('condition-card'), true);
  });

  test('диагноз не влияет на расчёт калорий', async () => {
    await fill({ sex: 'female', age: 30, height: 170, weight: 65, activity: 1.55, goal: 'keep' });
    await chooseCondition('none');
    const before = await num('target');

    await chooseCondition('heart_failure');
    assert.equal(await num('target'), before, 'расчёт нормы на диагноз опираться не должен');

    await chooseCondition('none');
  });
});

describe('превью для мессенджеров', () => {
  const SITE = 'https://vitalishaklach.github.io/hexlet-vibecoding/';
  const IMAGE = `${SITE}og-preview.png`;

  /** Значение мета-тега по property (Open Graph) или name (Twitter). */
  async function meta(key) {
    return page.getAttribute(`meta[property="${key}"], meta[name="${key}"]`, 'content');
  }

  test('заголовок, описание и картинка заданы', async () => {
    assert.equal(await meta('og:title'), 'Калькулятор калорий — норма, БЖУ и ИМТ');
    assert.ok((await meta('og:description')).length > 50, 'описание слишком короткое');
    assert.equal(await meta('og:image'), IMAGE);
  });

  test('адреса абсолютные — иначе Телеграм не загрузит картинку', async () => {
    for (const key of ['og:image', 'og:url', 'twitter:image']) {
      assert.match(await meta(key), /^https:\/\//, `${key} должен быть полным адресом`);
    }
  });

  test('размеры картинки объявлены как 1200×630', async () => {
    assert.equal(await meta('og:image:width'), '1200');
    assert.equal(await meta('og:image:height'), '630');
  });

  test('картинка лежит в проекте и совпадает с объявленными размерами', async () => {
    const { readFileSync } = await import('node:fs');
    const file = new URL('../og-preview.png', import.meta.url);
    const png = readFileSync(file);

    assert.equal(png.subarray(1, 4).toString(), 'PNG', 'файл не PNG');
    // Ширина и высота лежат в заголовке IHDR: байты 16-23.
    assert.equal(png.readUInt32BE(16), 1200);
    assert.equal(png.readUInt32BE(20), 630);
  });

  test('карточка Twitter крупная', async () => {
    assert.equal(await meta('twitter:card'), 'summary_large_image');
  });
});

describe('переключатель темы', () => {
  const LIGHT_BG = 'rgb(244, 245, 247)';
  const DARK_BG = 'rgb(20, 22, 26)';

  // Тема живёт в localStorage, поэтому каждый случай получает свой контекст:
  // иначе выбор из одного теста протёк бы в следующий.
  let context;
  let themePage;

  async function open(colorScheme) {
    context = await browser.newContext({ colorScheme });
    themePage = await context.newPage();
    await themePage.goto(PAGE_URL);
  }

  afterEach(async () => {
    await context?.close();
    context = undefined;
  });

  const background = () =>
    themePage.evaluate(() => getComputedStyle(document.body).backgroundColor);

  const chosen = () =>
    themePage.evaluate(() => document.documentElement.getAttribute('data-theme'));

  const saved = () => themePage.evaluate(() => localStorage.getItem('theme'));

  test('без выбора страница следует системной теме', async () => {
    await open('dark');

    assert.equal(await background(), DARK_BG);
    assert.equal(await chosen(), null, 'без явного выбора атрибута быть не должно');
    assert.equal(await saved(), null);
  });

  test('клик меняет фон и цвета', async () => {
    await open('light');
    assert.equal(await background(), LIGHT_BG);

    await themePage.click('#theme-toggle');

    assert.equal(await background(), DARK_BG);
    assert.equal(await chosen(), 'dark');
    // Меняется не только фон: текст и карточки тоже переключаются на тёмные.
    const card = await themePage.evaluate(
      () => getComputedStyle(document.querySelector('.card')).backgroundColor);
    assert.notEqual(card, 'rgb(255, 255, 255)');
  });

  test('выбор сохраняется и переживает перезагрузку', async () => {
    await open('light');
    await themePage.click('#theme-toggle');
    assert.equal(await saved(), 'dark');

    await themePage.reload();

    assert.equal(await chosen(), 'dark', 'тема должна примениться сразу при загрузке');
    assert.equal(await background(), DARK_BG);
  });

  test('явный выбор перекрывает системную тему', async () => {
    await open('dark');
    await themePage.click('#theme-toggle');

    assert.equal(await chosen(), 'light');
    assert.equal(await background(), LIGHT_BG, 'светлая тема должна победить системную тёмную');

    await themePage.reload();
    assert.equal(await background(), LIGHT_BG, 'и остаться такой после перезагрузки');
  });

  test('переключение работает в обе стороны', async () => {
    await open('light');

    await themePage.click('#theme-toggle');
    assert.equal(await background(), DARK_BG);

    await themePage.click('#theme-toggle');
    assert.equal(await background(), LIGHT_BG);
    assert.equal(await saved(), 'light');
  });

  test('кнопка подписана и иконка соответствует теме', async () => {
    await open('light');

    assert.match(await themePage.getAttribute('#theme-toggle', 'aria-label'), /тёмную/);
    assert.equal(await themePage.locator('#icon-moon').isVisible(), true);
    assert.equal(await themePage.locator('#icon-sun').isVisible(), false);

    await themePage.click('#theme-toggle');

    assert.match(await themePage.getAttribute('#theme-toggle', 'aria-label'), /светлую/);
    assert.equal(await themePage.locator('#icon-sun').isVisible(), true);
    assert.equal(await themePage.locator('#icon-moon').isVisible(), false);
  });

  test('кнопка доступна с клавиатуры', async () => {
    await open('light');

    await themePage.focus('#theme-toggle');
    await themePage.keyboard.press('Enter');

    assert.equal(await background(), DARK_BG);
  });

  test('страница работает при запрещённом localStorage', async () => {
    context = await browser.newContext({ colorScheme: 'light' });
    themePage = await context.newPage();
    // Приватный режим в некоторых браузерах бросает исключение на любой доступ.
    await themePage.addInitScript(() => {
      Object.defineProperty(window, 'localStorage', {
        get() { throw new Error('доступ к хранилищу запрещён'); },
      });
    });

    const errors = [];
    themePage.on('pageerror', (error) => errors.push(String(error)));
    await themePage.goto(PAGE_URL);

    await themePage.click('#theme-toggle');

    assert.equal(await background(), DARK_BG, 'переключение должно работать и без хранилища');
    assert.deepEqual(errors, [], 'необработанных исключений быть не должно');
  });
});
