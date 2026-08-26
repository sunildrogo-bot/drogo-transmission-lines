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
  }
}

document.addEventListener('DOMContentLoaded', () => {
  if (document.getElementById('help-tab-badge')) refreshHelpBadge();

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
});

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
