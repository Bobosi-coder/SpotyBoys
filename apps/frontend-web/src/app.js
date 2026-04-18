const recommendationBase = window.SPOTIBOYS_RECOMMENDATION_API || "";
const eventBase = window.SPOTIBOYS_EVENT_API || "";

const state = {
  session: null,
  browseSurface: { featured_items: [], random_carousel_items: [] },
  queue: { items: [], revision: 1, drawer_default_open: false },
  drawerOpen: false,
  currentTrack: null,
  playbackAttemptId: null,
  emittedPlaybackStarts: new Set()
};

const els = {
  featured: document.getElementById("featured"),
  random: document.getElementById("random-carousel"),
  drawer: document.getElementById("playlist-drawer"),
  queue: document.getElementById("queue-list"),
  playlistButton: document.getElementById("playlist-button"),
  closeDrawer: document.getElementById("close-drawer"),
  playPause: document.getElementById("play-pause"),
  skipNext: document.getElementById("skip-next"),
  skipBack: document.getElementById("skip-back"),
  nowTitle: document.getElementById("now-title"),
  nowArtist: document.getElementById("now-artist"),
  audio: document.getElementById("audio-player"),
  status: document.getElementById("status"),
  revision: document.getElementById("queue-revision"),
  modelVersion: document.getElementById("model-version")
};

function apiUrl(base, path) {
  return `${base}${path}`;
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options
  });
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}`);
  }
  return response.json();
}

async function bootstrap() {
  try {
    const payload = await fetchJson(apiUrl(recommendationBase, "/session/bootstrap"));
    state.session = { session_id: payload.session_id, user_id: payload.user_id };
    state.browseSurface = payload.browse_surface;
    state.queue = payload.queue;
    state.drawerOpen = false;
    renderAll();
    emitImpression("bootstrap_render", payload.queue.items[0]?.request_id || "req_bootstrap", payload.queue.items[0]?.impression_id || "imp_bootstrap");
  } catch (error) {
    showStatus("Service unavailable. Demo UI is waiting for backend recovery.");
  }
}

function renderAll() {
  renderFeatured();
  renderRandom();
  renderQueue();
  renderDrawer();
  els.revision.textContent = `queue rev ${state.queue.revision}`;
}

function renderFeatured() {
  const items = state.browseSurface.featured_items.slice(0, 4);
  els.featured.replaceChildren(...items.map((item) => trackCard(item, "track-card")));
}

function renderRandom() {
  const items = state.browseSurface.random_carousel_items.slice(0, 10);
  els.random.replaceChildren(...items.map((item) => trackCard(item, "random-card")));
}

function trackCard(item, className) {
  const button = document.createElement("button");
  button.className = className;
  button.dataset.trackId = item.track_id;
  button.innerHTML = `<h2>${escapeHtml(item.title)}</h2><p>${escapeHtml(item.artist)}</p>`;
  button.addEventListener("click", () => startTrack(item));
  return button;
}

function renderQueue() {
  const before = state.queue.items.map((item) => item.track_id).join(",");
  els.queue.replaceChildren(...state.queue.items.map((item) => {
    const li = document.createElement("li");
    li.className = "queue-item";
    li.dataset.trackId = item.track_id;
    li.innerHTML = `<strong>${item.queue_position}</strong><div><p>${escapeHtml(item.title)}</p><span>${escapeHtml(item.artist)}</span></div>`;
    li.addEventListener("click", () => startTrack(item));
    return li;
  }));
  els.queue.dataset.queueFingerprint = before;
}

function renderDrawer() {
  els.drawer.classList.toggle("open", state.drawerOpen);
  els.drawer.setAttribute("aria-hidden", state.drawerOpen ? "false" : "true");
}

function toggleDrawer(open) {
  const before = state.queue.items.map((item) => item.track_id).join(",");
  state.drawerOpen = open;
  renderDrawer();
  const after = state.queue.items.map((item) => item.track_id).join(",");
  if (before !== after) {
    showStatus("Queue state changed unexpectedly while opening playlist.");
  }
}

async function startTrack(item) {
  state.currentTrack = item;
  state.playbackAttemptId = `${item.track_id}:${Date.now()}`;
  els.nowTitle.textContent = item.title;
  els.nowArtist.textContent = item.artist;
  try {
    const playable = await fetchJson(apiUrl(recommendationBase, `/playable-track/${item.track_id}`));
    els.audio.src = apiUrl(recommendationBase, playable.stream_path);
    await els.audio.play();
  } catch (error) {
    showStatus("Selected track could not start. Moving to the next approved playable item.");
  }
}

function emitPlaybackStartOnce() {
  if (!state.currentTrack || !state.playbackAttemptId || state.emittedPlaybackStarts.has(state.playbackAttemptId)) {
    return;
  }
  state.emittedPlaybackStarts.add(state.playbackAttemptId);
  const linked = queueLinkForTrack(state.currentTrack.track_id);
  const payload = {
    event_id: `evt_${state.playbackAttemptId}`,
    event_type: "playback_start",
    session_id: state.session.session_id,
    user_id: state.session.user_id,
    track_id: state.currentTrack.track_id,
    request_id: linked.request_id,
    impression_id: linked.impression_id,
    position_ms: 0,
    playback_ms: 0,
    occurred_at: new Date().toISOString(),
    client_event_seq: state.emittedPlaybackStarts.size
  };
  fetchJson(apiUrl(eventBase, "/events/playback"), { method: "POST", body: JSON.stringify(payload) }).catch(() => {
    showStatus("Playback continues while event logging is degraded.");
  });
}

function queueLinkForTrack(trackId) {
  return state.queue.items.find((item) => item.track_id === trackId) || state.queue.items[0] || {
    request_id: "req_unknown",
    impression_id: "imp_unknown"
  };
}

function emitImpression(impressionId, requestId, fallbackImpressionId) {
  const visible = [
    ...state.browseSurface.featured_items.slice(0, 4),
    ...state.browseSurface.random_carousel_items.slice(0, 10)
  ].map((item) => ({ track_id: item.track_id, surface_slot: item.surface_slot }));
  fetchJson(apiUrl(eventBase, "/events/impression"), {
    method: "POST",
    body: JSON.stringify({
      impression_id: fallbackImpressionId || impressionId,
      request_id: requestId,
      session_id: state.session.session_id,
      user_id: state.session.user_id,
      visible_items: visible,
      surface: "browse_surface",
      rendered_at: new Date().toISOString()
    })
  }).catch(() => showStatus("Playback works, but event logging is degraded."));
}

async function refreshRecommendations() {
  const payload = await fetchJson(apiUrl(recommendationBase, "/recommendations/next"), {
    method: "POST",
    body: JSON.stringify({
      session_id: state.session.session_id,
      user_id: state.session.user_id,
      queue_revision: state.queue.revision
    })
  });
  state.browseSurface = payload.browse_surface;
  state.queue = { ...state.queue, ...payload.queue, drawer_default_open: false };
  els.modelVersion.textContent = payload.model_version;
  renderAll();
  emitImpression(payload.impression_id, payload.request_id, payload.impression_id);
}

function showStatus(message) {
  els.status.textContent = message;
  els.status.classList.remove("hidden");
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    "\"": "&quot;",
    "'": "&#039;"
  }[ch]));
}

els.playlistButton.addEventListener("click", () => toggleDrawer(!state.drawerOpen));
els.closeDrawer.addEventListener("click", () => toggleDrawer(false));
els.playPause.addEventListener("click", () => {
  if (els.audio.paused && state.currentTrack) {
    els.audio.play();
  } else if (els.audio.paused && state.queue.items[0]) {
    startTrack(state.queue.items[0]);
  } else {
    els.audio.pause();
  }
});
els.skipNext.addEventListener("click", () => {
  const currentIndex = state.queue.items.findIndex((item) => state.currentTrack && item.track_id === state.currentTrack.track_id);
  const next = state.queue.items[currentIndex + 1] || state.queue.items[0];
  if (next) startTrack(next);
});
els.skipBack.addEventListener("click", () => {
  if (els.audio.currentTime > 3) {
    els.audio.currentTime = 0;
  }
});
els.audio.addEventListener("playing", emitPlaybackStartOnce);
els.audio.addEventListener("ended", refreshRecommendations);

bootstrap();
