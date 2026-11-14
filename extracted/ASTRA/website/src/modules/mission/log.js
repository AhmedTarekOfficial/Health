// Notes stay in memory for this page session; nothing is sent to a server.
const entries = [];
const escapeHtml = text => text.replace(/[&<>"']/g, character => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
}[character]));

export function addEntry(title, note) {
  entries.unshift({ title, note, time: new Date() });
  renderLog();
}

function renderLog() {
  const root = document.querySelector('#log-list');
  document.querySelector('#export-log').disabled = !entries.length;
  document.querySelector('#note-count').textContent =
    `${entries.length} ${entries.length === 1 ? 'note' : 'notes'}`;

  root.innerHTML = entries.length ? entries.map(entry => `
    <article class="log-entry">
      <time datetime="${entry.time.toISOString()}">${new Intl.DateTimeFormat('en-GB', {
        dateStyle: 'medium', timeStyle: 'short'
      }).format(entry.time)}</time>
      <h3>${escapeHtml(entry.title)}</h3>
      <p>${escapeHtml(entry.note)}</p>
    </article>
  `).join('') : '<p class="notes-empty">Your saved check-ins will appear here.</p>';
}

export function initLog() {
  renderLog();
  document.querySelector('#export-log').addEventListener('click', () => {
    if (!entries.length) return;
    const text = 'ORBITAL — Session notes\nDesign prototype. No medical assessment.\n\n' +
      entries.map(entry => `${entry.time.toISOString()}\n${entry.title}\n${entry.note}`).join('\n\n');
    const url = URL.createObjectURL(new Blob([text], { type: 'text/plain' }));
    const link = document.createElement('a');
    link.href = url;
    link.download = 'orbital-session-notes.txt';
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
}
