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


class TestNavidromeClientPlaylistMethods:
    """Test that NavidromeClient has the required playlist methods."""

    def test_client_has_get_playlists(self):
        assert hasattr(NavidromeClient, "get_playlists")

    def test_client_has_get_playlist(self):
        assert hasattr(NavidromeClient, "get_playlist")


class TestNavidromeLibraryPlaylists:
    """Test NavidromeLibrary playlist methods."""

    def test_get_selected_playlists_with_tracks(self):
        """Fetch playlists with full track listings."""
        lib = NavidromeLibrary("http://test", "user", "pass", "/tmp/cache")
        with patch.object(lib.client, "get_playlist") as mock_get_pl:
            mock_get_pl.side_effect = [
                {
                    "id": "pl1",
                    "name": "Chill Vibes",
                    "entry": [
                        {"id": "s1", "title": "Song 1"},
                        {"id": "s2", "title": "Song 2"},
                    ],
                },
                {
                    "id": "pl2",
                    "name": "Workout",
                    "entry": [{"id": "s3", "title": "Song 3"}],
                },
            ]

            result = lib.get_selected_playlists_with_tracks(["pl1", "pl2"])

            assert len(result) == 2
            assert result[0]["name"] == "Chill Vibes"
            assert result[0]["song_ids"] == ["s1", "s2"]
            assert result[0]["song_count"] == 2
            assert result[1]["name"] == "Workout"
            assert result[1]["song_ids"] == ["s3"]
            assert result[1]["song_count"] == 1
            assert mock_get_pl.call_count == 2

    def test_get_selected_playlists_with_tracks_skips_failed(self):
        """Gracefully skip playlists that fail to fetch."""
        lib = NavidromeLibrary("http://test", "user", "pass", "/tmp/cache")
        with patch.object(lib.client, "get_playlist") as mock_get_pl:
            mock_get_pl.side_effect = [
                Exception("Network error"),
                {
                    "id": "pl2",
                    "name": "Good Playlist",
                    "entry": [{"id": "s1"}],
                },
            ]

            result = lib.get_selected_playlists_with_tracks(["pl1", "pl2"])

            assert len(result) == 1
            assert result[0]["name"] == "Good Playlist"

    def test_get_selected_playlist_song_ids_merges_all(self):
        """Return set of all song IDs across selected playlists."""
        lib = NavidromeLibrary("http://test", "user", "pass", "/tmp/cache")
        with patch.object(lib, "get_selected_playlists_with_tracks") as mock_get_pl:
            mock_get_pl.return_value = [
                {"name": "A", "song_ids": ["s1", "s2"], "song_count": 2},
                {"name": "B", "song_ids": ["s2", "s3"], "song_count": 2},
            ]

            result = lib.get_selected_playlist_song_ids(["pl1", "pl2"])

            assert result == {"s1", "s2", "s3"}

    def test_sync_accepts_playlist_ids_keyword(self):
        """sync() accepts playlist_ids parameter."""
        lib = NavidromeLibrary("http://test", "user", "pass", "/tmp/cache")
        with patch.object(lib, "_resolve_songs") as mock_resolve, \
             patch.object(lib, "get_all_cached_songs") as mock_cached, \
             patch.object(lib, "get_selected_playlist_song_ids") as mock_pl_songs:

            mock_pl_songs.return_value = {"s1", "s2"}
            mock_resolve.return_value = [{"id": "s1"}, {"id": "s2"}]
            mock_cached.return_value = []

            lib.sync(playlist_ids=["pl1", "pl2"])

            mock_pl_songs.assert_called_once_with(["pl1", "pl2"])

    def test_sync_merges_song_ids_and_playlist_ids(self):
        """When both song_ids and playlist_ids are given, merge their song IDs."""
        lib = NavidromeLibrary("http://test", "user", "pass", "/tmp/cache")
        with patch.object(lib, "_resolve_songs") as mock_resolve, \
             patch.object(lib, "get_all_cached_songs") as mock_cached, \
             patch.object(lib, "get_selected_playlist_song_ids") as mock_pl_songs:

            mock_pl_songs.return_value = {"s3", "s4"}
            mock_cached.return_value = []

            lib.sync(song_ids=["s1", "s2"], playlist_ids=["pl1"])

            # Should merge: s1, s2, s3, s4
            mock_resolve.assert_called_once()
            resolved_arg = mock_resolve.call_args[0][0]
            assert set(resolved_arg) == {"s1", "s2", "s3", "s4"}

    def test_sync_playlist_ids_defaults_to_none(self):
        """playlist_ids parameter should default to None."""
        lib = NavidromeLibrary("http://test", "user", "pass", "/tmp/cache")
        import inspect
        sig = inspect.signature(lib.sync)
        assert sig.parameters["playlist_ids"].default is None

    def test_generate_playlist_m3us_creates_files(self, tmp_path):
        """_generate_playlist_m3us creates M3U8 files for each playlist."""
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()

        # Pre-create some cached song files
        (cache_dir / "song1.mp3").write_bytes(b"data1")
        (cache_dir / "song2.mp3").write_bytes(b"data2")
        (cache_dir / "song3.flac").write_bytes(b"data3")

        lib = NavidromeLibrary("http://test", "user", "pass", str(cache_dir))
        with patch.object(lib, "get_selected_playlists_with_tracks") as mock_get_pl:
            mock_get_pl.return_value = [
                {"name": "Chill Vibes", "song_ids": ["song1", "song2"], "song_count": 2},
                {"name": "Workout Mix", "song_ids": ["song3"], "song_count": 1},
            ]

            lib._generate_playlist_m3us(["pl1", "pl2"])

            # Check M3U files exist
            m3u1 = cache_dir / "Chill Vibes.m3u8"
            m3u2 = cache_dir / "Workout Mix.m3u8"
            assert m3u1.exists()
            assert m3u2.exists()

            # Check content
            content1 = m3u1.read_text()
            assert "#EXTM3U" in content1
            assert str(cache_dir / "song1.mp3") in content1
            assert str(cache_dir / "song2.mp3") in content1

            content2 = m3u2.read_text()
            assert "#EXTM3U" in content2
            assert str(cache_dir / "song3.flac") in content2

    def test_generate_playlist_m3us_sanitizes_filenames(self, tmp_path):
        """Playlist names with weird chars get sanitized for safe filenames."""
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()

        lib = NavidromeLibrary("http://test", "user", "pass", str(cache_dir))
        with patch.object(lib, "get_selected_playlists_with_tracks") as mock_get_pl:
            mock_get_pl.return_value = [
                {"name": "Rock / Metal // Favorites!", "song_ids": [], "song_count": 0},
            ]

            lib._generate_playlist_m3us(["pl1"])

            # Slashes should become underscores
            m3u = cache_dir / "Rock _ Metal __ Favorites_.m3u8"
            assert m3u.exists()

    def test_generate_playlist_m3us_skips_missing_songs(self, tmp_path):
        """Playlist songs not found in cache dir are silently skipped."""
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()

        lib = NavidromeLibrary("http://test", "user", "pass", str(cache_dir))
        with patch.object(lib, "get_selected_playlists_with_tracks") as mock_get_pl:
            mock_get_pl.return_value = [
                {"name": "Partial", "song_ids": ["exists", "missing"], "song_count": 2},
            ]

            (cache_dir / "exists.mp3").write_bytes(b"x")

            lib._generate_playlist_m3us(["pl1"])

            m3u = cache_dir / "Partial.m3u8"
            assert m3u.exists()
            content = m3u.read_text()
            assert str(cache_dir / "exists.mp3") in content
            # Only 1 track line + EXTINF = 2 lines total
            assert len(content.strip().split("\n")) == 2

    def test_sync_with_playlist_ids_generates_m3us(self, tmp_path):
        """sync() with playlist_ids calls _generate_playlist_m3us after download."""
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()

        lib = NavidromeLibrary("http://test", "user", "pass", str(cache_dir))
        with patch.object(lib, "_resolve_songs") as mock_resolve, \
             patch.object(lib, "get_all_cached_songs") as mock_cached, \
             patch.object(lib, "get_selected_playlist_song_ids") as mock_pl_songs, \
             patch.object(lib, "_generate_playlist_m3us") as mock_gen_m3u, \
             patch.object(lib, "_download_file") as mock_dl:

            mock_pl_songs.return_value = {"s1"}
            mock_resolve.return_value = [{"id": "s1", "suffix": "mp3", "size": 100}]
            mock_cached.return_value = []

            lib.sync(playlist_ids=["pl1"])

            mock_gen_m3u.assert_called_once_with(["pl1"])

    def test_sync_without_playlist_ids_skips_m3u_generation(self, tmp_path):
        """sync() without playlist_ids does NOT call _generate_playlist_m3us."""
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()

        lib = NavidromeLibrary("http://test", "user", "pass", str(cache_dir))
        with patch.object(lib, "_resolve_songs") as mock_resolve, \
             patch.object(lib, "get_all_cached_songs") as mock_cached, \
             patch.object(lib, "_generate_playlist_m3us") as mock_gen_m3u, \
             patch.object(lib, "_download_file") as mock_dl:

            mock_resolve.return_value = [{"id": "s1", "suffix": "mp3", "size": 100}]
            mock_cached.return_value = []

            lib.sync()

            mock_gen_m3u.assert_not_called()