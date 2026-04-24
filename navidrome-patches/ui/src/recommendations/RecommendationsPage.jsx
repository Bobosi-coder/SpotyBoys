/**
 * RecommendationsPage
 *
 * A Navidrome-integrated recommendations UI powered by the SpotyBoys
 * recommendation-api (C1–C4 GRU pipeline).
 *
 * Shows "Featured" (up to 4 tracks) and "Discover" (up to 10 tracks) sections.
 * Clicking any track adds the full recommendation batch to Navidrome's queue
 * and starts playback immediately using Navidrome's native player.
 *
 * Playback events (start / skip / complete) are captured by SpotiboysEventBridge
 * which is always mounted in the app layout.
 */

import React, { useCallback } from 'react'
import { useDispatch } from 'react-redux'
import { Card, CardContent, Typography, Grid, IconButton, Chip, CircularProgress, makeStyles } from '@material-ui/core'
import PlayArrowIcon from '@material-ui/icons/PlayArrow'
import RefreshIcon from '@material-ui/icons/Refresh'
import MusicNoteIcon from '@material-ui/icons/MusicNote'
import { v4 as uuidv4 } from 'uuid'
import { PLAYER_PLAY_TRACKS } from '../actions/player'
import { useSpotiboysSession } from './useSpotiboysSession'

const useStyles = makeStyles((theme) => ({
  root: {
    padding: theme.spacing(3),
    paddingBottom: 100, // leave room for player dock
  },
  sectionTitle: {
    marginBottom: theme.spacing(1),
    marginTop: theme.spacing(3),
    fontWeight: 600,
  },
  trackCard: {
    display: 'flex',
    alignItems: 'center',
    padding: theme.spacing(1.5),
    cursor: 'pointer',
    transition: 'background 0.15s',
    '&:hover': {
      background: theme.palette.action.hover,
    },
  },
  cover: {
    width: 56,
    height: 56,
    borderRadius: 4,
    marginRight: theme.spacing(2),
    flexShrink: 0,
    background: theme.palette.primary.dark,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    overflow: 'hidden',
  },
  coverImg: {
    width: '100%',
    height: '100%',
    objectFit: 'cover',
  },
  trackInfo: {
    flex: 1,
    minWidth: 0,
  },
  trackTitle: {
    fontWeight: 500,
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
  },
  trackArtist: {
    color: theme.palette.text.secondary,
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
  },
  metaBar: {
    display: 'flex',
    alignItems: 'center',
    gap: theme.spacing(1),
    marginBottom: theme.spacing(2),
    flexWrap: 'wrap',
  },
  chip: {
    fontSize: '0.7rem',
  },
  header: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginBottom: theme.spacing(1),
  },
  errorBox: {
    padding: theme.spacing(3),
    color: theme.palette.error.main,
  },
  loadingBox: {
    display: 'flex',
    justifyContent: 'center',
    padding: theme.spacing(6),
  },
}))

/** Convert a SpotyBoys track object into the shape Navidrome's player expects. */
function toNavidromeTrack(track, sessionContext) {
  return {
    // These fields are used by Navidrome's player (mapToAudioLists in playerReducer)
    id: track.track_id,
    title: track.title || track.track_id,
    artist: track.artist || 'Unknown Artist',
    album: track.album || '',
    duration: track.duration_sec || 30,
    // Override stream URL and cover — patched into playerReducer.js
    musicSrc: `/stream/${track.track_id}`,
    cover: `/covers/${track.track_id}`,
    // SpotyBoys session context — read by SpotiboysEventBridge for event capture
    _spotiboys: {
      track_id: track.track_id,
      session_id: sessionContext.session_id,
      user_id: sessionContext.user_id,
      impression_id: sessionContext.impression_id,
      request_id: sessionContext.request_id,
    },
  }
}

function TrackCard({ track, onPlay, classes }) {
  return (
    <Card variant="outlined" style={{ marginBottom: 8 }}>
      <div className={classes.trackCard} onClick={() => onPlay(track)}>
        <div className={classes.cover}>
          <img
            className={classes.coverImg}
            src={`/covers/${track.track_id}`}
            alt={track.title}
            onError={(e) => { e.target.style.display = 'none' }}
          />
          <MusicNoteIcon style={{ color: 'white', position: 'absolute' }} />
        </div>
        <div className={classes.trackInfo}>
          <Typography className={classes.trackTitle} variant="body2">
            {track.title || track.track_id}
          </Typography>
          <Typography className={classes.trackArtist} variant="caption">
            {track.artist || 'Unknown Artist'}
          </Typography>
        </div>
        <IconButton size="small" onClick={(e) => { e.stopPropagation(); onPlay(track) }}>
          <PlayArrowIcon />
        </IconButton>
      </div>
    </Card>
  )
}

export default function RecommendationsPage() {
  const classes = useStyles()
  const dispatch = useDispatch()
  const {
    loading,
    error,
    authInfo,
    bootstrapData,
    modelVersion,
    fallbackLevel,
    refreshRecommendations,
  } = useSpotiboysSession()

  const handlePlay = useCallback(
    (startTrack) => {
      if (!bootstrapData || !authInfo) return

      const surface = bootstrapData.browse_surface || {}
      const allTracks = [
        ...(surface.featured_items || []),
        ...(surface.random_carousel_items || []),
      ]

      const sessionContext = {
        session_id: authInfo.session_id,
        user_id: authInfo.user_id,
        impression_id: bootstrapData.impression_id || null,
        request_id: bootstrapData.request_id || null,
      }

      // Build a data map keyed by track_id (Navidrome player expects an object)
      const data = {}
      allTracks.forEach((t) => {
        data[t.track_id] = toNavidromeTrack(t, sessionContext)
      })

      dispatch({
        type: PLAYER_PLAY_TRACKS,
        id: startTrack.track_id,
        data,
      })
    },
    [dispatch, bootstrapData, authInfo],
  )

  if (loading) {
    return (
      <div className={classes.loadingBox}>
        <CircularProgress />
      </div>
    )
  }

  if (error) {
    return (
      <Card>
        <CardContent className={classes.errorBox}>
          <Typography variant="h6">Could not load recommendations</Typography>
          <Typography variant="body2">{error}</Typography>
        </CardContent>
      </Card>
    )
  }

  const surface = bootstrapData?.browse_surface || {}
  const featured = surface.featured_items || []
  const random = surface.random_carousel_items || []

  return (
    <div className={classes.root}>
      {/* Header + metadata */}
      <div className={classes.header}>
        <Typography variant="h5">Recommendations</Typography>
        <IconButton onClick={refreshRecommendations} title="Refresh recommendations">
          <RefreshIcon />
        </IconButton>
      </div>

      <div className={classes.metaBar}>
        {modelVersion && (
          <Chip
            className={classes.chip}
            size="small"
            label={`model: ${modelVersion}`}
            variant="outlined"
          />
        )}
        {fallbackLevel && fallbackLevel !== 'none' && (
          <Chip
            className={classes.chip}
            size="small"
            color="secondary"
            label={`fallback: ${fallbackLevel}`}
          />
        )}
      </div>

      {/* Featured */}
      {featured.length > 0 && (
        <>
          <Typography variant="subtitle1" className={classes.sectionTitle}>
            Featured for you
          </Typography>
          <Grid container spacing={2}>
            {featured.map((track) => (
              <Grid item xs={12} sm={6} key={track.track_id}>
                <TrackCard track={track} onPlay={handlePlay} classes={classes} />
              </Grid>
            ))}
          </Grid>
        </>
      )}

      {/* Discover */}
      {random.length > 0 && (
        <>
          <Typography variant="subtitle1" className={classes.sectionTitle}>
            Discover
          </Typography>
          {random.map((track) => (
            <TrackCard
              key={track.track_id}
              track={track}
              onPlay={handlePlay}
              classes={classes}
            />
          ))}
        </>
      )}

      {featured.length === 0 && random.length === 0 && (
        <Typography color="textSecondary" style={{ marginTop: 32 }}>
          No recommendations available yet. Play some tracks to personalise your feed.
        </Typography>
      )}
    </div>
  )
}
