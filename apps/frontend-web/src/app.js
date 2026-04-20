const recommendationBase = window.SPOTIBOYS_RECOMMENDATION_API || "";
const eventBase = window.SPOTIBOYS_EVENT_API || "";

const state = {
  session: null,
  browseSurface: { featured_items: [], random_carousel_items: [] },
  queue: { items: [], revision: 1, drawer_default_open: false },
  drawerOpen: false,
  currentTrack: null,
  currentLink: null,
  refreshInFlight: null,
  playbackAttemptId: null,
  emittedPlaybackStarts: new Set(),
  authMode: "login"
};

const els = {
  authPanel: document.getElementById("auth-panel"),
  authCopy: document.getElementById("auth-copy"),
  authEmail: document.getElementById("auth-email"),
  authPassword: document.getElementById("auth-password"),
  authDisplayName: document.getElementById("auth-display-name"),
  authError: document.getElementById("auth-error"),
  authSubmit: document.getElementById("auth-submit"),
  authToggle: document.getElementById("auth-toggle"),
  featured: document.getElementById("featured"),
  random: document.getElementById("random-carousel"),
  drawer: document.getElementById("playlist-drawer"),
  queue: document.getElementById("queue-list"),
  closeDrawer: document.getElementById("close-drawer"),
  playPause: document.getElementById("play-pause"),
  skipNext: document.getElementById("skip-next"),
  skipBack: document.getElementById("skip-back"),
  nowTitle: document.getElementById("now-title"),
  nowArtist: document.getElementById("now-artist"),
  audio: document.getElementById("audio-player"),
  status: document.getElementById("status"),
  revision: document.getElementById("queue-revision"),
  modelVersion: document.getElementById("model-version"),
  logout: document.getElementById("logout-button")
};

function apiUrl(base, path) {
  return `${base}${path}`;
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    ...options
  });
  if (!response.ok) {
    let detail = "";
    try {
      const payload = await response.json();
      detail = Array.isArray(payload.detail)
        ? payload.detail.map((item) => item.msg || item.type || "validation error").join("; ")
        : (payload.detail || "");
    } catch (_error) {
      detail = response.statusText;
    }
    throw new Error(`${response.status} ${detail || response.statusText}`);
  }
  return response.json();
}

async function bootstrap() {
  try {
    const payload = await fetchJson(apiUrl(recommendationBase, "/session/bootstrap"));
    showAuth(false);
    state.session = { session_id: payload.session_id, user_id: payload.user_id };
    state.browseSurface = payload.browse_surface;
    state.queue = payload.queue;
    state.drawerOpen = false;
    renderAll();
    emitImpression("bootstrap_render", payload.queue.items[0]?.request_id || "req_bootstrap", payload.queue.items[0]?.impression_id || "imp_bootstrap");
  } catch (error) {
    showAuth(true);
    if (!String(error.message).startsWith("401")) {
      showStatus("Service unavailable. You can sign in once the backend recovers.");
    }
  }
}

function showAuth(open) {
  els.authPanel.classList.toggle("hidden", !open);
}

async function submitAuth() {
  clearAuthError();
  const email = els.authEmail.value.trim();
  const password = els.authPassword.value;
  if (!email.includes("@")) {
    showAuthError("Enter a valid email address.");
    return;
  }
  if (password.length < 8) {
    showAuthError("Password must be at least 8 characters.");
    return;
  }
  const path = state.authMode === "signup" ? "/auth/signup" : "/auth/login";
  const body = {
    email,
    password
  };
  if (state.authMode === "signup") {
    body.display_name = els.authDisplayName.value;
  }
  try {
    els.authSubmit.disabled = true;
    await fetchJson(apiUrl(recommendationBase, path), { method: "POST", body: JSON.stringify(body) });
    showStatus("");
    els.status.classList.add("hidden");
    await bootstrap();
  } catch (error) {
    const message = String(error.message);
    showAuthError(
      state.authMode === "signup"
        ? `Signup failed. ${message.replace(/^422\s*/, "")}`
        : "Login failed. Check your email and password, or create a new account."
    );
  } finally {
    els.authSubmit.disabled = false;
  }
}

function showAuthError(message) {
  els.authError.textContent = message;
  els.authError.classList.remove("hidden");
}

function clearAuthError() {
  els.authError.textContent = "";
  els.authError.classList.add("hidden");
}

function toggleAuthMode() {
  state.authMode = state.authMode === "login" ? "signup" : "login";
  const signup = state.authMode === "signup";
  els.authDisplayName.classList.toggle("hidden", !signup);
  els.authSubmit.textContent = signup ? "Sign up" : "Log in";
  els.authToggle.textContent = signup ? "Use login" : "Create account";
  els.authCopy.textContent = signup ? "Create a first-party account for this listening session." : "Sign in to start your listening session.";
  clearAuthError();
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
  state.currentLink = queueLinkForTrack(item.track_id);
  state.playbackAttemptId = `${item.track_id}:${Date.now()}`;
  els.nowTitle.textContent = item.title;
  els.nowArtist.textContent = item.artist;
  try {
    const playable = await fetchJson(apiUrl(recommendationBase, `/playable-track/${item.track_id}`));
    els.audio.src = apiUrl(recommendationBase, playable.stream_path);
    updatePlayPauseButton();
    await els.audio.play();
  } catch (error) {
    showStatus("Selected track could not start. Moving to the next approved playable item.");
    updatePlayPauseButton();
  }
}

async function emitPlaybackStartOnce() {
  if (!state.currentTrack || !state.playbackAttemptId || state.emittedPlaybackStarts.has(state.playbackAttemptId)) {
    return;
  }
  state.emittedPlaybackStarts.add(state.playbackAttemptId);
  const linked = state.currentLink || queueLinkForTrack(state.currentTrack.track_id);
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
  try {
    await fetchJson(apiUrl(eventBase, "/events/playback"), { method: "POST", body: JSON.stringify(payload) });
    await refreshRecommendations({ preserveCurrent: true });
  } catch (_error) {
    showStatus("Playback continues while event logging is degraded.");
  }
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

async function refreshRecommendations(options = {}) {
  if (state.refreshInFlight) {
    return state.refreshInFlight;
  }
  state.refreshInFlight = doRefreshRecommendations(options).finally(() => {
    state.refreshInFlight = null;
  });
  return state.refreshInFlight;
}

async function doRefreshRecommendations(options = {}) {
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
  if (options.autoplayFirst && state.queue.items[0]) {
    await startTrack(state.queue.items[0]);
  }
}

async function emitPlaybackLifecycle(eventType, track = state.currentTrack) {
  if (!track || !state.session) return;
  const linked = state.currentLink || queueLinkForTrack(track.track_id);
  const eventId = `evt_${eventType}_${track.track_id}_${Date.now()}`;
  await fetchJson(apiUrl(eventBase, "/events/playback"), {
    method: "POST",
    body: JSON.stringify({
      event_id: eventId,
      event_type: eventType,
      session_id: state.session.session_id,
      user_id: state.session.user_id,
      track_id: track.track_id,
      request_id: linked.request_id,
      impression_id: linked.impression_id,
      position_ms: Math.max(0, Math.floor((els.audio.currentTime || 0) * 1000)),
      playback_ms: Math.max(0, Math.floor((els.audio.currentTime || 0) * 1000)),
      occurred_at: new Date().toISOString(),
      client_event_seq: state.emittedPlaybackStarts.size + 1
    })
  });
}

async function playNextFromQueue() {
  if (!state.queue.items.length) {
    await refreshRecommendations({ autoplayFirst: true });
    return;
  }
  const currentIndex = state.queue.items.findIndex((item) => state.currentTrack && item.track_id === state.currentTrack.track_id);
  const next = state.queue.items[currentIndex + 1];
  if (next) {
    await startTrack(next);
    return;
  }
  await refreshRecommendations({ autoplayFirst: true });
}

function updatePlayPauseButton() {
  els.playPause.textContent = els.audio.paused ? "Play" : "Pause";
  els.playPause.setAttribute("aria-label", els.audio.paused ? "Play" : "Pause");
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

els.closeDrawer.addEventListener("click", () => toggleDrawer(false));
els.playPause.addEventListener("click", () => {
  if (els.audio.paused && state.currentTrack) {
    els.audio.play();
  } else if (els.audio.paused && state.queue.items[0]) {
    startTrack(state.queue.items[0]);
  } else {
    els.audio.pause();
  }
  updatePlayPauseButton();
});
els.skipNext.addEventListener("click", async () => {
  await emitPlaybackLifecycle("skip").catch(() => showStatus("Skip logged locally while event service recovers."));
  await playNextFromQueue();
});
els.skipBack.addEventListener("click", () => {
  if (els.audio.currentTime > 3) {
    els.audio.currentTime = 0;
  }
});
els.authSubmit.addEventListener("click", submitAuth);
els.authToggle.addEventListener("click", toggleAuthMode);
[els.authEmail, els.authPassword, els.authDisplayName].forEach((input) => {
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      submitAuth();
    }
  });
});
els.logout.addEventListener("click", async () => {
  await fetchJson(apiUrl(recommendationBase, "/auth/logout"), { method: "POST", body: "{}" }).catch(() => null);
  state.session = null;
  state.queue = { items: [], revision: 1, drawer_default_open: false };
  state.browseSurface = { featured_items: [], random_carousel_items: [] };
  els.audio.pause();
  els.audio.removeAttribute("src");
  updatePlayPauseButton();
  renderAll();
  showAuth(true);
});
els.audio.addEventListener("playing", emitPlaybackStartOnce);
els.audio.addEventListener("play", updatePlayPauseButton);
els.audio.addEventListener("pause", updatePlayPauseButton);
els.audio.addEventListener("ended", async () => {
  updatePlayPauseButton();
  await emitPlaybackLifecycle("complete").catch(() => showStatus("Completion logging is degraded."));
  await playNextFromQueue();
});

bootstrap();
