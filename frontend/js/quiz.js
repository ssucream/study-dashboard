import { api } from './api.js';
import { state } from './state.js';
import { $, $$, esc } from './utils.js';
import { renderMarkdown } from './markdown.js';

// ═══════════════════════════════════════════════════════════════
// 예상 문제 (생성 / 풀이 / 채점)
// ═══════════════════════════════════════════════════════════════

const QTYPE_LABEL = { multiple_choice: '객관식', short_answer: '단답형', essay: '서술형' };

let _uploadedMaterials = [];   // {id, name, text_chars, text_empty}
let _currentSet = null;        // 풀이 중인 문제셋 (questions 포함)

function _aiEnabled() {
  return state.settings.AI_ENABLED === 'true';
}

function _pollTask(taskId, { onDone, onError }) {
  const tick = async () => {
    try {
      const task = await api('GET', `/api/tasks/${taskId}`);
      if (task.status === 'completed') return onDone(task);
      if (task.status === 'failed' || task.status === 'cancelled') {
        return onError(new Error(task.error || '작업이 실패했습니다.'));
      }
      setTimeout(tick, 1500);
    } catch (err) {
      onError(err);
    }
  };
  setTimeout(tick, 1000);
}

// ───────────────────────────── 목록 ─────────────────────────────
export async function loadQuizSets() {
  const loading = $('#quiz-loading');
  const empty = $('#quiz-empty');
  const list = $('#quiz-list');

  $('#quiz-ai-warning').classList.toggle('hidden', _aiEnabled());
  list.innerHTML = '';
  empty.classList.add('hidden');
  loading.classList.remove('hidden');
  loading.classList.add('flex');

  try {
    const res = await api('GET', '/api/quiz');
    _renderQuizList(res.quiz_sets || []);
  } catch (err) {
    list.innerHTML = `<p class="text-red-400 text-sm">${esc(err.message)}</p>`;
  } finally {
    loading.classList.add('hidden');
    loading.classList.remove('flex');
  }
}

function _renderQuizList(sets) {
  const list = $('#quiz-list');
  const empty = $('#quiz-empty');
  list.innerHTML = '';

  if (!sets.length) {
    empty.classList.remove('hidden');
    empty.classList.add('flex');
    return;
  }
  empty.classList.add('hidden');

  sets.forEach(s => {
    const by = s.question_counts_by_type || {};
    const chips = Object.entries(by)
      .filter(([, n]) => n > 0)
      .map(([t, n]) => `${QTYPE_LABEL[t] || t} ${n}`)
      .join(' · ');
    const date = (s.created_at || '').slice(0, 16).replace('T', ' ');
    const card = document.createElement('div');
    card.className = 'bg-[#1E293B] rounded-2xl border border-slate-700 p-5 flex items-center gap-4';
    card.innerHTML = `
      <div class="w-9 h-9 rounded-xl bg-indigo-500/10 flex items-center justify-center shrink-0">
        <i class="fa-solid fa-clipboard-question text-indigo-400 text-sm"></i>
      </div>
      <div class="flex-1 min-w-0">
        <p class="text-sm font-semibold text-slate-100 truncate">${esc(s.course_name)}</p>
        <p class="text-xs text-slate-500 mt-0.5 truncate">${esc((s.weeks || []).join(', ') || '자료 기반')} · ${esc(chips)} · ${esc(date)}</p>
        ${s.status === 'failed' ? '<p class="text-xs text-red-400 mt-0.5">생성 실패</p>' : ''}
      </div>
      <button data-act="solve" class="shrink-0 px-3 py-1.5 bg-indigo-500 hover:bg-indigo-400 rounded-lg text-xs text-white font-medium transition-all">풀기</button>
      <button data-act="delete" class="shrink-0 w-8 h-8 rounded-lg text-slate-500 hover:text-red-400 hover:bg-red-500/10 transition-all" aria-label="삭제">
        <i class="fa-solid fa-trash text-xs"></i>
      </button>
    `;
    card.querySelector('[data-act="solve"]').addEventListener('click', () => openQuizSolve(s.id));
    card.querySelector('[data-act="delete"]').addEventListener('click', async () => {
      if (!confirm('이 문제셋을 삭제할까요?')) return;
      try {
        await api('DELETE', `/api/quiz/${s.id}`);
        loadQuizSets();
      } catch (err) {
        alert(err.message);
      }
    });
    list.appendChild(card);
  });
}

// ───────────────────────────── 생성 폼 ─────────────────────────────
async function _openForm() {
  const form = $('#quiz-form');
  form.classList.remove('hidden');
  _uploadedMaterials = [];
  _renderMaterialList();
  $('#quiz-form-error').classList.add('hidden');
  $('#quiz-material-input').value = '';

  const sel = $('#quiz-course');
  if (!state.courses.length) {
    try {
      state.courses = await api('GET', '/api/courses');
    } catch { /* 무시 — 아래에서 빈 목록 처리 */ }
  }
  sel.innerHTML = state.courses.map(c => `<option value="${esc(c.id)}">${esc(c.name)}</option>`).join('')
    || '<option value="">과목을 먼저 불러오세요</option>';
  if (state.courses.length) _loadWeeks(sel.value);
}

function _closeForm() {
  $('#quiz-form').classList.add('hidden');
}

async function _loadWeeks(courseId) {
  const box = $('#quiz-weeks');
  if (!courseId) { box.innerHTML = ''; return; }
  box.innerHTML = '<p class="text-xs text-slate-500">주차를 불러오는 중...</p>';
  try {
    const detail = await api('GET', `/api/courses/${courseId}`);
    const weeks = detail.weeks || [];
    if (!weeks.length) { box.innerHTML = '<p class="text-xs text-slate-500">주차 정보가 없습니다.</p>'; return; }
    box.innerHTML = '';
    weeks.forEach(w => {
      const id = `qw-${w.week_number}`;
      const label = document.createElement('label');
      label.className = 'flex items-center gap-1.5 px-2.5 py-1.5 bg-slate-900 border border-slate-700 rounded-lg text-xs text-slate-300 cursor-pointer hover:border-indigo-500/50';
      label.innerHTML = `<input type="checkbox" id="${id}" value="${w.week_number}" class="quiz-week accent-indigo-500">${esc(w.title)}`;
      box.appendChild(label);
    });
  } catch (err) {
    box.innerHTML = `<p class="text-xs text-red-400">${esc(err.message)}</p>`;
  }
}

function _renderMaterialList() {
  const box = $('#quiz-material-list');
  box.innerHTML = '';
  _uploadedMaterials.forEach(m => {
    const row = document.createElement('div');
    row.className = 'flex items-center gap-2 text-xs text-slate-400 bg-slate-900 rounded-lg px-2.5 py-1.5';
    row.innerHTML = `
      <i class="fa-solid fa-file-lines text-slate-500"></i>
      <span class="flex-1 truncate text-slate-300">${esc(m.name)}</span>
      ${m.text_empty ? '<span class="text-amber-400">텍스트 없음</span>' : `<span class="text-slate-500">${m.text_chars.toLocaleString()}자</span>`}
      <button data-id="${esc(m.id)}" class="text-slate-500 hover:text-red-400" aria-label="삭제"><i class="fa-solid fa-xmark"></i></button>
    `;
    row.querySelector('button').addEventListener('click', async () => {
      try { await api('DELETE', `/api/quiz/materials/${encodeURIComponent(m.id)}`); } catch { /* 무시 */ }
      _uploadedMaterials = _uploadedMaterials.filter(x => x.id !== m.id);
      _renderMaterialList();
    });
    box.appendChild(row);
  });
}

async function _uploadMaterials(files) {
  const courseId = $('#quiz-course').value;
  if (!courseId) { alert('먼저 과목을 선택하세요.'); return; }
  const weekLabel = ($$('.quiz-week').find(c => c.checked)?.parentElement.textContent || '').trim();
  const status = $('#quiz-form-status');

  for (const file of files) {
    status.textContent = `${file.name} 업로드 중...`;
    const fd = new FormData();
    fd.append('course_id', courseId);
    fd.append('week_label', weekLabel);
    fd.append('file', file);
    try {
      const res = await fetch('/api/quiz/materials', { method: 'POST', body: fd });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: '업로드 실패' }));
        throw new Error(err.detail || res.statusText);
      }
      const meta = await res.json();
      _uploadedMaterials.push(meta);
      _renderMaterialList();
    } catch (err) {
      alert(`${file.name}: ${err.message}`);
    }
  }
  status.textContent = '';
  $('#quiz-material-input').value = '';
}

async function _submitForm(e) {
  e.preventDefault();
  const err = $('#quiz-form-error');
  err.classList.add('hidden');

  const courseId = $('#quiz-course').value;
  const weekNumbers = $$('.quiz-week').filter(c => c.checked).map(c => Number(c.value));
  const counts = {
    multiple_choice: Number($('#quiz-count-mc').value) || 0,
    short_answer: Number($('#quiz-count-short').value) || 0,
    essay: Number($('#quiz-count-essay').value) || 0,
  };
  if (!courseId) return _formError('과목을 선택하세요.');
  if (counts.multiple_choice + counts.short_answer + counts.essay === 0) return _formError('문제 개수를 1개 이상 지정하세요.');
  if (!weekNumbers.length && !_uploadedMaterials.length) return _formError('시험 범위 주차나 업로드 자료를 1개 이상 선택하세요.');

  const submitBtn = $('#quiz-form-submit');
  const status = $('#quiz-form-status');
  submitBtn.disabled = true;
  status.textContent = '문제 생성 요청 중...';

  try {
    const res = await api('POST', '/api/quiz', {
      course_id: courseId,
      week_numbers: weekNumbers,
      material_ids: _uploadedMaterials.map(m => m.id),
      counts,
      include_stt: $('#quiz-include-stt').checked,
    });
    status.textContent = 'AI가 문제를 생성하는 중입니다. 잠시 기다려 주세요...';
    _pollTask(res.task_id, {
      onDone: () => {
        submitBtn.disabled = false;
        status.textContent = '';
        _closeForm();
        loadQuizSets();
      },
      onError: (e2) => {
        submitBtn.disabled = false;
        status.textContent = '';
        _formError(e2.message);
      },
    });
  } catch (e2) {
    submitBtn.disabled = false;
    status.textContent = '';
    _formError(e2.message);
  }
}

function _formError(msg) {
  const err = $('#quiz-form-error');
  err.textContent = msg;
  err.classList.remove('hidden');
}

// ───────────────────────────── 풀이 ─────────────────────────────
export async function openQuizSolve(setId) {
  const form = $('#quiz-solve-form');
  form.innerHTML = '<p class="text-slate-400 text-sm"><i class="fa-solid fa-spinner fa-spin mr-2"></i>문제를 불러오는 중...</p>';
  $('#quiz-solve-error').classList.add('hidden');
  $('#quiz-solve-status').textContent = '';
  location.hash = '';
  _navigate('quiz-solve');

  try {
    _currentSet = await api('GET', `/api/quiz/${setId}`);
  } catch (err) {
    form.innerHTML = `<p class="text-red-400 text-sm">${esc(err.message)}</p>`;
    return;
  }

  $('#quiz-solve-title').textContent = _currentSet.course_name;
  $('#quiz-solve-meta').textContent = [
    (_currentSet.weeks || []).join(', '),
    `${_currentSet.question_count}문항`,
  ].filter(Boolean).join(' · ');

  form.innerHTML = '';
  _currentSet.questions.forEach((q, i) => {
    const wrap = document.createElement('div');
    wrap.className = 'bg-[#1E293B] rounded-2xl border border-slate-700 p-5';
    let body = '';
    if (q.qtype === 'multiple_choice') {
      body = (q.choices || []).map((c, idx) => `
        <label class="flex items-start gap-2.5 py-1.5 text-sm text-slate-300 cursor-pointer">
          <input type="radio" name="q-${q.id}" value="${idx}" class="mt-0.5 accent-indigo-500">
          <span>${esc(c)}</span>
        </label>`).join('');
    } else {
      const rows = q.qtype === 'essay' ? 5 : 2;
      body = `<textarea name="q-${q.id}" rows="${rows}" class="w-full px-3 py-2 bg-slate-900 border border-slate-700 rounded-xl text-sm text-slate-200 focus:outline-none focus:border-indigo-500/70" placeholder="답안을 입력하세요"></textarea>`;
    }
    wrap.innerHTML = `
      <div class="flex items-center gap-2 mb-3">
        <span class="text-xs font-bold text-indigo-400">Q${i + 1}</span>
        <span class="px-2 py-0.5 bg-slate-800 text-slate-400 text-xs rounded-md">${QTYPE_LABEL[q.qtype] || q.qtype}</span>
      </div>
      <p class="text-sm text-slate-100 mb-3 whitespace-pre-wrap">${esc(q.question)}</p>
      ${body}
    `;
    form.appendChild(wrap);
  });
}

async function _submitAttempt() {
  if (!_currentSet) return;
  const btn = $('#btn-quiz-submit');
  const status = $('#quiz-solve-status');
  const errEl = $('#quiz-solve-error');
  errEl.classList.add('hidden');

  const answers = _currentSet.questions.map(q => {
    const el = document.querySelector(`[name="q-${q.id}"]:checked`) || document.querySelector(`textarea[name="q-${q.id}"]`);
    return { question_id: q.id, answer: el ? el.value.trim() : '' };
  });

  btn.disabled = true;
  status.textContent = '채점 중...';
  const setId = _currentSet.id;
  try {
    const res = await api('POST', `/api/quiz/${setId}/attempts`, { answers });
    if (!res.task_id) {
      btn.disabled = false;
      status.textContent = '';
      return openQuizResult(setId, res.attempt_id);
    }
    status.textContent = 'AI가 주관식을 채점하는 중입니다...';
    _pollTask(res.task_id, {
      onDone: () => { btn.disabled = false; status.textContent = ''; openQuizResult(setId, res.attempt_id); },
      onError: (e) => {
        btn.disabled = false;
        status.textContent = '';
        errEl.textContent = `주관식 채점 실패: ${e.message} — 객관식 결과만 표시합니다.`;
        errEl.classList.remove('hidden');
        openQuizResult(setId, res.attempt_id);
      },
    });
  } catch (err) {
    btn.disabled = false;
    status.textContent = '';
    errEl.textContent = err.message;
    errEl.classList.remove('hidden');
  }
}

// ───────────────────────────── 채점 결과 ─────────────────────────────
export async function openQuizResult(setId, attemptId) {
  _navigate('quiz-result');
  const scoreBox = $('#quiz-result-score');
  const listBox = $('#quiz-result-list');
  scoreBox.innerHTML = '';
  listBox.innerHTML = '<p class="text-slate-400 text-sm"><i class="fa-solid fa-spinner fa-spin mr-2"></i>결과를 불러오는 중...</p>';

  let data;
  try {
    data = await api('GET', `/api/quiz/${setId}/attempts/${attemptId}`);
  } catch (err) {
    listBox.innerHTML = `<p class="text-red-400 text-sm">${esc(err.message)}</p>`;
    return;
  }

  $('#quiz-result-title').textContent = '채점 결과';
  $('#quiz-result-meta').textContent = (data.created_at || '').slice(0, 16).replace('T', ' ');

  const total = data.score_total;
  const partial = data.graded_status === 'partial';
  scoreBox.innerHTML = `
    <div class="bg-[#1E293B] rounded-2xl border border-slate-700 p-6 flex items-center gap-5">
      <div class="text-4xl font-black ${total >= 60 ? 'text-emerald-400' : 'text-amber-400'}">${total == null ? '—' : Math.round(total)}<span class="text-lg text-slate-600">/100</span></div>
      <div class="text-sm text-slate-400">
        ${data.results.filter(r => r.correct).length} / ${data.results.length} 문항 정답
        ${partial ? '<br><span class="text-amber-400">주관식 채점 대기 중</span>' : ''}
      </div>
    </div>
  `;

  listBox.innerHTML = '';
  data.results.forEach((r, i) => {
    const wrap = document.createElement('div');
    wrap.className = `rounded-2xl border p-5 ${r.correct ? 'border-emerald-500/30 bg-emerald-500/5' : 'border-red-500/30 bg-red-500/5'}`;
    const yourAnswer = (data.answers.find(a => a.question_id === r.question_id) || {}).answer || '(무응답)';
    let answerInfo = '';
    if (r.qtype === 'multiple_choice') {
      const correctIdx = Number(r.correct_answer);
      answerInfo = `
        <p class="text-xs text-slate-400 mt-2">내 답: ${esc(r.choices[Number(yourAnswer)] ?? yourAnswer)}</p>
        <p class="text-xs text-emerald-400">정답: ${esc(r.choices[correctIdx] ?? '')}</p>`;
    } else {
      answerInfo = `
        <p class="text-xs text-slate-400 mt-2 whitespace-pre-wrap">내 답: ${esc(yourAnswer)}</p>
        ${r.correct_answer ? `<p class="text-xs text-emerald-400 whitespace-pre-wrap">모범답안: ${esc(r.correct_answer)}</p>` : ''}
        <p class="text-xs text-slate-500 mt-1">점수: ${Math.round(r.score)}/100 · 참고용 채점입니다.</p>`;
    }
    wrap.innerHTML = `
      <div class="flex items-center gap-2 mb-2">
        <span class="text-xs font-bold ${r.correct ? 'text-emerald-400' : 'text-red-400'}">Q${i + 1} ${r.correct ? '정답' : '오답'}</span>
        <span class="px-2 py-0.5 bg-slate-800 text-slate-400 text-xs rounded-md">${QTYPE_LABEL[r.qtype] || r.qtype}</span>
      </div>
      <p class="text-sm text-slate-100 whitespace-pre-wrap">${esc(r.question)}</p>
      ${answerInfo}
      ${r.feedback ? `<div class="mt-2 text-xs text-slate-300 bg-slate-900/60 rounded-lg p-3"><span class="text-slate-500">채점 사유 </span>${esc(r.feedback)}</div>` : ''}
      ${r.explanation ? `<details class="mt-2 text-xs text-slate-400"><summary class="cursor-pointer text-slate-500">해설 보기</summary><div class="mt-1 quiz-expl"></div></details>` : ''}
    `;
    if (r.explanation) renderMarkdown(wrap.querySelector('.quiz-expl'), r.explanation);
    listBox.appendChild(wrap);
  });
}

// ───────────────────────────── 라우팅 헬퍼 ─────────────────────────────
function _navigate(page) {
  $$('.page').forEach(el => el.classList.add('hidden'));
  const target = $(`#page-${page}`);
  if (target) { target.classList.remove('hidden'); target.classList.add('fade-in'); }
}

function _backToList() {
  $$('.page').forEach(el => el.classList.add('hidden'));
  $('#page-quiz').classList.remove('hidden');
  $$('.nav-item').forEach(b => b.classList.toggle('active', b.dataset.page === 'quiz'));
  loadQuizSets();
}

// ───────────────────────────── 이벤트 바인딩 ─────────────────────────────
$('#btn-quiz-new')?.addEventListener('click', () => {
  if (!_aiEnabled()) { $('#quiz-ai-warning').classList.remove('hidden'); return; }
  _openForm();
});
$('#quiz-form-cancel')?.addEventListener('click', _closeForm);
$('#quiz-form')?.addEventListener('submit', _submitForm);
$('#quiz-course')?.addEventListener('change', (e) => _loadWeeks(e.target.value));
$('#quiz-material-input')?.addEventListener('change', (e) => _uploadMaterials([...e.target.files]));
$('#btn-quiz-submit')?.addEventListener('click', _submitAttempt);
$('#btn-quiz-solve-back')?.addEventListener('click', _backToList);
$('#btn-quiz-result-back')?.addEventListener('click', _backToList);
$('#quiz-ai-warning-btn')?.addEventListener('click', () => {
  $$('.nav-item').find(b => b.dataset.page === 'settings')?.click();
});
