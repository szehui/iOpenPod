"""Navidrome/Subsonic API client for iOpenPod sync.

Downloads a Navidrome library into a local cache directory so existing
PCLibrary scanning can pick it up, or serves as a source of PCTrack-compatible
metadata for direct sync integration.
"""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
from typing import Any

logger = logging.getLogger(__name__)


class NavidromeClient:
    """Low-level Subsonic API client for Navidrome."""

    API_VERSION = "1.16.0"
    CLIENT_NAME = "iOpenPod"

    def __init__(self, url: str, username: str, password: str) -> None:
        self.url = url.rstrip("/")
        self.username = username
        self.password = password

    # ── API helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _md5(s: str) -> str:
        return hashlib.md5(s.encode("utf-8")).hexdigest().lower()

    def _make_signed_params(self) -> dict[str, str]:
        """Return auth + common query params using token-based auth."""
        salt = secrets.token_hex(8)
        token = self._md5(self.password + salt)
        return {
            "u": self.username,
            "t": token,
            "s": salt,
            "v": self.API_VERSION,
            "c": self.CLIENT_NAME,
            "f": "json",
        }

    def _get(self, endpoint: str, extra: dict[str, str] | None = None) -> dict[str, Any]:
        """GET a Subsonic REST endpoint and return the parsed ``subsonic-response`` dict."""
        import requests  # deferred import; caller must have requests installed

        params = self._make_signed_params()
        if extra:
            params.update(extra)
        url = f"{self.url}/rest/{endpoint}.view"
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict):
            raise ValueError(f"Non-dict response from {endpoint}: {type(data).__name__}")
        sr = data.get("subsonic-response", {})
        if not isinstance(sr, dict):
            raise ValueError(f"Invalid subsonic-response from {endpoint}")
        status = sr.get("status", "")
        if status != "ok" and status:
            err_node = sr.get("error", {})
            if isinstance(err_node, dict):
                err_msg = err_node.get("message", str(err_node))
            else:
                err_msg = str(err_node)
            raise ValueError(f"Subsonic API error ({status}): {err_msg}")
        return sr

    # ── Public API ───────────────────────────────────────────────────────────

    def ping(self) -> bool:
        """Check server connectivity."""
        try:
            sr = self._get("ping")
            return sr.get("status") == "ok"
        except Exception:
            return False

    def get_artists(self) -> list[dict[str, Any]]:
        """Return all artists (flat list)."""
        sr = self._get("getArtists", {"type": "alphabeticalByName"})
        index_list = sr.get("artists", {}).get("index", [])
        if isinstance(index_list, dict):
            index_list = [index_list]
        artists: list[dict[str, Any]] = []
        for idx in index_list:
            children = idx.get("artist", [])
            if isinstance(children, dict):
                children = [children]
            artists.extend(children)
        return artists

    def get_album_list(
        self,
        *,
        list_type: str = "alphabeticalByName",
        size: int = 500,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Return albums of *type*."""
        sr = self._get("getAlbumList2", {
            "type": list_type,
            "size": str(size),
            "offset": str(offset),
        })
        container = sr.get("albumList2") if "albumList2" in sr else sr.get("albumList")
        if isinstance(container, dict):
            album_list = container.get("album", [])
        else:
            album_list = container if isinstance(container, list) else []
        if isinstance(album_list, dict):
            album_list = [album_list]
        return album_list

    def get_all_albums(self) -> list[dict[str, Any]]:
        """Fetch every album (paginate until empty)."""
        all_albums: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        offset = 0
        size = 500
        while True:
            batch = self.get_album_list(list_type="alphabeticalByName", size=size, offset=offset)
            if not batch:
                break
            added = 0
            for a in batch:
                aid = a.get("id", "")
                if aid and aid not in seen_ids:
                    seen_ids.add(aid)
                    all_albums.append(a)
                    added += 1
            if added == 0:
                break
            offset += size
        return all_albums

    def get_album(self, album_id: str) -> dict[str, Any]:
        """Return a single album with its song list."""
        sr = self._get("getAlbum", {"id": album_id})
        return sr.get("album", {})

    def get_song(self, song_id: str) -> dict[str, Any]:
        """Return metadata for a single song."""
        sr = self._get("getSong", {"id": song_id})
        return sr.get("song", {})

    def get_all_songs(self) -> list[dict[str, Any]]:
        """Fetch every song in the library by iterating albums."""
        seen: set[str] = set()
        songs: list[dict[str, Any]] = []
        for album in self.get_all_albums():
            album_detail = self.get_album(album["id"])
            children = album_detail.get("song", [])
            if isinstance(children, dict):
                children = [children]
            for s in children:
                sid = s.get("id", "")
                if sid and sid not in seen:
                    seen.add(sid)
                    songs.append(s)
        return songs

    def stream_url(self, song_id: str) -> str:
        """Return the download/stream URL for a song."""
        from urllib.parse import urlencode

        params = self._make_signed_params()
        params["id"] = song_id
        return f"{self.url}/rest/stream.view?{urlencode(params)}"

    def get_song_list(
        self,
        *,
        size: int = 500,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Return songs via getSongList (a flat, paginated endpoint).

        Uses the Subsonic ``getSongList`` (not via albums). Falls back to
        the ``alphabeticalByTitle`` list type.
        """
        sr = self._get("getSongList", {
            "type": "alphabeticalByTitle",
            "size": str(size),
            "offset": str(offset),
        })
        song_list = sr.get("songList", {}).get("song", [])
        if isinstance(song_list, dict):
            song_list = [song_list]
        return song_list


class NavidromeLibrary:
    """Downloads a Navidrome library into a local cache directory.

    Use ``sync()`` to download missing/changed tracks, then run
    ``PCLibrary`` over the cache dir to build the track catalogue.
    """

    def __init__(
        self,
        url: str,
        username: str,
        password: str,
        cache_dir: str,
    ) -> None:
        self.client = NavidromeClient(url, username, password)
        self.cache_dir = cache_dir
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    # ── song listing ─────────────────────────────────────────────────────────

    def get_all_songs(self) -> list[dict[str, Any]]:
        """Fetch every song from the client."""
        return self.client.get_all_songs()

    # ── sync / download ──────────────────────────────────────────────────────

    def get_song_metadata(self, song_id: str) -> dict[str, Any] | None:
        """Fetch metadata for a single song by ID."""
        try:
            return self.client.get_song(song_id)
        except Exception:
            logger.exception(f"Failed to fetch metadata for song {song_id}")
            return None

    def get_all_cached_songs(self) -> list[str]:
        """Return a list of filenames (with extensions) of all cached songs."""
        try:
            return [f for f in os.listdir(self.cache_dir) if os.path.isfile(os.path.join(self.cache_dir, f))]
        except OSError:
            return []

    def _resolve_songs(
        self,
        song_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Resolve song metadata: all songs (if ids is None) or only the given IDs."""
        if song_ids is not None:
            songs: list[dict[str, Any]] = []
            for sid in song_ids:
                meta = self.get_song_metadata(sid)
                if meta:
                    songs.append(meta)
            return songs
        return self.get_all_songs()

    def sync(
        self,
        progress_callback=None,
        is_cancelled=None,
        song_ids: list[str] | None = None,
    ) -> None:
        """Download tracks from Navidrome that aren't already cached.

        If *song_ids* is provided, only those tracks are downloaded.
        Otherwise every track in the library is downloaded (existing behaviour).
        """
        os.makedirs(self.cache_dir, exist_ok=True)
        songs = self._resolve_songs(song_ids)
        if not songs:
            logger.warning("NavidromeLibrary: no songs returned from API")
            return

        total = len(songs)
        label = f"{total} selected track(s)" if song_ids else f"{total} song(s)"
        logger.info("NavidromeLibrary: syncing %s to %s", label, self.cache_dir)
        downloaded = skipped = failed = 0

        for _i, song in enumerate(songs):
            if is_cancelled and is_cancelled():
                break

            sid = song.get("id")
            if not sid:
                continue

            ext = song.get("suffix", "mp3")
            if not ext.startswith("."):
                ext = f".{ext}"
            filepath = os.path.join(self.cache_dir, f"{sid}{ext}")

            expected_size = song.get("size", 0)
            if isinstance(expected_size, str):
                try:
                    expected_size = int(expected_size)
                except (ValueError, TypeError):
                    expected_size = 0

            if os.path.isfile(filepath):
                try:
                    actual = os.path.getsize(filepath)
                    if expected_size and actual == expected_size:
                        skipped += 1
                        if progress_callback:
                            progress_callback(downloaded + skipped + failed, total, os.path.basename(filepath))
                        continue
                except OSError:
                    pass  # treat as missing/broken -> will download

            url = self.client.stream_url(sid)
            success = self._download_file(url, filepath, expected_size)
            if success:
                downloaded += 1
            else:
                failed += 1

            if progress_callback:
                progress_callback(downloaded + skipped + failed, total, os.path.basename(filepath))

        logger.info(
            "NavidromeLibrary: sync complete — %d downloaded, %d skipped, %d failed",
            downloaded,
            skipped,
            failed,
        )

    @staticmethod
    def _download_file(url: str, dest: str, expected_size: int) -> bool:
        """Stream *url* to *dest*. Returns True on success."""
        import requests

        try:
            with requests.get(url, stream=True, timeout=60) as r:
                r.raise_for_status()
                with open(dest, "wb") as f:
                    for chunk in r.iter_content(8192):
                        if chunk:
                            f.write(chunk)
            return True
        except Exception:
            logger.exception(f"Failed to download {url}")
            try:
                os.remove(dest)
            except OSError:
                pass
            return False
