#!/usr/bin/env node
/** Offline DOM integration smoke test; not browser/layout verification.
 * Run: node scripts/test_renderer.mjs
 * Uses only Node built-ins and never fetches or changes application data.
 */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';

const read = path => readFileSync(fileURLToPath(new URL(`../${path}`, import.meta.url)), 'utf8');
const candidate = JSON.parse(read('data.json'));
const monitor = JSON.parse(read('monitor_status.json'));
const source = read('app.js');
assert.match(source, /load\(\);\s*$/, 'Expected terminal load() entry point');
const offlineSource = source.replace(/load\(\);\s*$/, '');

class Element {
  constructor() {
    this.children = [];
    this.options = [];
    this.value = '';
    this.dataset = {};
    this.textContent = '';
    this.innerHTML = '';
    this.attributes = new Map();
    this.classList = { add() {}, remove() {}, toggle() { return false; } };
  }
  get childElementCount() { return this.children.length; }
  setAttribute(name, value) { this.attributes.set(name, String(value)); }
  removeAttribute(name) { this.attributes.delete(name); }
  addEventListener() {}
  appendChild(node) { this.children.push(node); return node; }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  querySelector() { return null; }
  querySelectorAll() { return []; }
  closest() { return null; }
}

function harness(fixture) {
  const elements = new Map();
  const document = {
    getElementById(id) {
      if (!elements.has(id)) elements.set(id, new Element());
      return elements.get(id);
    },
    createElement: () => new Element(),
    createElementNS: () => new Element(),
    createTextNode: textContent => ({ textContent }),
    querySelector: () => new Element(),
  };
  const context = vm.createContext({ document, console, Intl, Date });
  vm.runInContext(offlineSource, context, { filename: 'app.js' });
  context.fixture = structuredClone(fixture);
  context.monitorFixture = structuredClone(monitor);
  vm.runInContext('data = fixture; monitorStatus = monitorFixture; setup();', context);
  return { context, elements, run: code => vm.runInContext(code, context) };
}

function exerciseModes(h, memberIndex) {
  let combinations = 0;
  for (const mode of ['raw', 'noise', 'clustered']) {
    for (const fhour of [0, 12, 120, 240]) {
      h.elements.get('view').value = mode;
      h.elements.get('hour').value = fhour;
      h.run(`render(); showDetail(data.tracks[${memberIndex}]);`);
      combinations += 1;
    }
  }
  assert.equal(combinations, 12);
  assert.equal(Number(h.elements.get('hour').max), 240);
}

// Exercise the actual checked-in ensemble, including its real terminated tracks.
const actual = harness(candidate);
const stoppedIndex = candidate.tracks.findIndex(track => track.termination);
exerciseModes(actual, stoppedIndex < 0 ? 0 : stoppedIndex);
if (stoppedIndex >= 0) {
  assert.match(actual.elements.get('trackingIdentityNotice').textContent, /追跡打切り/);
  assert.match(actual.elements.get('detail').innerHTML, /追跡打切り:/);
}

// Recycled Invest numbers must not alias an older episode into the active one.
const identity = harness(candidate);
identity.run("monitorStatus = { state: 'active', trackingTargetId: '2026-10-invest-94w', targetAliases: ['94W'] }; data.meta.stormInfo = { id: '94W', aliases: ['94W'] };");
for (const [episode, expected] of [
  [undefined, true],
  ['2026-08-invest-94w', true],
  ['2026-10-invest-94w', false],
]) {
  identity.context.episode = episode;
  identity.run('data.meta.trackingTargetId = episode;');
  assert.equal(identity.run('activeMonitorDiffersFromData()'), expected, `Episode ${episode}`);
}

// The renderer must also tolerate the shortest legal NOISE prefix.
const prefixFixture = structuredClone(candidate);
Object.assign(prefixFixture.tracks[0], {
  points: prefixFixture.tracks[0].points.slice(0, 1),
  cluster: 'NOISE',
  noiseReasons: ['center_lost'],
  termination: { reason: 'center_lost', atForecastHour: 12, lastValidForecastHour: 0 },
});
const prefix = harness(prefixFixture);
exerciseModes(prefix, 0);
assert.match(prefix.elements.get('trackingIdentityNotice').textContent, /追跡打切り/);
assert.match(prefix.elements.get('detail').innerHTML, /最終有効 \+0h/);

// Saved old analyses must visibly identify their age.
const staleFixture = structuredClone(candidate);
staleFixture.meta.init = '2000010100';
const stale = harness(staleFixture);
assert.match(stale.elements.get('trackingIdentityNotice').textContent, /保存済み解析:.*時間が経過/);

console.log(`Renderer DOM smoke passed: actual ${candidate.meta.init} (${candidate.summary.cleanMembers} clean, ${candidate.tracks.filter(t => t.termination).length} terminated); 12 actual + 12 single-point-prefix mode/time cases; 3 episode cases; stale notice`);
