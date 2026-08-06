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
  _openSummaryModal();

  try {
    const summary = await api('GET', `/api/summaries/${encodeURIComponent(summaryId)}`);
    if (state.currentSummaryId !== summaryId) return;
    $('#modal-summary-title').textContent = summary.title || lectureTitle || '강의 요약';
    renderMarkdown($('#modal-summary-content'), summary.content || '');
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
