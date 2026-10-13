const payload = JSON.parse(document.getElementById('review-data').textContent);
const records = new Map(payload.items.map(r => [r.id, r]));
const key = 'docomotive-proof-v2-' + payload.source_sha256 + '-' + payload.model_sha256 + '-' + payload.profile_sha256;
const status = document.getElementById('storage-status');
let decisions = {};
try {
  const restored = JSON.parse(localStorage.getItem(key) || '{}');
  if (restored && typeof restored === 'object' && !Array.isArray(restored)) decisions = restored;
} catch (e) { status.textContent = 'Stored decisions could not be restored; review the occurrences again before exporting.'; }
function persist() {
  try { localStorage.setItem(key, JSON.stringify(decisions)); }
  catch (e) { status.textContent = 'Browser storage unavailable; export before closing.'; }
}
function hasChangedReplacement(value, binding) {
  return Boolean(binding && typeof value === 'string' && value.trim() && value.trim() !== binding.before);
}
function resetAcknowledgement(group) {
  group.querySelector('.batch-reviewed').checked = false;
}
function save(article) {
  const choice = article.querySelector('input[type=radio]:checked');
  decisions[article.id] = {decision: choice ? choice.value : null,
    replacement_or_note: article.querySelector('.replacement').value,
    reviewed: article.querySelector('.reviewed').checked};
  persist();
}
for (const article of document.querySelectorAll('article')) {
  const r = records.get(article.id), d = decisions[article.id] || {};
  article.querySelector('.replacement').value = d.replacement_or_note || '';
  article.querySelector('.reviewed').checked = d.reviewed === true;
  for (const radio of article.querySelectorAll('input[type=radio]')) radio.checked = radio.value === d.decision;
  article.addEventListener('change', e => {
    if (e.target.classList.contains('proposal')) {
      article.querySelector('.replacement').value = e.target.value;
    }
    // Changing a proposal or replacement invalidates the acknowledgement.
    if (e.target.classList.contains('proposal') || e.target.classList.contains('replacement')) article.querySelector('.reviewed').checked = false;
    if (!e.target.classList.contains('selected')) save(article);
  });
  for (const detail of article.querySelectorAll('.proposal-evidence')) {
    detail.addEventListener('toggle', () => {
      if (detail.open) detail.querySelector('pre').textContent = JSON.stringify(payload.candidate_evidence[r.candidate_signals_key], null, 2);
    });
  }
  for (const img of article.querySelectorAll('img[data-image]')) img.src = payload.images[img.dataset.image];
}
for (const group of document.querySelectorAll('.group')) {
  group.addEventListener('change', e => { if (!e.target.classList.contains('batch-reviewed')) resetAcknowledgement(group); });
  group.querySelector('.select-all').onclick = () => { resetAcknowledgement(group); for (const c of group.querySelectorAll('.selected:not(:disabled)')) c.checked = true; };
  group.querySelector('.clear').onclick = () => { resetAcknowledgement(group); for (const c of group.querySelectorAll('.selected')) c.checked = false; };
  group.querySelector('.batch').onclick = () => {
    if (!group.querySelector('.batch-reviewed').checked) { status.textContent = 'Check every selected crop and acknowledge it before applying a group decision.'; return; }
    let count = 0, skipped = 0;
    for (const a of group.querySelectorAll('article')) {
      if (!a.querySelector('.selected').checked) continue;
      const r = records.get(a.id);
      if (!r.binding) { skipped++; continue; }
      const input = a.querySelector('.replacement');
      if (!input.value) input.value = r.proposal;
      if (!hasChangedReplacement(input.value, r.binding)) { skipped++; continue; }
      a.querySelector('input[value="ocr-error"]').checked = true;
      a.querySelector('.reviewed').checked = true;
      save(a); count++;
    }
    resetAcknowledgement(group);
    status.textContent = count + ' selected occurrences marked; ' + skipped + ' selected occurrences skipped (supply a changed replacement and an exact binding). Excluded occurrences retain their decisions.';
  };
}
document.getElementById('download').onclick = () => {
  const selected = payload.items.filter(r => decisions[r.id] && decisions[r.id].decision).map(r => ({...r, candidate_signals: (payload.candidate_evidence || {})[r.candidate_signals_key], ...decisions[r.id]}));
  const invalid = selected.filter(r => ['ocr-error','printed-typo'].includes(r.decision) && (!r.reviewed || !hasChangedReplacement(r.replacement_or_note, r.binding)));
  if (invalid.length) { status.textContent = invalid.length + ' corrections need a changed replacement, an exact row binding and a checked-source acknowledgement before export.'; return; }
  const output = {format_version: 2, source_sha256: payload.source_sha256, model_sha256: payload.model_sha256, profile_sha256: payload.profile_sha256, mode: 'review-only; not automatically applied', decisions: selected};
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([JSON.stringify(output, null, 2)], {type: 'application/json'}));
  a.download = 'proofreading-decisions.json'; a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  status.textContent = selected.length + ' occurrence decisions exported.';
};
