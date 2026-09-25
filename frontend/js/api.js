// 세션 만료로 인한 401을 감지했을 때 앱에 알리는 콜백. 로그인 요청 자체의 401
// (아이디/비밀번호 오류)은 이미 로그인 화면에 있으므로 대상에서 제외한다.
let _onUnauthorized = null;

export function setUnauthorizedHandler(fn) {
  _onUnauthorized = fn;
}

export async function getAutoSuppressions() {
  const res = await api('GET', '/api/auto/suppressions');
  return res.suppressions || [];
}

export async function resetAutoSuppression(courseId, lectureUrl) {
  const body = courseId && lectureUrl ? { course_id: courseId, lecture_url: lectureUrl } : {};
  const res = await api('DELETE', '/api/auto/suppressions', body);
  return res.reset || 0;
}

export async function api(method, path, body, timeoutMs = 0) {
  const controller = timeoutMs > 0 ? new AbortController() : null;
  const opts = {
    method,
    headers: { 'Content-Type': 'application/json' },
    signal: controller ? controller.signal : undefined,
  };
  if (body) opts.body = JSON.stringify(body);
  let timer = null;
  try {
    const fetchPromise = fetch(path, opts);
    const timeoutPromise = timeoutMs > 0
      ? new Promise((_, reject) => {
          timer = setTimeout(() => {
            if (controller) controller.abort();
            reject(new Error('요청 시간이 초과되었습니다. 네트워크 상태를 확인한 뒤 다시 시도하세요.'));
          }, timeoutMs);
        })
      : null;
    const res = timeoutMs > 0
      ? await Promise.race([fetchPromise, timeoutPromise])
      : await fetchPromise;
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: '알 수 없는 오류' }));
      if (res.status === 401 && path !== '/api/auth/login' && _onUnauthorized) {
        _onUnauthorized();
      }
      throw new Error(err.detail || res.statusText);
    }
    return res.json();
  } catch (err) {
    if (err.name === 'AbortError') {
      throw new Error('요청 시간이 초과되었습니다. 네트워크 상태를 확인한 뒤 다시 시도하세요.');
    }
    throw err;
  } finally {
    if (timer) clearTimeout(timer);
  }
}
