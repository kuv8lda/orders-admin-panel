// Run with: node --test tests/auto_refresh.test.cjs
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function setup() {
  const timers = [];
  const document = {
    hidden: false,
    blocked: false,
    querySelector(selector) {
      assert.ok(selector.includes('.modal-backdrop.open'));
      return this.blocked ? {} : null;
    },
    addEventListener() {},
  };
  const ctx = vm.createContext({
    Intl, URLSearchParams, AbortController, console, document, location: { hash: "#analytics" },
    I18N: { t() {}, tn() {} },
    setTimeout(fn, ms) { timers.push({ fn, ms }); return timers.length; },
    clearTimeout() {},
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../js/dashboard.js'), 'utf8'), ctx);
  return { ctx, document, timers, run: (code) => vm.runInContext(code, ctx) };
}

test('refreshes data silently without resetting filters and skips hidden/editing views', async () => {
  const { document, run } = setup();
  run(`
    globalThis.calls = [];
    state.q = 'хлеб'; state.page = 3;
    refreshStats = async (silent) => calls.push(['stats', silent]);
    refreshActivity = async () => calls.push(['activity']);
    refreshTable = async (...args) => calls.push(['table', ...args]);
  `);
  await run('autoRefresh()');
  assert.equal(run('JSON.stringify(calls)'), '[["stats",true],["activity"]]');
  assert.equal(run('state.q'), 'хлеб');
  assert.equal(run('state.page'), 3);
  run('calls = []');
  document.hidden = true;
  await run('autoRefresh()');
  document.hidden = false;
  document.blocked = true;
  await run('autoRefresh()');
  assert.equal(run('calls.length'), 0);
});

test('does not overlap polling and waits for completion before rescheduling', async () => {
  const { run, timers } = setup();
  run(`
    globalThis.count = 0;
    refreshStats = () => { count++; return new Promise(resolve => globalThis.release = resolve); };
    refreshActivity = async () => {};
    refreshTable = async () => {};
    startAutoRefresh();
  `);
  assert.equal(timers[0].ms, 5000);
  const pending = timers[0].fn();
  await run('autoRefresh()');
  assert.equal(run('count'), 1);
  assert.equal(timers.length, 1);
  run('release()');
  await pending;
  assert.equal(timers.length, 2);
});

test('clears busy flag after an unexpected failure', async () => {
  const { run } = setup();
  run(`
    refreshStats = async () => { throw new Error('offline'); };
    refreshActivity = async () => {};
    refreshTable = async () => {};
  `);
  await assert.rejects(run('autoRefresh()'), /offline/);
  assert.equal(run('autoRefreshBusy'), false);
});

test('overview polling never requests analytics or hidden order table', async () => {
  const { ctx, run } = setup();
  ctx.location.hash = '#overview';
  run(`
    globalThis.calls = [];
    refreshOverview = async () => calls.push('overview');
    refreshStats = async () => calls.push('stats');
    refreshTable = async () => calls.push('table');
  `);
  await run('autoRefresh()');
  assert.equal(run('JSON.stringify(calls)'), '["overview"]');
});

test('orders polling stays silent and never requests charts', async () => {
  const { ctx, run } = setup();
  ctx.location.hash = '#orders';
  run(`
    globalThis.calls = [];
    refreshStats = async () => calls.push(['stats']);
    refreshTable = async (...args) => calls.push(['table', ...args]);
  `);
  await run('autoRefresh()');
  assert.equal(run('JSON.stringify(calls)'), '[["table",true,true]]');
});
