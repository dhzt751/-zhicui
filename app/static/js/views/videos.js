import { api } from '../api.js';
import {
  closeModal,
  confirmDialog,
  escapeHtml,
  fmtClock,
  fmtDuration,
  openModal,
  platformLabel,
  statusBadge,
  toast,
} from '../ui.js';
import { mountPlayer } from '../player.js';

let pollTimer = null;
let listTimer = null;
let player = null;

export function cleanupVideos() {
  clearInterval(pollTimer);
  clearInterval(listTimer);
  pollTimer = null;
  listTimer = null;
  if (player) {
    player.destroy();
    player = null;
  }
}

export async function renderVideos(root, params) {
  cleanupVideos();
  if (params.id) {
    await renderDetail(root, Number(params.id));
  } else {
    await renderList(root);
  }
}

function listItem(video) {
  const badge = statusBadge(video.status);
  const title =
    video.title || (video.status === 'failed' ? '未命名视频（解析失败）' : '解析中…');
  const thumb = video.cover
    ? `<img src="${escapeHtml(video.cover)}" alt="" loading="lazy" referrerpolicy="no-referrer">`
    : '<span>视频</span>';
  const running =
    video.status === 'running' || video.status === 'queued'
      ? `<div class="progress" style="margin-top:7px"><i style="width:${Number(video.progress || 0)}%"></i></div>`
      : '';
  return `<button class="video-item" data-id="${video.id}" type="button">
    <div class="thumb">${thumb}</div>
    <div class="vmeta">
      <div class="vtitle">${escapeHtml(title)}</div>
      <div class="vsub">${badge}<span>·</span><span>${video.kp_count || 0} 个知识点</span></div>
      ${running}
    </div>
  </button>`;
}

async function renderList(root) {
  root.innerHTML = `<div class="page">
    <div class="page-head">
      <div>
        <p class="eyebrow">VIDEO → KNOWLEDGE</p>
        <h1 class="page-title">把一节课，读成一页知识</h1>
        <p class="page-sub">粘贴 B 站或 YouTube 链接，或上传本地视频。系统会转写内容、提炼带时间戳的知识点，并生成可导出的总结。</p>
      </div>
    </div>
    <div class="card" style="padding:18px">
      <div class="url-row">
        <input class="input" id="video-url" spellcheck="false"
          placeholder="https://www.bilibili.com/video/BV... 或 https://www.youtube.com/watch?v=..." />
        <button class="btn primary" id="btn-parse">解析视频</button>
        <button class="btn" id="btn-upload">上传视频</button>
      </div>
      <p class="hint-line">B 站无字幕时自动走语音转写；YouTube 需要网络可达，可在 .env 配置代理。支持 mp4 / mkv / mov / webm。</p>
      <input type="file" id="video-file" accept="video/*" class="hidden" />
    </div>
    <div class="section">
      <div class="section-head">
        <h2 class="section-title">视频库</h2>
        <span class="section-note" id="video-count"></span>
      </div>
      <div id="video-list"><div class="empty">正在读取…</div></div>
    </div>
  </div>`;

  const urlInput = root.querySelector('#video-url');
  const parseBtn = root.querySelector('#btn-parse');
  const uploadBtn = root.querySelector('#btn-upload');
  const fileInput = root.querySelector('#video-file');
  const listBox = root.querySelector('#video-list');
  const countBox = root.querySelector('#video-count');

  const refresh = async () => {
    try {
      const videos = await api.videos();
      countBox.textContent = videos.length ? `共 ${videos.length} 个` : '';
      listBox.innerHTML = videos.length
        ? `<div class="doc-grid">${videos.map(listItem).join('')}</div>`
        : `<div class="card empty"><b>还没有视频</b><p>先粘贴一个链接，或上传一段课程录像试试。</p></div>`;
      const hasRunning = videos.some((v) => v.status === 'queued' || v.status === 'running');
      clearInterval(listTimer);
      if (hasRunning) {
        listTimer = setInterval(refresh, 3000);
      }
    } catch (error) {
      listBox.innerHTML = `<div class="card empty"><b>加载失败</b><p>${escapeHtml(error.message)}</p></div>`;
    }
  };

  listBox.addEventListener('click', (event) => {
    const item = event.target.closest('.video-item');
    if (item) location.hash = `#/videos/${item.dataset.id}`;
  });

  const submitUrl = async () => {
    const url = urlInput.value.trim();
    if (!url) {
      toast('请先粘贴视频链接', 'error');
      urlInput.focus();
      return;
    }
    parseBtn.disabled = true;
    parseBtn.textContent = '提交中…';
    try {
      const result = await api.createVideo(url);
      location.hash = `#/videos/${result.video_id}`;
    } catch (error) {
      toast(error.message, 'error');
      parseBtn.disabled = false;
      parseBtn.textContent = '解析视频';
    }
  };

  parseBtn.onclick = submitUrl;
  urlInput.onkeydown = (event) => {
    if (event.key === 'Enter') submitUrl();
  };
  uploadBtn.onclick = () => fileInput.click();
  fileInput.onchange = async () => {
    const file = fileInput.files?.[0];
    if (!file) return;
    uploadBtn.disabled = true;
    uploadBtn.textContent = '上传中…';
    try {
      const result = await api.uploadVideo(file);
      location.hash = `#/videos/${result.video_id}`;
    } catch (error) {
      toast(error.message, 'error');
      uploadBtn.disabled = false;
      uploadBtn.textContent = '上传视频';
    }
    fileInput.value = '';
  };

  await refresh();
}

const SOURCE_LABELS = { subtitle: '平台字幕', asr: '语音转写', import: '导入字幕' };

function metaLine(video) {
  const parts = [platformLabel(video)];
  if (video.author) parts.push(escapeHtml(video.author));
  if (video.duration) parts.push(fmtDuration(video.duration));
  if (video.transcript_source) {
    parts.push(`内容来源：${SOURCE_LABELS[video.transcript_source] || video.transcript_source}`);
  }
  return parts.filter(Boolean).join(' · ');
}

function openTranscriptImport(videoId) {
  openModal(
    `<h3>导入字幕或讲义</h3>
     <p class="m-sub">支持 SRT / VTT 文件，也可以直接粘贴讲解文字。导入后会以这份内容为准重新提炼知识点。</p>
     <textarea class="input" id="tr-text" spellcheck="false"
       style="height:180px;padding:10px 12px;resize:vertical;line-height:1.6"
       placeholder="粘贴字幕、讲义或课堂笔记…"></textarea>
     <div style="margin-top:10px;display:flex;gap:8px;align-items:center">
       <input type="file" id="tr-file" accept=".srt,.vtt,.txt,.md" style="font-size:12px" />
       <span class="section-note">或选择一个字幕文件</span>
     </div>
     <div class="modal-actions">
       <button class="btn ghost" data-act="cancel" type="button">取消</button>
       <button class="btn primary" data-act="save" type="button">导入并重新提炼</button>
     </div>`,
    (box) => {
      const area = box.querySelector('#tr-text');
      box.querySelector('#tr-file').onchange = (event) => {
        const file = event.target.files?.[0];
        if (!file) return;
        const reader = new FileReader();
        reader.onload = () => {
          area.value = String(reader.result || '');
          toast(`已读取 ${file.name}`);
        };
        reader.readAsText(file);
      };
      box.querySelector('[data-act="cancel"]').onclick = () => closeModal();
      box.querySelector('[data-act="save"]').onclick = async () => {
        const text = area.value.trim();
        if (text.length < 20) {
          toast('内容太短，请至少提供一段完整讲解', 'error');
          return;
        }
        try {
          await api.importTranscript(videoId, text);
          closeModal();
          toast('已导入，正在重新提炼知识点');
          await renderVideos(document.getElementById('view'), { id: String(videoId) });
        } catch (error) {
          toast(error.message, 'error');
        }
      };
    },
  );
}

async function renderDetail(root, id) {
  if (!id) {
    location.hash = '#/videos';
    return;
  }
  root.innerHTML = `<div class="page">
    <button class="btn ghost sm" id="btn-back" type="button">← 返回视频库</button>
    <div id="detail-head" style="margin-top:14px"></div>
    <div id="detail-body" style="margin-top:16px"><div class="card empty"><b>读取中</b><p>正在加载视频信息…</p></div></div>
  </div>`;
  root.querySelector('#btn-back').onclick = () => {
    location.hash = '#/videos';
  };

  const headBox = root.querySelector('#detail-head');
  const body = root.querySelector('#detail-body');

  const load = async () => {
    let data;
    try {
      data = await api.video(id);
    } catch (error) {
      body.innerHTML = `<div class="card empty"><b>视频不存在</b><p>${escapeHtml(error.message)}</p></div>`;
      return;
    }
    const video = data.video;
    headBox.innerHTML = `<div class="video-head">
      <div>
        <h1 class="video-title">${escapeHtml(video.title || '解析中…')}</h1>
        <div class="video-meta">${metaLine(video)} ${statusBadge(video.status)}</div>
      </div>
      <div class="head-actions">
        ${video.status === 'done' ? `<a class="btn sm" href="/api/videos/${id}/summary.md">导出总结</a>` : ''}
        <button class="btn sm" id="btn-import" type="button">导入字幕</button>
        <button class="btn sm danger" id="btn-del" type="button">删除</button>
      </div>
    </div>`;

    root.querySelector('#btn-import').onclick = () => openTranscriptImport(id);
    root.querySelector('#btn-del').onclick = async () => {
      const ok = await confirmDialog('删除这个视频？', '知识点、字幕、总结都会一并移除，无法恢复。', '删除');
      if (!ok) return;
      try {
        await api.deleteVideo(id);
        toast('已删除');
        location.hash = '#/videos';
      } catch (error) {
        toast(error.message, 'error');
      }
    };

    if (video.status === 'done') {
      renderReady(body, data);
    } else if (video.status === 'failed') {
      body.innerHTML = `<div class="card empty"><b>解析失败</b><p>${escapeHtml(video.error || '未知错误')}</p></div>`;
    } else {
      renderProgress(body, video);
      clearInterval(pollTimer);
      pollTimer = setInterval(async () => {
        try {
          const next = await api.video(id);
          if (next.video.status === 'done') {
            clearInterval(pollTimer);
            pollTimer = null;
            await renderDetailInPlace(headBox, body, next, id, root);
          } else if (next.video.status === 'failed') {
            clearInterval(pollTimer);
            pollTimer = null;
            body.innerHTML = `<div class="card empty"><b>解析失败</b><p>${escapeHtml(next.video.error || '未知错误')}</p></div>`;
          } else {
            updateProgress(body, next.video);
          }
        } catch {
          clearInterval(pollTimer);
        }
      }, 1400);
    }
  };

  await load();
}

function renderProgress(box, video) {
  box.innerHTML = `<div class="card progress-card">
    <div class="ptitle">正在解析与提炼</div>
    <div class="progress"><i id="bar" style="width:${Number(video.progress || 2)}%"></i></div>
    <div class="pstage"><span id="stage">${escapeHtml(video.stage || '准备中')}</span><span id="pct">${Number(video.progress || 0)}%</span></div>
    <p class="hint-line">长视频需要先转写再提炼，通常几分钟；可以先去处理文档，稍后回来。</p>
  </div>`;
}

function updateProgress(box, video) {
  const bar = box.querySelector('#bar');
  const stage = box.querySelector('#stage');
  const pct = box.querySelector('#pct');
  if (bar) bar.style.width = `${Number(video.progress || 0)}%`;
  if (stage) stage.textContent = video.stage || '处理中';
  if (pct) pct.textContent = `${Number(video.progress || 0)}%`;
}

async function renderDetailInPlace(headBox, body, data, id, root) {
  const video = data.video;
  headBox.innerHTML = `<div class="video-head">
    <div>
      <h1 class="video-title">${escapeHtml(video.title || '未命名视频')}</h1>
      <div class="video-meta">${metaLine(video)} ${statusBadge(video.status)}</div>
    </div>
    <div class="head-actions">
      <a class="btn sm" href="/api/videos/${id}/summary.md">导出总结</a>
      <button class="btn sm" id="btn-import" type="button">导入字幕</button>
      <button class="btn sm danger" id="btn-del" type="button">删除</button>
    </div>
  </div>`;
  headBox.querySelector('#btn-import').onclick = () => openTranscriptImport(id);
  headBox.querySelector('#btn-del').onclick = async () => {
    const ok = await confirmDialog('删除这个视频？', '知识点、字幕、总结都会一并移除，无法恢复。', '删除');
    if (!ok) return;
    await api.deleteVideo(id);
    location.hash = '#/videos';
  };
  renderReady(body, data);
}

function summaryHtml(video) {
  const summary = video.summary;
  if (!summary) {
    return `<div class="card summary"><h3>视频总结</h3><p class="section-note">这个视频还没有生成总结。</p></div>`;
  }
  const chapters = (summary.chapters || [])
    .map(
      (chapter) => `<div class="chapter">
        <h4><time>${fmtClock(chapter.start_ms || 0)}</time>${escapeHtml(chapter.title || '')}</h4>
        <p style="margin:0">${escapeHtml(chapter.summary || '')}</p>
        ${
          chapter.key_points?.length
            ? `<ul>${chapter.key_points.map((k) => `<li>${escapeHtml(k)}</li>`).join('')}</ul>`
            : ''
        }
      </div>`,
    )
    .join('');
  return `<div class="card summary">
    <h3>视频总结</h3>
    <p>${escapeHtml(summary.overview || '')}</p>
    ${chapters}
    ${
      summary.conclusion
        ? `<div class="chapter"><h4>结论与建议</h4><p style="margin:0">${escapeHtml(summary.conclusion)}</p></div>`
        : ''
    }
  </div>`;
}

function renderReady(body, data) {
  const video = data.video;
  const points = data.knowledge_points || [];
  const segments = data.segments || [];

  const kpHtml = points.length
    ? points
        .map((point, index) => {
          const confidence = Number(point.confidence || 0);
          const unsure = confidence > 0 && confidence < 0.72;
          const badges = [];
          if (point.difficulty) badges.push(`<span class="diff">${escapeHtml(point.difficulty)}</span>`);
          if (point.kind) badges.push(`<span class="chip">${escapeHtml(point.kind)}</span>`);
          if (unsure) badges.push('<span class="diff">待确认</span>');
          const chips = badges
            .concat((point.keywords || []).slice(0, 4).map((k) => `<span class="chip">${escapeHtml(k)}</span>`))
            .join('');
          return `<div class="kp ${unsure ? 'low' : ''}" data-ms="${point.start_ms}" data-index="${index}">
            <div class="kp-top">
              <span class="kp-title">${escapeHtml(point.title)}</span>
              <span class="kp-time">${fmtClock(point.start_ms)}</span>
            </div>
            <div class="kp-sum">${escapeHtml(point.summary || '')}</div>
            ${point.evidence ? `<div class="kp-evidence">原文：${escapeHtml(point.evidence)}</div>` : ''}
            ${chips ? `<div class="chips">${chips}</div>` : ''}
          </div>`;
        })
        .join('')
    : `<div class="card empty" style="padding:26px 18px"><b>没有提取到知识点</b><p>视频可能以闲聊或演示为主，可查看下方字幕确认。</p></div>`;

  const subtitleHtml = segments.length
    ? `<details class="card" style="padding:14px 18px;margin-top:18px">
        <summary style="cursor:pointer;font-size:13px;color:var(--muted)">查看完整字幕（${segments.length} 段，点击可跳转）</summary>
        <div id="subs" style="margin-top:10px;max-height:340px;overflow:auto;font-size:12px;color:var(--ink-2)">
          ${segments
            .map(
              (seg) =>
                `<div data-ms="${seg.start_ms}" style="padding:4px 6px;border-radius:5px;cursor:pointer"><span class="mono" style="color:var(--muted);margin-right:8px">${fmtClock(seg.start_ms)}</span>${escapeHtml(seg.text)}</div>`,
            )
            .join('')}
        </div>
      </details>`
    : '';

  body.innerHTML = `<div class="detail-grid">
    <div class="detail-main">
      <div class="player-shell" id="player"></div>
      <div class="player-note" id="player-note"></div>
      <div class="section">${summaryHtml(video)}</div>
      ${subtitleHtml}
    </div>
    <aside>
      <div class="section-head" style="margin-top:0">
        <h2 class="section-title">知识时间轴</h2>
        <span class="section-note">${points.length} 个知识点</span>
      </div>
      <div class="timeline" id="timeline">${kpHtml}</div>
    </aside>
  </div>`;

  const shell = body.querySelector('#player');
  const note = body.querySelector('#player-note');
  const cards = [...body.querySelectorAll('.kp')];
  let activeIndex = -1;

  const setActive = (index, scroll = true) => {
    if (index === activeIndex) return;
    cards[activeIndex]?.classList.remove('active');
    activeIndex = index;
    const card = cards[activeIndex];
    if (!card) return;
    card.classList.add('active');
    if (scroll) card.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  };

  player = mountPlayer(shell, video, (ms) => {
    if (!points.length) return;
    let index = points.findIndex((p) => ms >= p.start_ms && ms < p.end_ms);
    if (index === -1) {
      index = points.findLastIndex((p) => p.start_ms <= ms);
      if (index === -1) index = 0;
    }
    setActive(index);
  });

  const notes = {
    follow: '播放时知识点会自动高亮；点击右侧卡片可跳转到对应时间。',
    jump: 'B 站内嵌播放器无法读取播放进度，点击知识点会直接跳转定位，高亮为手动标记。',
    none: '这个链接不支持内嵌播放，可以对照右侧时间轴在原平台观看。',
  };
  note.textContent = notes[player.mode] || notes.none;

  if (player.mode === 'jump' && points.length) {
    setActive(0, false);
  }

  body.querySelector('#timeline').addEventListener('click', (event) => {
    const card = event.target.closest('.kp');
    if (!card) return;
    const index = Number(card.dataset.index);
    const point = points[index];
    if (!point) return;
    player.seek(point.start_ms);
    if (player.mode === 'jump') setActive(index, false);
    else card.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  });

  body.querySelector('#subs')?.addEventListener('click', (event) => {
    const row = event.target.closest('[data-ms]');
    if (!row) return;
    player.seek(Number(row.dataset.ms));
  });

  if (window.__ZHI_STATUS?.demo_mode) {
    const tip = document.createElement('div');
    tip.className = 'player-note';
    tip.textContent = '当前为演示模式：未配置大模型 API Key，知识点与总结由规则生成，效果有限。';
    note.after(tip);
  }
}