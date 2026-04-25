/**
 * useSpotiboysSession
 *
 * Reads the SpotyBoys Bearer token from localStorage (set by the /login page
 * after a successful login or signup). If no token is present, redirects to
 * /login so the user can authenticate via the SpotyBoys login page.
 *
 * All API requests include: Authorization: Bearer {token}
 * The cookie spotiboys_token (set httpOnly by the API) is used by nginx for
 * Navidrome auth_request validation — no JS action required for that.
 */

import { useState, useEffect, useCallback } from 'react'

function getToken() {
  return localStorage.getItem('spotiboys_token')
}

function getAuthHeader() {
  const token = getToken()
  return token ? { Authorization: `Bearer ${token}` } : {}
}

async function fetchBootstrap() {
  const resp = await fetch('/session/bootstrap', { headers: getAuthHeader() })
  if (resp.status === 401) {
    localStorage.removeItem('spotiboys_token')
    window.location.href = '/login'
    throw new Error('Session expired')
  }
  if (!resp.ok) {
    throw new Error(`Bootstrap failed: ${resp.status}`)
  }
  return resp.json()
}

async function fetchNextRecommendations(sessionId, userId, queueRevision) {
  const resp = await fetch('/recommendations/next', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...getAuthHeader() },
    body: JSON.stringify({
      session_id: sessionId,
      user_id: userId,
      queue_revision: queueRevision,
    }),
  })
  if (!resp.ok) {
    throw new Error(`Recommendations failed: ${resp.status}`)
  }
  return resp.json()
}

export function useSpotiboysSession() {
  const [state, setState] = useState({
    loading: true,
    error: null,
    authInfo: null,      // { token, user_id, user_int_id, display_name }
    bootstrapData: null, // full bootstrap response: { session_id, user_id, queue, model_version, fallback_level }
    modelVersion: null,
    fallbackLevel: null,
  })

  useEffect(() => {
    let cancelled = false

    async function init() {
      const token = getToken()
      if (!token) {
        window.location.href = '/login'
        return
      }

      try {
        const bootstrapData = await fetchBootstrap()

        if (cancelled) return

        setState({
          loading: false,
          error: null,
          authInfo: { token },
          bootstrapData,
          modelVersion: bootstrapData.model_version ?? null,
          fallbackLevel: bootstrapData.fallback_level ?? null,
        })
      } catch (err) {
        if (cancelled) return
        setState((s) => ({
          ...s,
          loading: false,
          error: err.message || 'Failed to connect to SpotyBoys',
        }))
      }
    }

    init()
    return () => {
      cancelled = true
    }
  }, [])

  const refreshRecommendations = useCallback(async () => {
    const { authInfo, bootstrapData } = state
    if (!authInfo || !bootstrapData) return null

    const sessionId = bootstrapData.session_id
    const userId = bootstrapData.user_id
    const queueRevision = bootstrapData.queue?.revision ?? 0

    try {
      const data = await fetchNextRecommendations(sessionId, userId, queueRevision)
      setState((s) => ({
        ...s,
        bootstrapData: { ...s.bootstrapData, queue: data.queue },
        modelVersion: data.model_version ?? s.modelVersion,
        fallbackLevel: data.queue?.fallback_level ?? s.fallbackLevel,
      }))
      return data
    } catch (err) {
      setState((s) => ({ ...s, error: err.message }))
      return null
    }
  }, [state])

  return { ...state, refreshRecommendations }
}
