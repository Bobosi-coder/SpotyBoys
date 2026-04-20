from __future__ import annotations

import re
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
INDEX = (PROJECT_ROOT / "apps" / "frontend-web" / "index.html").read_text(encoding="utf-8")
APP_JS = (PROJECT_ROOT / "apps" / "frontend-web" / "src" / "app.js").read_text(encoding="utf-8")
CSS = (PROJECT_ROOT / "apps" / "frontend-web" / "src" / "styles.css").read_text(encoding="utf-8")


class FrontendContractTests(unittest.TestCase):
    def test_left_rail_renders_only_spotiboys_brand(self) -> None:
        match = re.search(r'<aside class="brand-rail"[^>]*>(.*?)</aside>', INDEX, re.S)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1).strip(), "SpotiBoys")

    def test_no_persistent_right_queue_panel(self) -> None:
        combined = (INDEX + APP_JS + CSS).lower()
        self.assertNotIn("right queue panel", combined)
        self.assertNotIn("queue-sidebar", combined)
        self.assertIn("playlist-drawer", combined)

    def test_browse_caps_are_enforced_in_frontend_rendering(self) -> None:
        self.assertIn("featured_items.slice(0, 4)", APP_JS)
        self.assertIn("random_carousel_items.slice(0, 10)", APP_JS)

    def test_playlist_drawer_hidden_by_default_and_toggle_is_local(self) -> None:
        self.assertIn('class="playlist-drawer"', INDEX)
        self.assertIn('aria-hidden="true"', INDEX)
        self.assertIn("state.drawerOpen = false", APP_JS)
        self.assertIn("toggleDrawer(!state.drawerOpen)", APP_JS)
        self.assertIn("before !== after", APP_JS)

    def test_bottom_dock_primary_controls(self) -> None:
        for control in ["skip-back", "play-pause", "skip-next", "playlist-button"]:
            self.assertIn(f'id="{control}"', INDEX)
        self.assertEqual(INDEX.count('class="dock-button'), 4)

    def test_one_playback_start_per_attempt_guard_exists(self) -> None:
        self.assertIn("emittedPlaybackStarts", APP_JS)
        self.assertIn("emitPlaybackStartOnce", APP_JS)
        self.assertIn("state.emittedPlaybackStarts.has(state.playbackAttemptId)", APP_JS)

    def test_queue_does_not_wrap_after_last_track(self) -> None:
        self.assertIn("playNextFromQueue", APP_JS)
        self.assertIn("refreshRecommendations({ autoplayFirst: true })", APP_JS)
        self.assertNotIn("state.queue.items[currentIndex + 1] || state.queue.items[0]", APP_JS)

    def test_playback_interaction_refreshes_recommendations(self) -> None:
        self.assertIn('event_type: "playback_start"', APP_JS)
        self.assertIn("await refreshRecommendations({ preserveCurrent: true })", APP_JS)
        self.assertIn('emitPlaybackLifecycle("complete")', APP_JS)


if __name__ == "__main__":
    unittest.main()
