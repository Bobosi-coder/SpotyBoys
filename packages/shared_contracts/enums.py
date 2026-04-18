from __future__ import annotations

from enum import Enum


class FallbackLevel(str, Enum):
    NONE = "none"
    CACHED_POPULARITY = "cached-popularity"
    NON_PERSONALIZED = "non-personalized"
    CATALOG_SAFE_FALLBACK = "catalog-safe-fallback"
    SESSION_RECOVERY = "session-recovery"


class PlaybackEventType(str, Enum):
    PLAYBACK_START = "playback_start"
    HEARTBEAT = "heartbeat"
    PAUSE = "pause"
    RESUME = "resume"
    SKIP = "skip"
    COMPLETE = "complete"


class FeedbackType(str, Enum):
    LIKE = "like"
    DISLIKE = "dislike"
    SAVE = "save"


class BrowseSurfaceSlot(str, Enum):
    FEATURED_1 = "featured_1"
    FEATURED_2 = "featured_2"
    FEATURED_3 = "featured_3"
    FEATURED_4 = "featured_4"
    RANDOM_1 = "random_1"
    RANDOM_2 = "random_2"
    RANDOM_3 = "random_3"
    RANDOM_4 = "random_4"
    RANDOM_5 = "random_5"
    RANDOM_6 = "random_6"
    RANDOM_7 = "random_7"
    RANDOM_8 = "random_8"
    RANDOM_9 = "random_9"
    RANDOM_10 = "random_10"

