/**
 * RecommendationsPage
 *
 * A Navidrome-integrated recommendations UI powered by the SpotyBoys
 * recommendation-api (C1–C4 GRU pipeline).
 *
 * Shows "Up Next" (top 4 GRU predictions, larger cards) and
 * "Queue" (remaining 10 tracks, compact list).
 *
 * Clicking any track loads all 14 tracks into Navidrome's player queue
 * and starts playback immediately.
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
import { PLAYER_PLAY_TRACKS } from '../actions/player'
import { useSpotiboysSession } from './useSpotiboysSession'

const useStyles = makeStyles((theme) => ({
  root: {
    padding: theme.spacing(3),
    paddingBottom: 100,
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
    position: 'relative',
  },
  coverSmall: {
    width: 36,
    height: 36,
    borderRadius: 3,
    marginRight: theme.spacing(1.5),
    flexShrink: 0,
    background: theme.palette.primary.dark,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    overflow: 'hidden',
    position: 'relative',
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
  queueRow: {
    display: 'flex',
    alignItems: 'center',
    padding: theme.spacing(0.75, 1),
    cursor: 'pointer',
    borderRadius: 4,
    '&:hover': {
      background: theme.palette.action.hover,
    },
  },
}))

/** Convert a SpotyBoys QueueItem into the shape Navidrome's player expects. */
function toNavidromeTrack(track, sessionId) {
  const navidromeId = track.navidrome_track_id || track.track_id
  return {
    id: navidromeId,
    albumId: navidromeId,
    artistId: navidromeId,
    coverArt: navidromeId,
    title: track.title || track.track_id,
    artist: track.artist || 'Unknown Artist',
    album: track.album || '',
    duration: track.duration_sec || 30,
    musicSrc: `/rest/stream.view?id=${encodeURIComponent(navidromeId)}`,
    cover: track.cover_art_url || `/covers/${track.track_id}`,
    // Metadata read by SpotiboysEventBridge for event capture
    _spotiboys: {
      track_id: track.track_id,
      navidrome_track_id: navidromeId,
      session_id: sessionId,
      position: (track.queue_position || 1) - 1,  // convert 1-based to 0-based
      duration_sec: track.duration_sec || 30,
      impression_id: track.impression_id || null,
      request_id: track.request_id || null,
    },
  }
}

function UpNextCard({ track, onPlay, classes }) {
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
          <MusicNoteIcon style={{ color: 'white', position: 'absolute', opacity: 0.4 }} />
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

function QueueRow({ track, onPlay, classes }) {
  return (
    <div className={classes.queueRow} onClick={() => onPlay(track)}>
      <div className={classes.coverSmall}>
        <img
          className={classes.coverImg}
          src={`/covers/${track.track_id}`}
          alt={track.title}
          onError={(e) => { e.target.style.display = 'none' }}
        />
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
        <PlayArrowIcon fontSize="small" />
      </IconButton>
    </div>
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
      if (!bootstrapData) return

      const sessionId = bootstrapData.session_id
      const queue = bootstrapData.queue || {}
      const allTracks = [
        ...(queue.up_next || []),
        ...(queue.remaining || []),
      ]

      const data = {}
      allTracks.forEach((t) => {
        const navidromeId = t.navidrome_track_id || t.track_id
        data[navidromeId] = toNavidromeTrack(t, sessionId)
      })

      const startNavidromeId = startTrack.navidrome_track_id || startTrack.track_id
      dispatch({
        type: PLAYER_PLAY_TRACKS,
        id: startNavidromeId,
        data,
      })
    },
    [dispatch, bootstrapData],
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

  const queue = bootstrapData?.queue || {}
  const upNext = queue.up_next || []
  const remaining = queue.remaining || []

  return (
    <div className={classes.root}>
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

      {upNext.length > 0 && (
        <>
          <Typography variant="subtitle1" className={classes.sectionTitle}>
            Up Next
          </Typography>
          <Grid container spacing={2}>
            {upNext.map((track) => (
              <Grid item xs={12} sm={6} key={track.track_id}>
                <UpNextCard track={track} onPlay={handlePlay} classes={classes} />
              </Grid>
            ))}
          </Grid>
        </>
      )}

      {remaining.length > 0 && (
        <>
          <Typography variant="subtitle1" className={classes.sectionTitle}>
            Queue
          </Typography>
          {remaining.map((track) => (
            <QueueRow
              key={track.track_id}
              track={track}
              onPlay={handlePlay}
              classes={classes}
            />
          ))}
        </>
      )}

      {upNext.length === 0 && remaining.length === 0 && (
        <Typography color="textSecondary" style={{ marginTop: 32 }}>
          No recommendations available yet. Play some tracks to personalise your feed.
        </Typography>
      )}
    </div>
  )
}
