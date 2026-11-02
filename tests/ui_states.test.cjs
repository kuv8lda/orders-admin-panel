// Run with: node --test tests/ui_states.test.cjs
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = (name) => fs.readFileSync(path.join(__dirname, '..', 'js', name), 'utf8');

function dashboard() {
  const context = vm.createContext({
    Intl, URLSearchParams, AbortController,
    I18N: { t: (key) => key, tn() {} },
    document: { addEventListener() {} },
    location: { pathname: '/dashboard', search: '', hash: '#orders' },
    history: { replaceState(_state, _title, url) { context.lastUrl = url; } },
  });
  vm.runInContext(source('dashboard.js'), context);
  return context;
}

test('status menu flips above the trigger near the bottom edge', () => {
  const ctx = dashboard();
  const result = vm.runInContext('statusMenuPosition({right: 980, top: 720, bottom: 752}, 200, 120, 1000, 800)', ctx);
  assert.equal(result.left, 780);
  assert.equal(result.top, 594);
});

test('status menu clamps to the viewport and opens below when space is available', () => {
  const ctx = dashboard();
  const result = vm.runInContext('statusMenuPosition({right: 90, top: 20, bottom: 52}, 190, 120, 390, 844)', ctx);
  assert.equal(result.left, 8);
  assert.equal(result.top, 58);
});

test('filter URL updates preserve the selected workspace section', () => {
  const ctx = dashboard();
  vm.runInContext('state.q = "Анна"; state.page = 2; pushStateToUrl()', ctx);
  const url = new URL(ctx.lastUrl, 'http://localhost');
  assert.equal(url.hash, '#orders');
  assert.equal(url.searchParams.get('q'), 'Анна');
  assert.equal(url.searchParams.get('page'), '2');
});

test('translation applies to the passed root element, not just its descendants', () => {
  const ctx = vm.createContext({
    Intl,
    window: {},
    localStorage: { getItem() { return null; } },
    document: { documentElement: {}, body: { dataset: {} }, getElementById() { return null; } },
  });
  vm.runInContext(source('i18n.js'), ctx);
  const root = {
    dataset: { i18n: 'nav.orders', i18nAttr: 'aria-label:nav.orders' },
    querySelectorAll() { return []; },
    matches() { return true; },
    setAttribute(key, value) { this[key] = value; },
  };
  ctx.window.I18N.apply(root);
  assert.equal(root.textContent, 'Заказы');
  assert.equal(root['aria-label'], 'Заказы');
});

test('money formatting follows the current language without reloading the script', () => {
  const ctx = dashboard();
  ctx.I18N.getLang = () => 'ru';
  const ru = vm.runInContext('money(123456)', ctx);
  ctx.I18N.getLang = () => 'en';
  const en = vm.runInContext('money(123456)', ctx);
  assert.equal(ru, new Intl.NumberFormat('ru', { style: 'currency', currency: 'RUB', maximumFractionDigits: 0 }).format(1234.56));
  assert.equal(en, new Intl.NumberFormat('en', { style: 'currency', currency: 'RUB', maximumFractionDigits: 0 }).format(1234.56));
  assert.notEqual(ru, en);
});

test('closing a status menu restores it to connected actions and preserves its ID', () => {
  const ctx = dashboard();
  const calls = [];
  ctx.menu = { id: 'unique-menu', classList: { remove: value => calls.push(value) }, remove: () => calls.push('removed') };
  ctx.actions = { isConnected: true, appendChild: node => calls.push(node.id) };
  ctx.trigger = { isConnected: true, setAttribute: (key, value) => calls.push([key, value]), focus: () => calls.push('focus') };
  vm.runInContext('activeStatusMenu = menu; statusMenuParent = actions; statusMenuTrigger = trigger; closeAllStatusMenus(true)', ctx);
  assert.deepEqual(calls, ['open', 'unique-menu', ['aria-expanded', 'false'], 'focus']);
  assert.equal(vm.runInContext('activeStatusMenu', ctx), null);
  assert.equal(vm.runInContext('statusMenuParent', ctx), null);
});

test('closing a menu removes it if its row was replaced', () => {
  const ctx = dashboard();
  let removed = false;
  ctx.menu = { classList: { remove() {} }, remove() { removed = true; } };
  ctx.actions = { isConnected: false, appendChild() { throw new Error('detached row'); } };
  vm.runInContext('activeStatusMenu = menu; statusMenuParent = actions; closeAllStatusMenus()', ctx);
  assert.equal(removed, true);
});

test('local today window uses local midnight across UTC dates and DST changes', () => {
  const previous = process.env.TZ;
  try {
    const cases = [
      ['Europe/Moscow', '2026-10-25T01:30:00+03:00', '2026-10-24T21:00:00.000Z', '2026-10-24'],
      ['America/Los_Angeles', '2026-10-25T23:30:00-07:00', '2026-10-25T07:00:00.000Z', '2026-10-26'],
      ['America/New_York', '2026-03-08T12:00:00-04:00', '2026-03-08T05:00:00.000Z', '2026-03-08'],
      ['America/New_York', '2026-11-01T12:00:00-05:00', '2026-11-01T04:00:00.000Z', '2026-11-01'],
    ];
    for (const [zone, now, midnight, dateTo] of cases) {
      process.env.TZ = zone;
      const ctx = dashboard();
      const result = vm.runInContext(`localTodayWindow(new Date('${now}'))`, ctx);
      assert.equal(result.start, Date.parse(midnight), zone);
      assert.equal(result.end, Date.parse(now), zone);
      assert.equal(result.dateFrom, midnight.slice(0, 10), zone);
      assert.equal(result.dateTo, dateTo, zone);
    }
  } finally {
    if (previous === undefined) delete process.env.TZ;
    else process.env.TZ = previous;
  }
});
