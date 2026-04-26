/**
 * SpotiboysEventBridge
 *
 * Always-mounted component (injected into Navidrome's Layout) that watches
 * the Redux player state and forwards playback lifecycle events to the
 * SpotyBoys recommendation-api.
 *
 * Recommendation tracks carry `_spotiboys` metadata and are logged with the
 * canonical SpotyBoys track id. Regular Navidrome library tracks are logged
 * with the Navidrome media id; the backend maps it back to the canonical id.
 *
 * Event model:
 *   Each physical track play gets a single playback_id (uuid) generated at start.
 *   The same playback_id is reused for the skip/complete update so the server
 *   can INSERT on playback_start and UPDATE on skip/complete.
 *
 * Events emitted:
 *   playback_start  — INSERT: new row, playratio=null
 *   skip            — UPDATE: set event_type + playratio (elapsed / duration)
 *   complete        — UPDATE: set event_type + playratio=0.95
 */

import { useEffect, useRef } from 'react'
import { useSelector } from 'react-redux'
import { v4 as uuidv4 } from 'uuid'

function getAuthHeader() {
  const token = localStorage.getItem('spotiboys_token')
  return token ? { Authorization: `Bearer ${token}` } : {}
}

async function postEvent(endpoint, payload) {
  try {
    await fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...getAuthHeader() },
      body: JSON.stringify(payload),
    })
  } catch {
    // Non-fatal — playback continues even if event logging fails
  }
}

function eventContextFromCurrent(current) {
  const song = current.song || {}
  const spotiboys = song._spotiboys
  if (spotiboys) {
    return {
      endpoint: '/events/playback',
      track_id: spotiboys.track_id,
      session_id: spotiboys.session_id,
      position: spotiboys.position || 0,
      duration_sec: spotiboys.duration_sec || song.duration || 30,
    }
  }
  const navidromeTrackId = song.id
  if (!navidromeTrackId) {
    return null
  }
  return {
    endpoint: '/events/navidrome-playback',
    navidrome_track_id: navidromeTrackId,
    position: 0,
    duration_sec: song.duration || 30,
  }
}

function playbackPayload(context, playbackId, eventType, extra = {}) {
  const base = {
    playback_id: playbackId,
    event_type: eventType,
    position: context.position || 0,
    ...extra,
  }
  if (context.endpoint === '/events/playback') {
    return {
      ...base,
      track_id: context.track_id,
      session_id: context.session_id,
    }
  }
  return {
    ...base,
    navidrome_track_id: context.navidrome_track_id,
  }
}

export default function SpotiboysEventBridge() {
  const current = useSelector((state) => state.player?.current || {})
  const prevUuidRef = useRef(null)
  const playbackIdRef = useRef(null)       // uuid reused across start/skip/complete
  const prevEventContextRef = useRef(null)
  const startTimeRef = useRef(null)
  const completedRef = useRef(false)

  useEffect(() => {
    const uuid = current.uuid
    const ended = current.ended
    const eventContext = eventContextFromCurrent(current)

    // ── Track changed ──────────────────────────────────────────────────────────
    if (uuid && uuid !== prevUuidRef.current) {
      // Emit skip for the previous track if it didn't complete naturally
      if (prevEventContextRef.current && !completedRef.current && prevUuidRef.current !== null) {
        const elapsed = startTimeRef.current ? (Date.now() - startTimeRef.current) : 0
        const durationMs = (prevEventContextRef.current.duration_sec || 30) * 1000
        const playratio = Math.min(0.99, Math.max(0, elapsed / durationMs))
        postEvent(
          prevEventContextRef.current.endpoint,
          playbackPayload(prevEventContextRef.current, playbackIdRef.current, 'skip', {
            playratio: Math.round(playratio * 100) / 100,
            position_ms: elapsed,
          }),
        )
      }

      prevUuidRef.current = uuid
      completedRef.current = false
      startTimeRef.current = Date.now()

      if (eventContext && !ended) {
        const newPlaybackId = uuidv4()
        playbackIdRef.current = newPlaybackId
        prevEventContextRef.current = eventContext
        postEvent(
          eventContext.endpoint,
          playbackPayload(eventContext, newPlaybackId, 'playback_start', {
            playratio: null,
          }),
        )
      } else {
        playbackIdRef.current = null
        prevEventContextRef.current = null
      }
      return
    }

    // ── Track ended naturally ────────────────────────────────────────────────
    if (ended && uuid === prevUuidRef.current && !completedRef.current && prevEventContextRef.current && playbackIdRef.current) {
      completedRef.current = true
      postEvent(
        prevEventContextRef.current.endpoint,
        playbackPayload(prevEventContextRef.current, playbackIdRef.current, 'complete', {
          playratio: 0.95,
        }),
      )
    }
  }, [current])

  return null
}
