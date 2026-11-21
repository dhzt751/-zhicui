function isDirectMedia(url = '') {
  return /\.(mp4|webm|ogg|m4v)(\?|$)/i.test(url) || url.startsWith('/media/');
}

export function mountPlayer(container, video, onTime) {
  container.innerHTML = '';
  const platform = video.platform || '';
  const playUrl = video.play_url || '';

  if (platform === 'upload' || isDirectMedia(playUrl)) {
    const el = document.createElement('video');
    el.controls = true;
    el.preload = 'metadata';
    el.src = playUrl;
    el.addEventListener('timeupdate', () => onTime(el.currentTime * 1000));
    container.appendChild(el);
    return {
      mode: 'follow',
      seek(ms) {
        el.currentTime = Math.max(0, ms / 1000);
        el.play().catch(() => {});
      },
      destroy() {
        el.pause();
        el.removeAttribute('src');
        el.load();
      },
    };
  }

  if (platform === 'youtube') {
    const videoId = video.external_id || '';
    const iframe = document.createElement('iframe');
    const origin = encodeURIComponent(location.origin);
    iframe.src = `https://www.youtube.com/embed/${videoId}?enablejsapi=1&origin=${origin}&rel=0&playsinline=1`;
    iframe.allow = 'accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture';
    iframe.allowFullscreen = true;
    container.appendChild(iframe);

    const channel = `zc-${Math.random().toString(36).slice(2)}`;
    const post = (payload) => {
      try {
        iframe.contentWindow?.postMessage(JSON.stringify({ ...payload, channel, id: 1 }), '*');
      } catch {
        /* ignore */
      }
    };
    const onMessage = (event) => {
      if (event.source !== iframe.contentWindow) return;
      let data;
      try {
        data = JSON.parse(event.data);
      } catch {
        return;
      }
      if (data.event === 'infoDelivery' && data.info && typeof data.info.currentTime === 'number') {
        onTime(data.info.currentTime * 1000);
      }
    };
    iframe.addEventListener('load', () => {
      post({ event: 'listening' });
      setTimeout(() => post({ event: 'listening' }), 500);
    });
    window.addEventListener('message', onMessage);
    const ticker = setInterval(() => post({ event: 'command', func: 'getCurrentTime', args: [] }), 600);

    return {
      mode: 'follow',
      seek(ms) {
        post({ event: 'command', func: 'seekTo', args: [Math.max(0, ms / 1000), true] });
      },
      destroy() {
        clearInterval(ticker);
        window.removeEventListener('message', onMessage);
      },
    };
  }

  if (platform === 'bilibili' && playUrl) {
    let current = 0;
    const build = (seconds) => {
      const url = new URL(playUrl, location.origin);
      url.searchParams.set('t', String(Math.max(0, Math.floor(seconds))));
      url.searchParams.set('autoplay', seconds > 0 ? '1' : '0');
      return url.toString();
    };
    const iframe = document.createElement('iframe');
    iframe.src = build(0);
    iframe.allowFullscreen = true;
    iframe.setAttribute('scrolling', 'no');
    iframe.setAttribute('frameborder', '0');
    container.appendChild(iframe);

    return {
      mode: 'jump',
      seek(ms) {
        current = ms;
        iframe.src = build(ms / 1000);
      },
      destroy() {
        iframe.removeAttribute('src');
      },
      get current() {
        return current;
      },
    };
  }

  container.innerHTML = `<div class="placeholder"><b>这个链接无法内嵌播放</b><span>已保留知识点与总结，可点击标题在新窗口打开原视频。</span></div>`;
  return {
    mode: 'none',
    seek() {},
    destroy() {},
  };
}