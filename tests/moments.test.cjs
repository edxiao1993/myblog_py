const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const vm = require('node:vm');

function setup(language, markdown = false) {
  const catalog = JSON.parse(readFileSync('translations/zh-CN.json', 'utf8'));
  const resize = [], observers = [], sanitized = [];
  let clock = 1000;
  const source = '# Heading\n\n**bold** and [link](https://example.com)\n\n```js\nconst x = 1;\n```\n\n| A | B |\n| --- | --- |\n| 1 | 2 |\n\n<script>alert(1)</script><img src=x onerror=alert(1)>[bad](javascript:alert(1))';
  const cards = [20, 100, 120, 220].map(height => {
    const full = {scrollHeight: height, style: {}, width: 600, textContent: source, listeners: {},
      addEventListener(event, callback) { this.listeners[event] = callback; },
      getBoundingClientRect() { return {width: this.width}; }};
    const attributes = {'aria-expanded': 'false'};
    const listeners = {};
    const card = {addEventListener(event, callback) { listeners[event] = callback; }};
    return {full, listeners, button: {
      closest: selector => selector === '[data-moment-card]' ? card : {querySelector: () => full},
      getAttribute: key => attributes[key],
      setAttribute: (key, value) => { attributes[key] = value; },
      addEventListener(event, callback) { this.click = callback; }
    }};
  });
  vm.runInNewContext(readFileSync('static/js/moments.js', 'utf8'), {
    Date: {now: () => clock},
    document: {querySelectorAll: () => cards.map(card => card.button)},
    window: {
      ...(markdown ? {marked: require('../static/js/marked.min.js'), DOMPurify: {sanitize(html, options) {
        sanitized.push({html, options}); return '<p>Sanitizer-approved HTML only</p>';
      }}} : {}),
      AppI18n: {t: key => language === 'zh-CN' ? catalog[key] : key},
      getComputedStyle: () => ({lineHeight: '20px'}),
      addEventListener: (event, callback) => resize.push(callback),
      ResizeObserver: class {
        constructor(callback) { observers.push(callback); }
        observe() {}
      }
    }
  });
  return {cards, sanitized, tick: (ms = 100) => { clock += ms; }, resize: () => resize.forEach(callback => callback()), observers};
}

test('Markdown is parsed before sanitization and only sanitizer output reaches the DOM', () => {
  const {cards, sanitized} = setup('en', true);
  assert.equal(sanitized.length, cards.length);
  const {html, options} = sanitized[0];
  for (const token of ['<h1>', '<strong>', '<a href="https://example.com">', '<pre><code', '<table>', '<script>', 'onerror=', 'javascript:']) assert.ok(html.includes(token), token);
  assert.equal(options.USE_PROFILES.html, true);
  for (const card of cards) {
    assert.equal(card.full.innerHTML, '<p>Sanitizer-approved HTML only</p>');
    assert.equal(card.full.style.whiteSpace, 'normal');
  }
  const card = cards[0];
  card.full.scrollHeight = 180;
  card.full.listeners.load();
  assert.equal(card.button.hidden, false);
  assert.equal(card.full.style.maxHeight, '100px');
});

test('unavailable Markdown libraries leave escaped text untouched', () => {
  const {cards} = setup('en');
  assert.equal(cards[0].full.innerHTML, undefined);
  assert.match(cards[0].full.textContent, /# Heading/);
});

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

test('double-click toggles long cards independently and ignores interactive targets and short cards', () => {
  const {cards} = setup('en');
  const event = {target: {closest: () => null}};
  cards[2].listeners.dblclick(event);
  assert.equal(cards[2].button.getAttribute('aria-expanded'), 'true');
  assert.equal(cards[3].button.getAttribute('aria-expanded'), 'false');
  cards[2].listeners.dblclick({target: {closest: () => ({})}}); // link/delete/button
  assert.equal(cards[2].button.getAttribute('aria-expanded'), 'true');
  cards[2].listeners.dblclick(event);
  assert.equal(cards[2].full.style.maxHeight, '100px');
  cards[0].listeners.dblclick(event);
  assert.equal(cards[0].button.getAttribute('aria-expanded'), 'false');
});

test('touch double-tap toggles once, ignores synthetic dblclick, scrolling, and cancelled gestures', () => {
  const {cards, tick} = setup('zh-CN');
  const card = cards[2];
  let prevented = 0;
  const event = {pointerType:'touch', pointerId:1, clientX:30, clientY:30,
    target:{closest:() => null}, preventDefault:() => { prevented++; }};
  const tap = () => { card.listeners.pointerdown(event); tick(50); card.listeners.pointerup(event); };
  tap();
  assert.equal(card.button.getAttribute('aria-expanded'), 'false');
  tick(50); tap();
  assert.equal(card.button.getAttribute('aria-expanded'), 'true');
  assert.equal(card.button.textContent, '收起全文');
  assert.equal(prevented, 1);
  card.listeners.dblclick(event);
  assert.equal(card.button.getAttribute('aria-expanded'), 'true');
  tick(800); tap(); tick(50); tap();
  assert.equal(card.button.getAttribute('aria-expanded'), 'false');
  tick(800); tap();
  card.listeners.pointerdown(event);
  card.listeners.pointerup({...event, clientY:100});
  tap();
  assert.equal(card.button.getAttribute('aria-expanded'), 'false');
  card.listeners.pointercancel();
  tap();
  assert.equal(card.button.getAttribute('aria-expanded'), 'false');
  tick(800);
  const interactive = {...event, target:{closest:() => ({})}};
  for (let i=0; i<2; i++) {
    card.listeners.pointerdown(interactive); tick(50); card.listeners.pointerup(interactive);
  }
  assert.equal(card.button.getAttribute('aria-expanded'), 'false');
});

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
