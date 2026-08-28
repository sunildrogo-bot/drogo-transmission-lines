/* settings.js — Settings page: delete password, activity log, announcements */

function settingsNav(viewId, el) {
  document.querySelectorAll('.settings-nav-item').forEach(t => t.classList.remove('active'));
  const navItem = el || document.querySelector(`.settings-nav-item[data-settings-view="${viewId}"]`);
  if (navItem) navItem.classList.add('active');
  document.querySelectorAll('.settings-view').forEach(v => v.classList.remove('active'));
  const view = document.getElementById(viewId);
  if (!view) return;
  view.classList.add('active');

  if (viewId === 'view-security') loadActivityLog();
  if (viewId === 'view-notifications') {
    loadAnnouncementsTab();
    loadHelpTab();
  }
  if (viewId === 'view-uploads') {
    loadStorageSummary();
    loadAllProjects();
    loadDataHealth();
    loadBackupHistory();
  }
  if (viewId === 'view-inspection') loadInspectionTaxonomy();
}

document.addEventListener('DOMContentLoaded', () => {
  if (document.getElementById('help-tab-badge')) refreshHelpBadge();
  installTaxonomyManager();
  installBackupManager();

  // Cross-page links (the new-ticket dashboard popup, mainly) land here
  // with ?view=help so Notifications opens automatically.
  const params = new URLSearchParams(window.location.search);
  const requested = params.get('view');
  const legacyViews = {
    help: 'view-notifications', announcements: 'view-notifications',
    projects: 'view-uploads', activity: 'view-security',
    password: 'view-security', assistant: 'view-assistant'
  };
  if (legacyViews[requested]) {
    settingsNav(legacyViews[requested]);
  }
  if (document.getElementById('view-uploads')?.classList.contains('active')) {
    loadDataHealth();
    loadBackupHistory();
  }
});

let backupPollTimer = null;

function installBackupManager() {
  const view = document.getElementById('view-uploads');
  if (!view || document.getElementById('backup-upgrade-manager')) return;
  const wrap = document.createElement('div');
  wrap.id = 'backup-upgrade-manager';
  wrap.innerHTML = `<div class="section-label">BACKUP &amp; UPGRADE SAFETY</div>
    <div class="settings-card">
      <div class="settings-card-title">Data health and recovery</div>
      <div class="settings-card-desc">Check that database records and stored files agree, then create a recoverable checkpoint before moving to another application version. Secrets from .env are never included.</div>
      <div class="backup-health-grid" id="backup-health-grid"><div class="backup-message" style="grid-column:1/-1">Run a health check to inspect the current data.</div></div>
      <div id="backup-health-message"></div>
      <div class="backup-actions"><button class="btn-ghost" id="data-health-btn" onclick="loadDataHealth(true)">Check Data Health</button>
        <button class="btn-primary" id="backup-create-btn" onclick="createApplicationBackup(false)">Create Backup</button>
        <button class="btn-ghost" id="backup-upgrade-btn" onclick="createApplicationBackup(true)">Create Pre-Upgrade Backup</button></div>
    </div>
    <div class="section-label">BACKUP HISTORY</div><div id="backup-history" class="backup-list"><div class="backup-message">Loading backup history…</div></div>`;
  view.appendChild(wrap);
}

async function loadDataHealth(forceMessage = false) {
  const grid = document.getElementById('backup-health-grid');
  const message = document.getElementById('backup-health-message');
  const btn = document.getElementById('data-health-btn');
  if (!grid) return;
  if (btn) { btn.disabled = true; btn.textContent = 'Checking…'; }
  try {
    const res = await fetch('/api/settings/data-health');
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Health check failed.');
    grid.innerHTML = `<div class="backup-health-stat"><div class="backup-health-label">Status</div><div class="backup-health-value">${escapeHtml(data.status)}</div><div class="backup-health-note">Revision ${escapeHtml(data.database.migration_revision)}</div></div>
      <div class="backup-health-stat"><div class="backup-health-label">Database</div><div class="backup-health-value">${escapeHtml(data.database.backend)}</div><div class="backup-health-note">${data.records.projects} projects · ${data.records.photos} photos</div></div>
      <div class="backup-health-stat"><div class="backup-health-label">Stored files</div><div class="backup-health-value">${Number(data.files.upload_count).toLocaleString()}</div><div class="backup-health-note">${escapeHtml(data.files.upload_size)}</div></div>
      <div class="backup-health-stat"><div class="backup-health-label">Free space</div><div class="backup-health-value">${escapeHtml(data.disk.free)}</div><div class="backup-health-note">${data.files.missing_count} missing · ${data.files.untracked_count} untracked</div></div>`;
    const notes = [...(data.issues || [])];
    if (!data.database.snapshot_supported) notes.push('This external database requires its own database-server backup tool.');
    message.innerHTML = notes.length
      ? `<div class="backup-message warn">${notes.map(escapeHtml).join('<br>')}</div>`
      : `<div class="backup-message">Database and tracked uploads are consistent. You can create a backup checkpoint.</div>`;
  } catch (e) {
    message.innerHTML = `<div class="backup-message warn">${escapeHtml(e.message)}</div>`;
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Check Data Health'; }
  }
}

async function createApplicationBackup(preUpgrade) {
  const createBtn = document.getElementById('backup-create-btn');
  const upgradeBtn = document.getElementById('backup-upgrade-btn');
  [createBtn, upgradeBtn].forEach(btn => { if (btn) btn.disabled = true; });
  try {
    const res = await fetch('/api/settings/backups', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({pre_upgrade:preUpgrade})});
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Could not start backup.');
    await loadBackupHistory();
  } catch (e) {
    document.getElementById('backup-health-message').innerHTML = `<div class="backup-message warn">${escapeHtml(e.message)}</div>`;
  } finally {
    [createBtn, upgradeBtn].forEach(btn => { if (btn) btn.disabled = false; });
  }
}

async function loadBackupHistory() {
  const box = document.getElementById('backup-history');
  if (!box) return;
  try {
    const res = await fetch('/api/settings/backups');
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Could not load backups.');
    const rows = data.backups || [];
    box.innerHTML = rows.length ? rows.map(job => {
      const terminal = job.status === 'Completed' || job.status === 'Failed';
      const statusClass = job.status === 'Completed' ? 'completed' : (job.status === 'Failed' ? 'failed' : '');
      return `<div class="backup-row"><div><div class="backup-row-title">${escapeHtml(job.backup_type || 'Manual')} backup</div><div class="backup-row-sub">${escapeHtml(job.created_at || '')} · ${escapeHtml(job.created_by || 'Admin')}</div></div>
        <div><span class="backup-status ${statusClass}">${escapeHtml(job.status)}</span>${terminal ? '' : `<div class="backup-progress"><span style="width:${Number(job.progress || 0)}%"></span></div>`}</div>
        <div><div class="backup-row-title">${escapeHtml(job.file_size_label || '—')}</div><div class="backup-row-sub">${job.summary ? `${Number(job.summary.upload_count || 0).toLocaleString()} uploads · revision ${escapeHtml(job.summary.migration_revision || '—')}` : escapeHtml(job.error || '')}</div></div>
        <div class="backup-row-actions">${job.status === 'Completed' ? `<a class="btn-ghost" href="/api/settings/backups/${encodeURIComponent(job.id)}/download">Download</a>` : ''}${terminal ? `<button class="btn-ghost" onclick="deleteApplicationBackup('${escapeHtml(job.id)}')">Delete</button>` : ''}</div></div>`;
    }).join('') : '<div class="backup-message">No backups created yet.</div>';
    const running = rows.some(job => !['Completed','Failed'].includes(job.status));
    clearTimeout(backupPollTimer);
    if (running) backupPollTimer = setTimeout(loadBackupHistory, 2000);
  } catch (e) {
    box.innerHTML = `<div class="backup-message warn">${escapeHtml(e.message)}</div>`;
  }
}

async function deleteApplicationBackup(jobId) {
  try {
    const res = await fetch(`/api/settings/backups/${encodeURIComponent(jobId)}`, {method:'DELETE'});
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Could not delete backup.');
    loadBackupHistory();
  } catch (e) {
    document.getElementById('backup-health-message').innerHTML = `<div class="backup-message warn">${escapeHtml(e.message)}</div>`;
  }
}

let taxonomyComponents = [];

function installTaxonomyManager() {
  const view = document.getElementById('view-inspection');
  if (!view || document.getElementById('taxonomy-manager')) return;
  const wrap = document.createElement('div');
  wrap.id = 'taxonomy-manager';
  wrap.innerHTML = `<div class="section-label">COMPONENTS &amp; DEFECT TYPES</div>
    <div class="settings-card"><div class="settings-card-title">Inspection taxonomy</div>
    <div class="settings-card-desc">Standardize names used by new annotations. Existing inspection records remain unchanged.</div>
    <div class="taxonomy-toolbar"><input class="modal-select taxonomy-search" id="taxonomy-search" placeholder="Search components or defect types…" oninput="filterTaxonomy(this.value)">
      <button class="btn-ghost" onclick="setAllTaxonomyOpen(true)">Expand All</button><button class="btn-ghost" onclick="setAllTaxonomyOpen(false)">Collapse All</button></div>
    <div id="taxonomy-list"><div style="font-size:12px;color:var(--text-muted)">Open this section to load taxonomy.</div></div>
    <div class="error-msg" id="taxonomy-error" style="display:none;margin-top:12px"></div>
    <div class="save-msg" id="taxonomy-success">Taxonomy saved.</div>
    <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:14px">
      <button class="btn-ghost" onclick="addTaxonomyComponent()">Add Component</button>
      <button class="btn-primary" onclick="saveInspectionTaxonomy()">Save Taxonomy</button>
    </div></div>`;
  view.appendChild(wrap);
}

async function loadInspectionTaxonomy() {
  const list = document.getElementById('taxonomy-list');
  if (!list || list.dataset.loaded === '1') return;
  list.innerHTML = '<div style="font-size:12px;color:var(--text-muted)">Loading…</div>';
  try {
    const res = await fetch('/api/settings/inspection-taxonomy');
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Could not load taxonomy.');
    taxonomyComponents = data.components || [];
    if (!taxonomyComponents.length) {
      const grouped = {};
      (data.observed || []).forEach(row => {
        const component = row.component === 'Unspecified' ? 'General' : row.component;
        if (!grouped[component]) grouped[component] = [];
        if (row.defect_type !== 'Unspecified' && !grouped[component].includes(row.defect_type)) grouped[component].push(row.defect_type);
      });
      taxonomyComponents = Object.keys(grouped).sort().map(name => ({
        name, active:true, supports_rgb:true, supports_thermal:false, severity_required:true,
        defect_types: grouped[name].sort().map(type => ({name:type, report_name:type, training_class:type, aliases:[], severities:['Minor','Major','Critical'], active:true}))
      }));
    }
    list.dataset.loaded = '1';
    renderTaxonomyManager();
  } catch (e) {
    list.innerHTML = `<div class="error-msg">${escapeHtml(e.message)}</div>`;
  }
}

function renderTaxonomyManager() {
  const list = document.getElementById('taxonomy-list');
  if (!list) return;
  if (!taxonomyComponents.length) {
    list.innerHTML = '<div style="font-size:12px;color:var(--text-muted)">No components configured. Add the first component.</div>';
    return;
  }
  list.className = 'taxonomy-grid';
  list.innerHTML = taxonomyComponents.map((component, ci) => `
    <div class="taxonomy-component" data-taxonomy-index="${ci}" data-taxonomy-search="${escapeHtml(`${component.name || ''} ${(component.defect_types || []).map(type => type.name || '').join(' ')}`.toLowerCase())}">
      <button type="button" class="taxonomy-component-head" onclick="toggleTaxonomyComponent(${ci})"><span class="taxonomy-chevron">›</span><span class="taxonomy-component-name">${escapeHtml(component.name || 'New component')}</span><span class="taxonomy-count">${(component.defect_types || []).length} defect type${(component.defect_types || []).length === 1 ? '' : 's'}</span></button>
      <div class="taxonomy-component-body"><div class="taxonomy-component-fields">
        <div class="field"><label>Component</label><input value="${escapeHtml(component.name || '')}" onchange="taxonomyComponents[${ci}].name=this.value"></div>
        <label style="font-size:12px;color:var(--text-secondary);padding-bottom:10px"><input type="checkbox" ${component.active !== false ? 'checked' : ''} onchange="taxonomyComponents[${ci}].active=this.checked"> Active</label>
      </div>
      <div style="margin:10px 0 6px;font-size:10px;font-weight:700;color:var(--text-muted);letter-spacing:.08em">DEFECT TYPES</div>
      ${(component.defect_types || []).map((type, ti) => `<div class="taxonomy-defect-row">
        <input class="modal-select" value="${escapeHtml(type.name || '')}" aria-label="Defect type" onchange="taxonomyComponents[${ci}].defect_types[${ti}].name=this.value">
        <input class="modal-select" value="${escapeHtml(type.training_class || type.name || '')}" aria-label="Training class" placeholder="Training class" onchange="taxonomyComponents[${ci}].defect_types[${ti}].training_class=this.value">
        <label style="font-size:11px"><input type="checkbox" ${type.active !== false ? 'checked' : ''} onchange="taxonomyComponents[${ci}].defect_types[${ti}].active=this.checked"> Active</label>
      </div>`).join('') || '<div class="taxonomy-empty">No defect types configured.</div>'}
      <button class="btn-ghost" style="margin-top:6px" onclick="addTaxonomyDefect(${ci})">Add Defect Type</button>
    </div></div>`).join('');
}

function toggleTaxonomyComponent(componentIndex) {
  document.querySelector(`.taxonomy-component[data-taxonomy-index="${componentIndex}"]`)?.classList.toggle('open');
}

function setAllTaxonomyOpen(open) {
  document.querySelectorAll('.taxonomy-component:not(.hidden)').forEach(card => card.classList.toggle('open', open));
}

function filterTaxonomy(value) {
  const query = (value || '').trim().toLowerCase();
  document.querySelectorAll('.taxonomy-component').forEach(card => {
    const matches = !query || (card.dataset.taxonomySearch || '').includes(query);
    card.classList.toggle('hidden', !matches);
    if (query && matches) card.classList.add('open');
  });
}

function addTaxonomyComponent() {
  taxonomyComponents.push({name:'', active:true, supports_rgb:true, supports_thermal:false, severity_required:true, defect_types:[]});
  renderTaxonomyManager();
  toggleTaxonomyComponent(taxonomyComponents.length - 1);
}

function addTaxonomyDefect(componentIndex) {
  taxonomyComponents[componentIndex].defect_types.push({name:'', report_name:'', training_class:'', aliases:[], severities:['Minor','Major','Critical'], active:true});
  renderTaxonomyManager();
  toggleTaxonomyComponent(componentIndex);
}

async function saveInspectionTaxonomy() {
  const error = document.getElementById('taxonomy-error');
  const success = document.getElementById('taxonomy-success');
  error.style.display = 'none'; success.style.display = 'none';
  try {
    const res = await fetch('/api/settings/inspection-taxonomy', {
      method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({components:taxonomyComponents})
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Could not save taxonomy.');
    success.style.display = 'block';
    document.getElementById('taxonomy-list').dataset.loaded = '0';
    await loadInspectionTaxonomy();
  } catch (e) {
    error.textContent = e.message; error.style.display = 'block';
  }
}

async function loadStorageSummary() {
  const total = document.getElementById('storage-total');
  const files = document.getElementById('storage-files');
  const mode = document.getElementById('storage-mode');
  if (!total || !files || !mode) return;
  try {
    const res = await fetch('/api/settings/storage-summary');
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Storage summary failed');
    total.textContent = data.formatted_size || '0 B';
    files.textContent = Number(data.file_count || 0).toLocaleString();
    mode.textContent = data.storage_type || 'Application uploads folder';
  } catch (e) {
    total.textContent = 'Unavailable';
    files.textContent = '—';
  }
}

function escapeHtml(str) {
  const d = document.createElement('div');
  d.textContent = str == null ? '' : String(str);
  return d.innerHTML;
}

async function saveDeletePassword() {
  const currentInput = document.getElementById('dp-current');
  const newInput = document.getElementById('dp-new');
  const errBox = document.getElementById('dp-error');
  const successMsg = document.getElementById('dp-success');
  const btn = document.getElementById('dp-save-btn');

  errBox.style.display = 'none';
  successMsg.style.display = 'none';

  const newPassword = newInput.value;
  if (!newPassword || newPassword.length < 4) {
    errBox.textContent = 'New password must be at least 4 characters.';
    errBox.style.display = 'block';
    return;
  }

  btn.disabled = true; btn.textContent = 'Saving…';
  try {
    const res = await fetch('/api/settings/delete-password', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        current_password: currentInput ? currentInput.value : '',
        new_password: newPassword
      })
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      errBox.textContent = data.error || 'Could not save password.';
      errBox.style.display = 'block';
      return;
    }
    successMsg.style.display = 'block';
    newInput.value = '';
    if (currentInput) currentInput.value = '';
    setTimeout(() => window.location.reload(), 900);
  } catch (e) {
    errBox.textContent = 'Network error — please try again.';
    errBox.style.display = 'block';
  } finally {
    btn.disabled = false;
    btn.textContent = currentInput ? 'Change Password' : 'Set Password';
  }
}

async function loadActivityLog() {
  const body = document.getElementById('activity-log-body');
  try {
    const res = await fetch('/api/settings/activity-log');
    const data = await res.json();
    const entries = data.entries || [];
    if (!entries.length) {
      body.innerHTML = `<tr class="empty-row"><td colspan="7">No activity recorded yet.</td></tr>`;
      return;
    }
    body.innerHTML = entries.map(e => `
      <tr>
        <td><span class="action-chip ${escapeHtml(e.action)}">${escapeHtml(e.action)}</span></td>
        <td>${escapeHtml(e.entity_name || '—')}</td>
        <td>${escapeHtml(e.module || '—')}</td>
        <td>${escapeHtml(e.performed_by || '—')}</td>
        <td>${escapeHtml(e.role || '—')}</td>
        <td>${escapeHtml(e.duration || '—')}</td>
        <td title="${escapeHtml(e.created_at)}">${timeAgo(e.created_at_iso)}</td>
      </tr>`).join('');
  } catch (e) {
    body.innerHTML = `<tr class="empty-row"><td colspan="7">Could not load activity log.</td></tr>`;
  }
}

function timeAgo(iso) {
  if (!iso) return '—';
  const then = new Date(iso).getTime();
  if (isNaN(then)) return '—';
  const secs = Math.max(0, Math.floor((Date.now() - then) / 1000));
  if (secs < 60) return 'Just now';
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  if (days < 30) return `${days}d ago`;
  const months = Math.floor(days / 30);
  if (months < 12) return `${months}mo ago`;
  return `${Math.floor(months / 12)}y ago`;
}

async function submitAnnouncement() {
  const titleInput = document.getElementById('ann-title');
  const messageInput = document.getElementById('ann-message');
  const imageInput = document.getElementById('ann-image');
  const errBox = document.getElementById('ann-error');
  const successMsg = document.getElementById('ann-success');
  const btn = document.getElementById('ann-submit-btn');

  errBox.style.display = 'none';
  successMsg.style.display = 'none';

  const title = titleInput.value.trim();
  if (!title) {
    errBox.textContent = 'Please give the announcement a title.';
    errBox.style.display = 'block';
    return;
  }

  const fd = new FormData();
  fd.append('title', title);
  fd.append('message', messageInput.value.trim());
  if (imageInput.files[0]) fd.append('image', imageInput.files[0]);

  btn.disabled = true; btn.textContent = 'Posting…';
  try {
    const res = await fetch('/api/announcements', { method: 'POST', body: fd });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      errBox.textContent = data.error || 'Could not post — please try again.';
      errBox.style.display = 'block';
      return;
    }
    successMsg.style.display = 'block';
    titleInput.value = '';
    messageInput.value = '';
    imageInput.value = '';
    loadAnnouncementsTab();
  } catch (e) {
    errBox.textContent = 'Network error — please try again.';
    errBox.style.display = 'block';
  } finally {
    btn.disabled = false; btn.textContent = 'Post Announcement';
  }
}

async function loadAnnouncementsTab() {
  const list = document.getElementById('ann-list');
  if (!list) return;
  try {
    const res = await fetch('/api/announcements');
    const data = await res.json();
    const items = data.announcements || [];
    if (!items.length) {
      list.innerHTML = `<div style="padding:20px;text-align:center;color:var(--text-muted);font-size:12.5px;">Nothing posted yet.</div>`;
      return;
    }
    list.innerHTML = items.map(a => `
      <div class="ann-card">
        ${a.image_url ? `<img src="${escapeHtml(a.image_url)}" alt=""/>` : ''}
        <div class="ann-card-body">
          <div class="ann-card-top">
            <span class="ann-title">${escapeHtml(a.title)}</span>
            <button class="ann-delete-btn" onclick="deleteAnnouncement(${a.id})">Delete</button>
          </div>
          <div class="ann-meta">${escapeHtml(a.created_by)} · ${escapeHtml(a.created_at)}</div>
          ${a.message ? `<div class="ann-message">${escapeHtml(a.message)}</div>` : ''}
        </div>
      </div>
    `).join('');
  } catch (e) {
    list.innerHTML = `<div style="padding:20px;text-align:center;color:var(--text-muted);font-size:12.5px;">Could not load announcements.</div>`;
  }
}

async function deleteAnnouncement(id) {
  if (!confirm('Delete this announcement?')) return;
  try {
    await fetch(`/api/announcements/${id}`, { method: 'DELETE' });
    loadAnnouncementsTab();
  } catch (e) { /* ignore */ }
}

function _tkPillClass(status) {
  return status === 'Resolved' ? 'st-resolved' : status === 'Checking' ? 'st-checking' : 'st-open';
}

async function refreshHelpBadge() {
  const badge = document.getElementById('help-tab-badge');
  if (!badge) return;
  try {
    const res = await fetch('/api/help-tickets');
    const data = await res.json();
    const pending = (data.tickets || []).filter(t => t.status !== 'Resolved').length;
    badge.textContent = pending;
    badge.style.display = pending > 0 ? 'inline-flex' : 'none';
  } catch (e) { /* non-critical */ }
}

async function loadHelpTab() {
  const list = document.getElementById('help-tab-list');
  if (!list) return;
  try {
    const res = await fetch('/api/help-tickets');
    const data = await res.json();
    const tickets = data.tickets || [];
    refreshHelpBadge();
    if (!tickets.length) {
      list.innerHTML = `<div style="padding:30px;text-align:center;color:var(--text-muted);font-size:12.5px;">No help requests yet.</div>`;
      return;
    }
    list.innerHTML = tickets.map(t => `
      <div class="tk-row">
        <div class="tk-row-top">
          <span class="tk-subject">${escapeHtml(t.subject)}</span>
          <span class="tk-pill ${_tkPillClass(t.status)}">${escapeHtml(t.status)}</span>
        </div>
        <div class="tk-meta">${escapeHtml(t.reporter_type)} — ${escapeHtml(t.submitted_by)} · ${escapeHtml(t.created_at)}</div>
        ${t.description ? `<div class="tk-desc">${escapeHtml(t.description)}</div>` : ''}
        ${t.status !== 'Resolved' ? `
        <div class="tk-actions">
          ${t.status !== 'Checking' ? `<button class="tk-action-btn" onclick="setTicketStatus(${t.id}, 'Checking')">Checking</button>` : ''}
          <button class="tk-action-btn primary" onclick="setTicketStatus(${t.id}, 'Resolved')">Problem Resolved</button>
        </div>` : ''}
      </div>
    `).join('');
  } catch (e) {
    list.innerHTML = `<div style="padding:30px;text-align:center;color:var(--text-muted);font-size:12.5px;">Could not load help requests.</div>`;
  }
}

async function setTicketStatus(id, status) {
  try {
    await fetch(`/api/help-tickets/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status }),
    });
    loadHelpTab();
  } catch (e) { /* ignore */ }
}

async function loadAllProjects() {
  const body = document.getElementById('all-projects-body');
  if (!body) return;
  try {
    const res = await fetch('/api/settings/all-projects');
    const data = await res.json();
    const projects = data.projects || [];
    if (!projects.length) {
      body.innerHTML = `<tr class="empty-row"><td colspan="5">No projects yet.</td></tr>`;
      return;
    }
    body.innerHTML = projects.map(p => `
      <tr>
        <td>${escapeHtml(p.module)}</td>
        <td>${p.open_url ? `<a href="${p.open_url}" style="color:var(--accent);font-weight:600;text-decoration:none;">${escapeHtml(p.name)}</a>` : escapeHtml(p.name)}</td>
        <td>${escapeHtml(p.detail)}</td>
        <td>${escapeHtml(p.created_at)}</td>
        <td style="text-align:right;">
          <button type="button" class="browse-btn" onclick="openTowerBrowser(${p.id}, '${escapeHtml(p.name).replace(/'/g, "\\'")}')">Browse</button>
          <button type="button" class="ann-delete-btn" onclick="requestDelete({url:'${p.delete_url}', label:'${escapeHtml(p.name).replace(/'/g, "\\'")}', onSuccess: loadAllProjects})">Delete</button>
        </td>
      </tr>`).join('');
  } catch (e) {
    body.innerHTML = `<tr class="empty-row"><td colspan="5">Could not load projects.</td></tr>`;
  }
}

// ── Project drill-down browser: project -> divisions -> lines -> towers ──
let _tb = { level: 'divisions', projectId: null, projectName: '', divisionId: null, divisionName: '', lineId: null, lineName: '' };

function openTowerBrowser(projectId, projectName) {
  _tb = { level: 'divisions', projectId, projectName, divisionId: null, divisionName: '', lineId: null, lineName: '' };
  document.getElementById('tb-modal-backdrop').classList.add('open');
  tbLoadDivisions();
}

function closeTowerBrowser() {
  document.getElementById('tb-modal-backdrop').classList.remove('open');
}

function tbRenderBreadcrumb() {
  const parts = [`<a onclick="tbLoadDivisions()">${escapeHtml(_tb.projectName)}</a>`];
  if (_tb.level === 'lines' || _tb.level === 'towers') {
    parts.push('›', _tb.level === 'lines'
      ? `<span>${escapeHtml(_tb.divisionName)}</span>`
      : `<a onclick="tbLoadLines(${_tb.divisionId}, '${escapeHtml(_tb.divisionName).replace(/'/g, "\\'")}')">${escapeHtml(_tb.divisionName)}</a>`);
  }
  if (_tb.level === 'towers') {
    parts.push('›', `<span>${escapeHtml(_tb.lineName)}</span>`);
  }
  document.getElementById('tb-breadcrumb').innerHTML = parts.join(' ');
}

async function tbLoadDivisions() {
  _tb.level = 'divisions';
  tbRenderBreadcrumb();
  const body = document.getElementById('tb-modal-body');
  body.innerHTML = `<div class="tb-empty">Loading…</div>`;
  try {
    const res = await fetch(`/api/settings/projects/${_tb.projectId}/divisions`);
    const data = await res.json();
    const divisions = data.divisions || [];
    if (!divisions.length) { body.innerHTML = `<div class="tb-empty">No divisions in this project.</div>`; return; }
    body.innerHTML = divisions.map(d => `
      <div class="tb-row">
        <div class="tb-row-main" onclick="tbLoadLines(${d.id}, '${escapeHtml(d.name).replace(/'/g, "\\'")}')">
          <div class="tb-row-name">${escapeHtml(d.name)}</div>
          <div class="tb-row-sub">${d.line_count} line${d.line_count !== 1 ? 's' : ''}</div>
        </div>
        <span class="tb-row-arrow">›</span>
      </div>`).join('');
  } catch (e) {
    body.innerHTML = `<div class="tb-empty">Could not load divisions.</div>`;
  }
}

async function tbLoadLines(divisionId, divisionName) {
  _tb.level = 'lines';
  _tb.divisionId = divisionId;
  _tb.divisionName = divisionName;
  tbRenderBreadcrumb();
  const body = document.getElementById('tb-modal-body');
  body.innerHTML = `<div class="tb-empty">Loading…</div>`;
  try {
    const res = await fetch(`/api/settings/divisions/${divisionId}/lines`);
    const data = await res.json();
    const lines = data.lines || [];
    if (!lines.length) { body.innerHTML = `<div class="tb-empty">No lines in this division.</div>`; return; }
    body.innerHTML = lines.map(l => `
      <div class="tb-row">
        <div class="tb-row-main" onclick="tbLoadTowers(${l.id}, '${escapeHtml(l.name).replace(/'/g, "\\'")}')">
          <div class="tb-row-name">${escapeHtml(l.name)}</div>
          <div class="tb-row-sub">${l.tower_count} tower${l.tower_count !== 1 ? 's' : ''} planned</div>
        </div>
        <span class="tb-row-arrow">›</span>
      </div>`).join('');
  } catch (e) {
    body.innerHTML = `<div class="tb-empty">Could not load lines.</div>`;
  }
}

async function tbLoadTowers(lineId, lineName) {
  _tb.level = 'towers';
  _tb.lineId = lineId;
  _tb.lineName = lineName;
  tbRenderBreadcrumb();
  const body = document.getElementById('tb-modal-body');
  body.innerHTML = `<div class="tb-empty">Loading…</div>`;
  try {
    const res = await fetch(`/api/settings/lines/${lineId}/towers`);
    const data = await res.json();
    const towers = data.towers || [];
    if (!towers.length) { body.innerHTML = `<div class="tb-empty">No photos uploaded on this line yet — nothing to delete.</div>`; return; }
    body.innerHTML = towers.map(t => `
      <div class="tb-row">
        <div class="tb-row-main" style="cursor:default;">
          <div class="tb-row-name">Tower ${escapeHtml(t.label)}</div>
          <div class="tb-row-sub">${t.photo_count} photo${t.photo_count !== 1 ? 's' : ''} · ${t.defect_count} defect${t.defect_count !== 1 ? 's' : ''}</div>
        </div>
        <button type="button" class="tb-delete-btn" onclick="requestDelete({url:'/api/settings/lines/${lineId}/towers/${encodeURIComponent(t.label)}', label:'Tower ${escapeHtml(t.label).replace(/'/g, "\\'")} (all photos and defects)', onSuccess: () => tbLoadTowers(${lineId}, '${escapeHtml(lineName).replace(/'/g, "\\'")}')})">Delete</button>
      </div>`).join('');
  } catch (e) {
    body.innerHTML = `<div class="tb-empty">Could not load towers.</div>`;
  }
}
