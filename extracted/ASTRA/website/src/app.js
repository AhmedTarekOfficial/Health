import { mountIcons } from './shared/icons.js';
import { initAmbient } from './modules/ambient/ambient.js';
import { initDashboard } from './modules/dashboard/dashboard.js';
import { initCheckin } from './modules/checkin/checkin.js';
import { initLog } from './modules/mission/log.js';

const dialog = document.querySelector('#detail-dialog');

function showDialog(html) {
  document.querySelector('#dialog-content').innerHTML = html;
  dialog.showModal();
}

let toastTimer;
function toast(message) {
  const node = document.querySelector('#toast');
  node.textContent = message;
  node.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { node.hidden = true; }, 4000);
}

// Everything lives on one page. Metric explanations and optional writing use small dialogs.
mountIcons();
initDashboard(showDialog, toast);
initLog();
initCheckin(toast);
initAmbient();

for (const selector of ['#close-dialog', '#dialog-done']) {
  document.querySelector(selector).addEventListener('click', () => dialog.close());
}
dialog.addEventListener('click', event => {
  if (event.target !== dialog) return;
  const rect = dialog.getBoundingClientRect();
  const outside = event.clientX < rect.left || event.clientX > rect.right ||
    event.clientY < rect.top || event.clientY > rect.bottom;
  if (outside) dialog.close();
});
