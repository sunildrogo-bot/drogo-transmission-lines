let datasetProjects = [];
let previewValid = false;
let pollTimer = null;

function dsEscape(value) {
  const node = document.createElement('div');
  node.textContent = value == null ? '' : String(value);
  return node.innerHTML;
}

function showDatasetError(message) {
  const box = document.getElementById('ds-error');
  box.textContent = message || '';
  box.style.display = message ? 'block' : 'none';
}

function showClassError(message) {
  const box = document.getElementById('ds-class-error');
  box.textContent = message || '';
  box.style.display = message ? 'block' : 'none';
}

async function loadDatasetClasses() {
  const target = document.getElementById('ds-class-manager');
  try {
    const response = await fetch('/api/training-datasets/classes');
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Could not load classes.');
    const classes = data.classes || [];
    target.innerHTML = '';
    const header = document.createElement('div');
    header.className = 'class-head';
    header.innerHTML = '<span>Observed defect type</span><span>Training class</span><span>Use</span>';
    target.appendChild(header);
    classes.forEach(item => {
      const row = document.createElement('div');
      row.className = 'class-map-row';
      row.dataset.source = item.source;
      const source = document.createElement('div');
      source.className = 'class-source';
      source.textContent = item.source;
      const count = document.createElement('small');
      count.textContent = `${item.annotation_count} annotation${item.annotation_count === 1 ? '' : 's'}`;
      source.appendChild(count);
      const input = document.createElement('input');
      input.className = 'class-target';
      input.value = item.target;
      input.maxLength = 100;
      input.disabled = !item.enabled;
      const enabledWrap = document.createElement('label');
      enabledWrap.className = 'class-enabled';
      const enabled = document.createElement('input');
      enabled.type = 'checkbox';
      enabled.className = 'class-enabled-input';
      enabled.checked = item.enabled;
      enabled.setAttribute('aria-label', `Use ${item.source}`);
      enabled.addEventListener('change', () => { input.disabled = !enabled.checked; invalidatePreview(); });
      input.addEventListener('input', invalidatePreview);
      enabledWrap.appendChild(enabled);
      row.append(source, input, enabledWrap);
      target.appendChild(row);
    });
    if (!classes.length) target.innerHTML = '<div class="preview-empty" style="padding:22px">No defect types have been saved yet.</div>';
  } catch (error) {
    target.innerHTML = `<div class="preview-empty" style="padding:22px">${dsEscape(error.message)}</div>`;
  }
}

async function saveDatasetClasses() {
  showClassError('');
  const button = document.getElementById('ds-save-classes');
  const classes = [...document.querySelectorAll('.class-map-row')].map(row => ({
    source: row.dataset.source,
    target: row.querySelector('.class-target').value.trim(),
    enabled: row.querySelector('.class-enabled-input').checked,
  }));
  button.disabled = true; button.textContent = 'Saving…';
  try {
    const response = await fetch('/api/training-datasets/classes', {method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify({classes})});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Could not save class mapping.');
    button.textContent = 'Saved';
    invalidatePreview();
    setTimeout(() => { button.textContent = 'Save Class Mapping'; }, 900);
  } catch (error) {
    showClassError(error.message);
    button.textContent = 'Save Class Mapping';
  } finally {
    button.disabled = false;
  }
}

function datasetPayload() {
  return {
    project_id: document.getElementById('ds-project').value,
    line_ids: [...document.querySelectorAll('.ds-line:checked')].map(input => Number(input.value)),
    format: document.getElementById('ds-format').value,
    inspection_done_only: document.getElementById('ds-done-only').checked,
    include_negatives: document.getElementById('ds-negatives').checked,
    negative_ratio: Number(document.getElementById('ds-negative-ratio').value),
    labels_only: document.getElementById('ds-labels-only').checked,
    split_seed: 42,
  };
}

function invalidatePreview() {
  previewValid = false;
  document.getElementById('ds-generate-btn').disabled = true;
}

async function loadDatasetOptions() {
  const select = document.getElementById('ds-project');
  try {
    const response = await fetch('/api/training-datasets/options');
    const data = await response.json();
    datasetProjects = data.projects || [];
    select.innerHTML = '<option value="">Select project</option>' + datasetProjects.map(project =>
      `<option value="${project.id}">${dsEscape(project.name)} · ${dsEscape(project.module)}</option>`
    ).join('');
  } catch (_) {
    select.innerHTML = '<option value="">Could not load projects</option>';
  }
}

function renderDatasetLines() {
  const projectId = Number(document.getElementById('ds-project').value);
  const project = datasetProjects.find(row => row.id === projectId);
  const target = document.getElementById('ds-lines');
  invalidatePreview();
  if (!project) {
    target.innerHTML = '<div class="preview-empty" style="padding:18px">Select a project</div>';
    return;
  }
  const divisions = project.divisions || [];
  target.innerHTML = divisions.map(division => {
    const lines = division.lines || [];
    return `<div><label class="ds-check group"><input type="checkbox" class="ds-division" checked> ${dsEscape(division.name)}</label>${lines.map(line =>
      `<label class="ds-check" style="padding-left:22px"><input type="checkbox" class="ds-line" value="${line.id}" checked> ${dsEscape(line.name)}</label>`
    ).join('')}</div>`;
  }).join('') || '<div class="preview-empty" style="padding:18px">No lines in this project</div>';
  target.querySelectorAll('.ds-division').forEach(group => group.addEventListener('change', () => {
    group.closest('div').querySelectorAll('.ds-line').forEach(line => { line.checked = group.checked; });
    invalidatePreview();
  }));
  target.querySelectorAll('.ds-line').forEach(line => line.addEventListener('change', invalidatePreview));
}

function renderPreview(data) {
  const classes = Object.entries(data.classes || {}).sort((a, b) => a[0].localeCompare(b[0]));
  document.getElementById('ds-preview').innerHTML = `
    <div class="preview-grid">
      <div class="preview-stat"><div class="preview-label">Images</div><div class="preview-value">${data.image_count}</div></div>
      <div class="preview-stat"><div class="preview-label">Annotations</div><div class="preview-value">${data.annotation_count}</div></div>
      <div class="preview-stat"><div class="preview-label">Classes</div><div class="preview-value">${data.class_count}</div></div>
      <div class="preview-stat"><div class="preview-label">Negatives</div><div class="preview-value">${data.negative_images}</div></div>
    </div>
    <div class="class-list">${classes.map(([name, count]) => `<div class="class-row"><span>${dsEscape(name)}</span><b>${count}</b></div>`).join('')}</div>
    <div class="ds-note">Split: ${data.splits.train} train · ${data.splits.val} validation · ${data.splits.test} test</div>
    ${(data.warnings || []).map(warning => `<div class="warning">⚠ ${dsEscape(warning)}</div>`).join('')}`;
}

async function previewDataset() {
  showDatasetError('');
  const button = document.getElementById('ds-preview-btn');
  button.disabled = true; button.textContent = 'Validating…';
  try {
    const response = await fetch('/api/training-datasets/preview', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(datasetPayload())});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Could not validate dataset.');
    renderPreview(data);
    previewValid = true;
    document.getElementById('ds-generate-btn').disabled = false;
  } catch (error) {
    invalidatePreview();
    showDatasetError(error.message);
  } finally {
    button.disabled = false; button.textContent = 'Validate & Preview';
  }
}

async function generateDataset() {
  if (!previewValid) return;
  showDatasetError('');
  const button = document.getElementById('ds-generate-btn');
  button.disabled = true; button.textContent = 'Starting…';
  try {
    const response = await fetch('/api/training-datasets/exports', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(datasetPayload())});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Could not start export.');
    await loadDatasetJobs();
  } catch (error) {
    showDatasetError(error.message);
  } finally {
    button.textContent = 'Generate Dataset ZIP';
    button.disabled = !previewValid;
  }
}

function formatBytes(value) {
  let size = Number(value || 0); const units = ['B','KB','MB','GB','TB']; let index = 0;
  while (size >= 1024 && index < units.length - 1) { size /= 1024; index += 1; }
  return `${size.toFixed(index ? 2 : 0)} ${units[index]}`;
}

async function loadDatasetJobs() {
  const target = document.getElementById('ds-jobs');
  try {
    const response = await fetch('/api/training-datasets/exports');
    const data = await response.json();
    const jobs = data.exports || [];
    if (!jobs.length) { target.innerHTML = '<div class="preview-empty">No exports yet.</div>'; return; }
    target.innerHTML = jobs.map(job => `<div class="job"><div class="job-top"><div><div class="job-name">${dsEscape(job.summary?.project || 'Training dataset')}</div><div class="job-meta">${dsEscape(job.selection?.format || '')} · ${dsEscape(job.created_by || '')}</div></div><span class="job-state ${dsEscape(job.status)}">${dsEscape(job.status)}</span></div>${!['Completed','Failed'].includes(job.status) ? `<div class="progress"><span style="width:${Number(job.progress || 0)}%"></span></div>` : ''}${job.status === 'Completed' ? `<div class="job-actions"><a class="btn-secondary" href="/api/training-datasets/exports/${job.id}/download">Download · ${formatBytes(job.file_size)}</a> <button class="btn-ghost" onclick="deleteDatasetExport('${job.id}')">Delete</button></div>` : ''}${job.status === 'Failed' ? `<div class="job-actions"><button class="btn-ghost" onclick="deleteDatasetExport('${job.id}')">Remove</button></div>` : ''}${job.error ? `<div class="job-error">${dsEscape(job.error)}</div>` : ''}</div>`).join('');
    const active = jobs.some(job => !['Completed','Failed'].includes(job.status));
    if (active && !pollTimer) pollTimer = setInterval(loadDatasetJobs, 2000);
    if (!active && pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  } catch (_) { target.innerHTML = '<div class="preview-empty">Could not load exports.</div>'; }
}

async function deleteDatasetExport(jobId) {
  if (!confirm('Delete this generated dataset package and its export record?')) return;
  const response = await fetch(`/api/training-datasets/exports/${jobId}`, {method:'DELETE'});
  const data = await response.json().catch(() => ({}));
  if (!response.ok) { showDatasetError(data.error || 'Could not delete export.'); return; }
  loadDatasetJobs();
}

document.addEventListener('DOMContentLoaded', () => {
  loadDatasetClasses(); loadDatasetOptions(); loadDatasetJobs();
  document.getElementById('ds-project').addEventListener('change', renderDatasetLines);
  ['ds-format','ds-negative-ratio','ds-done-only','ds-negatives','ds-labels-only'].forEach(id => document.getElementById(id).addEventListener('change', invalidatePreview));
});
