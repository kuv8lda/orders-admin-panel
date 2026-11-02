const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function setup() {
  class Element {
    constructor() { this.children = []; this.listeners = {}; this.dataset = {}; this.value = ''; }
    append(...children) { this.children.push(...children); }
    replaceChildren(...children) { this.children = children; }
    setAttribute(key, value) { this[key] = value; }
    getAttribute(key) { return this[key]; }
    removeAttribute(key) { delete this[key]; }
    addEventListener(event, fn) { this.listeners[event] = fn; }
    focus() {}
  }
  const elements = {};
  const ids = ['productRows', 'productsShowHidden', 'productsNotice', 'productsDatalist',
    'productHistory', 'productModalHistory', 'productModalTitle', 'productName', 'productPrice',
    'productModalHistoryWrap', 'productForm', 'productModal', 'productFormNotice', 'productSave',
    'productAdd', 'productsRefresh'];
  for (const id of ids) elements[id] = new Element();
  const document = {
    listeners: {}, activeElement: new Element(),
    createElement: () => new Element(),
    getElementById: (id) => elements[id],
    addEventListener(event, fn) { this.listeners[event] = fn; },
    dispatchEvent(event) { dispatched.push(event); return this.listeners[event.type]?.(event); },
  };
  const products = [
    { id: 1, name: '<b>Мой товар</b>', price_cents: 12500, is_active: 1 },
    { id: 2, name: 'Скрытый', price_cents: 5000, is_active: 0 },
  ];
  const history = [];
  const calls = [], dispatched = [], errors = [], toasts = [];
  let failWrite = false;
  const context = vm.createContext({
    document, Intl, Date, console,
    CustomEvent: class { constructor(type, options) { this.type = type; this.detail = options.detail; } },
    I18N: { t: (key) => key, getLang: () => 'ru', onChange() {}, apply() {} },
    OrdersApp: {
      async api(url, options) {
        calls.push({ url, options });
        if (options?.method) {
          if (failWrite) throw new Error('network');
          const payload = JSON.parse(options.body);
          const product = options.method === 'POST' ? { id: 3, is_active: 1 } : products.find((p) => url === `/api/products/${p.id}`);
          if ('price_cents' in payload && product.price_cents && product.price_cents !== payload.price_cents) {
            history.unshift({ id: 1, product_id: product.id, name: payload.name,
              old_price_cents: product.price_cents, new_price_cents: payload.price_cents,
              created_at: '2026-10-23T12:00:00Z' });
          }
          Object.assign(product, payload);
          if (options.method === 'POST') products.push(product);
          return product;
        }
        if (url === '/api/products/history') return { items: history };
        if (url === '/api/products') return { items: products };
        return { csrf_token: 'test' };
      },
      toast: (text) => toasts.push(text),
      openModal: () => { elements.productModal.open = true; },
      closeModal: () => { elements.productModal.open = false; },
      clearFieldErrors() {},
      renderFieldErrors: (_, fields) => { if (fields) errors.push(fields); },
    },
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../js/products.js'), 'utf8'), context);
  return { document, elements, calls, dispatched, errors, toasts, products, history,
    failWrites() { failWrite = true; } };
}

const tick = () => new Promise((resolve) => setImmediate(resolve));
const submit = (elements) => elements.productForm.listeners.submit({ preventDefault() {} });

test('editing OWN price saves integer cents and refreshes catalogue plus audit history', async () => {
  const { document, elements, calls, dispatched } = setup();
  await document.listeners.DOMContentLoaded();
  assert.equal(calls[0].url, '/api/me');
  assert.equal(elements.productRows.children.length, 1);
  assert.equal(elements.productRows.children[0].children[0].textContent, '<b>Мой товар</b>');
  document.listeners['products:edit']({ detail: { id: 1, name: '<b>Мой товар</b>', price_cents: 12500 } });
  await tick();
  assert.equal(elements.productPrice.value, '125.00');
  elements.productPrice.value = '130,05';
  elements.productName.value = '  Новый товар  ';
  await submit(elements);
  const write = calls.find((c) => c.options?.method === 'PATCH');
  assert.equal(write.url, '/api/products/1');
  assert.deepEqual(JSON.parse(write.options.body), { name: 'Новый товар', price_cents: 13005 });
  assert.equal(elements.productModal.open, false);
  assert.match(elements.productHistory.children[0].textContent, /Новый товар/);
  assert.match(elements.productModalHistory.children[0].textContent, /Новый товар/);
  assert.equal(elements.productsDatalist.children[0].value, 'Новый товар');
  assert.equal(dispatched[0].type, 'products:changed');
  assert.equal(dispatched[0].detail.price_cents, 13005);
  const writeIndex = calls.indexOf(write);
  assert.ok(calls.slice(writeIndex + 1).some((c) => c.url === '/api/products/history'));
});

test('add, hide and reactivate update active datalist and preserve hidden rows', async () => {
  const { document, elements, calls } = setup();
  await document.listeners.DOMContentLoaded();
  elements.productAdd.listeners.click();
  await tick();
  elements.productName.value = 'Новинка';
  elements.productPrice.value = '0.01';
  await submit(elements);
  const create = calls.find((c) => c.options?.method === 'POST');
  assert.deepEqual(JSON.parse(create.options.body), { name: 'Новинка', price_cents: 1 });
  let toggle = elements.productRows.children[0].children[3].children[0].children[1];
  await toggle.listeners.click();
  assert.equal(elements.productsDatalist.children.length, 1);
  elements.productsShowHidden.checked = true;
  elements.productsShowHidden.listeners.change();
  assert.equal(elements.productRows.children.length, 3);
  toggle = elements.productRows.children[0].children[3].children[0].children[1];
  await toggle.listeners.click();
  const patches = calls.filter((c) => c.options?.method === 'PATCH').map((c) => JSON.parse(c.options.body));
  assert.deepEqual(patches, [{ is_active: false }, { is_active: true }]);
  assert.equal(elements.productsDatalist.children.length, 2);
});

test('invalid prices never write and failed save retains editable input', async () => {
  const state = setup();
  const { document, elements, calls, errors } = state;
  await document.listeners.DOMContentLoaded();
  elements.productAdd.listeners.click();
  await tick();
  elements.productName.value = 'Товар';
  for (const price of ['', '0', '-1', '1.001', '1e3', '10000000.01']) {
    elements.productPrice.value = price;
    await submit(elements);
    assert.ok(errors.at(-1).price_cents);
  }
  assert.equal(calls.filter((c) => c.options?.method).length, 0);
  state.failWrites();
  elements.productPrice.value = '12.34';
  await submit(elements);
  assert.equal(elements.productPrice.value, '12.34');
  assert.equal(elements.productModal.open, true);
  assert.equal(elements.productSave.disabled, false);
  assert.equal(elements.productFormNotice.textContent, 'products.save_error');
});
