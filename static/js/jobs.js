const $ = (id) => document.getElementById(id);
const fields = {
  external_job_id: $('external-job-id'), company: $('company'), title: $('title'), location: $('location'),
  employment_type: $('employment-type'), salary: $('salary'), status: $('status'),
  posted_at: $('posted-at'), applied_at: $('applied-at'), apply_url: $('apply-url'),
  description: $('description'), notes: $('notes'),
};
let currentUrl = '';
let sourceSite = '';
let validThrough = '';
let hasAiKey = false;

function message(text = '', kind = '') {
  $('message').textContent = text;
  $('message').className = `message ${kind}`;
}

async function api(url, options = {}) {
  const res = await fetch(url, {credentials: 'same-origin', headers: {'Content-Type': 'application/json'}, ...options});
  if (res.status === 401) { location.href = '/login'; throw new Error('Sign in required'); }
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || 'Request failed');
  return body;
}

function renderAiStatus(settings) {
  hasAiKey = Boolean(settings.has_api_key);
  $('ai-enabled').checked = Boolean(settings.enabled);
  $('ai-model').value = settings.model || 'gemini-2.5-flash-lite';
  $('ai-key').value = '';
  $('ai-key').placeholder = hasAiKey ? 'Saved securely — enter only to replace' : 'Paste a new key';
  $('ai-key-hint').textContent = hasAiKey
    ? 'A key is saved securely. Its value is never returned to this page.'
    : 'No key saved. Paste the replacement key here; it will be encrypted.';
  $('ai-status').textContent = settings.enabled && hasAiKey ? 'On · key saved' : hasAiKey ? 'Off · key saved' : 'Not configured';
}

async function saveAiSettings({removeKey = false, quiet = false} = {}) {
  const key = $('ai-key').value.trim();
  const payload = {
    enabled: $('ai-enabled').checked,
    model: $('ai-model').value.trim() || 'gemini-2.5-flash-lite',
    remove_key: removeKey,
  };
  if (key) payload.api_key = key;
  const settings = await api('/api/jobs/ai-settings', {method: 'PUT', body: JSON.stringify(payload)});
  renderAiStatus(settings);
  if (!quiet) message(removeKey ? 'Gemini API key removed.' : 'Gemini settings saved.', 'ok');
  return settings;
}

async function loadAiSettings() {
  try { renderAiStatus(await api('/api/jobs/ai-settings')); }
  catch (err) { $('ai-status').textContent = 'Unavailable'; message(err.message, 'error'); }
}

function openForm(job = {}) {
  $('job-id').value = job.id || '';
  currentUrl = job.job_url || $('job-url').value.trim();
  sourceSite = job.source_site || '';
  validThrough = job.valid_through || '';
  for (const [name, el] of Object.entries(fields)) el.value = job[name] || (name === 'status' ? 'saved' : '');
  $('job-form').classList.add('open');
  $('company').focus();
}

function closeForm() {
  $('job-form').classList.remove('open');
  $('job-form').reset();
  $('job-id').value = '';
}

$('scrape-btn').addEventListener('click', async () => {
  const url = $('job-url').value.trim();
  if (!url) return message('Paste a job-posting URL first.', 'error');
  const btn = $('scrape-btn'); btn.disabled = true; btn.textContent = 'Reading…';
  message('Fetching the posting and extracting its details…');
  try {
    const data = await api('/api/jobs/scrape', {method: 'POST', body: JSON.stringify({url})});
    openForm(data.draft || {});
    if (data.ai_used) message('Gemini filled details the regular scraper could not find. Review before saving.', 'ok');
    else if (data.ai_warning) message(`Basic details extracted. Gemini could not help: ${data.ai_warning}`, 'error');
    else message(data.draft?.structured ? 'Structured job details found. Review before saving.' : 'Basic details extracted. Please fill any missing fields.', 'ok');
  } catch (err) { message(err.message, 'error'); }
  finally { btn.disabled = false; btn.textContent = 'Import job'; }
});

$('cancel-btn').addEventListener('click', closeForm);
$('job-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const id = $('job-id').value;
  const payload = Object.fromEntries(Object.entries(fields).map(([name, el]) => [name, el.value.trim()]));
  payload.job_url = currentUrl;
  payload.apply_url = payload.apply_url || currentUrl;
  payload.source_site = sourceSite;
  payload.valid_through = validThrough;
  payload.raw_metadata = {};
  try {
    await api(id ? `/api/jobs/${encodeURIComponent(id)}` : '/api/jobs', {method: id ? 'PUT' : 'POST', body: JSON.stringify(payload)});
    closeForm(); $('job-url').value = ''; message(id ? 'Application updated.' : 'Application saved.', 'ok'); await loadJobs();
  } catch (err) { message(err.message, 'error'); }
});

function esc(text) { const node = document.createElement('span'); node.textContent = text || ''; return node.innerHTML; }
function attr(text) { return esc(text).replaceAll('"', '&quot;').replaceAll("'", '&#39;'); }
function render(jobs) {
  const root = $('jobs');
  if (!jobs.length) { root.innerHTML = '<div class="empty">No applications here yet.</div>'; return; }
  root.innerHTML = jobs.map(job => `<article class="job-card">
    <div class="company">${esc(job.company || 'Company not set')}</div><h3>${esc(job.title || 'Position not set')}</h3>
    <div class="meta">${esc([job.external_job_id ? `Job ID ${job.external_job_id}` : '', job.location, job.employment_type, job.salary].filter(Boolean).join(' · '))}</div>
    <span class="badge">${esc(job.status)}</span>
    <div class="job-actions"><a href="${attr(job.job_url)}" target="_blank" rel="noopener noreferrer">Open posting ↗</a><button type="button" data-edit="${attr(job.id)}">Edit</button></div>
  </article>`).join('');
  root.querySelectorAll('[data-edit]').forEach(button => button.addEventListener('click', () => openForm(jobs.find(job => job.id === button.dataset.edit))));
}

function renderSuggestions(items) {
  const root = $('mail-suggestions');
  if (!items.length) {
    root.innerHTML = '<div class="empty">No email updates waiting for review.</div>';
    return;
  }
  root.innerHTML = items.map(item => `<article class="mail-card">
    <h3>${esc(item.company)} — ${esc(item.title)}</h3>
    <div class="mail-subject">${esc(item.subject || 'No subject')} · ${esc(item.sender || 'Unknown sender')}</div>
    <div class="mail-evidence">${esc(item.evidence)}</div>
    <div class="mail-actions"><span class="badge">${esc(item.current_status)} → ${esc(item.proposed_status)}</span><button class="btn secondary" type="button" data-dismiss-suggestion="${attr(item.id)}">Dismiss</button><button class="btn" type="button" data-accept-suggestion="${attr(item.id)}">Apply status</button></div>
  </article>`).join('');
  root.querySelectorAll('[data-accept-suggestion]').forEach(button => button.addEventListener('click', () => reviewSuggestion(button.dataset.acceptSuggestion, 'accept')));
  root.querySelectorAll('[data-dismiss-suggestion]').forEach(button => button.addEventListener('click', () => reviewSuggestion(button.dataset.dismissSuggestion, 'dismiss')));
}

async function loadSuggestions() {
  try { renderSuggestions((await api('/api/jobs/email-suggestions')).suggestions || []); }
  catch (err) { message(err.message, 'error'); }
}

async function reviewSuggestion(id, action) {
  try {
    await api(`/api/jobs/email-suggestions/${encodeURIComponent(id)}/${action}`, {method: 'POST', body: '{}'});
    message(action === 'accept' ? 'Job status updated.' : 'Email suggestion dismissed.', 'ok');
    await Promise.all([loadSuggestions(), loadJobs()]);
  } catch (err) { message(err.message, 'error'); }
}

async function loadJobs() {
  try {
    const status = $('status-filter').value;
    const data = await api('/api/jobs' + (status ? `?status=${encodeURIComponent(status)}` : ''));
    render(data.jobs || []);
  } catch (err) { message(err.message, 'error'); }
}

$('status-filter').addEventListener('change', loadJobs);
$('scan-email').addEventListener('click', async () => {
  const button = $('scan-email'); button.disabled = true; button.textContent = 'Scanning…';
  message('Checking recent email for application updates…');
  try {
    const result = await api('/api/jobs/scan-email', {method: 'POST', body: '{}'});
    renderSuggestions(result.suggestions || []);
    message(result.found ? `Found ${result.found} possible job update${result.found === 1 ? '' : 's'} for review.` : 'No new job-status emails found.', 'ok');
  } catch (err) { message(err.message, 'error'); }
  finally { button.disabled = false; button.textContent = 'Scan email'; }
});
$('job-url').addEventListener('keydown', event => { if (event.key === 'Enter') { event.preventDefault(); $('scrape-btn').click(); } });
$('save-ai').addEventListener('click', async () => {
  const button = $('save-ai'); button.disabled = true;
  try { await saveAiSettings(); }
  catch (err) { message(err.message, 'error'); $('ai-panel').open = true; }
  finally { button.disabled = false; }
});
$('test-ai').addEventListener('click', async () => {
  const button = $('test-ai'); button.disabled = true; button.textContent = 'Testing…';
  try {
    const settings = await saveAiSettings({quiet: true});
    if (!settings.has_api_key) throw new Error('Paste and save a Gemini API key first.');
    const result = await api('/api/jobs/ai-settings/test', {method: 'POST', body: '{}'});
    message(`Gemini connection works (${result.model}).`, 'ok');
  } catch (err) { message(err.message, 'error'); $('ai-panel').open = true; }
  finally { button.disabled = false; button.textContent = 'Test connection'; }
});
$('remove-ai-key').addEventListener('click', async () => {
  if (!hasAiKey) return message('No Gemini key is saved.', 'error');
  if (!confirm('Remove the saved Gemini API key from Odysseus?')) return;
  try { $('ai-enabled').checked = false; await saveAiSettings({removeKey: true}); }
  catch (err) { message(err.message, 'error'); }
});
loadAiSettings();
loadJobs();
loadSuggestions();
