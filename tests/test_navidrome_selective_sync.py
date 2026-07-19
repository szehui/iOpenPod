"""Tests for Navidrome selective sync functionality."""
from __future__ import annotations

from unittest.mock import patch

from iopenpod.sync.navidrome_library import NavidromeClient, NavidromeLibrary


class TestNavidromeClientMethods:
    """Test NavidromeClient has required methods."""

    def test_client_has_get_all_albums(self):
        assert hasattr(NavidromeClient, "get_all_albums")

    def test_client_has_get_album(self):
        assert hasattr(NavidromeClient, "get_album")

    def test_client_has_get_all_songs(self):
        assert hasattr(NavidromeClient, "get_all_songs")

    def test_client_has_get_song(self):
        assert hasattr(NavidromeClient, "get_song")

    def test_client_has_stream_url(self):
        assert hasattr(NavidromeClient, "stream_url")


class TestNavidromeLibrarySync:
    """Test NavidromeLibrary sync method with selective sync."""

    def test_sync_with_song_ids_calls_resolve_songs(self):
        """When song_ids is provided, _resolve_songs is called instead of get_all_songs."""
        lib = NavidromeLibrary("http://test", "user", "pass", "/tmp/cache")
        with patch.object(lib, "_resolve_songs") as mock_resolve, \
             patch.object(lib, "get_all_cached_songs") as mock_cached:

            mock_resolve.return_value = [{"id": "1"}, {"id": "2"}]
            mock_cached.return_value = []

            lib.sync(song_ids=["1", "2"])

            mock_resolve.assert_called_once_with(["1", "2"])

    def test_sync_without_song_ids_calls_get_all_songs(self):
        """When song_ids is None, full sync is performed (backward compat)."""
        lib = NavidromeLibrary("http://test", "user", "pass", "/tmp/cache")
        with patch.object(lib, "get_all_songs") as mock_get_all, \
             patch.object(lib, "get_all_cached_songs") as mock_cached, \
             patch.object(lib, "_download_file"):

            mock_get_all.return_value = [{"id": "3"}]
            mock_cached.return_value = []

            lib.sync()

            mock_get_all.assert_called_once()

    def test_sync_song_ids_defaults_to_none(self):
        """song_ids parameter should default to None."""
        lib = NavidromeLibrary("http://test", "user", "pass", "/tmp/cache")
        import inspect
        sig = inspect.signature(lib.sync)
        assert sig.parameters["song_ids"].default is None

    def test_sync_with_empty_song_ids_syncs_nothing(self):
        """Empty song_ids list should sync nothing (not fall back to full)."""
        lib = NavidromeLibrary("http://test", "user", "pass", "/tmp/cache")
        with patch.object(lib, "get_all_songs") as mock_get_all, \
             patch.object(lib, "_resolve_songs") as mock_resolve, \
             patch.object(lib, "_download_file") as mock_dl, \
             patch.object(lib, "get_all_cached_songs") as mock_cached:

            mock_resolve.return_value = []
            mock_cached.return_value = []

            lib.sync(song_ids=[])

            # _resolve_songs([]) returns [] without calling get_all_songs
            mock_resolve.assert_called_once_with([])
            mock_get_all.assert_not_called()
            mock_dl.assert_not_called()

    def test_selective_sync_skips_already_cached(self, tmp_path):
        """Already-cached songs should be skipped during selective sync."""
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        # Pre-create files for songs 1 and 3 with non-zero size
        (cache_dir / "1.mp3").write_bytes(b"x" * 1024)  # 1KB file
        (cache_dir / "3.mp3").write_bytes(b"y" * 2048)  # 2KB file

        lib = NavidromeLibrary("http://test", "user", "pass", str(cache_dir))
        with patch.object(lib, "_resolve_songs") as mock_resolve, \
             patch.object(lib, "_download_file") as mock_dl, \
             patch.object(lib.client, "stream_url") as mock_stream:

            mock_resolve.return_value = [
                {"id": "1", "suffix": "mp3", "size": 1024},
                {"id": "2", "suffix": "flac", "size": 0},  # Unknown size, will download
                {"id": "3", "suffix": "mp3", "size": 2048},
            ]
            mock_stream.return_value = "http://stream/2"

            lib.sync(song_ids=["1", "2", "3"])

            # Only the uncached/unknown-size song should be downloaded
            assert mock_dl.call_count == 1
            args, _ = mock_dl.call_args
            assert "2.flac" in str(args[1])

    def test_selective_sync_all_cached_skips_download(self, tmp_path):
        """When all selected songs are already cached with matching size, nothing is downloaded."""
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        (cache_dir / "1.mp3").write_bytes(b"z" * 1024)  # 1KB file

        lib = NavidromeLibrary("http://test", "user", "pass", str(cache_dir))
        with patch.object(lib, "_resolve_songs") as mock_resolve, \
             patch.object(lib, "_download_file") as mock_dl:

            mock_resolve.return_value = [{"id": "1", "suffix": "mp3", "size": 1024}]

            lib.sync(song_ids=["1"])

            mock_dl.assert_not_called()


class TestNavidromeLibraryGetSongMetadata:
    """Test NavidromeLibrary get_song_metadata method."""

    def test_get_song_metadata_returns_song_dict(self):
        lib = NavidromeLibrary("http://test", "user", "pass", "/tmp/cache")
        with patch.object(lib.client, "get_song") as mock_get_song:
            mock_get_song.return_value = {"id": "1", "title": "Test Song"}
            result = lib.get_song_metadata("1")
            assert result == {"id": "1", "title": "Test Song"}
            mock_get_song.assert_called_once_with("1")

    def test_get_song_metadata_returns_none_on_exception(self):
        lib = NavidromeLibrary("http://test", "user", "pass", "/tmp/cache")
        with patch.object(lib.client, "get_song") as mock_get_song:
            mock_get_song.side_effect = Exception("API error")
            result = lib.get_song_metadata("1")
            assert result is None
            mock_get_song.assert_called_once_with("1")


class TestNavidromeLibraryGetAllCachedSongs:
    """Test NavidromeLibrary get_all_cached_songs method."""

    def test_get_all_cached_songs_returns_list_of_filenames(self, tmp_path):
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        (cache_dir / "1.mp3").touch()
        (cache_dir / "2.flac").touch()
        (cache_dir / "subdir").mkdir()
        (cache_dir / "subdir" / "nested.txt").touch()

        lib = NavidromeLibrary("http://test", "user", "pass", str(cache_dir))
        result = lib.get_all_cached_songs()

        assert set(result) == {"1.mp3", "2.flac"}

    def test_get_all_cached_songs_returns_empty_on_os_error(self, tmp_path):
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        (cache_dir / "1.mp3").touch()
        cache_dir.chmod(0)

        lib = NavidromeLibrary("http://test", "user", "pass", str(cache_dir))
        result = lib.get_all_cached_songs()

        cache_dir.chmod(0o755)
        assert result == []


class TestNavidromeLibraryResolveSongs:
    """Test NavidromeLibrary _resolve_songs method."""

    def test_resolve_songs_with_ids_returns_metadatas(self):
        lib = NavidromeLibrary("http://test", "user", "pass", "/tmp/cache")
        with patch.object(lib, "get_song_metadata") as mock_meta, \
             patch.object(lib, "get_all_songs") as mock_get_all:

            mock_meta.side_effect = [
                {"id": "1", "title": "Song 1"},
                {"id": "2", "title": "Song 2"},
                None,
            ]

            result = lib._resolve_songs(["1", "2", "3"])

            assert result == [
                {"id": "1", "title": "Song 1"},
                {"id": "2", "title": "Song 2"},
            ]
            assert mock_meta.call_count == 3
            mock_get_all.assert_not_called()

    def test_resolve_songs_without_ids_returns_all_songs(self):
        lib = NavidromeLibrary("http://test", "user", "pass", "/tmp/cache")
        with patch.object(lib, "get_all_songs") as mock_get_all, \
             patch.object(lib, "get_song_metadata") as mock_meta:

            mock_get_all.return_value = [{"id": "1"}, {"id": "2"}]

            result = lib._resolve_songs(None)

            assert result == [{"id": "1"}, {"id": "2"}]
            mock_get_all.assert_called_once()
            mock_meta.assert_not_called()

    def test_resolve_songs_with_empty_list_returns_empty_list(self):
        """When song_ids is empty list, should return empty list without calling get_all_songs."""
        lib = NavidromeLibrary("http://test", "user", "pass", "/tmp/cache")
        with patch.object(lib, "get_all_songs") as mock_get_all:
            result = lib._resolve_songs([])
            assert result == []
            mock_get_all.assert_not_called()


class TestNavidromeLibraryIntegration:
    """Integration tests for NavidromeLibrary selective sync."""

    def test_sync_accepts_song_ids_keyword(self):
        lib = NavidromeLibrary("http://test", "user", "pass", "/tmp/cache")
        lib.sync(song_ids=["1", "2"])

    def test_sync_song_ids_defaults_to_none(self):
        lib = NavidromeLibrary("http://test", "user", "pass", "/tmp/cache")
        import inspect
        sig = inspect.signature(lib.sync)
        assert sig.parameters["song_ids"].default is None