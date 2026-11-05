const $d = id => document.getElementById(id);
const esc = value => { const node = document.createElement('span'); node.textContent = value || ''; return node.innerHTML; };
const STAGE_RE = /\b(oa|online assessment|coding assessment|technical assessment|hackerrank|codesignal|interview|phone screen|screening call)\b/i;

function parseEventDate(value, allDay=false) {
  const match = String(value || '').match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (allDay && match) return new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
  return new Date(value);
}

function fmtTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleTimeString([], {hour:'numeric', minute:'2-digit'});
}

function eventDate(value, allDay=false) {
  const date = parseEventDate(value, allDay);
  return `<div class="event-date"><span>${esc(date.toLocaleDateString([], {month:'short'}))}</span><strong>${date.getDate()}</strong></div>`;
}

function renderCalendar(events) {
  const root = $d('dashboard-calendar'); if (!root) return;
  const now = new Date();
  const year = now.getFullYear(), month = now.getMonth();
  const first = new Date(year, month, 1), days = new Date(year, month + 1, 0).getDate();
  const eventDays = new Set(events.filter(e => { const d = parseEventDate(e.dtstart, e.all_day); return d.getFullYear() === year && d.getMonth() === month; }).map(e => parseEventDate(e.dtstart, e.all_day).getDate()));
  let html = `<div class="month-label">${esc(now.toLocaleDateString([], {month:'long', year:'numeric'}))}</div>`;
  ['S','M','T','W','T','F','S'].forEach(day => { html += `<div class="dow">${day}</div>`; });
  for (let i=0;i<first.getDay();i++) html += '<div class="day muted"></div>';
  for (let day=1;day<=days;day++) {
    const today = day === now.getDate();
    html += `<div class="day${today?' today':''}${eventDays.has(day)?' has-event':''}">${day}</div>`;
  }
  root.innerHTML = html;
}

function renderEvents(events) {
  const root = $d('dashboard-events'); if (!root) return;
  const now = Date.now();
  const upcoming = events.filter(e => parseEventDate(e.dtend || e.dtstart, e.all_day).getTime() >= now).slice(0,5);
  root.innerHTML = upcoming.length ? upcoming.map(e => `<div class="event-row">${eventDate(e.dtstart, e.all_day)}<div class="row-copy"><strong>${esc(e.summary || 'Untitled event')}</strong><small>${esc(e.all_day ? 'All day' : fmtTime(e.dtstart))}${e.location ? ` · ${esc(e.location)}` : ''}</small></div></div>`).join('') : '<div class="dashboard-empty">No upcoming events. <button class="card-link" data-open-calendar>Add one →</button></div>';
  bindCalendarButtons(root);
}

function renderStages(events) {
  const root = $d('dashboard-stages'); if (!root) return;
  const stages = events.filter(e => STAGE_RE.test(`${e.summary||''} ${e.description||''}`) && parseEventDate(e.dtend || e.dtstart, e.all_day).getTime() >= Date.now()).slice(0,4);
  root.innerHTML = stages.length ? stages.map(e => `<div class="stage-row"><div class="stage-icon">⌁</div><div class="row-copy"><strong>${esc(e.summary)}</strong><small>${esc(parseEventDate(e.dtstart, e.all_day).toLocaleDateString([], {weekday:'short', month:'short', day:'numeric'}))}${e.all_day?'':` · ${esc(fmtTime(e.dtstart))}`}</small></div><span class="status-pill">upcoming</span></div>`).join('') : '<div class="dashboard-empty">No OA or interviews scheduled yet. Add one to your calendar when an invitation arrives.</div>';
}

function renderJobs(jobs) {
  const root = $d('dashboard-jobs'); if (!root) return;
  const recent = jobs.slice(0,5);
  root.innerHTML = recent.length ? recent.map(job => `<a class="recent-job" href="/jobs"><div class="stage-icon">${esc((job.company || '?').slice(0,1).toUpperCase())}</div><div class="row-copy"><strong>${esc(job.title || 'Position not set')}</strong><small>${esc(job.company || 'Company not set')}${job.location ? ` · ${esc(job.location)}` : ''}</small></div><span class="status-pill ${esc(job.status)}">${esc(job.status)}</span></a>`).join('') : '<div class="dashboard-empty">No applications tracked yet. <a href="/jobs">Add your first job →</a></div>';
  const active = jobs.filter(j => !['rejected','withdrawn','archived'].includes(j.status)).length;
  $d('dash-total-jobs').textContent = jobs.length;
  $d('dash-active-jobs').textContent = active;
  $d('dash-interviews').textContent = jobs.filter(j => j.status === 'interviewing').length;
}

function renderSuggestions(items) {
  const root = $d('dashboard-email-updates'); if (!root) return;
  $d('dash-review-count').textContent = items.length;
  root.innerHTML = items.length ? items.slice(0,4).map(item => `<a class="attention-row" href="/jobs"><span class="attention-mark"></span><div class="row-copy"><strong>${esc(item.company)} — ${esc(item.proposed_status)}</strong><small>${esc(item.subject || 'Application email update')}</small></div><span class="status-pill">review</span></a>`).join('') : '<div class="dashboard-empty">No email updates waiting for review.</div>';
}

function renderPackages(items) {
  const root=$d('dashboard-packages'); if(!root)return;
  root.innerHTML=items.length?items.slice(0,4).map(p=>`<a class="recent-job" href="/packages"><div class="stage-icon">□</div><div class="row-copy"><strong>${esc(p.label||'Package')}</strong><small>${esc(p.carrier_name||p.tracking_number)}${p.status_detail?` · ${esc(p.status_detail)}`:''}</small></div><span class="status-pill ${esc(p.status)}">${esc((p.status||'pending').replaceAll('_',' '))}</span></a>`).join(''):'<div class="dashboard-empty">No active deliveries. <a href="/packages">Track a package →</a></div>';
}

function bindCalendarButtons(scope=document) {
  scope.querySelectorAll('[data-open-calendar]').forEach(button => {
    if (button.dataset.dashboardBound) return;
    button.dataset.dashboardBound = '1';
    button.addEventListener('click', () => $d('tool-calendar-btn')?.click());
  });
}

async function loadDashboard() {
  const now = new Date();
  const end = new Date(now); end.setDate(end.getDate()+60);
  const start = new Date(now); start.setDate(start.getDate()-7);
  const iso = date => date.toISOString();
  const fetchJson = url => fetch(url, {credentials:'same-origin'}).then(async response => {
    if (!response.ok) throw new Error(`Request failed (${response.status})`);
    return response.json();
  });
  const [jobsResult, eventsResult, suggestionsResult, packagesResult] = await Promise.allSettled([
    fetchJson('/api/jobs'),
    fetchJson(`/api/calendar/events?start=${encodeURIComponent(iso(start))}&end=${encodeURIComponent(iso(end))}`),
    fetchJson('/api/jobs/email-suggestions'),
    fetchJson('/api/packages'),
  ]);
  const jobs = jobsResult.status === 'fulfilled' ? (jobsResult.value.jobs || []) : [];
  const events = eventsResult.status === 'fulfilled' ? (eventsResult.value.events || []).sort((a,b)=>parseEventDate(a.dtstart,a.all_day)-parseEventDate(b.dtstart,b.all_day)) : [];
  const suggestions = suggestionsResult.status === 'fulfilled' ? (suggestionsResult.value.suggestions || []) : [];
  const packages = packagesResult.status === 'fulfilled' ? (packagesResult.value.packages || []) : [];
  renderJobs(jobs); renderCalendar(events); renderEvents(events); renderStages(events); renderSuggestions(suggestions); renderPackages(packages);
}

function initDashboard() {
  const now = new Date();
  const hour = now.getHours();
  $d('dashboard-greeting').textContent = hour < 12 ? 'Good morning' : hour < 18 ? 'Good afternoon' : 'Good evening';
  $d('dashboard-date').textContent = now.toLocaleDateString([], {weekday:'long', month:'long', day:'numeric'});
  const meta = $d('current-meta'); if (meta) meta.textContent = 'Dashboard';
  const input = $d('message'); if (input) input.placeholder = 'Ask Odysseus anything…';
  bindCalendarButtons();
  $d('dashboard-add-job')?.addEventListener('click', () => { location.href='/jobs'; });
  $d('dashboard-ask')?.addEventListener('click', () => input?.focus());
  $d('dashboard-scan-email')?.addEventListener('click', async event => {
    const button = event.currentTarget; button.disabled = true; button.textContent = 'Scanning…';
    try {
      const response = await fetch('/api/jobs/scan-email', {method:'POST', credentials:'same-origin', headers:{'Content-Type':'application/json'}, body:'{}'});
      if (!response.ok) throw new Error((await response.json().catch(()=>({}))).detail || 'Email scan failed');
      renderSuggestions((await response.json()).suggestions || []);
      button.textContent = 'Scan complete';
    } catch (error) { button.textContent = error.message; }
    setTimeout(() => { button.disabled=false; button.textContent='Scan email'; }, 2500);
  });
  loadDashboard().catch(() => {});
  window.addEventListener('focus', loadDashboard);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) loadDashboard(); });
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initDashboard, {once:true});
else initDashboard();
