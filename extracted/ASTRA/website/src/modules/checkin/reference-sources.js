import { messageDirection } from './message-renderer.js';

// Sources are supplied by the server's retrieval result, never invented by the model.
export function renderReferenceSources(bubble, sources = [], status = {}, reply = '') {
  bubble.querySelector('.reference-sources')?.remove();
  if (!status.state) return;
  const doc = bubble.ownerDocument;
  const arabic = messageDirection(reply) === 'rtl';
  const panel = doc.createElement('section');
  panel.className = 'reference-sources';
  panel.dir = arabic ? 'rtl' : 'ltr';
  panel.lang = arabic ? 'ar' : 'en';
  const title = doc.createElement('p');
  title.className = 'reference-title';
  title.textContent = arabic ? 'المراجع المسترجعة' : 'Retrieved references';
  panel.append(title);

  if (!sources.length) {
    const note = doc.createElement('p');
    note.textContent = status.state === 'unavailable'
      ? (arabic ? 'تعذر الوصول للمصادر المحددة. لا توجد مراجع متاحة لهذا الرد.' : 'Selected sources are unavailable. No references were retrieved for this reply.')
      : (arabic ? 'لم نجد معلومات مطابقة للسؤال في الصفحات المحددة.' : 'No matching information was found in the selected pages.');
    panel.append(note);
  }
  for (const source of sources) {
    let url;
    try { url = new URL(source.url); } catch { continue; }
    if (url.protocol !== 'https:' || url.username || url.password) continue;
    const row = doc.createElement('div');
    row.className = 'reference-row';
    const link = doc.createElement('a');
    link.href = url.href;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    link.dir = 'auto';
    link.textContent = `[${source.id}] ${source.title}`;
    row.append(link);
    const date = new Date(source.fetched_at);
    if (!Number.isNaN(date.getTime())) {
      const info = doc.createElement('small');
      info.textContent = arabic ? 'تاريخ الجلب: ' : 'Fetched: ';
      const stamp = doc.createElement('bdi');
      stamp.dir = 'ltr';
      stamp.textContent = date.toISOString().slice(0, 16).replace('T', ' ') + ' UTC';
      info.append(stamp);
      if (source.stale) info.append(arabic ? ' — نسخة محفوظة قديمة' : ' — older cached copy');
      row.append(info);
    }
    panel.append(row);
  }
  if (status.unavailable_sources?.length || status.stale_sources?.length) {
    const note = doc.createElement('p');
    note.textContent = arabic ? 'بعض المصادر غير متاحة أو تعذر تحديثها.' : 'Some sources are unavailable or could not be refreshed.';
    panel.append(note);
  }
  bubble.append(panel);
}
