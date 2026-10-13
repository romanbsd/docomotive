// Exercise shipped controls with a strict DOM double, not a browser smoke test.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const script = fs.readFileSync('scripts/proofread_review.js', 'utf8');
function setup(restored, restoredKey, label = 'bad', restoredMetrics) {
  let exported;
  let now = 0, visibility;
  function article(id, disabled = false) {
    const fields = {
      '.selected': {checked: false, disabled},
      '.replacement': {value: ''}, '.reviewed': {checked: false},
      '.proposal': {value: ''}
    };
    fields['.review-timer'] = {textContent: 'Start review timer'};
    const radios = ['keep', 'ocr-error', 'printed-typo', 'uncertain'].map(value => ({value, _checked: false}));
    for (const radio of radios) Object.defineProperty(radio, 'checked', {
      get() { return this._checked; },
      set(value) { if (value) for (const r of radios) r._checked = false; this._checked = value; }
    });
    const image = {dataset: {image: 'crop'}}, pre = {textContent: ''};
    const detail = {open: false, querySelector(selector) {
      assert.equal(selector, 'pre'); return pre;
    }, addEventListener(event, fn) { assert.equal(event, 'toggle'); this.toggle = fn; }};
    return {id, fields, radios, image, detail, pre,
      querySelector(selector) {
        if (fields[selector]) return fields[selector];
        if (selector === 'input[type=radio]:checked') return radios.find(r => r.checked);
        const r = radios.find(r => selector === `input[value="${r.value}"]`);
        assert.ok(r, 'Unexpected article selector: ' + selector); return r;
      }, querySelectorAll(selector) {
        if (selector === 'input[type=radio]') return radios;
        if (selector === 'img[data-image]') return [image];
        if (selector === '.proposal-evidence') return [detail];
        assert.fail('Unexpected article selector: ' + selector);
      }, addEventListener(event, fn) { assert.equal(event, 'change'); this.change = fn; }
    };
  }
  const articles = [article('first'), article('excluded'), article('unbound', true)];
  const controls = Object.fromEntries(['.select-all', '.clear', '.batch', '.batch-reviewed'].map(s => [s, {checked: false}]));
  const group = {querySelector(s) { assert.ok(controls[s]); return controls[s]; }, querySelectorAll(s) {
    if (s === 'article') return articles;
    if (s === '.selected') return articles.map(a => a.fields[s]);
    if (s === '.selected:not(:disabled)') return articles.map(a => a.fields['.selected']).filter(c => !c.disabled);
    assert.fail('Unexpected group selector: ' + s);
  }, addEventListener(event, fn) { assert.equal(event, 'change'); this.change = fn; }};
  const payload = {format_version: 2, source_sha256: 'source', model_sha256: 'model', profile_sha256: 'profile',
    images: {crop: 'data:image/jpeg;base64,dGVzdA=='}, candidate_evidence: {signals: {reason: 'witness'}},
    items: articles.map(a => ({id: a.id, word: label, proposal: 'good', candidate_signals_key: 'signals', binding: a.fields['.selected'].disabled ? null : {page: 1, before: 'bad', row_id: a.id, start: 4, end: 7, row_text_sha256: 'rowhash', bbox: [.1,.2,.9,.3]}}))};
  const status = {textContent: ''}, download = {};
  const expectedKey = "docomotive-proof-v2-source-model-profile";
  const stored = {};
  if (restored !== undefined) stored[restoredKey || expectedKey] = restored;
  if (restoredMetrics !== undefined) stored[expectedKey + '-metrics-v1'] = restoredMetrics;
  const context = {document: {
    hidden: false,
    addEventListener(event, fn) { assert.equal(event, 'visibilitychange'); visibility = fn; },
    getElementById(id) { const result = {'review-data': {textContent: JSON.stringify(payload)}, 'storage-status': status, download}[id]; assert.ok(result); return result; },
    querySelectorAll(s) { if (s === 'article') return articles; if (s === '.group') return [group]; assert.fail('Unexpected document selector: ' + s); },
    createElement(s) { assert.equal(s, 'a'); return {click() {}}; }
  }, localStorage: {getItem: k => stored[k], setItem: (k,v) => stored[k] = v},
  URL: {createObjectURL(blob) { exported = blob.text; return 'blob:test'; }, revokeObjectURL() {}},
  Blob: class {constructor(parts) { this.text = parts.join(''); }}, setTimeout: fn => fn(), performance: {now: () => now}};
  vm.runInNewContext(script, context);
  return {articles, controls, group, payload, status, download, stored, expectedKey,
    advance(ms) {now += ms;}, hide() {context.document.hidden = true; visibility();},
    show() {context.document.hidden = false; visibility();},
    exported: () => exported, clearExport: () => {exported = undefined;}};
}
function change(article, cls) { article.change({target: {classList: {contains: c => c === cls}, value: article.fields['.' + cls].value}}); }
const t = setup();
const [first, excluded, unbound] = t.articles;
assert.equal(first.image.src, t.payload.images.crop);
first.detail.open = true; first.detail.toggle();
assert.deepEqual(JSON.parse(first.pre.textContent), {reason: 'witness'});
t.controls['.batch-reviewed'].checked = true;
t.controls['.select-all'].onclick();
assert.equal(t.controls['.batch-reviewed'].checked, false);
assert.equal(unbound.fields['.selected'].checked, false);
excluded.fields['.selected'].checked = false;
t.controls['.batch'].onclick();
assert.match(t.status.textContent, /acknowledge/);
assert.equal(first.radios[1].checked, false);
t.controls['.batch-reviewed'].checked = true;
t.controls['.batch'].onclick();
assert.equal(first.fields['.reviewed'].checked, true);
assert.equal(excluded.fields['.reviewed'].checked, false);
t.download.onclick();
assert.equal(JSON.parse(t.exported()).decisions.length, 1);
assert.equal(JSON.parse(t.exported()).decisions[0].id, 'first');
assert.equal(JSON.parse(t.exported()).format_version, 2);
const exportedRecord = JSON.parse(t.exported()).decisions[0];
assert.equal(exportedRecord.decision, 'ocr-error');
assert.equal(exportedRecord.reviewed, true);
assert.equal(exportedRecord.replacement_or_note, 'good');
assert.deepEqual(exportedRecord.binding, t.payload.items[0].binding);
assert.deepEqual(Object.keys(t.stored), [t.expectedKey]);
first.fields['.proposal'].value = 'better'; change(first, 'proposal');
assert.equal(first.fields['.replacement'].value, 'better');
assert.equal(first.fields['.reviewed'].checked, false);
for (const replacement of ['   ', ' bad ', 'bad']) {
  first.fields['.replacement'].value = replacement; change(first, 'replacement');
  first.fields['.reviewed'].checked = true; change(first, 'reviewed');
  t.clearExport(); t.download.onclick(); assert.equal(t.exported(), undefined);
  assert.match(t.status.textContent, /acknowledgement/);
}
first.fields['.replacement'].value = 'good'; change(first, 'replacement');
assert.equal(first.fields['.reviewed'].checked, false);
t.controls['.batch-reviewed'].checked = true;
t.group.change({target: {classList: {contains: () => false}}});
assert.equal(t.controls['.batch-reviewed'].checked, false);
t.controls['.clear'].onclick();
assert.equal(first.fields['.selected'].checked, false);
assert.equal(first.fields['.replacement'].value, 'good');
const saved = setup(JSON.stringify({first: {decision: 'keep', replacement_or_note: 'retain', reviewed: true}}));
assert.equal(saved.articles[0].radios[0].checked, true);
assert.equal(saved.articles[0].fields['.replacement'].value, 'retain');
assert.equal(saved.articles[0].fields['.reviewed'].checked, true);
assert.match(setup('{broken').status.textContent, /could not be restored/);
const legacy = setup(JSON.stringify({first: {decision: 'ocr-error', replacement_or_note: 'good', reviewed: true}}), 'docomotive-proof-source');
assert.equal(legacy.articles[0].fields['.reviewed'].checked, false);
const missing = setup();
const missingArticle = missing.articles[2];
missingArticle.radios[1].checked = true;
missingArticle.fields['.replacement'].value = 'good';
missingArticle.fields['.reviewed'].checked = true; change(missingArticle, 'reviewed');
missing.download.onclick(); assert.equal(missing.exported(), undefined);
const skipped = setup();
skipped.articles[0].fields['.selected'].checked = true;
skipped.articles[0].fields['.replacement'].value = 'bad';
skipped.controls['.batch-reviewed'].checked = true;
skipped.controls['.batch'].onclick();
assert.match(skipped.status.textContent, /1 selected occurrences skipped/);

const differentLabel = setup(undefined, undefined, 'display label');
differentLabel.articles[0].fields['.selected'].checked = true;
differentLabel.articles[0].fields['.replacement'].value = 'bad';
differentLabel.controls['.batch-reviewed'].checked = true;
differentLabel.controls['.batch'].onclick();
assert.equal(differentLabel.articles[0].radios[1].checked, false);
assert.match(differentLabel.status.textContent, /1 selected occurrences skipped/);
console.log('Review controls passed: strict selectors, assets, proposals, exclusions, acknowledgements, trimmed exports and state restoration.');

const timed = setup();
timed.articles[0].fields['.review-timer'].onclick();
timed.advance(100);
timed.articles[1].fields['.review-timer'].onclick(); // Switch: no overlapping clocks.
timed.advance(200); timed.hide();
timed.advance(10000); timed.show(); // Hidden time never counted.
timed.articles[0].fields['.review-timer'].onclick();
timed.advance(50);
timed.articles[0].radios[0].checked = true;
timed.articles[0].fields['.reviewed'].checked = true; change(timed.articles[0], 'reviewed');
timed.advance(1000); timed.download.onclick();
let measured = JSON.parse(timed.exported()).review_metrics;
assert.equal(measured.active_ms, 350);
assert.equal(measured.tracked_occurrences.length, 2);
assert.equal(measured.tracked_occurrences[0].active_ms, 150);
assert.equal(measured.tracked_occurrences[0].starts, 2);
assert.equal(measured.tracked_occurrences[1].active_ms, 200);
assert.ok(!measured.tracked_occurrences.some(r => r.id === 'unbound'));
const resumed = setup(undefined, undefined, 'bad', JSON.stringify({first: {active_ms: 150, starts: 2}, obsolete: {active_ms: 900, starts: 1}}));
resumed.articles[0].fields['.review-timer'].onclick(); resumed.advance(25); resumed.download.onclick();
measured = JSON.parse(resumed.exported()).review_metrics;
assert.equal(measured.active_ms, 175);
assert.equal(measured.tracked_occurrences.length, 1);
assert.equal(measured.tracked_occurrences[0].starts, 3);
const malformedTiming = setup(undefined, undefined, 'bad', JSON.stringify({first: {active_ms: -1, starts: 1}, excluded: {active_ms: 10, starts: 1.5}}));
malformedTiming.download.onclick();
assert.equal(JSON.parse(malformedTiming.exported()).review_metrics.active_ms, 0);
assert.deepEqual(JSON.parse(malformedTiming.exported()).review_metrics.tracked_occurrences, []);
console.log('Review timers passed: switching, hidden time, acknowledgements, resume, malformed state and untimed items.');
