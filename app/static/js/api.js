async function request(path, options = {}) {
  const res = await fetch(path, options);
  if (!res.ok) {
    let detail = '';
    try {
      const data = await res.json();
      detail = data.detail || JSON.stringify(data);
    } catch {
      detail = await res.text().catch(() => '');
    }
    throw new Error(detail || `请求失败（${res.status}）`);
  }
  const type = res.headers.get('content-type') || '';
  if (type.includes('application/json')) return res.json();
  return res.text();
}

export const api = {
  status: () => request('/api/status'),
  stats: () => request('/api/stats'),

  videos: () => request('/api/videos'),
  video: (id) => request(`/api/videos/${id}`),
  createVideo: (url) =>
    request('/api/videos', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url }),
    }),
  uploadVideo: (file) => {
    const form = new FormData();
    form.append('file', file);
    return request('/api/videos/upload', { method: 'POST', body: form });
  },
  deleteVideo: (id) => request(`/api/videos/${id}`, { method: 'DELETE' }),
  importTranscript: (id, text) =>
    request(`/api/videos/${id}/transcript`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    }),

  documents: () => request('/api/documents'),
  document: (id) => request(`/api/documents/${id}`),
  uploadDocument: (file) => {
    const form = new FormData();
    form.append('file', file);
    return request('/api/documents', { method: 'POST', body: form });
  },
  deleteDocument: (id) => request(`/api/documents/${id}`, { method: 'DELETE' }),
  recluster: (id) => request(`/api/documents/${id}/recluster`, { method: 'POST' }),

  patchQuestion: (id, payload) =>
    request(`/api/questions/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }),

  knowledge: (q) => request(`/api/knowledge?q=${encodeURIComponent(q || '')}`),
};