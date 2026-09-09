export function renderMarkdown(container, markdown) {
  container.innerHTML = '';
  const lines = (markdown || '').split(/\r?\n/);
  let codeBlock = null;
  let pendingBlank = false;

  // 들여쓰기(indent)별 리스트 스택 — 빈 줄이나 하위 항목이 끼어들어도
  // 같은 리스트가 유지되도록 해 번호가 1로 리셋되지 않게 한다.
  let listStack = [];

  const closeLists = () => { listStack = []; };

  const addSpacer = () => {
    const spacer = document.createElement('div');
    spacer.className = 'h-3';
    container.appendChild(spacer);
  };

  const appendText = (tag, text, className) => {
    const el = document.createElement(tag);
    el.textContent = text;
    if (className) el.className = className;
    container.appendChild(el);
    return el;
  };

  // 주어진 indent/type에 해당하는 <ul>/<ol>을 찾거나 새로 만든다.
  const getList = (indent, type) => {
    while (listStack.length && listStack[listStack.length - 1].indent > indent) {
      listStack.pop();
    }
    const top = listStack[listStack.length - 1];
    if (top && top.indent === indent && top.type === type) return top.el;
    if (top && top.indent === indent && top.type !== type) listStack.pop();

    const el = document.createElement(type);
    el.className = type === 'ul'
      ? 'list-disc pl-6 my-2 space-y-1 text-slate-200'
      : 'list-decimal pl-6 my-2 space-y-1 text-slate-200';

    const parent = listStack[listStack.length - 1];
    if (parent) {
      (parent.el.lastElementChild || parent.el).appendChild(el);
    } else {
      container.appendChild(el);
    }
    listStack.push({ indent, el, type });
    return el;
  };

  lines.forEach(rawLine => {
    const line = rawLine.trimEnd();
    const trimmed = line.trim();

    if (trimmed.startsWith('```')) {
      closeLists();
      pendingBlank = false;
      if (codeBlock) {
        codeBlock = null;
      } else {
        codeBlock = document.createElement('pre');
        codeBlock.className = 'my-4 p-4 bg-slate-900 rounded-xl border border-slate-700 text-sm text-slate-300 whitespace-pre-wrap overflow-x-auto';
        container.appendChild(codeBlock);
      }
      return;
    }

    if (codeBlock) {
      codeBlock.textContent += `${line}\n`;
      return;
    }

    if (!trimmed) {
      // 빈 줄만으로는 리스트를 닫지 않는다 — 다음 줄을 보고 결정.
      pendingBlank = true;
      return;
    }

    const headingMatch = line.match(/^(#{1,3})\s+(.+)$/);
    const olMatch = line.match(/^(\s*)(\d+)[.)]\s+(.+)$/);
    const ulMatch = line.match(/^(\s*)[-*]\s+(.+)$/);

    if (olMatch || ulMatch) {
      const indentStr = (olMatch ? olMatch[1] : ulMatch[1]) || '';
      const indent = indentStr.replace(/\t/g, '  ').length;
      const type = olMatch ? 'ol' : 'ul';
      const text = olMatch ? olMatch[3] : ulMatch[2];

      // 빈 줄 뒤 새로운 최상위 리스트가 시작될 때만 간격을 준다.
      if (pendingBlank && !listStack.length) addSpacer();
      pendingBlank = false;

      const li = document.createElement('li');
      li.textContent = text;
      getList(indent, type).appendChild(li);
      return;
    }

    // 리스트가 아닌 내용이 오면 열린 리스트를 닫는다.
    if (pendingBlank) {
      closeLists();
      addSpacer();
      pendingBlank = false;
    } else {
      closeLists();
    }

    if (headingMatch) {
      const level = headingMatch[1].length;
      const classes = {
        1: 'text-2xl font-black text-white mt-2 mb-4',
        2: 'text-xl font-bold text-white mt-6 mb-3',
        3: 'text-base font-bold text-indigo-300 mt-5 mb-2',
      };
      appendText(`h${level}`, headingMatch[2], classes[level]);
      return;
    }

    appendText('p', line, 'my-2 text-slate-200 whitespace-pre-wrap');
  });

  if (!container.childNodes.length) {
    appendText('p', '요약 내용이 비어 있습니다.', 'text-slate-400');
  }
}
