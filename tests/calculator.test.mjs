// Браузерные тесты калькулятора: страница открывается в Chromium,
// значения вводятся как это делал бы человек, проверяется то, что видно на экране.
import { test, before, after, describe } from 'node:test';
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
