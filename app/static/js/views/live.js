import { escapeHtml, fmtClock, toast } from '../ui.js';

let dispose = null;

export function cleanupLive() {
  if (typeof dispose === 'function') {
    dispose();
    dispose = null;
  }
}

export async function renderLive(root) {
  cleanupLive();

  root.innerHTML = `<div class="page">
    <div class="page-head">
      <div>
        <p class="eyebrow">LIVE CAPTURE</p>
        <h1 class="page-title">边看边听，知识点实时长出来</h1>
        <p class="page-sub">点开始后选择正在播放课程的标签页，并勾选「分享标签页音频」。网站可以在后台持续接收，不需要下载视频。</p>
      </div>
      <div class="head-actions">
        <button class="btn primary" id="live-start" type="button">开始听课</button>
        <button class="btn" id="live-stop" type="button" disabled>结束</button>
      </div>
    </div>
    <div class="card" style="padding:14px 16px;margin-bottom:18px;display:flex;gap:12px;flex-wrap:wrap;align-items:center">
      <input class="input" id="live-title" style="max-width:340px" placeholder="课程名称（可选）" />
      <span class="section-note" id="live-hint">支持 Chrome / Edge；macOS 若捕获不到系统声音，请选「整个屏幕」或安装虚拟声卡。</span>
    </div>
    <div class="live-grid">
      <div class="card live-sub">
        <div class="live-status" id="live-status"><i class="live-dot idle"></i><span>等待开始</span></div>
        <div class="live-lines" id="live-lines">
          <div class="empty" style="padding:40px 10px"><b>还没有声音</b><p>开始听课后，这里会实时滚动转写内容。</p></div>
        </div>
      </div>
      <aside>
        <div class="section-head" style="margin-top:0">
          <h2 class="section-title">实时知识点</h2>
          <span class="section-note" id="live-count">0 个</span>
        </div>
        <div class="timeline" id="live-points">
          <div class="card empty" style="padding:26px 16px"><b>等待内容</b><p>字幕积累到一定长度后会自动提炼。</p></div>
        </div>
      </aside>
    </div>
  </div>`;

  const startBtn = root.querySelector('#live-start');
  const stopBtn = root.querySelector('#live-stop');
  const titleInput = root.querySelector('#live-title');
  const statusBox = root.querySelector('#live-status');
  const linesBox = root.querySelector('#live-lines');
  const pointsBox = root.querySelector('#live-points');
  const countBox = root.querySelector('#live-count');

  let audioStream = null;
  let audioContext = null;
  let processor = null;
  let sourceNode = null;
  let socket = null;
  let buffers = [];
  let buffered = 0;
  let started = false;
  let retries = 0;
  let heartbeat = null;
  let reconnectTimer = null;
  const seen = new Set();

  const setStatus = (text, live = false) => {
    statusBox.innerHTML = `<i class="live-dot ${live ? '' : 'idle'}"></i><span>${escapeHtml(text)}</span>`;
  };

  const appendLine = (text, startMs) => {
    const empty = linesBox.querySelector('.empty');
    if (empty) empty.remove();
    const row = document.createElement('div');
    row.className = 'live-line';
    row.innerHTML = `<time>${fmtClock(startMs || 0)}</time><span>${escapeHtml(text)}</span>`;
    linesBox.appendChild(row);
    linesBox.scrollTop = linesBox.scrollHeight;
  };

  const appendPoints = (points) => {
    const empty = pointsBox.querySelector('.empty');
    if (empty) empty.remove();
    for (const point of points) {
      if (seen.has(point.title)) continue;
      seen.add(point.title);
      const card = document.createElement('div');
      card.className = 'kp';
      const badges = [];
      if (point.kind) badges.push(`<span class="chip">${escapeHtml(point.kind)}</span>`);
      if (point.confidence && point.confidence < 0.72) badges.push('<span class="diff">待确认</span>');
      card.innerHTML = `<div class="kp-top">
          <span class="kp-title">${escapeHtml(point.title)}</span>
          <span class="kp-time">${fmtClock(point.start_ms || 0)}</span>
        </div>
        <div class="kp-sum">${escapeHtml(point.summary || '')}</div>
        ${badges.length ? `<div class="chips">${badges.join('')}</div>` : ''}`;
      pointsBox.prepend(card);
    }
    countBox.textContent = `${seen.size} 个`;
  };

  const sendPcm = (chunks, length) => {
    if (!socket || socket.readyState !== WebSocket.OPEN) return;
    const pcm = new Int16Array(length);
    let offset = 0;
    for (const chunk of chunks) {
      for (let i = 0; i < chunk.length; i += 1) {
        const sample = Math.max(-1, Math.min(1, chunk[i]));
        pcm[offset] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
        offset += 1;
      }
    }
    socket.send(pcm.buffer);
  };

  const teardownNodes = () => {
    try {
      processor?.disconnect();
      sourceNode?.disconnect();
      audioContext?.close();
    } catch {
      /* ignore */
    }
    processor = null;
    sourceNode = null;
    audioContext = null;
    buffers = [];
    buffered = 0;
  };

  const teardownAudio = () => {
    teardownNodes();
    audioStream?.getTracks().forEach((track) => track.stop());
    audioStream = null;
  };

  const stopHeartbeat = () => {
    if (heartbeat) {
      clearInterval(heartbeat);
      heartbeat = null;
    }
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
  };

  const startHeartbeat = () => {
    stopHeartbeat();
    heartbeat = setInterval(() => {
      if (socket && socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ type: 'ping' }));
      }
    }, 12000);
  };

  const beginAudio = (stream) => {
    teardownNodes();
    audioContext = new AudioContext({ sampleRate: 16000 });
    sourceNode = audioContext.createMediaStreamSource(stream);
    processor = audioContext.createScriptProcessor(4096, 1, 1);
    const mute = audioContext.createGain();
    mute.gain.value = 0;
    processor.onaudioprocess = (event) => {
      if (!started || !socket || socket.readyState !== WebSocket.OPEN) return;
      const data = event.inputBuffer.getChannelData(0);
      buffers.push(new Float32Array(data));
      buffered += data.length;
      if (buffered >= 16000 * 6) {
        sendPcm(buffers, buffered);
        buffers = [];
        buffered = 0;
      }
    };
    sourceNode.connect(processor);
    processor.connect(mute);
    mute.connect(audioContext.destination);
  };

  const connect = () => {
    if (!audioStream) return;
    const alive = audioStream.getAudioTracks().some((track) => track.readyState === 'live');
    if (!alive) {
      setStatus('屏幕共享已停止，请重新开始');
      teardownAudio();
      started = false;
      startBtn.disabled = false;
      stopBtn.disabled = true;
      return;
    }
    const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
    socket = new WebSocket(`${scheme}://${location.host}/ws/live`);
    socket.binaryType = 'arraybuffer';

    socket.onopen = () => {
      retries = 0;
      started = true;
      socket.send(
        JSON.stringify({ type: 'start', title: titleInput.value.trim() || '实时听课' }),
      );
      beginAudio(audioStream);
      startHeartbeat();
      setStatus('正在聆听，字幕每 6 秒左右更新一次', true);
      startBtn.disabled = true;
      stopBtn.disabled = false;
      if (!linesBox.querySelector('.live-line')) {
        linesBox.innerHTML = '';
      }
    };

    socket.onmessage = (event) => {
      let data;
      try {
        data = JSON.parse(event.data);
      } catch {
        return;
      }
      if (data.type === 'transcript') appendLine(data.text, data.start_ms);
      else if (data.type === 'knowledge') appendPoints(data.points || []);
      else if (data.type === 'pong') return;
      else if (data.type === 'error') {
        toast(data.message, 'error');
        setStatus(data.message);
      } else if (data.type === 'ready' && !data.asr) {
        toast('服务端未配置语音转写，无法实时转写', 'error');
        setStatus('服务端未配置语音转写');
      }
    };

    socket.onclose = () => {
      stopHeartbeat();
      if (!started) return;
      if (retries < 3) {
        retries += 1;
        setStatus(`连接中断，正在进行第 ${retries} 次重连…`);
        reconnectTimer = setTimeout(() => {
          if (started) connect();
        }, retries * 1500);
      } else {
        setStatus('连接多次中断，请结束并重新开始');
        teardownAudio();
        started = false;
        startBtn.disabled = false;
        stopBtn.disabled = true;
      }
    };

    socket.onerror = () => {
      /* 交给 onclose 统一处理 */
    };
  };

  const stop = () => {
    if (!started && !audioStream) return;
    started = false;
    stopHeartbeat();
    try {
      socket?.send(JSON.stringify({ type: 'stop' }));
      socket?.close();
    } catch {
      /* ignore */
    }
    socket = null;
    teardownAudio();
    startBtn.disabled = false;
    stopBtn.disabled = true;
    setStatus('已结束，可以重新开始');
  };

  dispose = () => {
    started = false;
    stopHeartbeat();
    try {
      socket?.close();
    } catch {
      /* ignore */
    }
    teardownAudio();
  };

  startBtn.onclick = async () => {
    if (!navigator.mediaDevices?.getDisplayMedia) {
      toast('当前浏览器不支持共享标签页音频', 'error');
      return;
    }
    startBtn.disabled = true;
    try {
      audioStream = await navigator.mediaDevices.getDisplayMedia({
        video: true,
        audio: true,
        selfBrowserSurface: 'include',
        systemAudio: 'include',
      });
    } catch {
      toast('需要选择标签页，并勾选「分享标签页音频」', 'error');
      startBtn.disabled = false;
      return;
    }
    const audioTracks = audioStream.getAudioTracks();
    if (!audioTracks.length) {
      teardownAudio();
      toast('没有捕获到音频，请重新选择并勾选「分享标签页音频」', 'error');
      startBtn.disabled = false;
      return;
    }
    audioStream.getVideoTracks().forEach((track) => track.stop());
    const audioOnly = new MediaStream(audioTracks);
    audioStream = audioOnly;
    retries = 0;
    linesBox.innerHTML = '';
    connect();
  };

  stopBtn.onclick = stop;
}