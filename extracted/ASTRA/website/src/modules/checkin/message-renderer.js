// Render a small Markdown subset as DOM nodes. Model/user text is never HTML.
// Each block chooses its own direction; Latin terms stay together inside Arabic.
const ARABIC = /[\u0620-\u064A\u066E-\u06D3\u0750-\u077F\u08A0-\u08C9]/u;
const LATIN_RUN = /\([A-Za-z0-9][A-Za-z0-9 \t.,:+/%°_–\-²₀-₉]*\)|[A-Za-z0-9](?:[A-Za-z0-9 \t.,:+/%°_–\-²₀-₉]*[A-Za-z0-9%°²₀-₉])?/g;
const INLINE = /(`[^`\n]+`|\*\*[^*\n]+\*\*|\*[^*\n]+\*|\[[^\]\n]+\]\([^\s)]+\)|https?:\/\/[^\s<>]+)/g;

export function messageDirection(text) {
  return ARABIC.test(String(text || '')) ? 'rtl' : 'ltr';
}

function setDirection(element, text) {
  element.dir = messageDirection(text);
  element.lang = element.dir === 'rtl' ? 'ar' : 'en';
}

function appendText(parent, text) {
  const doc = parent.ownerDocument;
  let offset = 0;
  for (const match of text.matchAll(LATIN_RUN)) {
    parent.append(doc.createTextNode(text.slice(offset, match.index)));
    const term = doc.createElement('bdi');
    term.dir = 'ltr';
    term.textContent = match[0];
    parent.append(term);
    offset = match.index + match[0].length;
  }
  parent.append(doc.createTextNode(text.slice(offset)));
}

function safeLink(raw) {
  try {
    const url = new URL(raw);
    return ['https:', 'http:'].includes(url.protocol) && !url.username && !url.password
      ? url.href : null;
  } catch {
    return null;
  }
}

function appendInline(parent, text) {
  const doc = parent.ownerDocument;
  let offset = 0;
  for (const match of text.matchAll(INLINE)) {
    appendText(parent, text.slice(offset, match.index));
    const token = match[0];
    const link = token.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
    if (token.startsWith('`')) {
      const code = doc.createElement('code');
      const value = token.slice(1, -1);
      setDirection(code, value);
      code.textContent = value;
      parent.append(code);
    } else if (token.startsWith('**') || token.startsWith('*')) {
      const strong = token.startsWith('**');
      const element = doc.createElement(strong ? 'strong' : 'em');
      appendText(element, token.slice(strong ? 2 : 1, strong ? -2 : -1));
      parent.append(element);
    } else {
      const href = safeLink(link ? link[2] : token);
      if (href) {
        const anchor = doc.createElement('a');
        anchor.href = href;
        anchor.target = '_blank';
        anchor.rel = 'noopener noreferrer';
        setDirection(anchor, link ? link[1] : token);
        appendText(anchor, link ? link[1] : token);
        parent.append(anchor);
      } else {
        appendText(parent, token);
      }
    }
    offset = match.index + token.length;
  }
  appendText(parent, text.slice(offset));
}

export function renderChatMessage(container, value) {
  const doc = container.ownerDocument;
  const text = String(value ?? '').replace(/\r\n?/g, '\n');
  const fragment = doc.createDocumentFragment();
  const lines = text.split('\n');
  let paragraph = [];
  let list = null;
  let fence = null;
  let codeLines = [];

  function flushParagraph() {
    if (!paragraph.length) return;
    const block = doc.createElement('p');
    setDirection(block, paragraph.join(' '));
    paragraph.forEach((line, index) => {
      if (index) block.append(doc.createElement('br'));
      appendInline(block, line);
    });
    fragment.append(block);
    paragraph = [];
  }

  function flushCode() {
    const pre = doc.createElement('pre');
    const code = doc.createElement('code');
    pre.dir = 'ltr';
    code.textContent = codeLines.join('\n');
    pre.append(code);
    fragment.append(pre);
    codeLines = [];
    fence = null;
  }

  for (const line of lines) {
    const trimmed = line.trim();
    if (fence) {
      if (trimmed.startsWith(fence)) flushCode();
      else codeLines.push(line);
      continue;
    }
    if (/^```/.test(trimmed)) {
      flushParagraph();
      list = null;
      fence = '```';
      continue;
    }
    if (!trimmed) {
      flushParagraph();
      list = null;
      continue;
    }
    const heading = trimmed.match(/^#{1,6}\s+(.+)$/);
    if (heading) {
      flushParagraph();
      list = null;
      const block = doc.createElement('h3');
      setDirection(block, heading[1]);
      appendInline(block, heading[1]);
      fragment.append(block);
      continue;
    }
    const item = trimmed.match(/^(?:([-*•])|([0-9٠-٩]+)[.)])\s+(.+)$/);
    if (item) {
      flushParagraph();
      const tag = item[2] ? 'ol' : 'ul';
      const direction = messageDirection(item[3]);
      if (!list || list.localName !== tag || list.dir !== direction) {
        list = doc.createElement(tag);
        setDirection(list, item[3]);
        if (item[2]) {
          const number = item[2].replace(/[٠-٩]/g, digit => String(digit.charCodeAt(0) - 0x660));
          list.start = Number(number);
        }
        fragment.append(list);
      }
      const block = doc.createElement('li');
      setDirection(block, item[3]);
      appendInline(block, item[3]);
      list.append(block);
      continue;
    }
    list = null;
    // Separate differently directed lines even without an empty line between them.
    if (paragraph.length && messageDirection(paragraph[0]) !== messageDirection(line)) flushParagraph();
    paragraph.push(line);
  }
  flushParagraph();
  if (fence) flushCode();
  container.classList.add('chat-message-content');
  container.dir = 'auto';
  container.replaceChildren(fragment);
}
