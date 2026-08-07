import { api } from './api.js';
import { state } from './state.js';
import { $, $$, esc, fmtTime } from './utils.js';
import { applySettingsVisibility, loadAppSettings, loadSettings } from './settings.js';
import { openSttText, openSummary } from './modals.js';
import { loadSummaries } from './summaries.js';
import { loadLogs, updateLogMenuState } from './logs.js';

const _filter = { query: '', completion: 'all' };
let _ws = null;

// ═══════════════════════════════════════════════════════════════
// 페이지 라우팅
// ═══════════════════════════════════════════════════════════════
function navigate(page) {
  state.currentPage = page;
  $$('.page').forEach(el => el.classList.add('hidden'));
  const target = $(`#page-${page}`);
  if (target) {
    target.classList.remove('hidden');
    target.classList.add('fade-in');
  }
  $$('.nav-item').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.page === page);
  });
  updateLogMenuState(page === 'logs');
  if (page === 'dashboard') loadStats();
  if (page === 'courses' && state.selectedTerm === null && state.courses.length === 0) loadCourses();
  if (page === 'courses') loadTerms();
  if (page === 'settings') loadSettings();
  if (page === 'logs') loadLogs();
  if (page === 'summaries') loadSummaries();
}

// ═══════════════════════════════════════════════════════════════
// 인증
// ═══════════════════════════════════════════════════════════════
async function checkAuth() {
  try {
    const res = await api('GET', '/api/auth/status');
    if (res.authenticated) {
      showApp(res.user_id);
    } else {
      showLogin();
    }
  } catch {
    showLogin();
  }
}

function showLogin() {
  $('#page-login').classList.remove('hidden');
  $('#app-shell').classList.add('hidden');
  $('#input-user-id').value = '';
  $('#input-password').value = '';
  $('#login-error').classList.add('hidden');
  state.deadlineChecked = false;
  state.userId = '';
  stopStatusWs();
  stopAllDownloadTaskPolling();
}

async function checkVersion() {
  try {
    const { current, update_available, latest } = await api('GET', '/api/version');
    const el = $('#version-current');
    if (el) el.textContent = `v${current}`;
    if (update_available && latest) {
      $('#version-latest-text').textContent = latest;
      $('#version-badge')?.classList.remove('hidden');
    }
  } catch {}
}

function showApp(userId) {
  state.userId = userId;
  $('#page-login').classList.add('hidden');
  $('#app-shell').classList.remove('hidden');
  $('#sidebar-user-id').textContent = userId;
  loadAppSettings()
    .then(s => {
      _checkSettingsBanner(s);
      if (!state.deadlineChecked) {
        state.deadlineChecked = true;
        api('POST', '/api/deadline/check').catch(() => {});
      }
    })
    .catch(() => {});
  navigate('dashboard');
  startStatusWs();
  checkVersion().catch(() => {});
}

// 로그인 폼
$('#login-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const userId = $('#input-user-id').value.trim();
  const password = $('#input-password').value;
  if (!userId || !password) {
    $('#login-error').textContent = '학번과 비밀번호를 모두 입력하세요.';
    $('#login-error').classList.remove('hidden');
    return;
  }

  const btn = $('#btn-login');
  btn.disabled = true;
  btn.textContent = '로그인 중...';
  $('#login-error').classList.add('hidden');

  try {
    const res = await api('POST', '/api/auth/login', { user_id: userId, password }, 60000);
    showApp(res.user_id);
  } catch (err) {
    $('#login-error').textContent = err.message;
    $('#login-error').classList.remove('hidden');
    alert(err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = '로그인';
  }
});

// 로그아웃
$('#btn-logout').addEventListener('click', async () => {
  await api('POST', '/api/auth/logout').catch(() => {});
  state.courses = [];
  showLogin();
});

// ═══════════════════════════════════════════════════════════════
// 재생 폴링
// ═══════════════════════════════════════════════════════════════
function startPolling() {
  stopPolling();
  updatePlayerUI(); // 즉시 1회
  state.pollingTimer = setInterval(updatePlayerUI, 2000);
}

function stopPolling() {
  if (state.pollingTimer) { clearInterval(state.pollingTimer); state.pollingTimer = null; }
}

function startStatusWs() {
  stopStatusWs();
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  _ws = new WebSocket(`${proto}//${location.host}/ws/status`);
  _ws.onmessage = (e) => {
    try {
      const data = JSON.parse(e.data);
      if (data.player) _applyPlayerStatus(data.player);
      if (data.auto) _applyAutoStatus(data.auto);
    } catch {}
  };
  _ws.onclose = () => {
    _ws = null;
    if (state.userId) setTimeout(() => { startPolling(); startAutoPolling(); }, 3000);
  };
  _ws.onerror = () => {};
}

function stopStatusWs() {
  if (_ws) { try { _ws.close(); } catch {} _ws = null; }
  stopPolling();
  stopAutoPolling();
}

function _applyPlayerStatus(s) {
  const active = $('#player-active');
  const idle = $('#player-idle');
  const message = $('#player-message');
  const messageTitle = $('#player-message-title');
  const messageBody = $('#player-message-body');
  const messageLog = $('#player-message-log');
  const status = s.status || (s.is_playing ? 'playing' : 'idle');
  const previousStatus = state.lastPlayerStatus;
  state.lastPlayerStatus = status;

  if (s.is_playing) {
    active.classList.remove('hidden');
    idle.classList.add('hidden');
    message.classList.add('hidden');
    $('#player-course-name').textContent = s.course_name || '';
    $('#player-lecture-title').textContent = s.lecture_title || '';
    $('#player-week').textContent = s.week_label || '';
    $('#player-pct-value').textContent = Math.round(s.progress_pct);
    $('#player-time').textContent = `${fmtTime(s.current)} / ${fmtTime(s.duration)}`;
    $('#player-bar').style.width = `${s.progress_pct}%`;
  } else {
    active.classList.add('hidden');
    idle.classList.remove('hidden');
    $('#player-bar').style.width = '0%';

    message.classList.add('hidden');
    messageTitle.textContent = '';
    messageBody.textContent = '';
    messageLog.classList.add('hidden');
    messageLog.textContent = '';

    if (s.error) {
      message.className = 'mt-5 px-4 py-3 rounded-xl border text-sm bg-red-500/10 border-red-500/30 text-red-300';
      messageTitle.textContent = '재생 실패';
      messageBody.textContent = s.error;
      if (s.log_path) {
        messageLog.textContent = `로그 저장: ${s.log_path}`;
        messageLog.classList.remove('hidden');
      }
    } else if (status === 'completed' && s.lecture_title) {
      message.className = 'mt-5 px-4 py-3 rounded-xl border text-sm bg-emerald-500/10 border-emerald-500/30 text-emerald-300';
      messageTitle.textContent = '재생 완료';
      messageBody.textContent = `${s.lecture_title} 강의 재생이 완료되었습니다.`;
      if (s.refresh_recommended) {
        messageLog.textContent = '강의 목록이 자동으로 업데이트되지 않았습니다. 강의 목록 탭의 새로고침 버튼을 눌러주세요.';
        messageLog.classList.remove('hidden');
      }
    } else if (status === 'stopped' && s.lecture_title) {
      message.className = 'mt-5 px-4 py-3 rounded-xl border text-sm bg-amber-500/10 border-amber-500/30 text-amber-300';
      messageTitle.textContent = '재생 중지됨';
      messageBody.textContent = `${s.lecture_title} 강의 재생이 중지되었습니다.`;
    }

    if (!messageTitle.textContent && !messageBody.textContent) {
      message.classList.add('hidden');
    } else {
      message.classList.remove('hidden');
    }
  }

  if (previousStatus === 'playing' && status === 'completed') {
    loadStats();
    state.courses = [];
    if (state.currentPage === 'courses') loadCourses();
    if (s.auto_download_task_id && state.autoDownloadStartedFor !== s.auto_download_task_id) {
      state.autoDownloadStartedFor = s.auto_download_task_id;
      _pollAutoDownloadTask(s.auto_download_task_id, $('#player-auto-download-log'));
    }
  }
}

async function updatePlayerUI() {
  try { _applyPlayerStatus(await api('GET', '/api/player/status')); } catch {}
}

// 재생 중지
$('#btn-stop').addEventListener('click', async () => {
  await api('POST', '/api/player/stop').catch(() => {});
  updatePlayerUI();
});

// ═══════════════════════════════════════════════════════════════
// 통계
// ═══════════════════════════════════════════════════════════════
const _STAT_IDS = ['stat-pending-videos', 'stat-pending-assignments', 'stat-pending-quizzes'];

function _setStatContent(html) {
  _STAT_IDS.forEach(id => { const el = $('#' + id); if (el) el.innerHTML = html; });
}

async function loadStats() {
  _setStatContent('<i class="fa-solid fa-spinner fa-spin text-lg opacity-40"></i>');
  try {
    const s = await api('GET', '/api/courses/stats');
    renderStats(s);
  } catch (err) {
    _setStatContent('<span class="text-slate-500 text-lg">—</span>');
    const errEl = $('#stats-error');
    if (errEl) {
      errEl.textContent = err.message || '강의 정보를 불러오지 못했습니다.';
      errEl.classList.remove('hidden');
    }
  }
}

function renderStats(stats) {
  $('#stats-error')?.classList.add('hidden');
  const pendingVideos = stats.pending_videos ?? (stats.total_videos - stats.completed_videos);
  $('#stat-pending-videos').textContent = pendingVideos;
  $('#stat-pending-assignments').textContent = stats.pending_assignments ?? 0;
  $('#stat-pending-quizzes').textContent = stats.pending_quizzes ?? 0;
}

// ═══════════════════════════════════════════════════════════════
// 학기 선택
// ═══════════════════════════════════════════════════════════════
async function loadTerms() {
  try {
    const { current_term, past_terms } = await api('GET', '/api/courses/terms');
    state.currentTerm = current_term;

    if (past_terms.length === 0) {
      $('#term-selector').classList.add('hidden');
      return;
    }

    const tabs = $('#term-tabs');
    tabs.innerHTML = '';
    const allTerms = [
      { label: current_term || '현재 학기', value: null },
      ...past_terms.map(t => ({ label: t, value: t })),
    ];
    allTerms.forEach(({ label, value }) => {
      const btn = document.createElement('button');
      const active = value === state.selectedTerm;
      btn.className = `term-tab px-4 py-2 rounded-xl text-sm font-medium transition-all border ${
        active
          ? 'bg-indigo-500 text-white border-indigo-500'
          : 'bg-slate-800 text-slate-400 border-slate-700 hover:border-indigo-500/50'
      }`;
      btn.textContent = label;
      btn.dataset.term = value ?? '';
      btn.addEventListener('click', () => switchTerm(value));
      tabs.appendChild(btn);
    });
    $('#term-selector').classList.remove('hidden');
  } catch {}
}

function _updateTermTabs() {
  $$('.term-tab').forEach(btn => {
    const active = (btn.dataset.term || null) === state.selectedTerm;
    btn.className = `term-tab px-4 py-2 rounded-xl text-sm font-medium transition-all border ${
      active
        ? 'bg-indigo-500 text-white border-indigo-500'
        : 'bg-slate-800 text-slate-400 border-slate-700 hover:border-indigo-500/50'
    }`;
  });
}

function switchTerm(term) {
  state.selectedTerm = term;
  _updateTermTabs();
  if (term === null) {
    state.courses = [];
    loadCourses();
  } else {
    loadSummaryTerm(term);
  }
}

async function loadSummaryTerm(term) {
  const list = $('#courses-list');
  list.innerHTML = `<div class="flex items-center justify-center py-16"><i class="fa-solid fa-spinner fa-spin text-indigo-400 text-2xl"></i></div>`;
  try {
    const { summaries } = await api('GET', '/api/summaries');
    const items = summaries.filter(s => s.term === term);
    if (!items.length) {
      list.innerHTML = `
        <div class="flex flex-col items-center justify-center py-16 text-center">
          <div class="w-14 h-14 bg-slate-800 rounded-2xl flex items-center justify-center mb-4">
            <i class="fa-solid fa-folder-open text-slate-500 text-xl"></i>
          </div>
          <p class="font-semibold text-slate-300">${esc(term)}</p>
          <p class="text-sm text-slate-500 mt-1">이 학기의 저장된 요약이 없습니다.</p>
        </div>`;
      return;
    }
    // 과목별 그룹핑
    const byCourse = {};
    for (const s of items) {
      if (!byCourse[s.course]) byCourse[s.course] = [];
      byCourse[s.course].push(s);
    }
    list.innerHTML = '';
    for (const [course, courseItems] of Object.entries(byCourse)) {
      const card = document.createElement('div');
      card.className = 'bg-[#1E293B] rounded-2xl border border-slate-700 overflow-hidden';
      card.innerHTML = `
        <div class="px-5 py-4 border-b border-slate-700 flex items-center justify-between">
          <div>
            <h3 class="font-semibold text-white">${esc(course)}</h3>
            <p class="text-xs text-slate-500 mt-0.5">요약 ${courseItems.length}개</p>
          </div>
          <div class="w-8 h-8 bg-indigo-500/10 rounded-xl flex items-center justify-center">
            <i class="fa-solid fa-file-lines text-indigo-400 text-sm"></i>
          </div>
        </div>
        <div class="divide-y divide-slate-700/50">
          ${courseItems.map(s => `
            <div class="flex items-center justify-between px-5 py-3 hover:bg-slate-800/50 cursor-pointer transition-colors lecture-row"
              data-summary-id="${esc(s.id)}" data-title="${esc(s.title)}" data-week="${esc(s.week)}">
              <div>
                <p class="text-sm text-slate-200">${esc(s.title)}</p>
                <p class="text-xs text-slate-500 mt-0.5">${esc(s.week)}</p>
              </div>
              <span class="shrink-0 text-xs px-2 py-0.5 rounded-full bg-indigo-500/10 border border-indigo-500/20 text-indigo-400">요약 보기</span>
            </div>`).join('')}
        </div>`;
      card.querySelectorAll('[data-summary-id]').forEach(row => {
        row.addEventListener('click', () => openSummary(row.dataset.summaryId, row.dataset.title, row.dataset.week));
      });
      list.appendChild(card);
    }
  } catch (err) {
    list.innerHTML = `<p class="text-red-400 text-sm px-2">${esc(err.message)}</p>`;
  }
}

// ═══════════════════════════════════════════════════════════════
// 강의 목록
// ═══════════════════════════════════════════════════════════════
async function loadCourses() {
  const list = $('#courses-list');
  const loading = $('#courses-loading');
  list.innerHTML = '';
  loading.classList.remove('hidden');
  loading.classList.add('flex');

  try {
    state.courses = await api('GET', '/api/courses');
    renderCourseCards(state.courses);
  } catch (err) {
    list.innerHTML = `<p class="text-red-400 text-sm">${esc(err.message)}</p>`;
  } finally {
    loading.classList.add('hidden');
    loading.classList.remove('flex');
  }
}

function renderCourseCards(courses) {
  const list = $('#courses-list');
  list.innerHTML = '';

  if (courses.length === 0) {
    list.innerHTML = '<p class="text-slate-400 text-sm text-center py-12">수강 중인 과목이 없습니다.</p>';
    return;
  }

  courses.forEach(course => {
    const pendingVideos = course.pending_videos ?? 0;
    const pendingAssignments = course.pending_assignments ?? 0;
    const pendingQuizzes = course.pending_quizzes ?? 0;
    const hasPending = pendingVideos + pendingAssignments + pendingQuizzes > 0;

    const card = document.createElement('div');
    card.className = 'bg-[#1E293B] rounded-2xl border border-slate-700 p-5 cursor-pointer hover:border-indigo-500/50 transition-all';
    card.setAttribute('role', 'button');
    card.setAttribute('tabindex', '0');
    card.setAttribute('aria-label', `${course.name} 주차별 강의 보기`);
    card.innerHTML = `
      <div class="flex items-start justify-between gap-4">
        <div class="flex-1 min-w-0">
          <h3 class="font-semibold text-white truncate">${esc(course.name)}</h3>
          <p class="text-xs text-slate-500 mt-0.5">${esc(course.term)}</p>
        </div>
        <div class="shrink-0 text-right">
          <span class="text-lg font-black ${hasPending ? 'text-amber-400' : 'text-emerald-400'}">${hasPending ? '진행 필요' : '완료'}</span>
        </div>
      </div>
      <div class="mt-4 flex flex-wrap items-center gap-2 text-xs text-slate-400">
        <span class="inline-flex items-center gap-1.5 px-2.5 py-1 bg-slate-900 border border-slate-700 rounded-full">
          <i class="fa-solid fa-video text-blue-400"></i> 미시청 영상 ${pendingVideos}개
        </span>
        <span class="inline-flex items-center gap-1.5 px-2.5 py-1 bg-slate-900 border border-slate-700 rounded-full">
          <i class="fa-solid fa-clipboard-list text-emerald-400"></i> 과제 ${pendingAssignments}개
        </span>
        <span class="inline-flex items-center gap-1.5 px-2.5 py-1 bg-slate-900 border border-slate-700 rounded-full">
          <i class="fa-solid fa-circle-question text-amber-400"></i> 퀴즈 ${pendingQuizzes}개
        </span>
      </div>
    `;
    const openDetail = () => loadCourseDetail(course.id, course.name);
    card.addEventListener('click', openDetail);
    card.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        openDetail();
      }
    });
    list.appendChild(card);
  });
}

async function loadCourseDetail(courseId, courseName) {
  const weeks = $('#detail-weeks');
  const settings = await loadAppSettings().catch(() => state.settings);
  state.currentCourseId = courseId;
  state.currentCourseName = courseName;
  navigate('course-detail');
  weeks.innerHTML = '<div class="p-6 text-slate-400 text-sm"><i class="fa-solid fa-spinner fa-spin mr-2"></i>불러오는 중...</div>';
  $('#detail-course-name').textContent = courseName;
  $('#detail-professors').textContent = '';
  _filter.query = '';
  _filter.completion = 'all';
  const fq = $('#filter-query'); if (fq) fq.value = '';
  const fc = $('#filter-completion'); if (fc) fc.value = 'all';

  try {
    const data = await api('GET', `/api/courses/${courseId}`);
    if (state.currentCourseId !== courseId) return;
    $('#detail-professors').textContent = data.professors || '';
    state.currentCourseDetail = data;
    _renderCourseWeeks(data, courseId);
  } catch (err) {
    if (state.currentCourseId === courseId) {
      weeks.innerHTML = `<div class="p-6 text-red-400 text-sm">${esc(err.message)}</div>`;
    }
  }
}

function _renderCourseWeeks(data, courseId) {
  const weeks = $('#detail-weeks');
  weeks.innerHTML = '';

  if (!data.weeks || data.weeks.length === 0) {
    weeks.innerHTML = '<div class="p-6 text-slate-400 text-sm">강의 정보가 없습니다.</div>';
    return;
  }

  const q = _filter.query.toLowerCase();
  let renderedSections = 0;
  data.weeks.forEach(week => {
    const videos = week.lectures.filter(l => {
      if (!l.is_video) return false;
      if (_filter.completion === 'pending' && l.completion === 'completed') return false;
      if (_filter.completion === 'completed' && l.completion !== 'completed') return false;
      if (q && !l.title.toLowerCase().includes(q)) return false;
      return true;
    });
    if (videos.length === 0) return;
    renderedSections += 1;

    const section = document.createElement('div');
    section.className = 'px-6 py-4';
    section.innerHTML = `
      <div class="flex items-center justify-between mb-3">
        <h4 class="text-sm font-bold text-slate-300">${esc(week.title)}</h4>
        ${week.pending_count > 0 ? `<span class="text-xs px-2 py-0.5 bg-amber-500/10 text-amber-400 border border-amber-500/20 rounded-full">${week.pending_count} 미수강</span>` : ''}
      </div>
      <div class="flex flex-col gap-2" data-week-rows></div>
    `;
    const rowContainer = section.querySelector('[data-week-rows]');
    videos.forEach(lec => {
      const comp = lec.completion === 'completed' ? 'completed' : 'incomplete';
      const row = document.createElement('div');
      row.className = 'lecture-row flex items-center gap-3 px-3 py-2.5 rounded-xl transition-all cursor-pointer';
      row.dataset.url = lec.full_url;
      row.dataset.title = lec.title;
      row.dataset.week = lec.week_label;
      row.dataset.course = courseId;
      row.innerHTML = `
        <div class="w-7 h-7 rounded-lg ${comp === 'completed' ? 'bg-emerald-500/10' : 'bg-slate-800'} flex items-center justify-center shrink-0">
          <i class="fa-solid ${comp === 'completed' ? 'fa-circle-check text-emerald-400' : 'fa-play text-slate-500'} text-xs"></i>
        </div>
        <div class="flex-1 min-w-0">
          <p class="text-sm text-slate-200 truncate"></p>
          ${lec.duration ? `<p class="text-xs text-slate-500"></p>` : ''}
        </div>
        <span class="completion-badge ${comp} shrink-0 text-xs px-2 py-0.5 border rounded-full font-medium">
          ${comp === 'completed' ? '완료' : '미수강'}
        </span>
        <div class="shrink-0 flex items-center gap-2" data-actions></div>
      `;
      row.querySelector('.flex-1 p:first-child').textContent = lec.title;
      if (lec.duration) row.querySelector('.flex-1 p:last-child').textContent = lec.duration;
      const actions = row.querySelector('[data-actions]');
      {
        const playBtn = document.createElement('button');
        playBtn.className = lec.needs_watch
          ? 'btn-play-lec px-3 py-1 bg-indigo-500 hover:bg-indigo-400 text-white text-xs font-bold rounded-lg transition-all'
          : 'btn-play-lec px-3 py-1 bg-slate-700 hover:bg-slate-600 text-slate-300 text-xs font-bold rounded-lg transition-all';
        playBtn.textContent = lec.needs_watch ? '재생' : '다시 재생';
        actions.appendChild(playBtn);
      }
      const dlInfo = lec.download_info || {};
      const aiEnabled = state.settings.AI_ENABLED === 'true';
      if (state.settings.DOWNLOAD_ENABLED === 'true' || comp === 'completed') {
        if (dlInfo.exists) {
          const badge = document.createElement('span');
          badge.className = 'shrink-0 px-2 py-0.5 bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 text-xs rounded-full font-medium';
          badge.textContent = '다운로드됨';
          actions.appendChild(badge);
          if (aiEnabled && !(lec.summary && lec.summary.available)) {
            const sumFromFileBtn = document.createElement('button');
            sumFromFileBtn.className = 'btn-summarize-from-file px-3 py-1 bg-violet-500/20 hover:bg-violet-500/30 border border-violet-500/30 text-violet-300 text-xs font-bold rounded-lg transition-all';
            sumFromFileBtn.textContent = 'AI 요약';
            sumFromFileBtn.dataset.courseId = courseId;
            sumFromFileBtn.dataset.lectureTitle = lec.title;
            sumFromFileBtn.dataset.weekLabel = lec.week_label;
            actions.appendChild(sumFromFileBtn);
          }
        } else {
          const downloadBtn = document.createElement('button');
          downloadBtn.className = 'btn-download-lec px-3 py-1 bg-sky-500 hover:bg-sky-400 text-white text-xs font-bold rounded-lg transition-all';
          downloadBtn.textContent = '영상 다운로드';
          actions.appendChild(downloadBtn);
          const downloadStatus = document.createElement('span');
          downloadStatus.className = 'download-status hidden text-xs text-slate-400';
          actions.appendChild(downloadStatus);

          // 검색/필터 재렌더링으로 이 row가 새로 만들어졌어도 진행 중인 다운로드가 있으면 폴링을 다시 붙인다.
          const activeTaskId = state.activeDownloads[lec.full_url];
          if (activeTaskId) {
            row.dataset.downloadTaskId = activeTaskId;
            updateDownloadButton(downloadBtn, '다운로드 중', true);
            stopDownloadTaskPolling(activeTaskId);
            pollDownloadTask(activeTaskId, row, downloadBtn);
            state.downloadTaskTimers[activeTaskId] = setInterval(() => {
              pollDownloadTask(activeTaskId, row, downloadBtn);
            }, 1500);
          }
        }
      }
      if (lec.summary && lec.summary.available && lec.summary.id) {
        row.dataset.summaryId = lec.summary.id;
        const summaryBtn = document.createElement('button');
        summaryBtn.className = 'btn-summary-lec px-3 py-1 bg-emerald-500 hover:bg-emerald-400 text-white text-xs font-bold rounded-lg transition-all';
        summaryBtn.textContent = '요약 내용 보기';
        actions.appendChild(summaryBtn);
      }
      rowContainer.appendChild(row);
    });
    weeks.appendChild(section);
  });

  if (renderedSections === 0) {
    weeks.innerHTML = '<div class="p-6 text-slate-400 text-sm">검색 결과가 없습니다.</div>';
    return;
  }

  $$('.btn-play-lec', weeks).forEach(btn => {
    btn.addEventListener('click', async (e) => {
      e.stopPropagation();
      const row = btn.closest('.lecture-row');
      await playLecture(row.dataset.course, row.dataset.url, row.dataset.title, row.dataset.week);
    });
  });
  $$('.btn-download-lec', weeks).forEach(btn => {
    btn.addEventListener('click', async (e) => {
      e.stopPropagation();
      const row = btn.closest('.lecture-row');
      await startDownload(row, btn);
    });
  });
  $$('.btn-summarize-from-file', weeks).forEach(btn => {
    btn.addEventListener('click', async (e) => {
      e.stopPropagation();
      const row = btn.closest('.lecture-row');
      await startSummarizeFromFile(btn.dataset.courseId, btn.dataset.lectureTitle, btn.dataset.weekLabel, row, btn);
    });
  });
  $$('.btn-summary-lec', weeks).forEach(btn => {
    btn.addEventListener('click', async (e) => {
      e.stopPropagation();
      const row = btn.closest('.lecture-row');
      await openSummary(row.dataset.summaryId, row.dataset.title, row.dataset.week);
    });
  });
}

function setDownloadStatus(row, message, tone = 'slate') {
  const status = row.querySelector('.download-status');
  if (!status) return;
  const toneClass = {
    slate: 'text-slate-400',
    sky: 'text-sky-400',
    emerald: 'text-emerald-400',
    amber: 'text-amber-400',
    red: 'text-red-400',
  }[tone] || 'text-slate-400';
  status.textContent = message;
  status.className = `download-status text-xs ${toneClass}`;
}

function stopDownloadTaskPolling(taskId) {
  if (state.downloadTaskTimers[taskId]) {
    clearInterval(state.downloadTaskTimers[taskId]);
    delete state.downloadTaskTimers[taskId];
  }
}

function stopAllDownloadTaskPolling() {
  Object.keys(state.downloadTaskTimers).forEach(stopDownloadTaskPolling);
  state.activeDownloads = {};
}

function updateDownloadButton(button, text, disabled) {
  button.textContent = text;
  button.disabled = disabled;
  button.classList.toggle('opacity-60', disabled);
  button.classList.toggle('cursor-not-allowed', disabled);
}

function _clearActiveDownload(row, taskId) {
  if (state.activeDownloads[row.dataset.url] === taskId) {
    delete state.activeDownloads[row.dataset.url];
  }
}

async function pollDownloadTask(taskId, row, button) {
  try {
    const task = await api('GET', `/api/tasks/${taskId}`);
    const pct = Number.isFinite(task.progress_pct) ? ` ${Math.round(task.progress_pct)}%` : '';
    if (task.status === 'completed') {
      stopDownloadTaskPolling(taskId);
      _clearActiveDownload(row, taskId);
      const files = (task.result?.files || []).filter(f => f.deleted !== 'true');
      const fileText = files.length
        ? '완료: ' + files.map(f => f.path.split('/').pop()).join(', ')
        : '다운로드 완료';
      setDownloadStatus(row, fileText, 'emerald');
      updateDownloadButton(button, '다시 다운로드', false);
      // STT 결과 보기 버튼 동적 추가
      const stt = task.result?.stt;
      if (stt?.status === 'completed' && !row.querySelector('.btn-stt-view')) {
        const sttBtn = document.createElement('button');
        sttBtn.className = 'btn-stt-view px-3 py-1 bg-sky-500/20 hover:bg-sky-500/30 border border-sky-500/30 text-sky-300 text-xs font-bold rounded-lg transition-all';
        sttBtn.textContent = 'STT 보기';
        sttBtn.addEventListener('click', (e) => {
          e.stopPropagation();
          openSttText(taskId, row.dataset.title);
        });
        row.querySelector('[data-actions]')?.appendChild(sttBtn);
      }
      // AI 요약 버튼 동적 추가 (STT 완료 && 요약 없음 && AI 활성화)
      const summary = task.result?.summary;
      const aiEnabled = state.settings?.AI_ENABLED === 'true';
      if (stt?.status === 'completed' && aiEnabled && !row.querySelector('.btn-summarize') && !row.querySelector('.btn-summary-view')) {
        if (!summary || summary.status !== 'completed') {
          const sumBtn = document.createElement('button');
          sumBtn.className = 'btn-summarize px-3 py-1 bg-violet-500/20 hover:bg-violet-500/30 border border-violet-500/30 text-violet-300 text-xs font-bold rounded-lg transition-all';
          sumBtn.textContent = '요약 실행';
          sumBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            startSummarize(taskId, row, sumBtn);
          });
          row.querySelector('[data-actions]')?.appendChild(sumBtn);
        } else if (summary.status === 'completed' && summary.summary_id) {
          const viewBtn = document.createElement('button');
          viewBtn.className = 'btn-summary-view px-3 py-1 bg-violet-500/20 hover:bg-violet-500/30 border border-violet-500/30 text-violet-300 text-xs font-bold rounded-lg transition-all';
          viewBtn.textContent = '요약 보기';
          viewBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            openSummary(summary.summary_id, row.dataset.title, row.dataset.week);
          });
          row.querySelector('[data-actions]')?.appendChild(viewBtn);
        }
      }
      return;
    }
    if (task.status === 'failed') {
      stopDownloadTaskPolling(taskId);
      _clearActiveDownload(row, taskId);
      setDownloadStatus(row, task.error || '다운로드 실패', 'red');
      updateDownloadButton(button, '재시도', false);
      return;
    }
    if (task.status === 'cancelled') {
      stopDownloadTaskPolling(taskId);
      _clearActiveDownload(row, taskId);
      setDownloadStatus(row, '다운로드가 취소되었습니다.', 'amber');
      updateDownloadButton(button, '다운로드', false);
      return;
    }
    setDownloadStatus(row, `${task.message || '다운로드 중...'}${pct}`, 'sky');
  } catch (err) {
    stopDownloadTaskPolling(taskId);
    _clearActiveDownload(row, taskId);
    setDownloadStatus(row, err.message, 'red');
    updateDownloadButton(button, '재시도', false);
  }
}

async function startDownload(row, button) {
  try {
    updateDownloadButton(button, '시작 중...', true);
    setDownloadStatus(row, '다운로드 작업을 준비하는 중입니다.', 'sky');
    const taskId = await startDownloadPayload({
      course_id: row.dataset.course,
      lecture_url: row.dataset.url,
      lecture_title: row.dataset.title,
      week_label: row.dataset.week,
    });
    row.dataset.downloadTaskId = taskId;
    state.activeDownloads[row.dataset.url] = taskId;
    updateDownloadButton(button, '다운로드 중', true);
    await pollDownloadTask(taskId, row, button);
    stopDownloadTaskPolling(taskId);
    state.downloadTaskTimers[taskId] = setInterval(() => {
      pollDownloadTask(taskId, row, button);
    }, 1500);
  } catch (err) {
    setDownloadStatus(row, err.message, 'red');
    updateDownloadButton(button, '재시도', false);
  }
}

async function startDownloadPayload(payload) {
  const res = await api('POST', '/api/tasks/download', payload);
  return res.task_id;
}

async function startSummarize(sourceTaskId, row, button) {
  button.disabled = true;
  button.textContent = '요약 중...';
  button.classList.add('opacity-50', 'cursor-not-allowed');
  try {
    const res = await api('POST', `/api/tasks/${sourceTaskId}/summarize`);
    const summarizeTaskId = res.task_id;
    const poll = async () => {
      try {
        const task = await api('GET', `/api/tasks/${summarizeTaskId}`);
        if (task.status === 'completed') {
          stopDownloadTaskPolling(summarizeTaskId);
          button.remove();
          const summaryId = task.result?.summary_id;
          if (summaryId) {
            const viewBtn = document.createElement('button');
            viewBtn.className = 'btn-summary-view px-3 py-1 bg-violet-500/20 hover:bg-violet-500/30 border border-violet-500/30 text-violet-300 text-xs font-bold rounded-lg transition-all';
            viewBtn.textContent = '요약 보기';
            viewBtn.addEventListener('click', (e) => {
              e.stopPropagation();
              openSummary(summaryId, row.dataset.title, row.dataset.week);
            });
            row.querySelector('[data-actions]')?.appendChild(viewBtn);
          }
          return;
        }
        if (task.status === 'failed' || task.status === 'cancelled') {
          stopDownloadTaskPolling(summarizeTaskId);
          button.disabled = false;
          button.textContent = '요약 재시도';
          button.classList.remove('opacity-50', 'cursor-not-allowed');
          return;
        }
        state.downloadTaskTimers[summarizeTaskId] = setTimeout(poll, 1500);
      } catch {
        stopDownloadTaskPolling(summarizeTaskId);
        button.disabled = false;
        button.textContent = '요약 재시도';
        button.classList.remove('opacity-50', 'cursor-not-allowed');
      }
    };
    poll();
  } catch (err) {
    button.disabled = false;
    button.textContent = '요약 실행';
    button.classList.remove('opacity-50', 'cursor-not-allowed');
    alert(`요약 시작 실패: ${err.message}`);
  }
}

async function startSummarizeFromFile(courseId, lectureTitle, weekLabel, row, button) {
  button.disabled = true;
  button.textContent = '요약 중...';
  button.classList.add('opacity-50', 'cursor-not-allowed');
  try {
    const res = await api('POST', '/api/tasks/summarize-from-file', {
      course_id: courseId,
      lecture_title: lectureTitle,
      week_label: weekLabel,
    });
    const taskId = res.task_id;
    const poll = async () => {
      try {
        const task = await api('GET', `/api/tasks/${taskId}`);
        if (task.status === 'completed') {
          stopDownloadTaskPolling(taskId);
          const summaryId = task.result?.summary_id;
          button.remove();
          if (summaryId) {
            const viewBtn = document.createElement('button');
            viewBtn.className = 'btn-summary-view px-3 py-1 bg-violet-500/20 hover:bg-violet-500/30 border border-violet-500/30 text-violet-300 text-xs font-bold rounded-lg transition-all';
            viewBtn.textContent = '요약 보기';
            viewBtn.addEventListener('click', (e) => {
              e.stopPropagation();
              openSummary(summaryId, lectureTitle, weekLabel);
            });
            row.querySelector('[data-actions]')?.appendChild(viewBtn);
          }
          return;
        }
        if (task.status === 'failed' || task.status === 'cancelled') {
          stopDownloadTaskPolling(taskId);
          button.disabled = false;
          button.textContent = 'AI 요약 재시도';
          button.classList.remove('opacity-50', 'cursor-not-allowed');
          return;
        }
        button.textContent = task.message || '요약 중...';
        state.downloadTaskTimers[taskId] = setTimeout(poll, 1500);
      } catch {
        stopDownloadTaskPolling(taskId);
        button.disabled = false;
        button.textContent = 'AI 요약 재시도';
        button.classList.remove('opacity-50', 'cursor-not-allowed');
      }
    };
    poll();
  } catch (err) {
    button.disabled = false;
    button.textContent = 'AI 요약';
    button.classList.remove('opacity-50', 'cursor-not-allowed');
    alert(`요약 시작 실패: ${err.message}`);
  }
}

function _pollAutoDownloadTask(taskId, messageLog) {
  if (!taskId) return;
  messageLog.textContent = '재생 완료 후 자동 다운로드 진행 중...';
  messageLog.classList.remove('hidden');
  const update = async () => {
    try {
      const task = await api('GET', `/api/tasks/${taskId}`);
      const pct = Number.isFinite(task.progress_pct) ? ` ${Math.round(task.progress_pct)}%` : '';
      if (task.status === 'completed') {
        stopDownloadTaskPolling(taskId);
        const files = (task.result?.files || []).filter(f => f.deleted !== 'true');
        messageLog.textContent = files.length
          ? '자동 다운로드 완료: ' + files.map(f => f.path.split('/').pop()).join(', ')
          : '자동 다운로드 완료';
        return;
      }
      if (task.status === 'failed') {
        stopDownloadTaskPolling(taskId);
        messageLog.textContent = `자동 다운로드 실패: ${task.error || '알 수 없는 오류'}`;
        return;
      }
      if (task.status === 'cancelled') {
        stopDownloadTaskPolling(taskId);
        messageLog.textContent = '자동 다운로드가 취소되었습니다.';
        return;
      }
      messageLog.textContent = `자동 다운로드 중: ${task.message || task.stage || ''}${pct}`;
    } catch (err) {
      stopDownloadTaskPolling(taskId);
      messageLog.textContent = `자동 다운로드 상태 확인 실패: ${err.message}`;
    }
  };
  update();
  stopDownloadTaskPolling(taskId);
  state.downloadTaskTimers[taskId] = setInterval(update, 1500);
}

async function playLecture(courseId, url, title, weekLabel) {
  try {
    await api('POST', '/api/player/play', {
      course_id: courseId,
      lecture_url: url,
      lecture_title: title,
      week_label: weekLabel,
    });
    navigate('dashboard');
    updatePlayerUI();
  } catch (err) {
    alert(`재생 시작 실패: ${err.message}`);
  }
}

// ── 강의 상세에서 목록으로 돌아가기
$('#btn-back-courses').addEventListener('click', () => {
  navigate('courses');
});

// 요약 상세에서 주차별 강의로 돌아가기
$('#btn-back-course-detail').addEventListener('click', () => {
  navigate(state.currentCourseId ? 'course-detail' : 'courses');
});

// 강의 목록 새로고침
$('#btn-refresh').addEventListener('click', async () => {
  const btn = $('#btn-refresh');
  btn.disabled = true;
  try {
    await api('POST', '/api/courses/refresh');
    state.courses = [];
    await loadCourses();
    await loadStats();
  } catch (err) {
    alert(err.message);
  } finally {
    btn.disabled = false;
  }
});

// 마감 임박 체크
$('#btn-deadline-check').addEventListener('click', async () => {
  const btn = $('#btn-deadline-check');
  btn.disabled = true;
  btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> 확인 중...';
  try {
    const res = await api('POST', '/api/deadline/check');
    const { found, sent, telegram_enabled, items } = res;
    if (found === 0) {
      alert('마감 임박 항목이 없습니다.');
    } else {
      const lines = items.map(item =>
        `[${item.type}] ${item.course} · ${item.title}\n  마감: ${item.end_date} (${item.remaining_hours}시간 남음)`
      ).join('\n\n');
      const tgMsg = telegram_enabled
        ? `\n\n텔레그램으로 ${sent}건 알림 전송됨`
        : '\n\n(텔레그램 미설정 — 알림 미전송)';
      alert(`마감 임박 ${found}건 발견${tgMsg}\n\n${lines}`);
    }
  } catch (err) {
    alert(`마감 확인 실패: ${err.message}`);
  } finally {
    btn.disabled = false;
    btn.innerHTML = '<i class="fa-solid fa-bell"></i> 마감 확인';
  }
});

// 로그 필터 선택 (state 업데이트 + navigate)
function selectLogType(type) {
  state.selectedLogType = type || '';
  navigate('logs');
}

// ═══════════════════════════════════════════════════════════════
// 자동 모드
// ═══════════════════════════════════════════════════════════════
function startAutoPolling() {
  stopAutoPolling();
  updateAutoUI();
  state.autoPollingTimer = setInterval(updateAutoUI, 3000);
}

function stopAutoPolling() {
  if (state.autoPollingTimer) { clearInterval(state.autoPollingTimer); state.autoPollingTimer = null; }
}

function updateScheduleLabel(hours) {
  const sorted = [...hours].sort((a, b) => a - b);
  $('#auto-schedule-label').textContent = sorted.map(h => `${String(h).padStart(2,'0')}시`).join('·');
}

function _applyAutoStatus(s) {
  state.autoEnabled = s.enabled;
  state.autoScheduleHours = s.schedule_hours || [9, 13, 18, 23];

  const toggle = $('#toggle-auto');
  if (toggle.checked !== s.enabled) toggle.checked = s.enabled;

  updateScheduleLabel(state.autoScheduleHours);

  const statusRow = $('#auto-status-row');
  if (s.enabled) {
    statusRow.classList.remove('hidden');
    $('#auto-processed').textContent = s.processed_count || 0;
    if (s.current_lecture) {
      $('#auto-current').classList.remove('hidden');
      $('#auto-current-text').textContent = `${s.current_course} · ${s.current_lecture}`;
    } else {
      $('#auto-current').classList.add('hidden');
    }
    if (s.next_run_at) {
      $('#auto-next-row').classList.remove('hidden');
      $('#auto-next').textContent = s.next_run_at;
    } else {
      $('#auto-next-row').classList.add('hidden');
    }
    if (s.pipeline_stage) {
      $('#auto-pipeline-row').classList.remove('hidden');
      $('#auto-pipeline-text').textContent = s.pipeline_stage;
    } else {
      $('#auto-pipeline-row').classList.add('hidden');
    }
    if (s.error) {
      $('#auto-error').textContent = s.error;
      $('#auto-error').classList.remove('hidden');
    } else {
      $('#auto-error').classList.add('hidden');
    }
  } else {
    statusRow.classList.add('hidden');
  }
}

async function updateAutoUI() {
  try { _applyAutoStatus(await api('GET', '/api/auto/status')); } catch {}
}

function _showAutoWarnings(warnings) {
  const el = $('#auto-warnings');
  if (!el) return;
  if (!warnings || !warnings.length) {
    el.classList.add('hidden');
    el.innerHTML = '';
    return;
  }
  el.innerHTML = warnings.map(w =>
    `<p class="text-xs text-amber-400"><i class="fa-solid fa-triangle-exclamation mr-1"></i>${w}</p>`
  ).join('');
  el.classList.remove('hidden');
}

$('#toggle-auto').addEventListener('change', async (e) => {
  const enabled = e.target.checked;
  try {
    if (enabled) {
      const res = await api('POST', '/api/auto/start', { schedule_hours: state.autoScheduleHours });
      _showAutoWarnings(res.warnings);
    } else {
      await api('POST', '/api/auto/stop');
      _showAutoWarnings([]);
    }
    state.autoEnabled = enabled;
    updateAutoUI();
  } catch (err) {
    e.target.checked = !enabled;
    alert(err.message);
  }
});

// ── 스케줄 모달 ───────────────────────────────────────────────
const _SCHEDULE_PRESETS = [
  [12],
  [9, 21],
  [9, 15, 21],
  [9, 13, 18, 23],
  [8, 11, 14, 18, 22],
  [7, 10, 13, 16, 19, 22],
];

let _scheduleSelected = new Set([9, 13, 18, 23]);

function _isPresetActive(hours) {
  if (hours.length !== _scheduleSelected.size) return false;
  return hours.every(h => _scheduleSelected.has(h));
}

function renderScheduleModal() {
  const presetsEl = $('#schedule-presets');
  presetsEl.innerHTML = '';
  _SCHEDULE_PRESETS.forEach((hours, i) => {
    const btn = document.createElement('button');
    const active = _isPresetActive(hours);
    btn.className = `py-1.5 text-xs font-bold rounded-lg border transition-all ${
      active ? 'bg-indigo-500 border-indigo-500 text-white'
             : 'bg-slate-800 border-slate-700 text-slate-300 hover:border-indigo-500/50'
    }`;
    btn.textContent = `${i + 1}회`;
    if (i === 3) btn.textContent += ' ★';
    btn.addEventListener('click', () => {
      _scheduleSelected = new Set(hours);
      renderScheduleModal();
    });
    presetsEl.appendChild(btn);
  });

  const gridEl = $('#schedule-hours-grid');
  gridEl.innerHTML = '';
  for (let h = 0; h < 24; h++) {
    const btn = document.createElement('button');
    const selected = _scheduleSelected.has(h);
    btn.className = `py-1.5 text-xs font-bold rounded-lg border transition-all ${
      selected ? 'bg-indigo-500 border-indigo-500 text-white'
               : 'bg-slate-800 border-slate-700 text-slate-400 hover:border-indigo-500/50'
    }`;
    btn.textContent = String(h).padStart(2, '0');
    btn.addEventListener('click', () => {
      if (_scheduleSelected.has(h)) {
        if (_scheduleSelected.size > 1) _scheduleSelected.delete(h);
      } else {
        if (_scheduleSelected.size < 6) _scheduleSelected.add(h);
      }
      renderScheduleModal();
    });
    gridEl.appendChild(btn);
  }
}

$('#btn-auto-schedule').addEventListener('click', () => {
  _scheduleSelected = new Set(state.autoScheduleHours);
  renderScheduleModal();
  $('#modal-schedule').classList.remove('hidden');
});

$('#btn-schedule-close').addEventListener('click', () => {
  $('#modal-schedule').classList.add('hidden');
});

$('#modal-schedule').addEventListener('click', (e) => {
  if (e.target === $('#modal-schedule')) $('#modal-schedule').classList.add('hidden');
});

$('#btn-schedule-apply').addEventListener('click', async () => {
  const hours = [..._scheduleSelected].sort((a, b) => a - b);
  state.autoScheduleHours = hours;
  updateScheduleLabel(hours);
  $('#modal-schedule').classList.add('hidden');

  if (state.autoEnabled) {
    try {
      await api('PUT', '/api/auto/schedule', { schedule_hours: hours });
      updateAutoUI();
    } catch (err) {
      alert(`스케줄 업데이트 실패: ${err.message}`);
    }
  }
});

// ═══════════════════════════════════════════════════════════════
// 내비게이션 이벤트
// ═══════════════════════════════════════════════════════════════
$$('.nav-item').forEach(btn => {
  btn.addEventListener('click', () => navigate(btn.dataset.page));
});

$$('.log-filter').forEach(btn => {
  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    selectLogType(btn.dataset.logType || '');
  });
});

$('#btn-refresh-logs').addEventListener('click', () => loadLogs());

// ═══════════════════════════════════════════════════════════════
// 설정 배너 (2-F)
// ═══════════════════════════════════════════════════════════════
function _checkSettingsBanner(settings) {
  const s = settings || state.settings;
  const missing = [];
  const hasAiKey = { gemini: s.HAS_GOOGLE_API_KEY, openai: s.HAS_OPENAI_API_KEY, openrouter: s.HAS_OPENROUTER_API_KEY }[s.AI_AGENT || 'gemini'];
  if (s.AI_ENABLED === 'true' && !hasAiKey) missing.push('AI 요약 API 키 미설정');
  if (s.TELEGRAM_ENABLED === 'true' && !s.HAS_TELEGRAM_BOT_TOKEN) missing.push('텔레그램 봇 토큰 미설정');
  const banner = $('#settings-banner');
  if (!banner) return;
  if (missing.length) {
    $('#settings-banner-msg').textContent = missing.join(' · ');
    banner.classList.remove('hidden');
    banner.classList.add('flex');
  } else {
    banner.classList.add('hidden');
    banner.classList.remove('flex');
  }
}

$('#settings-banner-btn')?.addEventListener('click', () => navigate('settings'));

// ═══════════════════════════════════════════════════════════════
// 미제출 항목 모달 (2-B)
// ═══════════════════════════════════════════════════════════════
async function showPendingItemsModal(type) {
  const modal = $('#pending-modal');
  const content = $('#pending-modal-content');
  $('#pending-modal-label').textContent = type === 'assignments' ? 'Assignments' : 'Quizzes';
  $('#pending-modal-title').textContent = type === 'assignments' ? '미제출 과제 목록' : '미제출 퀴즈 목록';
  content.innerHTML = '<p class="text-slate-400 text-sm p-2"><i class="fa-solid fa-spinner fa-spin mr-2"></i>불러오는 중...</p>';
  modal.classList.remove('hidden');
  modal.classList.add('flex');
  document.body.style.overflow = 'hidden';
  try {
    const res = await api('GET', '/api/courses/pending-items');
    const items = type === 'assignments' ? res.assignments : res.quizzes;
    if (!items || items.length === 0) {
      content.innerHTML = '<p class="text-slate-400 text-sm p-2">미제출 항목이 없습니다.</p>';
      return;
    }
    content.innerHTML = items.map(item => `
      <div class="px-4 py-3 bg-slate-800/50 rounded-xl border border-slate-700">
        <p class="text-sm font-medium text-slate-200">${esc(item.title)}</p>
        <p class="text-xs text-slate-400 mt-0.5">${esc(item.course)}${item.end_date ? ' · 마감: ' + esc(item.end_date) : ''}</p>
      </div>
    `).join('');
  } catch (err) {
    content.innerHTML = `<p class="text-red-400 text-sm p-2">${esc(err.message)}</p>`;
  }
}

function _closePendingModal() {
  $('#pending-modal').classList.add('hidden');
  $('#pending-modal').classList.remove('flex');
  document.body.style.overflow = '';
}

$('#card-stat-assignments')?.addEventListener('click', () => showPendingItemsModal('assignments'));
$('#card-stat-quizzes')?.addEventListener('click', () => showPendingItemsModal('quizzes'));
$('#btn-close-pending-modal')?.addEventListener('click', _closePendingModal);
$('#pending-modal-backdrop')?.addEventListener('click', _closePendingModal);
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && !$('#pending-modal').classList.contains('hidden')) _closePendingModal();
});

// ═══════════════════════════════════════════════════════════════
// 필터 / 검색 (2-E)
// ═══════════════════════════════════════════════════════════════
$('#filter-query')?.addEventListener('input', (e) => {
  _filter.query = e.target.value.trim();
  if (state.currentCourseDetail) _renderCourseWeeks(state.currentCourseDetail, state.currentCourseId);
});

$('#filter-completion')?.addEventListener('change', (e) => {
  _filter.completion = e.target.value;
  if (state.currentCourseDetail) _renderCourseWeeks(state.currentCourseDetail, state.currentCourseId);
});

// ═══════════════════════════════════════════════════════════════
// 초기화
// ═══════════════════════════════════════════════════════════════
checkAuth();
