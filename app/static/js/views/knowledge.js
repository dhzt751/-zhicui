import { api } from '../api.js';
import { debounce, escapeHtml, fmtClock } from '../ui.js';

export function cleanupKnowledge() {}

export async function renderKnowledge(root) {
  root.innerHTML = `<div class="page">
    <div class="page-head">
      <div>
        <p class="eyebrow">KNOWLEDGE BASE</p>
        <h1 class="page-title">所有提炼过的内容，都在这里</h1>
        <p class="page-sub">跨视频、跨文档检索知识点与题目。支持标题、摘要、关键词和题干。</p>
      </div>
    </div>
    <div class="search-row">
      <input class="input" id="k-search" placeholder="搜索知识点、关键词或题目…" spellcheck="false" />
    </div>
    <div id="k-stats" class="doc-grid" style="margin-bottom:20px"></div>
    <div id="k-results"><div class="card empty"><b>正在读取</b><p>加载知识库里…</p></div></div>
  </div>`;

  const search = root.querySelector('#k-search');
  const statsBox = root.querySelector('#k-stats');
  const results = root.querySelector('#k-results');

  try {
    const stats = await api.stats();
    statsBox.innerHTML = [
      ['视频', stats.videos],
      ['知识点', stats.knowledge_points],
      ['文档', stats.documents],
      ['题目', stats.questions],
    ]
      .map(
        ([label, value]) => `<div class="card" style="padding:14px 16px">
          <div style="font-family:var(--serif);font-size:24px;line-height:1.2">${value}</div>
          <div class="section-note">${label}</div>
        </div>`,
      )
      .join('');
  } catch {
    statsBox.innerHTML = '';
  }

  const render = async (term) => {
    try {
      const data = await api.knowledge(term);
      const empty = !data.points.length && !data.clusters.length && !data.questions.length;
      if (empty) {
        results.innerHTML = `<div class="card empty"><b>没有匹配的内容</b><p>换个关键词试试，或先去视频学习 / 文档题库里提炼内容。</p></div>`;
        return;
      }
      const pointsHtml = data.points.length
        ? `<div class="section">
            <div class="section-head"><h2 class="section-title">视频知识点</h2><span class="section-note">${data.points.length} 条</span></div>
            <div class="k-grid">${data.points
              .map(
                (point) => `<div class="card k-card" data-video="${point.video_id}" style="cursor:pointer">
                  <h4>${escapeHtml(point.title)}</h4>
                  <p>${escapeHtml(point.summary || '')}</p>
                  <div class="kfoot">
                    <span class="badge ok mono">${fmtClock(point.start_ms)}</span>
                    <span>${escapeHtml(point.video_title || '')}</span>
                    ${(point.keywords || []).slice(0, 3).map((k) => `<span class="chip">${escapeHtml(k)}</span>`).join('')}
                  </div>
                </div>`,
              )
              .join('')}</div>
          </div>`
        : '';
      const clustersHtml = data.clusters.length
        ? `<div class="section">
            <div class="section-head"><h2 class="section-title">文档知识点</h2><span class="section-note">${data.clusters.length} 组</span></div>
            <div class="k-grid">${data.clusters
              .map(
                (cluster) => `<div class="card k-card" data-doc="${cluster.document_id}" style="cursor:pointer">
                  <h4>${escapeHtml(cluster.title)}</h4>
                  <p>${escapeHtml(cluster.summary || '')}</p>
                  <div class="kfoot">
                    <span class="badge ok">${cluster.size} 题</span>
                    <span>${escapeHtml(cluster.filename || '')}</span>
                    ${(cluster.tags || []).slice(0, 3).map((t) => `<span class="chip">${escapeHtml(t)}</span>`).join('')}
                  </div>
                </div>`,
              )
              .join('')}</div>
          </div>`
        : '';
      const questionsHtml = data.questions.length
        ? `<div class="section">
            <div class="section-head"><h2 class="section-title">题目命中</h2><span class="section-note">${data.questions.length} 道</span></div>
            <div style="display:flex;flex-direction:column;gap:8px">${data.questions
              .map(
                (q) => `<div class="card" data-doc="${q.document_id}" style="padding:13px 16px;cursor:pointer">
                  <div style="font-size:13px">${escapeHtml((q.stem || '').slice(0, 140))}</div>
                  <div class="kfoot" style="margin-top:7px;display:flex;gap:7px;align-items:center;font-size:11px;color:var(--muted)">
                    <span>${escapeHtml(q.filename || '')}</span>
                    ${(q.tags || []).slice(0, 3).map((t) => `<span class="chip">${escapeHtml(t)}</span>`).join('')}
                  </div>
                </div>`,
              )
              .join('')}</div>
          </div>`
        : '';
      results.innerHTML = pointsHtml + clustersHtml + questionsHtml;
    } catch (error) {
      results.innerHTML = `<div class="card empty"><b>检索失败</b><p>${escapeHtml(error.message)}</p></div>`;
    }
  };

  results.addEventListener('click', (event) => {
    const card = event.target.closest('[data-video],[data-doc]');
    if (!card) return;
    if (card.dataset.video) location.hash = `#/videos/${card.dataset.video}`;
    if (card.dataset.doc) location.hash = `#/documents/${card.dataset.doc}`;
  });

  search.oninput = debounce(() => render(search.value.trim()), 280);
  await render('');
}