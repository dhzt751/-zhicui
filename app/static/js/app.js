import { api } from './api.js';
import { toast } from './ui.js';
import { cleanupVideos, renderVideos } from './views/videos.js';
import { cleanupDocuments, renderDocuments } from './views/documents.js';
import { cleanupKnowledge, renderKnowledge } from './views/knowledge.js';
import { cleanupLive, renderLive } from './views/live.js';

const routes = {
  videos: { render: renderVideos, cleanup: cleanupVideos },
  documents: { render: renderDocuments, cleanup: cleanupDocuments },
  knowledge: { render: renderKnowledge, cleanup: cleanupKnowledge },
  live: { render: renderLive, cleanup: cleanupLive },
};

let activeCleanup = null;

function parseRoute() {
  const raw = location.hash.replace(/^#\/?/, '');
  const [section, id] = raw.split('/');
  return { section: routes[section] ? section : 'videos', id: id || '' };
}

async function renderRoute() {
  const { section, id } = parseRoute();
  const view = routes[section];
  const root = document.getElementById('view');

  if (typeof activeCleanup === 'function') {
    try {
      activeCleanup();
    } catch {
      /* ignore */
    }
  }
  activeCleanup = view.cleanup;

  document.querySelectorAll('[data-nav]').forEach((link) => {
    link.classList.toggle('active', link.dataset.nav === section);
  });
  document.title = {
    videos: '视频学习 · 知萃',
    documents: '文档题库 · 知萃',
    knowledge: '知识库 · 知萃',
    live: '实时听课 · 知萃',
  }[section];

  window.scrollTo({ top: 0, behavior: 'instant' });

  try {
    await view.render(root, { id });
  } catch (error) {
    root.innerHTML = `<div class="page"><div class="card empty"><b>页面出错</b><p>${String(error.message || error)}</p></div></div>`;
    toast(error.message || '页面加载失败', 'error');
  }
}

async function initStatus() {
  const chip = document.getElementById('status-chip');
  const text = document.getElementById('status-text');
  try {
    const status = await api.status();
    window.__ZHI_STATUS = status;
    const live = status.llm_ready;
    chip.classList.toggle('live', live);
    text.textContent = live ? `模型已连接 · ${status.model}` : '演示模式';
    chip.onclick = () => {
      const lines = [
        live ? `已连接模型：${status.model}` : '未配置大模型，当前为演示模式。',
        `接口地址：${status.base_url || '未设置'}`,
        `语音转写：${status.asr_ready ? status.asr_model : '未配置'}`,
        `ffmpeg：${status.ffmpeg ? '已安装' : '未安装（长音频切片需要）'}`,
        '',
        '在项目根目录的 .env 里填写 LLM_BASE_URL、LLM_API_KEY、LLM_MODEL 后重启即可启用真实模型。',
      ];
      toast(lines.join('\n'));
    };
  } catch {
    chip.classList.remove('live');
    text.textContent = '服务未就绪';
  }
}

window.addEventListener('hashchange', renderRoute);

async function boot() {
  if (!location.hash) location.hash = '#/videos';
  await initStatus();
  await renderRoute();
}

if (document.readyState === 'loading') {
  window.addEventListener('DOMContentLoaded', boot);
} else {
  boot();
}