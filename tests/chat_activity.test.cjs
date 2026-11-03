const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const code = () => fs.readFileSync('game/static/game/master_activity.js', 'utf8');

function setup(enabled = true, hidden = false) {
  const listeners = {}, timers = new Map(), requests = [];
  let serial = 0;
  const root = {dataset:{activityUrl:'/master/chat/activity/'}};
  const message = (id, kind = 'text', outgoing = false) => ({
    dataset:{messageId:id, messageKind:kind}, classList:{contains:() => outgoing},
  });
  let messages = [message('9007199254740993')];
  const document = {
    hidden, title:'Стол мастера · Dungeons & Kabans',
    querySelector:() => enabled ? root : null,
    querySelectorAll:() => messages,
    addEventListener:(name, fn) => { listeners[name] = fn; },
  };
  const setTimeout = (fn, delay) => { timers.set(++serial, {fn, delay}); return serial; };
  vm.runInNewContext(code(), {document, URLSearchParams, AbortController,
    setTimeout, clearTimeout:id => timers.delete(id),
    fetch:(url, options) => new Promise((resolve, reject) => requests.push({url, options, resolve, reject})),
  });
  const tick = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };
  return {document, requests, timers, message, tick,
    messages:rows => { messages = rows; },
    visibility:value => { document.hidden = value; listeners.visibilitychange?.(); },
    swap:() => listeners['htmx:oobAfterSwap']?.({detail:{target:{id:'master-messages'}}}),
    blocked:id => { let blocked = false; listeners['htmx:beforeRequest']?.({
      detail:{elt:{id}}, preventDefault:() => { blocked = true; },
    }); return blocked; },
    fire:delay => { const [id, timer] = [...timers].find(([, t]) => t.delay === delay); timers.delete(id); timer.fn(); },
    respond:async (count, max_id = '9007199254740994') => {
      requests.at(-1).resolve({ok:true, json:async () => ({count, max_id})}); await tick();
    },
  };
}

(async () => {
  const s = setup();
  const original = s.document.title;
  assert.equal(s.requests.length, 0);
  assert.equal(s.blocked('poller'), false);
  s.visibility(true);
  assert.equal(s.blocked('poller'), true);
  assert.equal(s.blocked('other-request'), false);
  assert.equal(s.requests.length, 1);
  assert.match(s.requests[0].url, /after=9007199254740993/);
  await s.respond(3);
  assert.equal(s.document.title, '🔴 Новых сообщений: 3');
  s.fire(1000);
  assert.equal(s.document.title, `(3) ${original}`);
  s.fire(5000); await s.respond(3);
  assert.ok(s.document.title.includes('3')); // Cumulative, not 6.
  s.fire(5000); s.requests.at(-1).reject(new Error('offline')); await s.tick();
  assert.ok(s.document.title.includes('3'));
  s.fire(5000); await s.respond(0, '0');
  assert.equal(s.document.title, original); // History cleared remotely.
  s.fire(5000); await s.respond(2);
  s.visibility(false);
  assert.equal(s.document.title, original);
  assert.equal(s.timers.size, 0);
  s.messages([s.message('9007199254740999', 'action_use'), s.message('9007199254741005', 'health'),
    s.message('9007199254741006', 'text', true)]);
  s.swap(); s.visibility(true);
  assert.match(s.requests.at(-1).url, /after=9007199254740999/);
  const late = s.requests.at(-1);
  s.visibility(false); s.visibility(true);
  late.resolve({ok:true, json:async () => ({count:99, max_id:'9007199254741111'})}); await s.tick();
  assert.equal(s.document.title, original);
  await s.respond(1);
  assert.equal(s.document.title, '🔴 Новых сообщений: 1');
  // Hidden OOB updates must not acknowledge unseen activity.
  s.messages([s.message('9007199254741100')]); s.swap();
  s.fire(5000);
  assert.match(s.requests.at(-1).url, /after=9007199254740999/);
  s.visibility(false);
  assert.equal(setup(false).requests.length, 0);
  assert.equal(setup(true, true).requests.length, 1);
  console.log('Chat activity tests passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
