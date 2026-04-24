/**
 * SpotiboysEventBridge
 *
 * Always-mounted component (injected into Navidrome's Layout) that watches
 * the Redux player state and forwards playback lifecycle events to the
 * SpotyBoys event-api.
 *
 * Only emits events for tracks that carry `_spotiboys` metadata — i.e., tracks
 * that were queued by the RecommendationsPage. Regular Navidrome library
 * browsing is silently ignored.
 *
 * Events emitted:
 *   playback_start  — when a new _spotiboys track starts playing
 *   skip            — when the player advances to the next track while the
 *                     current _spotiboys track had not yet completed
 *   complete        — when a _spotiboys track's audio ends naturally
 */

import { useEffect, useRef } from 'react'
import { useSelector } from 'react-redux'

let clientEventSeq = 0

async function postEvent(payload) {
  try {
    await fetch('/events/playback', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      credentials: 'include',
    })
  } catch {
    // Non-fatal — playback continues even if event logging fails
  }
}

function buildEvent(eventType, current, positionMs) {
  const sb = current.song?._spotiboys
  if (!sb) return null

  clientEventSeq += 1
  return {
    event_id: `evt_${sb.track_id}_${Date.now()}_${clientEventSeq}`,
    event_type: eventType,
    session_id: sb.session_id,
    user_id: sb.user_id,
    track_id: sb.track_id,
    request_id: sb.request_id || null,
    impression_id: sb.impression_id || null,
    position_ms: positionMs,
    playback_ms: positionMs,
    occurred_at: new Date().toISOString(),
    client_event_seq: clientEventSeq,
  }
}

export default function SpotiboysEventBridge() {
  const current = useSelector((state) => state.player?.current || {})
  const prevUuidRef = useRef(null)
  const prevTrackIdRef = useRef(null)
  const startTimeRef = useRef(null)
  const completedRef = useRef(false)

  useEffect(() => {
    const uuid = current.uuid
    const ended = current.ended
    const song = current.song
    const spotiboys = song?._spotiboys
    const trackId = spotiboys?.track_id || null

    // ── Track changed ────────────────────────────────────────────────────────
    if (uuid && uuid !== prevUuidRef.current) {
      // If the previous track had _spotiboys and wasn't completed naturally,
      // emit a skip event for it.
      if (
        prevTrackIdRef.current &&
        !completedRef.current &&
        prevUuidRef.current !== null
      ) {
        const skipEvent = buildEvent('skip', { song: { _spotiboys: { track_id: prevTrackIdRef.current, session_id: spotiboys?.session_id, user_id: spotiboys?.user_id, request_id: spotiboys?.request_id, impression_id: spotiboys?.impression_id } } }, 0)
        // Use previous track's stored _spotiboys if available
        // (they're already gone from current at this point — best-effort)
        if (skipEvent) postEvent(skipEvent)
      }

      prevUuidRef.current = uuid
      prevTrackIdRef.current = trackId
      startTimeRef.current = Date.now()
      completedRef.current = false

      // playback_start
      if (spotiboys && !ended) {
        const evt = buildEvent('playback_start', current, 0)
        if (evt) postEvent(evt)
      }
      return
    }

    // ── Track ended naturally ────────────────────────────────────────────────
    if (ended && uuid === prevUuidRef.current && !completedRef.current) {
      if (spotiboys) {
        const posMs = startTimeRef.current
          ? Math.round(Date.now() - startTimeRef.current)
          : 0
        const evt = buildEvent('complete', current, posMs)
        if (evt) postEvent(evt)
      }
      completedRef.current = true
    }
  }, [current])

  // This component renders nothing — it's a pure side-effect hook.
  return null
}
