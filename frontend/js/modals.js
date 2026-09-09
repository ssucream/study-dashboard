import { api } from './api.js';
import { renderMarkdown } from './markdown.js';
import { state } from './state.js';
import { $, esc } from './utils.js';

// ═══════════════════════════════════════════════════════════════
// STT 텍스트 뷰어
// ═══════════════════════════════════════════════════════════════
function _openSttModal() {
  const modal = $('#stt-modal');
  modal.classList.remove('hidden');
  modal.classList.add('flex');
  document.body.style.overflow = 'hidden';
}

function _closeSttModal() {
  const modal = $('#stt-modal');
  modal.classList.add('hidden');
  modal.classList.remove('flex');
  document.body.style.overflow = '';
  $('#btn-download-stt')?.classList.add('hidden');
}

export async function openSttText(taskId, lectureTitle) {
  if (!taskId) return;
  $('#modal-stt-title').textContent = lectureTitle || 'STT 변환 결과';
  $('#modal-stt-meta').textContent = '';
  $('#modal-stt-content').innerHTML = '<span class="text-slate-400"><i class="fa-solid fa-spinner fa-spin mr-2"></i>STT 결과를 불러오는 중...</span>';
  const dlBtn = $('#btn-download-stt');
  if (dlBtn) {
    dlBtn.classList.remove('hidden');
    dlBtn.onclick = () => { window.location.href = `/api/tasks/${taskId}/stt/download`; };
  }
  _openSttModal();

  try {
    const res = await api('GET', `/api/tasks/${taskId}/stt`);
    const meta = [res.model ? `모델: ${res.model}` : '', res.language ? `언어: ${res.language}` : ''].filter(Boolean).join(' · ');
    $('#modal-stt-meta').textContent = meta;
    $('#modal-stt-content').textContent = res.content || '(내용 없음)';
  } catch (err) {
    $('#modal-stt-content').innerHTML = `<span class="text-red-400 text-sm">${esc(err.message)}</span>`;
  }
}

export async function openTranscript(transcriptId, lectureTitle, weekLabel, courseName) {
  if (!transcriptId) return;
  $('#modal-stt-title').textContent = lectureTitle || 'STT 원문';
  $('#modal-stt-meta').textContent = [courseName, weekLabel].filter(Boolean).join(' · ');
  $('#modal-stt-content').innerHTML = '<span class="text-slate-400"><i class="fa-solid fa-spinner fa-spin mr-2"></i>STT 원문을 불러오는 중...</span>';
  const dlBtn = $('#btn-download-stt');
  if (dlBtn) {
    dlBtn.classList.remove('hidden');
    dlBtn.onclick = () => { window.location.href = `/api/summaries/transcript/${encodeURIComponent(transcriptId)}/download`; };
  }
  _openSttModal();

  try {
    const res = await api('GET', `/api/summaries/transcript/${encodeURIComponent(transcriptId)}`);
    $('#modal-stt-content').textContent = res.content || '(내용 없음)';
  } catch (err) {
    $('#modal-stt-content').innerHTML = `<span class="text-red-400 text-sm">${esc(err.message)}</span>`;
  }
}

$('#btn-close-stt-modal').addEventListener('click', _closeSttModal);
$('#stt-modal-backdrop').addEventListener('click', _closeSttModal);
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && !$('#stt-modal').classList.contains('hidden')) {
    _closeSttModal();
  }
});

// ═══════════════════════════════════════════════════════════════
// 요약 모달
// ═══════════════════════════════════════════════════════════════
function _openSummaryModal() {
  const modal = $('#summary-modal');
  modal.classList.remove('hidden');
  modal.classList.add('flex');
  document.body.style.overflow = 'hidden';
}

function _closeSummaryModal() {
  const modal = $('#summary-modal');
  modal.classList.add('hidden');
  modal.classList.remove('flex');
  document.body.style.overflow = '';
  state.currentSummaryId = null;
  $('#btn-download-summary')?.classList.add('hidden');
  const copyBtn = $('#btn-copy-summary');
  if (copyBtn) { copyBtn.classList.add('hidden'); copyBtn.onclick = null; _resetCopyBtn(copyBtn); }
}

let _copyBtnResetTimer = null;

function _resetCopyBtn(btn) {
  clearTimeout(_copyBtnResetTimer);
  btn.querySelector('i').className = 'fa-regular fa-copy text-xs';
  btn.querySelector('span').textContent = '복사';
  btn.classList.remove('text-emerald-400', 'text-red-400');
}

async function _copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    try {
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      const ok = document.execCommand('copy');
      document.body.removeChild(ta);
      return ok;
    } catch {
      return false;
    }
  }
}

export async function openSummary(summaryId, lectureTitle, weekLabel) {
  if (!summaryId) return;
  state.currentSummaryId = summaryId;

  $('#modal-summary-title').textContent = lectureTitle || '강의 요약';
  $('#modal-summary-meta').textContent = [state.currentCourseName, weekLabel].filter(Boolean).join(' · ');
  $('#modal-summary-content').innerHTML = '<p class="text-slate-400"><i class="fa-solid fa-spinner fa-spin mr-2"></i>요약을 불러오는 중...</p>';
  const dlBtn = $('#btn-download-summary');
  if (dlBtn) {
    dlBtn.classList.remove('hidden');
    dlBtn.onclick = () => { window.location.href = `/api/summaries/${encodeURIComponent(summaryId)}/download`; };
  }
  const copyBtn = $('#btn-copy-summary');
  if (copyBtn) {
    copyBtn.classList.add('hidden');
    _resetCopyBtn(copyBtn);
    copyBtn.onclick = null;
  }
  _openSummaryModal();

  try {
    const summary = await api('GET', `/api/summaries/${encodeURIComponent(summaryId)}`);
    if (state.currentSummaryId !== summaryId) return;
    $('#modal-summary-title').textContent = summary.title || lectureTitle || '강의 요약';
    renderMarkdown($('#modal-summary-content'), summary.content || '');
    if (copyBtn) {
      const raw = summary.content || '';
      copyBtn.classList.toggle('hidden', !raw);
      copyBtn.onclick = async () => {
        const ok = await _copyText(raw);
        clearTimeout(_copyBtnResetTimer);
        copyBtn.querySelector('i').className = ok ? 'fa-solid fa-check text-xs' : 'fa-solid fa-xmark text-xs';
        copyBtn.querySelector('span').textContent = ok ? '복사됨' : '복사 실패';
        copyBtn.classList.add(ok ? 'text-emerald-400' : 'text-red-400');
        _copyBtnResetTimer = setTimeout(() => _resetCopyBtn(copyBtn), 2000);
      };
    }
  } catch (err) {
    if (state.currentSummaryId === summaryId) {
      $('#modal-summary-content').innerHTML = `<p class="text-red-400 text-sm">${esc(err.message)}</p>`;
    }
  }
}

$('#btn-close-summary-modal').addEventListener('click', _closeSummaryModal);
$('#summary-modal-backdrop').addEventListener('click', _closeSummaryModal);
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && !$('#summary-modal').classList.contains('hidden')) {
    _closeSummaryModal();
  }
});
