import { api } from './api.js';
import { state } from './state.js';
import { $, esc } from './utils.js';
import { openSummary, openTranscript } from './modals.js';

// ═══════════════════════════════════════════════════════════════
// 요약 대시보드
// ═══════════════════════════════════════════════════════════════
let _summariesData = [];
let _summariesFilter = '';

export async function loadSummaries() {
  const loading = $('#summaries-loading');
  const list = $('#summaries-list');
  const empty = $('#summaries-empty');
  const filterEl = $('#summaries-filter');

  list.innerHTML = '';
  filterEl.classList.add('hidden');
  empty.classList.add('hidden');
  loading.classList.remove('hidden');
  loading.classList.add('flex');

  try {
    const res = await api('GET', '/api/summaries');
    _summariesData = res.summaries || [];
    _summariesFilter = '';
    renderSummaries();
  } catch (err) {
    list.innerHTML = `<p class="text-red-400 text-sm">${esc(err.message)}</p>`;
  } finally {
    loading.classList.add('hidden');
    loading.classList.remove('flex');
  }
}

function renderSummaries() {
  const list = $('#summaries-list');
  const empty = $('#summaries-empty');
  const filterEl = $('#summaries-filter');
  list.innerHTML = '';

  if (_summariesData.length === 0) {
    filterEl.classList.add('hidden');
    empty.classList.remove('hidden');
    empty.classList.add('flex');
    return;
  }
  empty.classList.add('hidden');

  const courses = [...new Set(_summariesData.map(s => s.course))].sort((a, b) => a.localeCompare(b, 'ko'));
  filterEl.innerHTML = '';
  filterEl.classList.remove('hidden');
  ['', ...courses].forEach(course => {
    const btn = document.createElement('button');
    const active = _summariesFilter === course;
    btn.className = `px-3 py-1.5 rounded-xl border text-xs font-medium transition-all ${
      active
        ? 'bg-indigo-500 border-indigo-500 text-white'
        : 'bg-slate-800 border-slate-700 text-slate-400 hover:border-indigo-500/50'
    }`;
    btn.textContent = course || '전체';
    btn.addEventListener('click', () => { _summariesFilter = course; renderSummaries(); });
    filterEl.appendChild(btn);
  });

  const filtered = _summariesFilter
    ? _summariesData.filter(s => s.course === _summariesFilter)
    : _summariesData;

  if (filtered.length === 0) {
    empty.classList.remove('hidden');
    empty.classList.add('flex');
    return;
  }

  const grouped = {};
  filtered.forEach(s => {
    if (!grouped[s.course]) grouped[s.course] = {};
    if (!grouped[s.course][s.week]) grouped[s.course][s.week] = [];
    grouped[s.course][s.week].push(s);
  });

  Object.entries(grouped).forEach(([course, weeks]) => {
    const section = document.createElement('div');
    section.className = 'bg-[#1E293B] rounded-2xl border border-slate-700 overflow-hidden';

    const header = document.createElement('div');
    header.className = 'px-6 py-4 border-b border-slate-700 flex items-center gap-3';
    header.innerHTML = `
      <div class="w-7 h-7 rounded-lg bg-indigo-500/10 flex items-center justify-center shrink-0">
        <i class="fa-solid fa-book text-indigo-400 text-xs"></i>
      </div>
      <h3 class="font-bold text-white">${esc(course)}</h3>
      <span class="ml-auto text-xs text-slate-500">${Object.values(weeks).flat().length}개</span>
    `;
    section.appendChild(header);

    const body = document.createElement('div');
    body.className = 'divide-y divide-slate-700/50';

    Object.entries(weeks)
      .sort(([a], [b]) => a.localeCompare(b, 'ko', { numeric: true }))
      .forEach(([, items]) => {
        items.forEach(item => {
          const row = document.createElement('div');
          row.className = 'flex items-center gap-3 px-6 py-3.5 hover:bg-slate-800/40 cursor-pointer transition-all';

          const sttEnabled = !!item.transcript_available;
          const sttBtn = sttEnabled
            ? `<button data-stt class="shrink-0 px-2 py-0.5 bg-sky-500/10 border border-sky-500/20 text-sky-400 hover:bg-sky-500/20 text-xs rounded-full font-medium transition-all">전체 STT</button>`
            : `<span data-stt-disabled title="요약 시 원본 STT를 삭제해 열람할 수 없습니다" class="shrink-0 px-2 py-0.5 bg-slate-700/40 border border-slate-700 text-slate-600 text-xs rounded-full font-medium cursor-not-allowed">전체 STT</span>`;

          row.innerHTML = `
            <div class="w-7 h-7 rounded-lg bg-emerald-500/10 flex items-center justify-center shrink-0">
              <i class="fa-solid fa-file-lines text-emerald-400 text-xs"></i>
            </div>
            <div class="flex-1 min-w-0">
              <p class="text-sm text-slate-200 truncate">${esc(item.title)}</p>
              <p class="text-xs text-slate-500 mt-0.5">${esc(item.week)}</p>
            </div>
            ${sttBtn}
            <span class="shrink-0 px-2 py-0.5 bg-indigo-500/10 border border-indigo-500/20 text-indigo-400 text-xs rounded-full font-medium">AI 요약</span>
            <i class="fa-solid fa-chevron-right text-slate-600 text-xs shrink-0"></i>
          `;
          row.addEventListener('click', () => openSummary(item.id, item.title, item.week));
          if (sttEnabled) {
            row.querySelector('[data-stt]').addEventListener('click', (e) => {
              e.stopPropagation();
              openTranscript(item.transcript_id, item.title, item.week, item.course);
            });
          } else {
            row.querySelector('[data-stt-disabled]').addEventListener('click', (e) => e.stopPropagation());
          }
          body.appendChild(row);
        });
      });

    section.appendChild(body);
    list.appendChild(section);
  });
}

$('#btn-refresh-summaries').addEventListener('click', loadSummaries);
