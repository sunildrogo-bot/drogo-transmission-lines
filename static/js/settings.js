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
    loadThumbnailRepair();
    loadDuplicatePhotos();
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
  installDuplicatePhotoManager();
  installCompatibilityManager();
  installActivityControls();
  installProductionHealthManager();

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
let duplicatePhotoPollTimer = null;

function createStorageTool(id, title, copy, glyph, order, wide = false) {
  const grid = document.getElementById('storage-tool-grid');
  if (!grid) return null;
  const details = document.createElement('details');
  details.id = id;
  details.className = `storage-tool-card${wide ? ' storage-tool-wide' : ''}`;
  details.style.order = String(order || 0);
  details.innerHTML = `<summary class="storage-tool-summary">
    <span class="storage-tool-icon" aria-hidden="true">${glyph}</span>
    <span><span class="storage-tool-title">${escapeHtml(title)}</span><span class="storage-tool-copy">${escapeHtml(copy)}</span></span>
    <span class="storage-tool-chevron" aria-hidden="true">›</span>
  </summary><div class="storage-tool-body"></div>`;
  grid.appendChild(details);
  return details.querySelector('.storage-tool-body');
}

function installDuplicatePhotoManager() {
  const view = document.getElementById('view-uploads');
  if (!view || document.getElementById('duplicate-photo-manager')) return;
  const wrap = createStorageTool('duplicate-photo-manager', 'Duplicate images',
    'Find exact re-uploads, protect inspection evidence, and remove only safe copies.', '≡', 15, true);
  if (!wrap) return;
  wrap.innerHTML = `<div class="settings-card">
    <div class="settings-card-title">Exact duplicate image control</div>
    <div class="settings-card-desc">The scan compares original file fingerprints, not filenames. It does not delete anything. Cleanup keeps the canonical image and never auto-removes conflicting tower assignments or multiple copies containing review, defect, thermal, or audit evidence.</div>
    <div class="backup-health-grid" id="duplicate-photo-summary"><div class="backup-message" style="grid-column:1/-1">Run a scan to inspect existing uploads.</div></div>
    <div id="duplicate-photo-details"></div>
    <div class="backup-actions" style="align-items:center">
      <button type="button" class="btn-ghost" id="duplicate-scan-btn" onclick="startDuplicatePhotoScan()">Scan Exact Duplicates</button>
      <input type="password" class="modal-select" id="duplicate-delete-password" placeholder="Delete password" autocomplete="off" style="max-width:190px" data-lpignore="true" data-1p-ignore="true" data-bwignore="true">
      <button type="button" class="btn-primary" id="duplicate-cleanup-btn" onclick="startDuplicatePhotoCleanup()" disabled>Remove Safe Copies</button>
    </div>
    <div id="duplicate-photo-status" class="backup-message" style="margin-top:12px">No duplicate scan has run yet.</div>
  </div>`;
}

function duplicateStat(label, value, note) {
  return `<div class="backup-health-stat"><div class="backup-health-label">${escapeHtml(label)}</div><div class="backup-health-value">${Number(value || 0).toLocaleString()}</div><div class="backup-health-note">${escapeHtml(note)}</div></div>`;
}

function formatDuplicateDuration(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) return 'Calculating…';
  const rounded = Math.max(0, Math.round(seconds));
  if (rounded < 60) return `${rounded}s`;
  if (rounded < 3600) return `${Math.floor(rounded / 60)}m ${rounded % 60}s`;
  const hours = Math.floor(rounded / 3600);
  const minutes = Math.floor((rounded % 3600) / 60);
  return `${hours}h ${minutes}m`;
}

function duplicateProgressView(job) {
  const total = Number(job.progress_total || 0);
  const rawCurrent = Number(job.progress_current || 0);
  const current = Math.min(rawCurrent, total || rawCurrent);
  const queued = job.status === 'Queued';
  const percentage = total ? Math.min(100, Math.max(0, (current / total) * 100)) : 0;
  const startedAt = Date.parse(job.started_at || job.created_at || '');
  const elapsedSeconds = Number.isFinite(startedAt) ? Math.max(0, (Date.now() - startedAt) / 1000) : NaN;
  const rate = current > 0 && elapsedSeconds > 0 ? current / elapsedSeconds : 0;
  const remaining = total ? Math.max(0, total - current) : null;
  const etaSeconds = rate > 0 && remaining !== null ? remaining / rate : NaN;
  const heartbeatAt = Date.parse(job.heartbeat_at || '');
  const heartbeatAge = Number.isFinite(heartbeatAt) ? Math.max(0, (Date.now() - heartbeatAt) / 1000) : NaN;
  const stalled = !queued && Number.isFinite(heartbeatAge) && heartbeatAge > 90;
  const operation = job.job_type === 'duplicate_photo_cleanup' ? 'Safe duplicate cleanup' : 'Exact duplicate scan';
  const defaultPhase = queued
    ? 'Waiting for background worker'
    : (total ? 'Processing stored images' : 'Preparing image inventory');
  const phase = job.result?.phase || defaultPhase;
  const perMinute = rate * 60;
  const rateLabel = rate > 0 ? `${perMinute.toFixed(perMinute >= 10 ? 0 : 1)} images/min` : 'Calculating speed…';
  const heartbeatLabel = queued
    ? 'Waiting for worker'
    : (!Number.isFinite(heartbeatAge) ? 'Worker starting…'
      : (stalled ? `No update for ${formatDuplicateDuration(heartbeatAge)}` : `Worker active · updated ${formatDuplicateDuration(heartbeatAge)} ago`));
  const progressClass = total ? '' : ' preparing';
  const remainingLabel = remaining === null ? '—' : remaining.toLocaleString();
  const etaLabel = queued ? 'Waiting' : (remaining === 0 && total ? 'Finishing…' : formatDuplicateDuration(etaSeconds));

  return {
    stalled,
    html: `<div class="duplicate-progress-panel">
      <div class="duplicate-progress-head">
        <div><div class="duplicate-progress-kicker">${escapeHtml(operation)} · ${escapeHtml(job.status)}</div><div class="duplicate-progress-title">${escapeHtml(phase)}</div></div>
        <div class="duplicate-progress-percent">${percentage.toFixed(1)}%</div>
      </div>
      <div class="duplicate-progress-track" role="progressbar" aria-label="${escapeHtml(operation)} progress" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${percentage.toFixed(1)}"><span class="duplicate-progress-fill${progressClass}" style="width:${percentage.toFixed(2)}%"></span></div>
      <div class="duplicate-progress-metrics">
        <div class="duplicate-progress-metric"><span>Completed</span><strong>${current.toLocaleString()}</strong></div>
        <div class="duplicate-progress-metric"><span>Total images</span><strong>${total ? total.toLocaleString() : 'Preparing…'}</strong></div>
        <div class="duplicate-progress-metric"><span>Remaining</span><strong>${remainingLabel}</strong></div>
        <div class="duplicate-progress-metric"><span>Estimated wait</span><strong>${etaLabel}</strong></div>
      </div>
      <div class="duplicate-progress-foot"><span>Elapsed ${formatDuplicateDuration(elapsedSeconds)} · ${rateLabel}</span><span class="${stalled ? 'stalled' : 'active'}">${escapeHtml(heartbeatLabel)}</span></div>
    </div>`,
    message: queued
      ? 'The task is queued. Start or verify the background worker to begin scanning.'
      : (stalled
        ? 'The scan has stopped reporting progress. Check the background worker before waiting longer.'
        : `${operation} is running. This display refreshes automatically every two seconds.`),
  };
}

async function loadDuplicatePhotos() {
  const summary = document.getElementById('duplicate-photo-summary');
  const details = document.getElementById('duplicate-photo-details');
  const status = document.getElementById('duplicate-photo-status');
  const scanButton = document.getElementById('duplicate-scan-btn');
  const cleanupButton = document.getElementById('duplicate-cleanup-btn');
  if (!summary || !details || !status || !scanButton || !cleanupButton) return;
  try {
    const response = await fetch('/api/settings/duplicate-photos');
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Could not load duplicate-image status.');
    const jobs = data.jobs || [];
    const active = jobs.find(job => ['Queued', 'Processing'].includes(job.status));
    const latest = jobs[0];
    clearTimeout(duplicatePhotoPollTimer);
    scanButton.disabled = !!active;
    cleanupButton.disabled = true;

    if (active) {
      const progress = duplicateProgressView(active);
      summary.innerHTML = progress.html;
      details.innerHTML = '';
      status.className = progress.stalled ? 'backup-message warn' : 'backup-message';
      status.textContent = progress.message;
      duplicatePhotoPollTimer = setTimeout(loadDuplicatePhotos, 2000);
      return;
    }

    if (!latest) {
      summary.innerHTML = '<div class="backup-message" style="grid-column:1/-1">Run a scan to fingerprint legacy images and identify exact duplicates.</div>';
      details.innerHTML = '';
      status.textContent = 'Scanning is read-only. Cleanup is a separate password-protected action.';
      return;
    }

    if (latest.status === 'Failed') {
      summary.innerHTML = duplicateStat('Status', 0, 'Scan needs attention');
      details.innerHTML = '';
      status.className = 'backup-message warn';
      status.textContent = latest.error || 'Duplicate-image task failed.';
      return;
    }

    const result = latest.result || {};
    if (latest.job_type === 'duplicate_photo_cleanup') {
      summary.innerHTML = duplicateStat('Removed', result.deleted_photos, 'redundant photo records') +
        duplicateStat('Files cleared', result.deleted_files, 'unreferenced stored copies') +
        duplicateStat('Protected', result.remaining_protected_groups, 'groups needing manual review') +
        duplicateStat('Tower conflicts', result.remaining_tower_conflicts, 'never auto-deleted');
      details.innerHTML = '';
      status.className = result.cleanup_errors?.length ? 'backup-message warn' : 'backup-message';
      status.textContent = result.cleanup_errors?.length
        ? `Cleanup completed with ${result.cleanup_errors.length} file warning(s). Run Data Health, then scan again.`
        : 'Safe duplicate cleanup completed. Run Scan Exact Duplicates again to verify the final state.';
      return;
    }

    const safeGroups = result.safe_groups || [];
    const protectedCount = (result.protected_groups || []).length;
    const conflictCount = (result.tower_conflicts || []).length;
    summary.innerHTML = duplicateStat('Photos scanned', result.photos_scanned, `${Number(result.hashes_added || 0).toLocaleString()} legacy fingerprints added`) +
      duplicateStat('Safe copies', result.safe_to_remove, `${safeGroups.length} exact duplicate group${safeGroups.length === 1 ? '' : 's'}`) +
      duplicateStat('Protected groups', protectedCount, 'evidence retained') +
      duplicateStat('Tower conflicts', conflictCount, 'manual review required');
    const visibleGroups = safeGroups.slice(0, 20);
    details.innerHTML = visibleGroups.length ? `<div class="backup-message"><strong>Safe duplicate groups</strong><div style="display:grid;gap:6px;margin-top:8px">${visibleGroups.map(group =>
      `<div>${escapeHtml(group.project_name || 'Project')} · ${escapeHtml(group.line_name)} · Tower ${escapeHtml(group.tower_label)} — keep photo #${Number(group.keeper_photo_id)}, remove ${group.delete_photo_ids.map(id => `#${Number(id)}`).join(', ')}</div>`
    ).join('')}</div>${safeGroups.length > visibleGroups.length ? `<div style="margin-top:8px">${safeGroups.length - visibleGroups.length} more group(s) are included in cleanup.</div>` : ''}</div>` : '';
    cleanupButton.disabled = !Number(result.safe_to_remove || 0);
    status.className = (protectedCount || conflictCount || result.missing_files) ? 'backup-message warn' : 'backup-message';
    status.textContent = Number(result.safe_to_remove || 0)
      ? 'Review the counts, enter the shared delete password, then remove only the confirmed safe copies.'
      : (result.duplicate_groups ? 'No group is safe for automatic removal; protected/conflicting copies remain unchanged.' : 'No exact duplicate tower images were found.');
  } catch (error) {
    status.className = 'backup-message warn';
    status.textContent = error.message;
  }
}

async function startDuplicatePhotoScan() {
  const button = document.getElementById('duplicate-scan-btn');
  if (button) { button.disabled = true; button.textContent = 'Scan Queued…'; }
  try {
    const response = await fetch('/api/settings/duplicate-photos/scan', {method:'POST'});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Could not queue duplicate scan.');
    await loadDuplicatePhotos();
  } catch (error) {
    const status = document.getElementById('duplicate-photo-status');
    status.className = 'backup-message warn'; status.textContent = error.message;
  } finally {
    if (button) { button.disabled = false; button.textContent = 'Scan Exact Duplicates'; }
  }
}

async function startDuplicatePhotoCleanup() {
  const passwordInput = document.getElementById('duplicate-delete-password');
  const password = passwordInput?.value || '';
  const status = document.getElementById('duplicate-photo-status');
  if (!password) {
    status.className = 'backup-message warn';
    status.textContent = 'Enter the shared delete password before cleanup.';
    passwordInput?.focus();
    return;
  }
  if (!window.confirm('Remove only the exact duplicate copies classified as safe? Canonical images and all protected evidence will remain.')) return;
  try {
    const response = await fetch('/api/settings/duplicate-photos/cleanup', {
      method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({password})
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Could not queue duplicate cleanup.');
    passwordInput.value = '';
    await loadDuplicatePhotos();
  } catch (error) {
    status.className = 'backup-message warn'; status.textContent = error.message;
  }
}

function installCompatibilityManager() {
  const view = document.getElementById('view-uploads');
  if (!view || document.getElementById('release-compatibility-card')) return;
  const wrap = createStorageTool('release-compatibility-card', 'Version & compatibility',
    'Verify protected application features before an upgrade.', '✓', 50);
  if (!wrap) return;
  wrap.innerHTML = `<div class="settings-card"><div class="settings-card-title">Application release status</div>
    <div class="settings-card-desc">Confirms that this source package contains every protected application feature before an upgrade.</div>
    <div id="compatibility-summary" class="backup-message">Checking release compatibility…</div>
    <div style="margin-top:14px"><button class="btn-ghost" onclick="loadCompatibilityStatus()">Check Again</button></div></div>`;
  loadCompatibilityStatus();
  installStorageMigrationManager(view);
}

function installStorageMigrationManager(view) {
  if (document.getElementById('storage-migration-card')) return;
  const wrap = createStorageTool('storage-migration-card', 'Object storage',
    'Provider readiness and tracked-file migration.', '↥', 30);
  if (!wrap) return;
  wrap.innerHTML = `<div class="settings-card">
    <div class="settings-card-title">Tracked-file migration</div>
    <div class="settings-card-desc">Copies database-linked uploads that still exist only on this server into the configured object store. It never deletes local files.</div>
    <div id="storage-migration-summary" class="backup-message">Checking storage status…</div>
    <div style="margin-top:14px"><button class="btn-primary" id="storage-migration-btn" onclick="startStorageMigration()">Copy Missing Files</button></div>
  </div>`;
  loadStorageMigrationStatus();
}

function installProductionHealthManager() {
  const view = document.getElementById('view-uploads');
  if (!view || document.getElementById('production-health-card')) return;
  const wrap = createStorageTool('production-health-card', 'Deployment health',
    'Web, database, worker, jobs and map checks.', '●', 40);
  if (!wrap) return;
  wrap.innerHTML = `<div class="settings-card">
    <div class="settings-card-title">Production services</div>
    <div class="settings-card-desc">Checks the web process, reverse-proxy configuration, database, object storage, background worker, map source and persistent jobs.</div>
    <div class="backup-health-grid" id="production-health-grid"><div class="backup-message" style="grid-column:1/-1">Checking services…</div></div>
    <div id="production-health-message"></div><div style="margin-top:14px"><button class="btn-ghost" onclick="loadProductionHealth()">Check Again</button></div></div>`;
  loadProductionHealth();
}

async function loadProductionHealth() {
  const grid = document.getElementById('production-health-grid'); const message = document.getElementById('production-health-message');
  if (!grid) return;
  try {
    const response = await fetch('/api/settings/system-health'); const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Service health check failed.');
    const checks = data.checks || {}; const web = checks.web || {}; const jobs = checks.jobs || {};
    const headerText = document.getElementById('settings-system-health-text');
    if (headerText) headerText.textContent = `${web.environment || 'application'} · ${data.status}`;
    const healthTitle = document.getElementById('storage-health-title');
    const healthNote = document.getElementById('storage-health-note');
    if (healthTitle) healthTitle.textContent = data.status === 'healthy' ? 'Application is running normally' : 'Application needs attention';
    if (healthNote) healthNote.textContent = checks.worker?.age_seconds != null ? `Worker heartbeat ${checks.worker.age_seconds}s ago` : 'Background worker heartbeat not detected';
    [['database', checks.database], ['storage', checks.storage], ['worker', checks.worker], ['map', checks.map]].forEach(([name, check]) => {
      const dot = document.getElementById(`health-${name}-dot`);
      if (dot) dot.style.background = check?.status === 'ok' || check?.status === 'healthy' || check?.status === 'running' ? 'var(--success)' : 'var(--warning)';
    });
    grid.innerHTML = [
      ['Overall', data.status, `${data.response_ms || 0} ms probe`],
      ['Web', web.status || 'unknown', `${web.server || 'unknown'} · proxy ${web.reverse_proxy_trusted ? 'trusted' : 'not configured'}`],
      ['Database', checks.database?.status || 'unknown', checks.database?.backend || '—'],
      ['Storage', checks.storage?.status || 'unknown', checks.storage?.backend || '—'],
      ['Worker', checks.worker?.status || 'unknown', checks.worker?.age_seconds != null ? `${checks.worker.age_seconds}s since heartbeat` : 'No heartbeat'],
      ['Jobs', jobs.failed ? 'attention' : 'ok', `${jobs.queued || 0} queued · ${jobs.processing || 0} processing · ${jobs.failed || 0} failed`],
      ['Map', checks.map?.status || 'unknown', checks.map?.mode || '—']
    ].map(item => `<div class="backup-health-stat"><div class="backup-health-label">${escapeHtml(item[0])}</div><div class="backup-health-value" style="font-size:15px">${escapeHtml(item[1])}</div><div class="backup-health-note">${escapeHtml(item[2])}</div></div>`).join('');
    const warnings = web.warnings || [];
    message.innerHTML = warnings.length ? `<div class="backup-message warn">${warnings.map(escapeHtml).join('<br>')}</div>` : '';
  } catch (error) { grid.innerHTML = `<div class="backup-message warn" style="grid-column:1/-1">${escapeHtml(error.message)}</div>`; }
}

async function loadStorageMigrationStatus() {
  const target = document.getElementById('storage-migration-summary');
  if (!target) return;
  try {
    const response = await fetch('/api/settings/storage-migration'); const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Storage status failed.');
    const job = (data.jobs || [])[0];
    if (data.storage_backend === 'local') {
      target.textContent = 'Local storage is active. No object-storage copy is required.';
      document.getElementById('storage-migration-btn').disabled = true;
    } else if (!job) target.textContent = 'Object storage is active. Run once to copy any older local-only files.';
    else target.textContent = `Latest copy: ${job.status} · ${job.progress_current || 0} of ${job.progress_total || 0}`;
  } catch (error) { target.className = 'backup-message warn'; target.textContent = error.message; }
}

async function startStorageMigration() {
  const button = document.getElementById('storage-migration-btn');
  if (button) { button.disabled = true; button.textContent = 'Queued…'; }
  try {
    const response = await fetch('/api/settings/storage-migration', {method: 'POST'});
    const data = await response.json(); if (!response.ok) throw new Error(data.error || 'Could not queue storage copy.');
    await loadStorageMigrationStatus();
  } catch (error) {
    const target = document.getElementById('storage-migration-summary'); target.className = 'backup-message warn'; target.textContent = error.message;
  } finally { if (button) { button.disabled = false; button.textContent = 'Copy Missing Files'; } }
}

async function loadCompatibilityStatus() {
  const target = document.getElementById('compatibility-summary');
  if (!target) return;
  try {
    const response = await fetch('/api/settings/compatibility');
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Compatibility check failed.');
    const healthy = data.missing === 0;
    target.className = `backup-message${healthy ? '' : ' warn'}`;
    target.innerHTML = `<strong>Version ${escapeHtml(data.version)}</strong> · Revision ${escapeHtml(data.database_revision)}<br>
      ${Number(data.passed)} of ${Number(data.total)} protected features available · Worker: ${escapeHtml(data.worker)}
      ${healthy ? '<br>Source compatibility check passed.' : `<br>${Number(data.missing)} required feature(s) are missing. Do not deploy this source.`}`;
  } catch (error) {
    target.className = 'backup-message warn';
    target.textContent = error.message;
  }
}

function installBackupManager() {
  const view = document.getElementById('view-uploads');
  if (!view || document.getElementById('backup-upgrade-manager')) return;
  const wrap = createStorageTool('backup-upgrade-manager', 'Backup & recovery',
    'Check data health and create recoverable checkpoints.', '↻', 10, true);
  if (!wrap) return;
  wrap.innerHTML = `<div class="settings-card">
      <div class="settings-card-title">Data health and recovery</div>
      <div class="settings-card-desc">Check that database records and stored files agree, then create a recoverable checkpoint before moving to another application version. Secrets from .env are never included.</div>
      <div class="backup-health-grid" id="backup-health-grid"><div class="backup-message" style="grid-column:1/-1">Run a health check to inspect the current data.</div></div>
      <div id="backup-health-message"></div>
      <div id="data-health-relink"></div>
      <div class="backup-actions"><button class="btn-ghost" id="data-health-btn" onclick="loadDataHealth(true)">Check Data Health</button>
        <button class="btn-ghost" onclick="window.location.href='/api/settings/data-health/issues.csv'">Download Issue CSV</button>
        <button class="btn-primary" id="backup-create-btn" onclick="createApplicationBackup(false)">Create Backup</button>
        <button class="btn-ghost" id="backup-upgrade-btn" onclick="createApplicationBackup(true)">Create Pre-Upgrade Backup</button></div>
    </div>
    <div class="section-label">BACKUP HISTORY</div><div id="backup-history" class="backup-list"><div class="backup-message">Loading backup history…</div></div>`;
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
    const relink = document.getElementById('data-health-relink');
    const groups = Object.entries(data.files.missing_by_category || {});
    const suggestions = data.files.relink_suggestions || [];
    relink.innerHTML = `${groups.length ? `<div class="backup-message"><strong>Missing files by category:</strong><br>${groups.map(([name,count]) => `${escapeHtml(name)}: ${Number(count).toLocaleString()}`).join(' · ')}</div>` : ''}
      ${suggestions.length ? `<div class="backup-message"><strong>${suggestions.length} safe relink suggestion(s)</strong><br>Only exact filenames with one untracked candidate are offered.<div style="margin-top:8px;display:grid;gap:6px">${suggestions.slice(0,10).map((row,index) => `<div style="display:flex;gap:8px;align-items:center;justify-content:space-between"><span style="overflow:hidden;text-overflow:ellipsis">${escapeHtml(row.missing)} → ${escapeHtml(row.candidate)}</span><button class="btn-ghost data-relink-btn" data-index="${index}">Relink</button></div>`).join('')}</div></div>` : ''}`;
    relink.querySelectorAll('.data-relink-btn').forEach(button => button.addEventListener('click', () => applyDataRelink(suggestions[Number(button.dataset.index)])));
  } catch (e) {
    message.innerHTML = `<div class="backup-message warn">${escapeHtml(e.message)}</div>`;
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Check Data Health'; }
  }
}

async function applyDataRelink(suggestion) {
  if (!suggestion || !confirm(`Relink the missing database path to this existing file?\n\n${suggestion.candidate}`)) return;
  const response = await fetch('/api/settings/data-health/relink', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(suggestion)});
  const data = await response.json().catch(() => ({}));
  if (!response.ok) { alert(data.error || 'Relink failed.'); return; }
  await loadDataHealth(true);
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
    const meter = document.getElementById('storage-meter-fill');
    const note = document.getElementById('storage-capacity-note');
    if (meter) meter.style.width = `${Math.max(0, Math.min(100, Number(data.used_percent || 0)))}%`;
    if (note) note.textContent = data.disk_free ? `${data.disk_free} available` : `${Number(data.file_count || 0).toLocaleString()} managed files`;
  } catch (e) {
    total.textContent = 'Unavailable';
    files.textContent = '—';
  }
}

let thumbnailRepairPoll = null;
async function loadThumbnailRepair() {
  const view = document.getElementById('view-uploads');
  if (!view) return;
  if (!document.getElementById('thumbnail-repair-card')) {
    const card = createStorageTool('thumbnail-repair-card', 'Thumbnail repair',
      'Regenerate missing previews without changing originals.', '▧', 20);
    if (!card) return;
    card.innerHTML = `<div class="settings-card-title">Thumbnail health and repair</div>
      <div class="settings-card-desc">Regenerate only missing gallery thumbnails. Original RGB and radiometric thermal files are never changed.</div>
      <div style="display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap">
        <div><div style="font-size:22px;font-weight:800" id="thumbnail-missing-count">—</div><div style="font-size:10.5px;color:var(--text-muted)">missing thumbnails</div></div>
        <button class="btn-primary" id="thumbnail-repair-btn" onclick="startThumbnailRepair()">Regenerate Missing Thumbnails</button>
      </div><div id="thumbnail-repair-status" style="font-size:11px;color:var(--text-muted);margin-top:10px">Checking…</div>`;
  }
  const count = document.getElementById('thumbnail-missing-count');
  const status = document.getElementById('thumbnail-repair-status');
  const button = document.getElementById('thumbnail-repair-btn');
  try {
    const res = await fetch('/api/settings/thumbnail-repair');
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Could not check thumbnails.');
    count.textContent = Number(data.missing || 0).toLocaleString();
    const active = (data.jobs || []).find(job => ['Queued','Processing'].includes(job.status));
    button.disabled = !!active || !data.missing;
    button.textContent = active ? 'Repair Running…' : 'Regenerate Missing Thumbnails';
    if (active) {
      status.textContent = `${active.status}: ${active.progress_current || 0}/${active.progress_total || data.missing || 0}`;
      clearTimeout(thumbnailRepairPoll);
      thumbnailRepairPoll = setTimeout(loadThumbnailRepair, 2000);
    } else {
      const latest = (data.jobs || [])[0], result = latest?.result || {};
      status.textContent = latest ? `Last job: ${latest.status} · ${result.repaired || 0} repaired · ${result.failed || 0} failed` : 'No repair job has been run.';
    }
  } catch (error) { status.textContent = error.message; }
  loadMediaMetadataRepair();
}

async function startThumbnailRepair() {
  const button = document.getElementById('thumbnail-repair-btn');
  if (button) button.disabled = true;
  try {
    const res = await fetch('/api/settings/thumbnail-repair', {method:'POST', headers:{'Content-Type':'application/json'}, body:'{}'});
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Could not queue thumbnail repair.');
    await loadThumbnailRepair();
  } catch (error) {
    document.getElementById('thumbnail-repair-status').textContent = error.message;
    if (button) button.disabled = false;
  }
}

let mediaMetadataPoll = null;
async function loadMediaMetadataRepair() {
  const view = document.getElementById('view-uploads');
  if (!view) return;
  if (!document.getElementById('media-metadata-card')) {
    const card = createStorageTool('media-metadata-card', 'Image metadata',
      'Classify existing RGB and thermal files safely.', 'i', 25);
    if (!card) return;
    card.innerHTML = `<div class="settings-card-title">Existing image metadata</div>
      <div class="settings-card-desc">Classify existing RGB/Thermal files and record dimensions, capture time and validation health without changing originals.</div>
      <div style="display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap">
        <div><div style="font-size:22px;font-weight:800" id="metadata-legacy-count">—</div><div style="font-size:10.5px;color:var(--text-muted)">legacy images awaiting metadata</div></div>
        <button class="btn-primary" id="metadata-repair-btn" onclick="startMediaMetadataRepair()">Repair Existing Metadata</button>
      </div><div id="metadata-repair-status" style="font-size:11px;color:var(--text-muted);margin-top:10px">Checking…</div>`;
  }
  const count = document.getElementById('metadata-legacy-count');
  const status = document.getElementById('metadata-repair-status');
  const button = document.getElementById('metadata-repair-btn');
  try {
    const res = await fetch('/api/settings/media-metadata-repair');
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Could not check existing image metadata.');
    count.textContent = Number(data.legacy_count || 0).toLocaleString();
    const active = (data.jobs || []).find(job => ['Queued','Processing'].includes(job.status));
    button.disabled = !!active || !data.legacy_count;
    button.textContent = active ? 'Metadata Repair Running…' : 'Repair Existing Metadata';
    if (active) {
      status.textContent = `${active.status}: ${active.progress_current || 0}/${active.progress_total || data.legacy_count || 0}`;
      clearTimeout(mediaMetadataPoll);
      mediaMetadataPoll = setTimeout(loadMediaMetadataRepair, 2000);
    } else {
      const latest = (data.jobs || [])[0], result = latest?.result || {};
      status.textContent = latest ? `Last job: ${latest.status} · ${result.updated || 0} updated · ${result.missing || 0} missing · ${result.failed || 0} invalid` : 'No metadata repair has been run.';
    }
  } catch (error) { status.textContent = error.message; }
}

async function startMediaMetadataRepair() {
  const button = document.getElementById('metadata-repair-btn');
  if (button) button.disabled = true;
  try {
    const res = await fetch('/api/settings/media-metadata-repair', {method:'POST', headers:{'Content-Type':'application/json'}, body:'{}'});
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Could not queue metadata repair.');
    await loadMediaMetadataRepair();
  } catch (error) {
    document.getElementById('metadata-repair-status').textContent = error.message;
    if (button) button.disabled = false;
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

let activityPage = 1;
let activityCategory = 'all';
let activitySearch = '';
let activitySearchTimer = null;

function installActivityControls() {
  document.querySelectorAll('#activity-filters .activity-filter').forEach(button => {
    button.addEventListener('click', () => {
      document.querySelectorAll('#activity-filters .activity-filter').forEach(item => item.classList.remove('active'));
      button.classList.add('active'); activityCategory = button.dataset.category || 'all'; loadActivityLog();
    });
  });
  const search = document.getElementById('activity-search');
  if (search) search.addEventListener('input', () => {
    clearTimeout(activitySearchTimer);
    activitySearchTimer = setTimeout(() => { activitySearch = search.value.trim(); loadActivityLog(); }, 300);
  });
}

async function loadActivityLog(append = false) {
  const body = document.getElementById('activity-log-body');
  if (!body) return;
  if (!append) { activityPage = 1; body.innerHTML = `<tr class="empty-row"><td colspan="7">Loading activity…</td></tr>`; }
  try {
    const query = new URLSearchParams({page: String(activityPage), per_page: '20', category: activityCategory});
    if (activitySearch) query.set('search', activitySearch);
    const res = await fetch(`/api/settings/activity-log?${query}`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Could not load activity.');
    const entries = data.entries || [];
    if (!entries.length && !append) {
      body.innerHTML = `<tr class="empty-row"><td colspan="7">No activity recorded yet.</td></tr>`;
    } else {
      const rows = entries.map(e => `
        <tr>
          <td><span class="action-chip ${escapeHtml(e.action)}">${escapeHtml(e.action)}</span></td>
          <td>${escapeHtml(e.entity_name || '—')}</td>
          <td>${escapeHtml(e.module || '—')}</td>
          <td>${escapeHtml(e.performed_by || '—')}</td>
          <td>${escapeHtml(e.role || '—')}</td>
          <td>${escapeHtml(e.duration || '—')}</td>
          <td title="${escapeHtml(e.created_at)}">${timeAgo(e.created_at_iso)}</td>
        </tr>`).join('');
      if (append) body.insertAdjacentHTML('beforeend', rows); else body.innerHTML = rows;
    }
    const more = document.getElementById('activity-load-more');
    if (more) more.style.display = data.has_more ? '' : 'none';
  } catch (e) {
    if (!append) body.innerHTML = `<tr class="empty-row"><td colspan="7">Could not load activity log.</td></tr>`;
  }
}

function loadMoreActivity() { activityPage += 1; loadActivityLog(true); }

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
  const grid = document.getElementById('all-projects-grid');
  if (!grid) return;
  try {
    const res = await fetch('/api/settings/all-projects');
    const data = await res.json();
    const projects = data.projects || [];
    if (!projects.length) {
      grid.innerHTML = `<div class="project-data-card"><div class="project-data-detail">No projects yet.</div></div>`;
      return;
    }
    grid.innerHTML = projects.map(p => `
      <article class="project-data-card">
        <div class="project-data-head"><div class="project-data-icon" aria-hidden="true">⌁</div><span class="project-data-module">${escapeHtml(p.module || 'TRANS')}</span></div>
        <div class="project-data-name">${escapeHtml(p.name)}</div>
        <div class="project-data-detail">${escapeHtml(p.detail)}</div>
        <div class="project-data-stats">
          <div><div class="project-data-value">${Number(p.division_count || 0).toLocaleString()}</div><div class="project-data-label">DIVISIONS</div></div>
          <div><div class="project-data-value">${Number(p.line_count || 0).toLocaleString()}</div><div class="project-data-label">LINES</div></div>
          <div><div class="project-data-value">${Number(p.tower_count || 0).toLocaleString()}</div><div class="project-data-label">TOWERS</div></div>
        </div>
        <div class="project-data-footer"><span class="project-data-created">Created ${escapeHtml(p.created_at)}</span><span class="project-data-actions">
          <button type="button" class="browse-btn" onclick="openTowerBrowser(${p.id}, '${escapeHtml(p.name).replace(/'/g, "\\'")}')">Browse</button>
          <button type="button" class="ann-delete-btn" onclick="requestDelete({url:'${p.delete_url}', label:'${escapeHtml(p.name).replace(/'/g, "\\'")}', onSuccess: loadAllProjects})">Delete</button>
        </span></div>
      </article>`).join('');
  } catch (e) {
    grid.innerHTML = `<div class="project-data-card"><div class="project-data-detail">Could not load projects.</div></div>`;
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
