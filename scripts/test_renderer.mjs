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

function harness(fixture, monitorOverride=monitor) {
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
  context.monitorFixture = structuredClone(monitorOverride);
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


// Explicit pending target never presents the old storm as a current forecast.
const archiveFixture = JSON.parse(read('history/2026100506.json'));
const pending = harness(archiveFixture, {state:'active',trackingTargetId:'2026-10-invest-95w',titleJa:'Invest 95W 個別監視中',analysisState:'analysis_pending_quality',checkedAt:'2026-10-09T11:52:00Z'});
pending.run('renderArchiveSeparation()');
assert.equal(pending.elements.get('analysisMap').hidden,true);
assert.equal(pending.elements.get('analysisSituation').hidden,true);
assert.equal(pending.elements.get('metrics').hidden,true);
assert.match(pending.elements.get('subtitle').textContent,/95W.*判定保留/);
assert.match(pending.elements.get('lastUpdated').textContent,/監視状態更新:.*新対象の合格解析なし/);
pending.elements.get('archiveToggle').onclick();
assert.equal(pending.elements.get('analysisMap').hidden,false);
assert.match(pending.elements.get('archiveSeparationNotice').textContent,/履歴:.*29号.*現在の監視対象の予測ではありません/);
pending.elements.get('archiveToggle').onclick();
assert.equal(pending.elements.get('analysisMap').hidden,true);
for(let i=0;i<3;i++){pending.run('setup(); render();');assert.equal(pending.elements.get('analysisMap').hidden,true)}
// Selecting a history record remains explicitly archived; returning current hides it.
pending.run("currentDataPath='./history/2026100506.json';archiveExpanded=true;setup()");
assert.equal(pending.elements.get('analysisMap').hidden,false);
pending.run("currentDataPath='./data.json';archiveExpanded=false;setup()");
assert.equal(pending.elements.get('analysisMap').hidden,true);
// A future complete same-target payload exits pending without a monitor-file rewrite.
pending.run("data.meta.trackingTargetId=monitorStatus.trackingTargetId;data.meta.stormInfo={id:'95W',aliases:['95W']};setup()");
assert.equal(pending.elements.get('analysisMap').hidden,false);
assert.equal(pending.elements.get('archiveToggle').hidden,true);
assert.doesNotMatch(pending.elements.get('subtitle').textContent,/判定保留/);
assert.doesNotMatch(pending.elements.get('lastUpdated').textContent,/新対象の合格解析なし/);
console.log('Pending/archive toggle, repeated setup, history return, and same-target success transition passed');
pending.context.fixture=structuredClone(archiveFixture);
pending.run("currentTargetAvailable=true;data=fixture;archiveExpanded=true;setup()");
assert.doesNotMatch(pending.elements.get('subtitle').textContent,/判定保留/);
assert.match(pending.elements.get('monitorStateBadge').textContent,/履歴表示/);
assert.match(pending.elements.get('lastUpdated').textContent,/履歴表示中/);
console.log('Successful-current-analysis followed by old archive does not revive stale pending state');
