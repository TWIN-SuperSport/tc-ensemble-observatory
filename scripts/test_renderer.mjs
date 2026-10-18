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
  constructor(tagName='div') {
    this.tagName = tagName;
    this.children = [];
    this.options = [];
    this.value = '';
    this.dataset = {};
    this.textContent = '';
    this.innerHTML = '';
    this.attributes = new Map();
    this.classList = { add() {}, remove() {}, toggle() { return false; } };
  }
  get textContent() { return this._textContent + this.children.map(node => node.textContent || '').join(''); }
  set textContent(value) { this._textContent = String(value); this.children = []; }
  get innerHTML() { return this._innerHTML; }
  set innerHTML(value) { this._innerHTML = String(value); this._textContent = ''; this.children = []; }
  get childElementCount() { return this.children.length; }
  setAttribute(name, value) { this.attributes.set(name, String(value)); }
  removeAttribute(name) { this.attributes.delete(name); }
  addEventListener() {}
  appendChild(node) { this.children.push(node); return node; }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this._textContent = ''; this._innerHTML = ''; this.children = nodes; }
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
    createElement: tag => new Element(tag),
    createElementNS: (_, tag) => new Element(tag),
    createTextNode: textContent => ({ textContent }),
    querySelector: () => new Element(),
  };
  const context = vm.createContext({ document, console, Intl, Date });
  vm.runInContext(offlineSource, context, { filename: 'app.js' });
  context.fixture = structuredClone(fixture);
  context.monitorFixture = structuredClone(monitorOverride);
  elements.get('view').value = 'clustered';
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
  assert.match(actual.elements.get('detail').textContent, /追跡打切り:/);
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
assert.match(prefix.elements.get('detail').textContent, /最終有効 \+0h/);

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

// The active JTWC warning serial takes precedence over its retained Invest ID.
// JMA's independently issued official number/name still takes first priority.
const labels = harness(candidate);
labels.run('delete data.meta.targetLabel;');
for (const [storm, expected] of [
  [{id:'96W',jtwcNumber:'29W',status:'tropical_depression',number:null,candidateNumber:null}, '29W'],
  [{id:'96W',jtwcNumber:'29W',status:'candidate',candidateNumber:30}, '29W（台風30号候補）'],
  [{id:'96W',jtwcNumber:'29W',status:'typhoon',number:30,nameJa:'テスト',name:'TEST'}, '台風30号（テスト / TEST）'],
  [{id:'94W',jtwcNumber:'27W',status:'typhoon',number:29,nameJa:'コグマ',name:'KOGUMA'}, '台風29号（コグマ / KOGUMA）'],
  [{id:'96W',jtwcNumber:null,status:'invest'}, '96W'],
]) {
  labels.context.storm = storm;
  labels.run('data.meta.stormInfo = storm;');
  assert.equal(labels.run('targetLabel()'), expected);
}
console.log('JTWC29W labels preserve Invest96W lineage and independent JMA number/name priority');


// Valid terminated prefixes are a separate class, visible in the default mode.
const partialFixture = structuredClone(candidate);
const templatePoints = candidate.tracks.find(t => t.points.at(-1)?.fhour === 240).points;
partialFixture.meta.clusterForecastHour = 120;
partialFixture.summary = {members:31, cleanMembers:16, terminatedMembers:10, validMembers:26, noiseMembers:5, clusterCount:2, clusteredMembers:21};
partialFixture.tracks = candidate.tracks.map((track,index) => {
  const full=index<16, terminated=index>=16&&index<26, last=full||!terminated?240:index<21?120:24;
  return {member:track.member,cluster:full?(index<10?'C1':'C2'):terminated?'TERMINATED':'NOISE',scenarioCluster:terminated&&index<21?'C1':null,noiseReasons:terminated||full?[]:['tracker_jump'],termination:terminated?{reason:'center_lost',atForecastHour:last+12,lastValidForecastHour:last}:null,points:templatePoints.filter(p=>p.fhour<=last).map(p=>({...p,member:track.member,lon:p.lon+index*.1}))};
});
partialFixture.summary.availableMembersByLead=templatePoints.map(p=>({fhour:p.fhour,count:p.fhour<=24?26:p.fhour<=120?21:16,total:31}));
partialFixture.clusters=[{id:'C1',label:'Test west',count:15,share:48.4,medianTrack:templatePoints.filter(p=>p.fhour<=120)},{id:'C2',label:'Test east',count:6,share:19.4,medianTrack:templatePoints.filter(p=>p.fhour<=120)}];
const partial=harness(partialFixture);
const descendants=node=>node.children.flatMap(child=>[child,...descendants(child)]);
const plotted=(h,tag,klass)=>descendants(h.elements.get('plot')).filter(node=>node.tagName===tag&&String(node.attributes.get('class')||'').split(/\s+/).includes(klass));
partial.run('render()');
assert.equal(plotted(partial,'path','track').length,26);
assert.equal(plotted(partial,'path','terminatedTrack').length,10);
assert.equal(plotted(partial,'circle','terminationEndpoint').length,10);
assert.equal(plotted(partial,'path','noiseTrack').length,0);
assert.match(partial.elements.get('trackingCoverage').innerHTML,/有効追跡 26 \/ 全31本/);
assert.match(partial.elements.get('trackingCoverage').innerHTML,/全予報時間 16本・打切り 10本/);
assert.match(partial.elements.get('trackingCoverage').innerHTML,/21本が揃う\+120hまで/);
assert.match(partial.elements.get('clusters').innerHTML,/15 \/ 全31本・\+120hまで/);
assert.match(partial.elements.get('coverageAtHour').textContent,/\+240h.*16 \/ 全31本/);
assert.match(partial.elements.get('legend').innerHTML,/追跡打切り/);
assert.doesNotMatch(partial.elements.get('trackingIdentityNotice').textContent,/短い線はNOISE/);
assert.doesNotMatch(partial.elements.get('situationCards').innerHTML,/来そうな予感|簡易スコア/);
const japanCard=partial.elements.get('situationCards').innerHTML.split('日本周辺に入る予測')[1];
assert.doesNotMatch(japanCard,/%/,'Censored tails must never become a survivor-normalized Japan probability');
partial.run('showDetail(data.tracks[16])');
assert.match(partial.elements.get('detail').textContent,/TERMINATED.*シナリオ: C1/);
assert.match(partial.elements.get('detail').textContent,/center_lost.*最終有効 \+120h/);
assert.match(partial.elements.get('detail').textContent,/この先の進路は不明/);
const endedPath=plotted(partial,'path','terminatedTrack')[0].attributes.get('d');
assert.equal(endedPath.split(' ').length,11,'Terminated path contains only its 0..120h measured points');
partial.elements.get('hour').value=24;partial.run('render()');
assert.match(partial.elements.get('coverageAtHour').textContent,/\+24h.*26 \/ 全31本/);
assert.equal(plotted(partial,'circle','terminationEndpoint').length,5,'End marker must not appear before the real termination');
partial.elements.get('view').value='noise';partial.run('render()');
assert.equal(plotted(partial,'path','terminatedTrack').length,0);
assert.equal(plotted(partial,'path','noiseTrack').length,5);
partial.elements.get('view').value='raw';partial.run('render()');
assert.equal(plotted(partial,'path','track').length,31);
partial.run("selectedCluster='C1';view.value='clustered';render()");
assert.equal(plotted(partial,'path','track').length,15,'Scenario filter includes TERMINATED members by scenarioCluster');
// A valid one-point prefix remains selectable without inventing a connecting segment.
partial.run("selectedCluster=null;data.tracks[25].points=data.tracks[25].points.slice(0,1);data.tracks[25].termination={reason:'domain_edge',atForecastHour:12,lastValidForecastHour:0};hour.value=240;render()");
assert.equal(plotted(partial,'path','terminatedTrack').length,9);
assert.equal(plotted(partial,'circle','terminationEndpoint').length,10);
partial.run('showDetail(data.tracks[25])');
assert.match(partial.elements.get('detail').textContent,/domain_edge.*最終有効 \+0h/);
// Failure/attempt timestamps must not masquerade as successful analysis publication.
partial.run("latestRun={init:data.meta.init,checkedAt:'2026-10-17T09:38:15Z'};updateStatus={lastAttemptAt:'2026-10-18T18:00:00Z',init:'2026101812',status:'failed',message:'Quality validation failed',publishedInit:data.meta.init};renderLastUpdated()");
assert.equal(partial.elements.get('updateAttemptStatus').hidden,false);
assert.match(partial.elements.get('updateAttemptStatus').textContent,/最終更新試行:.*更新失敗.*最後に公開できた解析/);
assert.match(partial.elements.get('lastUpdated').textContent,/保存解析の公開更新:.*2026\/10\/17/);
assert.doesNotMatch(partial.elements.get('lastUpdated').textContent,/2026\/10\/19/);
partial.run("updateStatus={lastAttemptAt:'2026-10-18T18:00:00Z',status:'unchanged'};renderLastUpdated()");
assert.match(partial.elements.get('updateAttemptStatus').textContent,/公開済みランを確認/);
partial.run('updateStatus=null;renderLastUpdated()');
assert.equal(partial.elements.get('updateAttemptStatus').hidden,true,'Legacy sites without update_status remain usable');
console.log('Partial-track renderer passed: valid prefixes, real endpoints, per-lead counts, explicit horizon/denominator, no censored probability, failure timestamp, and old payload compatibility');

// Zero-duration common coverage must not claim that a scenario was calculated.
partial.run("data.meta.clusterForecastHour=0;data.clusters=[];data.summary.clusterCount=0;data.summary.clusteredMembers=0;setup();render()");
assert.match(partial.elements.get('trackingCoverage').innerHTML,/比率・中央値は未算出/);
assert.match(partial.elements.get('situationCards').innerHTML,/共通の進路区間が不足/);
assert.equal(plotted(partial,'path','median').length,0);
console.log('Zero-horizon coverage displays valid prefixes without fabricating scenario summaries');
