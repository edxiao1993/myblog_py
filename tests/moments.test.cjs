const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const vm = require('node:vm');

function setup(language) {
  const catalog = JSON.parse(readFileSync('translations/zh-CN.json', 'utf8'));
  const resize = [], observers = [];
  const cards = [20, 100, 120, 220].map(height => {
    const full = {scrollHeight: height, style: {}, width: 600,
      getBoundingClientRect() { return {width: this.width}; }};
    const attributes = {'aria-expanded': 'false'};
    return {full, button: {
      closest: () => ({querySelector: () => full}),
      getAttribute: key => attributes[key],
      setAttribute: (key, value) => { attributes[key] = value; },
      addEventListener(event, callback) { this.click = callback; }
    }};
  });
  vm.runInNewContext(readFileSync('static/js/moments.js', 'utf8'), {
    document: {querySelectorAll: () => cards.map(card => card.button)},
    window: {
      AppI18n: {t: key => language === 'zh-CN' ? catalog[key] : key},
      getComputedStyle: () => ({lineHeight: '20px'}),
      addEventListener: (event, callback) => resize.push(callback),
      ResizeObserver: class {
        constructor(callback) { observers.push(callback); }
        observe() {}
      }
    }
  });
  return {cards, resize: () => resize.forEach(callback => callback()), observers};
}

for (const language of ['en', 'zh-CN']) {
  test(`five-line boundary and independent expand/collapse in ${language}`, () => {
    const {cards} = setup(language);
    for (const card of cards.slice(0, 2)) {
      assert.equal(card.button.hidden, true);
      assert.equal(card.full.style.maxHeight, '');
    }
    const card = cards[2];
    assert.equal(card.button.hidden, false);
    assert.equal(card.full.style.maxHeight, '100px');
    assert.equal(card.full.style.overflow, 'hidden');
    card.button.click();
    assert.equal(card.full.style.maxHeight, '');
    assert.equal(card.full.style.overflow, '');
    assert.equal(card.button.getAttribute('aria-expanded'), 'true');
    assert.equal(card.button.textContent, language === 'en' ? 'Show less' : '收起全文');
    assert.equal(cards[3].full.style.maxHeight, '100px');
    card.button.click();
    assert.equal(card.full.style.maxHeight, '100px');
    assert.equal(card.button.getAttribute('aria-expanded'), 'false');
    assert.equal(card.button.textContent, language === 'en' ? 'Show more' : '展开全文');
  });
}

test('remeasures wrapped lines after mobile resize and card width changes', () => {
  const ui = setup('en');
  const card = ui.cards[1];
  card.full.scrollHeight = 180;
  ui.resize();
  assert.equal(card.button.hidden, false);
  assert.equal(card.full.style.maxHeight, '100px');
  card.button.click();
  ui.resize();
  assert.equal(card.full.style.maxHeight, '');
  assert.equal(card.button.getAttribute('aria-expanded'), 'true');
  card.full.width = 900;
  card.full.scrollHeight = 80;
  ui.observers[1]();
  assert.equal(card.button.hidden, true);
  assert.equal(card.full.style.maxHeight, '');
  assert.equal(card.button.getAttribute('aria-expanded'), 'false');
  card.full.width = 300;
  card.full.scrollHeight = 200;
  ui.observers[1]();
  assert.equal(card.button.hidden, false);
  assert.equal(card.full.style.maxHeight, '');
});
