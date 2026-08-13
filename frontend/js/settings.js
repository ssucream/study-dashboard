import { api } from './api.js';
import { state } from './state.js';
import { $, $$ } from './utils.js';

// ═══════════════════════════════════════════════════════════════
// 설정 유틸
// ═══════════════════════════════════════════════════════════════
function autoResizeTextarea(el) {
  el.style.height = 'auto';
  el.style.height = el.scrollHeight + 'px';
}

const AI_MODEL_DEFAULTS = {
  GEMINI_MODEL: 'gemini-3.5-flash-lite',
  OPENAI_MODEL: 'gpt-5.6-luna',
  OPENROUTER_MODEL: 'openrouter/auto',
};

async function loadAiModelCatalog() {
  const catalog = await api('GET', '/api/settings/ai-models', null, 15000);
  Object.entries(catalog.providers || {}).forEach(([agent, provider]) => {
    const list = $(`#${agent}-model-list`);
    if (!list) return;
    list.replaceChildren(...(provider.models || []).map(model => {
      const option = document.createElement('option');
      option.value = model.id;
      option.label = model.name;
      return option;
    }));
    const status = $(`[data-model-status="${agent}"]`);
    if (!status) return;
    const count = provider.models?.length || 0;
    status.textContent = agent === 'openrouter'
      ? `OpenRouter 사용 가능 텍스트 모델 ${count}개 · 모델 ID 직접 입력 가능`
      : `2026년 8월 기준 ${count}개 · 모델 ID 직접 입력 가능`;
  });
}

function applyAiAgentVisibility(form = $('#settings-form')) {
  if (!form) return;
  const agent = form.elements.AI_AGENT?.value || state.settings.AI_AGENT || 'gemini';
  $$('[data-agent-group]', form).forEach(el => {
    el.classList.toggle('hidden', el.dataset.agentGroup !== agent);
  });
}

export function applySettingsVisibility(form = $('#settings-form')) {
  if (!form) return;
  const downloadEnabled = form.elements.DOWNLOAD_ENABLED?.checked ?? state.settings.DOWNLOAD_ENABLED === 'true';
  const downloadRule = form.elements.DOWNLOAD_RULE?.value || state.settings.DOWNLOAD_RULE || 'mp4';
  const sttSupported = downloadEnabled && ['mp3', 'both'].includes(downloadRule);
  const sttEnabled = sttSupported && (form.elements.STT_ENABLED?.checked ?? state.settings.STT_ENABLED === 'true');
  const downloadOptions = $('#download-options');
  const sttSection = $('#stt-settings-section');
  const sttWarning = $('#stt-rule-warning');
  const sttDeleteRow = $('#stt-delete-audio-row');
  const aiSection = $('#ai-settings-section');
  const summaryDeleteTextRow = $('#summary-delete-text-row');

  if (downloadOptions) {
    downloadOptions.classList.toggle('opacity-50', !downloadEnabled);
    $$('input, select', downloadOptions).forEach(el => { el.disabled = !downloadEnabled; });
  }
  if (form.elements.AUTO_DOWNLOAD_AFTER_PLAY) {
    form.elements.AUTO_DOWNLOAD_AFTER_PLAY.disabled = !downloadEnabled;
    if (!downloadEnabled) form.elements.AUTO_DOWNLOAD_AFTER_PLAY.checked = false;
  }
  if (sttSection) {
    sttSection.classList.toggle('hidden', !downloadEnabled);
    sttSection.classList.toggle('opacity-60', downloadEnabled && !sttSupported);
    $$('input, select', sttSection).forEach(el => { el.disabled = !sttSupported; });
  }
  if (sttWarning) sttWarning.classList.toggle('hidden', sttSupported || !downloadEnabled);
  if (!sttSupported) {
    if (form.elements.STT_ENABLED) form.elements.STT_ENABLED.checked = false;
    if (form.elements.STT_DELETE_AUDIO_AFTER_TRANSCRIBE) form.elements.STT_DELETE_AUDIO_AFTER_TRANSCRIBE.checked = false;
  }
  if (form.elements.STT_DELETE_AUDIO_AFTER_TRANSCRIBE) {
    form.elements.STT_DELETE_AUDIO_AFTER_TRANSCRIBE.disabled = !sttEnabled;
    if (!sttEnabled) form.elements.STT_DELETE_AUDIO_AFTER_TRANSCRIBE.checked = false;
  }
  if (sttDeleteRow) sttDeleteRow.classList.toggle('opacity-50', !sttEnabled);
  if (aiSection) aiSection.classList.toggle('hidden', !sttEnabled);
  if (!sttEnabled && form.elements.AI_ENABLED) form.elements.AI_ENABLED.checked = false;
  const aiEnabled = sttEnabled && (form.elements.AI_ENABLED?.checked ?? state.settings.AI_ENABLED === 'true');
  if (form.elements.SUMMARY_DELETE_TEXT_AFTER_SUMMARIZE) {
    form.elements.SUMMARY_DELETE_TEXT_AFTER_SUMMARIZE.disabled = !aiEnabled;
    if (!aiEnabled) form.elements.SUMMARY_DELETE_TEXT_AFTER_SUMMARIZE.checked = false;
  }
  if (summaryDeleteTextRow) summaryDeleteTextRow.classList.toggle('opacity-50', !aiEnabled);
  applyAiAgentVisibility(form);
}

export async function loadAppSettings() {
  const settings = await api('GET', '/api/settings');
  state.settings = { ...state.settings, ...settings };
  state.settingsLoaded = true;
  return state.settings;
}

// ═══════════════════════════════════════════════════════════════
// 설정 페이지
// ═══════════════════════════════════════════════════════════════
export async function loadSettings() {
  try {
    const [s] = await Promise.all([
      loadAppSettings(),
      loadAiModelCatalog().catch(() => null),
    ]);
    const form = $('#settings-form');

    Object.entries(s).forEach(([key, val]) => {
      const el = form.elements[key];
      if (!el) return;
      if (el.type === 'checkbox') {
        el.checked = val === 'true';
      } else {
        el.value = val || '';
      }
    });
    Object.entries(AI_MODEL_DEFAULTS).forEach(([name, def]) => {
      const el = form.elements[name];
      if (el && !el.value) el.value = def;
    });
    const prompt = $('#summary-prompt-template');
    if (prompt) {
      prompt.value = s.SUMMARY_PROMPT_TEMPLATE || s.SUMMARY_PROMPT_DEFAULT || '';
      prompt.readOnly = true;
      $('#btn-summary-prompt-edit').textContent = '편집';
      autoResizeTextarea(prompt);
    }
    applySettingsVisibility(form);
  } catch {}
}

$('#settings-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const form = e.target;
  const payload = {};

  new FormData(form); // trigger validation

  $$('input, select, textarea', form).forEach(el => {
    if (!el.name) return;
    if (el.type === 'checkbox') {
      payload[el.name] = el.checked ? 'true' : 'false';
    } else if (el.value.trim()) {
      payload[el.name] = el.value.trim();
    }
  });
  if (payload.DOWNLOAD_ENABLED !== 'true') {
    payload.AUTO_DOWNLOAD_AFTER_PLAY = 'false';
    payload.STT_ENABLED = 'false';
    payload.STT_DELETE_AUDIO_AFTER_TRANSCRIBE = 'false';
    payload.AI_ENABLED = 'false';
    payload.SUMMARY_DELETE_TEXT_AFTER_SUMMARIZE = 'false';
  } else if (!['mp3', 'both'].includes(payload.DOWNLOAD_RULE || 'mp4')) {
    payload.STT_ENABLED = 'false';
    payload.STT_DELETE_AUDIO_AFTER_TRANSCRIBE = 'false';
    payload.AI_ENABLED = 'false';
    payload.SUMMARY_DELETE_TEXT_AFTER_SUMMARIZE = 'false';
  } else if (payload.STT_ENABLED !== 'true') {
    payload.STT_DELETE_AUDIO_AFTER_TRANSCRIBE = 'false';
    payload.AI_ENABLED = 'false';
    payload.SUMMARY_DELETE_TEXT_AFTER_SUMMARIZE = 'false';
  } else if (payload.AI_ENABLED !== 'true') {
    payload.SUMMARY_DELETE_TEXT_AFTER_SUMMARIZE = 'false';
  }

  try {
    await api('PUT', '/api/settings', payload);
    await loadAppSettings();
    applySettingsVisibility(form);
    const msg = $('#settings-success');
    msg.classList.remove('hidden');
    setTimeout(() => msg.classList.add('hidden'), 3000);
  } catch (err) {
    alert(`저장 실패: ${err.message}`);
  }
});

['DOWNLOAD_ENABLED', 'DOWNLOAD_RULE', 'AUTO_DOWNLOAD_AFTER_PLAY', 'STT_ENABLED', 'AI_ENABLED', 'AI_AGENT'].forEach(name => {
  const el = $('#settings-form').elements[name];
  if (el) el.addEventListener('change', () => applySettingsVisibility());
});

$('#btn-telegram-test')?.addEventListener('click', async () => {
  const btn = $('#btn-telegram-test');
  const result = $('#telegram-test-result');
  btn.disabled = true;
  btn.textContent = '테스트 중...';
  result.textContent = '';
  result.className = 'text-xs';
  try {
    await api('POST', '/api/settings/telegram/test');
    result.textContent = '✓ 전송 성공';
    result.classList.add('text-green-400');
  } catch (err) {
    result.textContent = `✗ ${err.message}`;
    result.classList.add('text-red-400');
  } finally {
    btn.disabled = false;
    btn.textContent = '연결 테스트';
  }
});

$('#btn-summary-prompt-reset').addEventListener('click', () => {
  const prompt = $('#summary-prompt-template');
  prompt.value = state.settings.SUMMARY_PROMPT_DEFAULT || '';
  prompt.readOnly = true;
  $('#btn-summary-prompt-edit').textContent = '편집';
  autoResizeTextarea(prompt);
});

$('#btn-summary-prompt-edit').addEventListener('click', () => {
  const prompt = $('#summary-prompt-template');
  prompt.readOnly = !prompt.readOnly;
  $('#btn-summary-prompt-edit').textContent = prompt.readOnly ? '편집' : '편집 완료';
  if (!prompt.readOnly) prompt.focus();
});

$('#summary-prompt-template').addEventListener('input', function () {
  autoResizeTextarea(this);
});
