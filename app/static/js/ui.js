export function escapeHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

export function fmtClock(ms) {
  const total = Math.max(0, Math.floor((ms || 0) / 1000));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  if (hours > 0) return `${hours}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
  return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
}

export function fmtDuration(seconds) {
  const total = Math.round(seconds || 0);
  if (!total) return '—';
  if (total < 60) return `${total} 秒`;
  const minutes = Math.floor(total / 60);
  const rest = total % 60;
  if (minutes < 60) return rest ? `${minutes} 分 ${rest} 秒` : `${minutes} 分钟`;
  const hours = Math.floor(minutes / 60);
  return `${hours} 小时 ${minutes % 60} 分`;
}

let toastTimer = null;

export function toast(message, type = '') {
  const node = document.getElementById('toast');
  if (!node) return;
  node.textContent = message;
  node.className = `toast show ${type}`.trim();
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    node.className = 'toast';
  }, type === 'error' ? 5200 : 3200);
}

export function statusBadge(status) {
  const map = {
    queued: ['run', '排队中'],
    running: ['run', '处理中'],
    done: ['ok', '已完成'],
    failed: ['err', '失败'],
  };
  const [cls, label] = map[status] || ['', status || '未知'];
  return `<span class="badge ${cls}">${escapeHtml(label)}</span>`;
}

export function openModal(html, onMount) {
  const node = document.getElementById('modal');
  node.innerHTML = `<div class="modal-box">${html}</div>`;
  node.classList.remove('hidden');
  node.onclick = (event) => {
    if (event.target === node) closeModal();
  };
  if (onMount) onMount(node.querySelector('.modal-box'));
}

export function closeModal() {
  const node = document.getElementById('modal');
  node.classList.add('hidden');
  node.innerHTML = '';
}

export function confirmDialog(title, text, confirmLabel = '确认') {
  return new Promise((resolve) => {
    openModal(
      `<h3>${escapeHtml(title)}</h3>
       <p class="m-sub">${escapeHtml(text)}</p>
       <div class="modal-actions">
         <button class="btn ghost" data-act="cancel">取消</button>
         <button class="btn danger" data-act="ok">${escapeHtml(confirmLabel)}</button>
       </div>`,
      (box) => {
        box.querySelector('[data-act="cancel"]').onclick = () => {
          closeModal();
          resolve(false);
        };
        box.querySelector('[data-act="ok"]').onclick = () => {
          closeModal();
          resolve(true);
        };
      },
    );
  });
}

export function debounce(fn, wait = 260) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };
}

export function platformLabel(video) {
  if (video.platform === 'upload') return '本地上传';
  if (video.platform === 'youtube') return 'YouTube';
  if (video.platform === 'bilibili') return 'B 站';
  return '外部链接';
}