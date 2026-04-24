/**
 * useSpotiboysSession
 *
 * Bridges Navidrome's authenticated user to the SpotyBoys recommendation-api.
 *
 * On first call:
 *   1. Reads the Navidrome username from localStorage (set by Navidrome's
 *      authProvider after login).
 *   2. Attempts POST /auth/signup with email={username}@navidrome.local
 *      and a deterministic password derived from the username.
 *   3. If signup returns 409 (already exists), falls back to POST /auth/login.
 *   4. On success, calls GET /session/bootstrap to get initial recommendations.
 *
 * The session cookie (spotiboys_session) is set by the server as httpOnly and
 * is automatically sent on all subsequent same-origin requests.
 *
 * 30Music test users:
 *   Create a Navidrome account with username "40305".
 *   → email = "40305@navidrome.local"
 *   → our user_id = "user_40305"  (prefNN fires for this user)
 */

import { useState, useEffect, useCallback } from 'react'

const SPOTIBOYS_PASSWORD_PREFIX = 'spotiboys:'

/** Deterministic password: no storage needed, same value every login. */
async function derivePassword(username) {
  const encoder = new TextEncoder()
  const data = encoder.encode(SPOTIBOYS_PASSWORD_PREFIX + username)
  const hashBuffer = await crypto.subtle.digest('SHA-256', data)
  const hashArray = Array.from(new Uint8Array(hashBuffer))
  return hashArray.map((b) => b.toString(16).padStart(2, '0')).join('')
}

function getNavidromeUsername() {
  // Navidrome authProvider stores the username in localStorage under 'username'
  return (
    localStorage.getItem('username') ||
    localStorage.getItem('auth')?.username ||
    'anonymous'
  )
}

async function signupOrLogin(username) {
  const email = `${username}@navidrome.local`
  const password = await derivePassword(username)

  // Try signup first
  const signupResp = await fetch('/auth/signup', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      email,
      password,
      display_name: username,
    }),
    credentials: 'include',
  })

  if (signupResp.ok) {
    return signupResp.json()
  }

  if (signupResp.status === 409) {
    // User already exists — login instead
    const loginResp = await fetch('/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password }),
      credentials: 'include',
    })
    if (!loginResp.ok) {
      throw new Error(`Login failed: ${loginResp.status}`)
    }
    return loginResp.json()
  }

  throw new Error(`Signup failed: ${signupResp.status}`)
}

async function fetchBootstrap() {
  const resp = await fetch('/session/bootstrap', { credentials: 'include' })
  if (!resp.ok) {
    throw new Error(`Bootstrap failed: ${resp.status}`)
  }
  return resp.json()
}

async function fetchNextRecommendations(sessionId, userId, queueRevision) {
  const resp = await fetch('/recommendations/next', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      session_id: sessionId,
      user_id: userId,
      queue_revision: queueRevision,
    }),
    credentials: 'include',
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
    authInfo: null,      // { user_id, session_id, email, display_name }
    bootstrapData: null, // full bootstrap response
    queueRevision: null,
    modelVersion: null,
    fallbackLevel: null,
  })

  // Initialise: auth + bootstrap
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
          queueRevision: bootstrapData.queue_revision ?? 0,
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
    const { authInfo, queueRevision } = state
    if (!authInfo) return null

    try {
      const data = await fetchNextRecommendations(
        authInfo.session_id,
        authInfo.user_id,
        queueRevision ?? 0,
      )
      setState((s) => ({
        ...s,
        bootstrapData: { ...s.bootstrapData, browse_surface: data.browse_surface },
        queueRevision: data.queue_revision ?? (s.queueRevision ?? 0) + 1,
        modelVersion: data.model_version ?? s.modelVersion,
        fallbackLevel: data.fallback_level ?? s.fallbackLevel,
      }))
      return data
    } catch (err) {
      setState((s) => ({ ...s, error: err.message }))
      return null
    }
  }, [state])

  return { ...state, refreshRecommendations }
}
