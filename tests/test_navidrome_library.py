"""Tests for the NavidromeLibrary sync client."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from iopenpod.sync.navidrome_library import NavidromeLibrary


def test_navidrome_library_sync_downloads_and_skips_existing(tmp_path):
    """Sync downloads missing tracks and skips existing up-to-date files."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()

    # Both songs have same expected size so they both match after first download
    songs = [
        {
            "id": "1",
            "title": "Song One",
            "artist": "Artist A",
            "album": "Album Alpha",
            "track": "1",
            "year": "2020",
            "genre": "Rock",
            "suffix": ".mp3",
            "size": "1024",
        },
        {
            "id": "2",
            "title": "Song Two",
            "artist": "Artist B",
            "album": "Album Beta",
            "track": "2",
            "year": "2021",
            "genre": "Pop",
            "suffix": ".flac",
            "size": "1024",
        },
    ]

    with patch("iopenpod.sync.navidrome_library.NavidromeClient") as mock_client_class:
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_client.get_all_songs.return_value = songs
        mock_client.stream_url.side_effect = lambda sid: f"http://example.com/stream/{sid}"

        with patch("requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.__enter__.return_value = mock_resp
            mock_resp.__exit__.return_value = None
            mock_resp.iter_content.return_value = [b"x" * 1024]
            mock_resp.raise_for_status.return_value = None
            mock_get.return_value = mock_resp

            lib = NavidromeLibrary(
                url="http://example.com",
                username="user",
                password="pass",
                cache_dir=str(cache_dir),
            )
            hook = MagicMock()
            lib.sync(progress_callback=hook)

            # Both songs downloaded
            assert mock_get.call_count == 2
            assert hook.call_count == 2
            assert (cache_dir / "1.mp3").exists()
            assert (cache_dir / "2.flac").exists()
            assert (cache_dir / "1.mp3").stat().st_size == 1024
            assert (cache_dir / "2.flac").stat().st_size == 1024

            # Second sync skips both (file sizes match expected)
            mock_get.reset_mock()
            hook.reset_mock()
            lib.sync(progress_callback=hook)
            assert mock_get.call_count == 0
            assert hook.call_count == 2

            # Downloaded files unchanged
            assert (cache_dir / "1.mp3").stat().st_size == 1024
            assert (cache_dir / "2.flac").stat().st_size == 1024


def test_navidrome_library_sync_only_downloads_mismatched_size(tmp_path):
    """Only re-downloads tracks where file size on disk differs from expected."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()

    songs = [
        {"id": "1", "title": "Match", "suffix": ".mp3", "size": "512", "artist": "A", "album": "A"},
    ]

    with patch("iopenpod.sync.navidrome_library.NavidromeClient") as mock_client_class:
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_client.get_all_songs.return_value = songs
        mock_client.stream_url.side_effect = lambda sid: f"http://example.com/stream/{sid}"

        with patch("requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.__enter__.return_value = mock_resp
            mock_resp.__exit__.return_value = None
            mock_resp.iter_content.return_value = [b"y" * 512]
            mock_resp.raise_for_status.return_value = None
            mock_get.return_value = mock_resp

            lib = NavidromeLibrary(
                url="http://example.com", username="user", password="pass", cache_dir=str(cache_dir),
            )
            lib.sync()
            assert mock_get.call_count == 1
            assert (cache_dir / "1.mp3").stat().st_size == 512

            # Alter file on disk so size mismatches
            with open(cache_dir / "1.mp3", "wb") as f:
                f.write(b"x" * 128)

            mock_get.reset_mock()
            lib.sync()
            # Should re-download because 128 != 512
            assert mock_get.call_count == 1
            assert (cache_dir / "1.mp3").stat().st_size == 512


def test_navidrome_library_sync_handles_download_failure(tmp_path):
    """Sync continues when a download fails."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()

    songs = [
        {"id": "1", "title": "Song One", "suffix": ".mp3", "size": "1024", "artist": "A", "album": "A"},
        {"id": "2", "title": "Song Two", "suffix": ".mp3", "size": "2048", "artist": "B", "album": "B"},
    ]

    with patch("iopenpod.sync.navidrome_library.NavidromeClient") as mock_client_class:
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_client.get_all_songs.return_value = songs
        mock_client.stream_url.side_effect = lambda sid: f"http://example.com/stream/{sid}"

        with patch("requests.get") as mock_get:
            mock_fail = MagicMock()
            mock_fail.__enter__.side_effect = Exception("Network error")
            mock_fail.__exit__.return_value = None

            mock_ok = MagicMock()
            mock_ok.__enter__.return_value = mock_ok
            mock_ok.__exit__.return_value = None
            mock_ok.iter_content.return_value = [b"ok"]
            mock_ok.raise_for_status.return_value = None

            mock_get.side_effect = [mock_fail, mock_ok]

            lib = NavidromeLibrary(
                url="http://example.com",
                username="user",
                password="pass",
                cache_dir=str(cache_dir),
            )
            lib.sync()

            # Failed download cleaned up, successful one exists
            assert not (cache_dir / "1.mp3").exists()
            assert (cache_dir / "2.mp3").exists()
