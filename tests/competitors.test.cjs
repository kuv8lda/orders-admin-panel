const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function setup(storage = new Map()) {
  class Element {
    constructor(tag) { this.tag = tag; this.children = []; this.listeners = {}; }
    contains(element) { return this === element || this.children.some((child) => child.contains(element)); }
    append(...children) { this.children.push(...children); }
    replaceChildren(...children) { this.children = children; }
    setAttribute(key, value) { this[key] = value; }
    addEventListener(event, fn) { this.listeners[event] = fn; }
  }
  const elements = {};
  for (const id of ['competitorRows', 'competitorSource', 'competitorHistory',
    'competitorNotice', 'competitorThreshold', 'competitorRefresh']) elements[id] = new Element(id);
  elements.competitorThreshold.checked = false;
  const dispatched = [];
  const document = {
    hidden: false, listeners: {},
    createElement: (tag) => new Element(tag),
    getElementById: (id) => elements[id],
    addEventListener(event, fn) { this.listeners[event] = fn; },
    dispatchEvent(event) { dispatched.push(event); return this.listeners[event.type]?.(event); },
  };
  const calls = [], toasts = [], timers = [];
  const data = {
    source: 'http://127.0.0.1:5001', latest_event_id: 3,
    catalogue: [{ id: 1, name: 'Медовик, 1 кг', price_cents: 240000 }],
    items: [{ id: 1, name: '<img src=x onerror=alert(1)>', last_price_cents: 95000,
      my_product_id: null, my_product_name: null, my_price_cents: null, difference_cents: null }],
  };
  const events = [], history = [];
  const sync = { ok: true };
  const context = vm.createContext({
    document, Intl, Date, console,
    localStorage: {
      getItem: (key) => storage.has(key) ? storage.get(key) : null,
      setItem: (key, value) => storage.set(key, value),
    },
    CustomEvent: class { constructor(type, options) { this.type = type; this.detail = options.detail; } },
    window: { addEventListener() {} },
    I18N: { t: (key) => key, getLang: () => 'ru', onChange() {} },
    OrdersApp: {
      async api(url, options) {
        calls.push({ url, options });
        if (url === '/api/competitor/sync') return sync;
        if (url.includes('comparison')) return data;
        if (url.includes('history')) return { items: url.includes('after=') ? events : history };
        return {};
      },
      toast: (text) => toasts.push(text),
    },
    setTimeout(fn, ms) { timers.push({ fn, ms }); return timers.length; },
    clearTimeout() {},
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../js/competitors.js'), 'utf8'), context);
  return { document, elements, calls, toasts, timers, events, history, data, dispatched, sync };
}

const change = (overrides = {}) => ({
  id: 4, name: 'Кофе', old_price_cents: 100000, new_price_cents: 99000,
  change_percent: -1, created_at: '2026-10-23T10:00:00Z', ...overrides,
});

test('initial history is visible without toast; polling notifies even changes below 5% by default', async () => {
  const { document, elements, calls, toasts, timers, events, history } = setup();
  history.push(change({ id: 3 }));
  await document.listeners.DOMContentLoaded();
  assert.equal(calls[0].url, '/api/me');
  assert.equal(toasts.length, 0);
  assert.match(elements.competitorHistory.children[0].textContent, /Кофе/);
  assert.equal(elements.competitorRows.children[0].children[0].textContent, '<img src=x onerror=alert(1)>');
  assert.equal(timers[0].ms, 15000);
  events.push(change());
  await timers[0].fn();
  assert.ok(calls.some((c) => c.url.endsWith('significant=0&after=3')));
  assert.equal(toasts.length, 1);
  assert.match(elements.competitorNotice.textContent, /Кофе/);
  events.length = 0;
  await timers[1].fn();
  assert.ok(calls.some((c) => c.url.endsWith('after=4')));
  assert.equal(toasts.length, 1);
});

test('mapping writes use catalogue IDs; threshold applies to comparison, history and notifications', async () => {
  const { document, elements, calls } = setup();
  await document.listeners.DOMContentLoaded();
  const select = elements.competitorRows.children[0].children[2].children[0];
  select.value = '1';
  await select.listeners.change();
  const write = calls.find((c) => c.url.includes('/mapping') && c.options?.method === 'POST');
  assert.equal(write.url, '/api/competitor/products/1/mapping');
  assert.deepEqual(JSON.parse(write.options.body), { my_product_id: 1 });
  elements.competitorThreshold.checked = true;
  await elements.competitorThreshold.listeners.change();
  assert.ok(calls.some((c) => c.url === '/api/competitor/comparison?significant=1'));
  assert.ok(calls.some((c) => c.url === '/api/competitor/history?significant=1'));
  assert.ok(calls.some((c) => c.url === '/api/competitor/history?significant=1&after=3'));
});

test('comparison action edits OWN product and refreshes after product save; hidden mapping stays labelled', async () => {
  const { document, elements, calls, data, dispatched } = setup();
  data.items[0].my_product_id = 7;
  data.items[0].my_product_name = 'Мой скрытый товар';
  data.items[0].my_price_cents = 12500;
  await document.listeners.DOMContentLoaded();
  const row = elements.competitorRows.children[0];
  const select = row.children[2].children[0];
  assert.equal(select.value, '7');
  assert.match(select.children.at(-1).textContent, /products.hidden/);
  assert.equal(select.children.at(-1).disabled, true);
  const button = row.children[5].children[0];
  assert.equal(button.disabled, false);
  button.listeners.click();
  assert.equal(dispatched[0].type, 'products:edit');
  assert.deepEqual(JSON.parse(JSON.stringify(dispatched[0].detail)), {
    id: 7, name: 'Мой скрытый товар', price_cents: 12500,
  });
  const before = calls.filter((c) => c.url.includes('comparison')).length;
  document.listeners['products:changed']();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(calls.filter((c) => c.url.includes('comparison')).length, before + 1);
});

test('hidden tabs stop network polling', async () => {
  const { document, calls, timers } = setup();
  await document.listeners.DOMContentLoaded();
  const count = calls.length;
  document.hidden = true;
  await timers[0].fn();
  assert.equal(calls.length, count);
  assert.equal(timers.length, 1);
});

test('reload reports changes delivered while the panel was closed, without replaying seen events', async () => {
  const storage = new Map();
  const first = setup(storage);
  await first.document.listeners.DOMContentLoaded();
  assert.equal(storage.get('competitor:lastNotifiedEvent'), '3');

  const reopened = setup(storage);
  reopened.data.latest_event_id = 4;
  reopened.events.push(change());
  reopened.history.push(change());
  await reopened.document.listeners.DOMContentLoaded();
  assert.ok(reopened.calls.some((c) => c.url.endsWith('after=3')));
  assert.equal(reopened.toasts.length, 1);
  assert.match(reopened.toasts[0], /Кофе/);
  assert.equal(storage.get('competitor:lastNotifiedEvent'), '4');

  const again = setup(storage);
  again.data.latest_event_id = 4;
  await again.document.listeners.DOMContentLoaded();
  assert.ok(again.calls.some((c) => c.url.endsWith('after=4')));
  assert.equal(again.toasts.length, 0);
});

test('refresh synchronizes before reading prices; source failure keeps saved rows and polling', async () => {
  const app = setup();
  await app.document.listeners.DOMContentLoaded();
  assert.equal(app.calls[1].url, '/api/competitor/sync');
  assert.equal(app.calls[1].options.method, 'POST');
  assert.match(app.calls[2].url, /comparison/);
  app.sync.ok = false;
  await app.timers[0].fn();
  assert.equal(app.elements.competitorNotice.textContent, 'competitors.sync_error');
  assert.equal(app.elements.competitorRows.children.length, 1);
  app.sync.ok = true;
  app.data.items[0].last_price_cents = 123456;
  await app.timers[1].fn();
  assert.equal(app.elements.competitorNotice.textContent, '');
  assert.match(app.elements.competitorRows.children[0].children[1].textContent, /1.*234,56/);
});

test('blocked storage does not prevent polling and notifications', async () => {
  const app = setup({
    has() { throw new Error('Storage blocked'); },
    set() { throw new Error('Storage blocked'); },
  });
  await app.document.listeners.DOMContentLoaded();
  app.data.latest_event_id = 4;
  app.events.push(change());
  await app.timers[0].fn();
  assert.equal(app.toasts.length, 1);
});

test('polling preserves focused controls and applies latest snapshot after focus leaves', async () => {
  const app = setup();
  await app.document.listeners.DOMContentLoaded();
  const rows = app.elements.competitorRows;
  const original = rows.children[0];
  const select = original.children[2].children[0];
  app.document.activeElement = select;
  app.data.items[0].last_price_cents = 123456;
  await app.timers[0].fn();
  assert.equal(rows.children[0], original);
  app.data.items[0].last_price_cents = 234567;
  await app.timers[1].fn();
  assert.equal(rows.children[0], original);

  rows.listeners.focusout();
  app.document.activeElement = original.children[5].children[0];
  app.timers.at(-1).fn();
  assert.equal(rows.children[0], original);

  rows.listeners.focusout();
  app.document.activeElement = app.elements.competitorRefresh;
  app.timers.at(-1).fn();
  assert.notEqual(rows.children[0], original);
  assert.match(rows.children[0].children[1].textContent, /2.*345,67/);
});

test('mapping write protects controls even when disabling the select moves focus away', async () => {
  const app = setup();
  await app.document.listeners.DOMContentLoaded();
  const rows = app.elements.competitorRows;
  const original = rows.children[0];
  const select = original.children[2].children[0];
  select.value = '1';
  app.document.activeElement = select;
  const saving = select.listeners.change();
  app.document.activeElement = null;
  rows.listeners.focusout();
  app.timers.at(-1).fn();
  assert.equal(rows.children[0], original);
  await saving;
  assert.notEqual(rows.children[0], original);
});
