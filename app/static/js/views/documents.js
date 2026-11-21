import { api } from '../api.js';
import {
  closeModal,
  confirmDialog,
  debounce,
  escapeHtml,
  openModal,
  statusBadge,
  toast,
} from '../ui.js';

let pollTimer = null;
let listTimer = null;

export function cleanupDocuments() {
  clearInterval(pollTimer);
  clearInterval(listTimer);
  pollTimer = null;
  listTimer = null;
}

export async function renderDocuments(root, params) {
  cleanupDocuments();
  if (params.id) {
    await renderDocDetail(root, Number(params.id));
  } else {
    await renderDocList(root);
  }
}

function docCard(doc) {
  return `<div class="card doc-card" data-id="${doc.id}">
    <div class="dname">${escapeHtml(doc.filename)}</div>
    <div class="dmeta">
      ${statusBadge(doc.status)}
      <span>${doc.cluster_count || 0} 个知识点</span>
      <span>·</span>
      <span>${doc.question_count || 0} 道题</span>
    </div>
    ${
      doc.status === 'running' || doc.status === 'queued'
        ? `<div class="progress" style="margin-top:9px"><i style="width:${Number(doc.progress || 0)}%"></i></div>`
        : ''
    }
    ${doc.error ? `<p class="hint-line" style="color:var(--danger)">${escapeHtml(doc.error).slice(0, 90)}</p>` : ''}
  </div>`;
}

async function renderDocList(root) {
  root.innerHTML = `<div class="page">
    <div class="page-head">
      <div>
        <p class="eyebrow">DOCUMENT → QUESTION BANK</p>
        <h1 class="page-title">同一知识点，只留一处</h1>
        <p class="page-sub">上传练习题、讲义或错题图片，系统会抽取题目，把考同一知识点的题自动聚到一起。</p>
      </div>
    </div>
    <div class="dropzone" id="dropzone">
      <b>点击选择，或把文件拖到这里</b>
      <span>PDF · DOCX · TXT · MD · 图片（图片需要多模态模型）</span>
      <input type="file" id="doc-file" class="hidden"
        accept=".pdf,.docx,.txt,.md,.markdown,.csv,.png,.jpg,.jpeg,.webp,.bmp" />
    </div>
    <div class="section">
      <div class="section-head">
        <h2 class="section-title">文档库</h2>
        <span class="section-note" id="doc-count"></span>
      </div>
      <div id="doc-list"><div class="empty">正在读取…</div></div>
    </div>
  </div>`;

  const zone = root.querySelector('#dropzone');
  const fileInput = root.querySelector('#doc-file');
  const listBox = root.querySelector('#doc-list');
  const countBox = root.querySelector('#doc-count');

  const refresh = async () => {
    try {
      const docs = await api.documents();
      countBox.textContent = docs.length ? `共 ${docs.length} 份` : '';
      listBox.innerHTML = docs.length
        ? `<div class="doc-grid">${docs.map(docCard).join('')}</div>`
        : `<div class="card empty"><b>还没有文档</b><p>上传一份练习册或讲义，看看题目会被怎么归类。</p></div>`;
      const running = docs.some((d) => d.status === 'queued' || d.status === 'running');
      clearInterval(listTimer);
      if (running) listTimer = setInterval(refresh, 3000);
    } catch (error) {
      listBox.innerHTML = `<div class="card empty"><b>加载失败</b><p>${escapeHtml(error.message)}</p></div>`;
    }
  };

  listBox.addEventListener('click', (event) => {
    const card = event.target.closest('.doc-card');
    if (card) location.hash = `#/documents/${card.dataset.id}`;
  });

  const upload = async (file) => {
    if (!file) return;
    zone.classList.remove('drag');
    zone.querySelector('b').textContent = '正在上传…';
    try {
      const result = await api.uploadDocument(file);
      toast('已上传，正在解析题目');
      location.hash = `#/documents/${result.document_id}`;
    } catch (error) {
      toast(error.message, 'error');
      zone.querySelector('b').textContent = '点击选择，或把文件拖到这里';
    }
    fileInput.value = '';
  };

  zone.onclick = () => fileInput.click();
  fileInput.onchange = () => upload(fileInput.files?.[0]);
  zone.ondragover = (event) => {
    event.preventDefault();
    zone.classList.add('drag');
  };
  zone.ondragleave = () => zone.classList.remove('drag');
  zone.ondrop = (event) => {
    event.preventDefault();
    upload(event.dataTransfer?.files?.[0]);
  };

  await refresh();
}

function isAnswerOption(option, answer) {
  if (!answer) return false;
  const letter = (option.match(/^\s*([A-DＡ-Ｄ])/) || [])[1];
  if (!letter) return false;
  const normalized = letter.replace('Ａ', 'A').replace('Ｂ', 'B').replace('Ｃ', 'C').replace('Ｄ', 'D');
  return answer.toUpperCase().includes(normalized);
}

function questionHtml(question, clusters) {
  const options = (question.options || [])
    .map(
      (option) =>
        `<li class="${isAnswerOption(option, question.answer) ? 'answer' : ''}">${escapeHtml(option)}</li>`,
    )
    .join('');
  const tags = (question.tags || [])
    .map((tag) => `<span class="chip">${escapeHtml(tag)}</span>`)
    .join('');
  return `<div class="q" data-qid="${question.id}">
    <div class="q-stem"><span class="q-no">${question.number || ''}.</span>${escapeHtml(question.stem)}</div>
    ${options ? `<ul class="q-opts">${options}</ul>` : ''}
    ${question.answer ? `<div class="q-answer">答案：${escapeHtml(question.answer)}</div>` : ''}
    ${question.analysis ? `<div class="q-analysis">解析：${escapeHtml(question.analysis)}</div>` : ''}
    <div class="q-foot">${tags}<button class="btn ghost sm" data-edit="${question.id}">编辑归属</button></div>
  </div>`;
}

function clustersHtml(clusters, questions) {
  if (!questions.length) {
    return `<div class="card empty"><b>没有识别到题目</b><p>文档可能是纯叙述内容，或题目格式比较特殊。可尝试换成带答案的整理版。</p></div>`;
  }
  const groups = [
    ...clusters.map((cluster) => ({
      cluster,
      items: questions.filter((q) => q.cluster_id === cluster.id),
    })),
    { cluster: null, items: questions.filter((q) => !q.cluster_id) },
  ].filter((group) => group.items.length);

  return groups
    .map((group, index) => {
      const cluster = group.cluster;
      const title = cluster ? cluster.title : '未归类题目';
      const summary = cluster ? cluster.summary : '这些题还没能归入任何知识点，可以手动调整。';
      const tags = (cluster?.tags || [])
        .slice(0, 6)
        .map((tag) => `<span class="chip">${escapeHtml(tag)}</span>`)
        .join('');
      return `<div class="card cluster">
        <div class="cluster-head">
          <div>
            <h3>${escapeHtml(title)}</h3>
            <p>${escapeHtml(summary || '')}</p>
            ${tags ? `<div class="chips">${tags}</div>` : ''}
          </div>
          <span class="cluster-idx">${group.items.length} 题</span>
        </div>
        ${group.items.map((q) => questionHtml(q, clusters)).join('')}
      </div>`;
    })
    .join('');
}

function openEditor(question, clusters, onSaved) {
  const tagInputId = `tags-${question.id}`;
  const options = clusters
    .map(
      (cluster) =>
        `<option value="${cluster.id}" ${question.cluster_id === cluster.id ? 'selected' : ''}>${escapeHtml(cluster.title)}（${cluster.size} 题）</option>`,
    )
    .join('');
  openModal(
    `<h3>调整题目归属</h3>
     <p class="m-sub">修改知识点标签，或把这道题移到别的知识点下。</p>
     <label style="font-size:12px;color:var(--muted)">知识点标签（用逗号分隔）</label>
     <input class="input" id="${tagInputId}" style="margin:6px 0 14px" value="${escapeHtml((question.tags || []).join(', '))}" />
     <label style="font-size:12px;color:var(--muted)">归入知识点</label>
     <select class="input" id="cluster-select" style="margin-top:6px">
       <option value="">未归类</option>
       ${options}
     </select>
     <div class="modal-actions">
       <button class="btn ghost" data-act="cancel">取消</button>
       <button class="btn primary" data-act="save">保存</button>
     </div>`,
    (box) => {
      box.querySelector('[data-act="cancel"]').onclick = () => closeModal();
      box.querySelector('[data-act="save"]').onclick = async () => {
        const tags = box
          .querySelector(`#${tagInputId}`)
          .value.split(/[,，、]/)
          .map((tag) => tag.trim())
          .filter(Boolean)
          .slice(0, 8);
        const raw = box.querySelector('#cluster-select').value;
        try {
          await api.patchQuestion(question.id, {
            tags,
            cluster_id: raw ? Number(raw) : null,
          });
          closeModal();
          toast('已更新');
          onSaved();
        } catch (error) {
          toast(error.message, 'error');
        }
      };
    },
  );
}

async function renderDocDetail(root, id) {
  if (!id) {
    location.hash = '#/documents';
    return;
  }
  root.innerHTML = `<div class="page">
    <button class="btn ghost sm" id="btn-back" type="button">← 返回文档库</button>
    <div id="doc-head" style="margin-top:14px"></div>
    <div id="doc-body" style="margin-top:16px"><div class="card empty"><b>读取中</b><p>正在加载文档信息…</p></div></div>
  </div>`;
  root.querySelector('#btn-back').onclick = () => {
    location.hash = '#/documents';
  };

  const headBox = root.querySelector('#doc-head');
  const body = root.querySelector('#doc-body');

  const load = async () => {
    let data;
    try {
      data = await api.document(id);
    } catch (error) {
      body.innerHTML = `<div class="card empty"><b>文档不存在</b><p>${escapeHtml(error.message)}</p></div>`;
      return;
    }
    const doc = data.document;
    headBox.innerHTML = `<div class="video-head">
      <div>
        <h1 class="video-title">${escapeHtml(doc.filename)}</h1>
        <div class="video-meta">${statusBadge(doc.status)} <span>${data.clusters.length} 个知识点</span><span>·</span><span>${data.questions.length} 道题</span></div>
      </div>
      <div class="head-actions">
        <button class="btn sm" id="btn-recluster" type="button">重新聚合</button>
        <button class="btn sm danger" id="btn-del" type="button">删除</button>
      </div>
    </div>`;

    headBox.querySelector('#btn-del').onclick = async () => {
      const ok = await confirmDialog('删除这份文档？', '题目、知识点分组会一并删除。', '删除');
      if (!ok) return;
      try {
        await api.deleteDocument(id);
        toast('已删除');
        location.hash = '#/documents';
      } catch (error) {
        toast(error.message, 'error');
      }
    };

    headBox.querySelector('#btn-recluster').onclick = async () => {
      const ok = await confirmDialog(
        '重新聚合题目？',
        '会根据当前题目内容重新计算知识点分组，手动调整过的归属会被覆盖。',
        '开始聚合',
      );
      if (!ok) return;
      try {
        await api.recluster(id);
        toast('已提交重新聚合');
        await load();
      } catch (error) {
        toast(error.message, 'error');
      }
    };

    if (doc.status === 'done') {
      renderDocReady(body, data, load);
    } else if (doc.status === 'failed') {
      body.innerHTML = `<div class="card empty"><b>解析失败</b><p>${escapeHtml(doc.error || '未知错误')}</p></div>`;
    } else {
      body.innerHTML = `<div class="card progress-card">
        <div class="ptitle">正在抽取与聚合题目</div>
        <div class="progress"><i id="doc-bar" style="width:${Number(doc.progress || 2)}%"></i></div>
        <div class="pstage"><span id="doc-stage">${escapeHtml(doc.stage || '准备中')}</span><span id="doc-pct">${Number(doc.progress || 0)}%</span></div>
        <p class="hint-line">题目越多，聚合越久。完成后会按知识点分组展示。</p>
      </div>`;
      clearInterval(pollTimer);
      pollTimer = setInterval(async () => {
        try {
          const next = await api.document(id);
          if (next.document.status === 'done') {
            clearInterval(pollTimer);
            pollTimer = null;
            await load();
          } else if (next.document.status === 'failed') {
            clearInterval(pollTimer);
            pollTimer = null;
            body.innerHTML = `<div class="card empty"><b>解析失败</b><p>${escapeHtml(next.document.error || '未知错误')}</p></div>`;
          } else {
            const bar = body.querySelector('#doc-bar');
            const stage = body.querySelector('#doc-stage');
            const pct = body.querySelector('#doc-pct');
            if (bar) bar.style.width = `${Number(next.document.progress || 0)}%`;
            if (stage) stage.textContent = next.document.stage || '处理中';
            if (pct) pct.textContent = `${Number(next.document.progress || 0)}%`;
          }
        } catch {
          clearInterval(pollTimer);
        }
      }, 1400);
    }
  };

  await load();
}

function renderDocReady(body, data, reload) {
  const { clusters, questions } = data;
  body.innerHTML = `<div class="toolbar" style="margin-bottom:12px">
      <span class="section-note">共 ${clusters.length} 个知识点分组，${questions.length} 道题</span>
    </div>
    <div id="clusters">${clustersHtml(clusters, questions)}</div>`;

  body.querySelector('#clusters').addEventListener('click', (event) => {
    const button = event.target.closest('[data-edit]');
    if (!button) return;
    const question = questions.find((q) => String(q.id) === button.dataset.edit);
    if (!question) return;
    openEditor(question, clusters, reload);
  });
}