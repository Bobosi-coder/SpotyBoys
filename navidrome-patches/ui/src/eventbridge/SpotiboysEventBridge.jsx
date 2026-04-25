/**
 * SpotiboysEventBridge
 *
 * Always-mounted component (injected into Navidrome's Layout) that watches
 * the Redux player state and forwards playback lifecycle events to the
 * SpotyBoys recommendation-api.
 *
 * Only emits events for tracks that carry `_spotiboys` metadata — i.e., tracks
 * that were queued by the RecommendationsPage. Regular Navidrome library
 * browsing is silently ignored.
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

async function postEvent(payload) {
  try {
    await fetch('/events/playback', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...getAuthHeader() },
      body: JSON.stringify(payload),
    })
  } catch {
    // Non-fatal — playback continues even if event logging fails
  }
}

export default function SpotiboysEventBridge() {
  const current = useSelector((state) => state.player?.current || {})
  const prevUuidRef = useRef(null)
  const playbackIdRef = useRef(null)       // uuid reused across start/skip/complete
  const prevSpotiboysRef = useRef(null)    // _spotiboys metadata of current track
  const startTimeRef = useRef(null)
  const completedRef = useRef(false)

  useEffect(() => {
    const uuid = current.uuid
    const ended = current.ended
    const spotiboys = current.song?._spotiboys

    // ── Track changed ──────────────────────────────────────────────────────────
    if (uuid && uuid !== prevUuidRef.current) {
      // Emit skip for the previous track if it didn't complete naturally
      if (prevSpotiboysRef.current && !completedRef.current && prevUuidRef.current !== null) {
        const elapsed = startTimeRef.current ? (Date.now() - startTimeRef.current) : 0
        const durationMs = (prevSpotiboysRef.current.duration_sec || 30) * 1000
        const playratio = Math.min(0.99, Math.max(0, elapsed / durationMs))
        postEvent({
          playback_id: playbackIdRef.current,
          event_type: 'skip',
          track_id: prevSpotiboysRef.current.track_id,
          session_id: prevSpotiboysRef.current.session_id,
          position: prevSpotiboysRef.current.position || 0,
          playratio: Math.round(playratio * 100) / 100,
          position_ms: elapsed,
        })
      }

      prevUuidRef.current = uuid
      completedRef.current = false
      startTimeRef.current = Date.now()

      if (spotiboys && !ended) {
        const newPlaybackId = uuidv4()
        playbackIdRef.current = newPlaybackId
        prevSpotiboysRef.current = spotiboys
        postEvent({
          playback_id: newPlaybackId,
          event_type: 'playback_start',
          track_id: spotiboys.track_id,
          session_id: spotiboys.session_id,
          position: spotiboys.position || 0,
          playratio: null,
        })
      } else {
        playbackIdRef.current = null
        prevSpotiboysRef.current = null
      }
      return
    }

    // ── Track ended naturally ────────────────────────────────────────────────
    if (ended && uuid === prevUuidRef.current && !completedRef.current && spotiboys && playbackIdRef.current) {
      completedRef.current = true
      postEvent({
        playback_id: playbackIdRef.current,
        event_type: 'complete',
        track_id: spotiboys.track_id,
        session_id: spotiboys.session_id,
        position: spotiboys.position || 0,
        playratio: 0.95,
      })
    }
  }, [current])

  return null
}
