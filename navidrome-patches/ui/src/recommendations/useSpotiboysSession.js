/**
 * useSpotiboysSession
 *
 * Bridges Navidrome's authenticated user to the SpotyBoys recommendation-api.
 *
 * On first call:
 *   1. Reads the Navidrome username from localStorage (set by Navidrome's
 *      authProvider after login).
 *   2. Attempts POST /auth/signup with email={username}@navidrome.local
 *      and the fixed password "test123".
 *   3. If signup returns 409 (already exists), falls back to POST /auth/login.
 *   4. Stores the returned Bearer token in localStorage['spotiboys_token'].
 *   5. Calls GET /session/bootstrap to get initial recommendations.
 *
 * All subsequent requests include: Authorization: Bearer {token}
 *
 * 30Music test users:
 *   Any pre-seeded account is accessible as {uid}@navidrome.local / test123.
 *   Create a Navidrome account with username "40305" → login succeeds immediately
 *   because the row was pre-seeded by scripts/seed_30music_users.py.
 */

import { useState, useEffect, useCallback } from 'react'

const FIXED_PASSWORD = 'test123'

function getAuthHeader() {
  const token = localStorage.getItem('spotiboys_token')
  return token ? { Authorization: `Bearer ${token}` } : {}
}

function getNavidromeUsername() {
  return (
    localStorage.getItem('username') ||
    localStorage.getItem('auth')?.username ||
    'anonymous'
  )
}

async function signupOrLogin(username) {
  const email = `${username}@navidrome.local`

  const signupResp = await fetch('/auth/signup', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password: FIXED_PASSWORD, display_name: username }),
  })

  if (signupResp.ok) {
    const data = await signupResp.json()
    localStorage.setItem('spotiboys_token', data.token)
    return data
  }

  if (signupResp.status === 409) {
    const loginResp = await fetch('/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password: FIXED_PASSWORD }),
    })
    if (!loginResp.ok) {
      throw new Error(`Login failed: ${loginResp.status}`)
    }
    const data = await loginResp.json()
    localStorage.setItem('spotiboys_token', data.token)
    return data
  }

  throw new Error(`Signup failed: ${signupResp.status}`)
}

async function fetchBootstrap() {
  const resp = await fetch('/session/bootstrap', { headers: getAuthHeader() })
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
    authInfo: null,      // { token, user_id, display_name }
    bootstrapData: null, // full bootstrap response: { session_id, user_id, queue, model_version, fallback_level }
    modelVersion: null,
    fallbackLevel: null,
  })

  useEffect(() => {
    let cancelled = false

    async function init() {
      try {
        const username = getNavidromeUsername()
        const authInfo = await signupOrLogin(username)
        const bootstrapData = await fetchBootstrap()

        if (cancelled) return

        setState({
          loading: false,
          error: null,
          authInfo,
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
    const userId = authInfo.user_id
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
