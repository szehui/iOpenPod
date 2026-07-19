"""NavidromeBrowseDialog — browse albums/tracks on the Navidrome server
and select which tracks to sync to the iPod.
"""

from __future__ import annotations

import json
import logging
import os

from PyQt6.QtCore import QObject, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPalette
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from iopenpod.application.services import SettingsService as _SettingsService

from ..styles import (
    FONT_FAMILY,
    Colors,
    Metrics,
    accent_btn_css,
    btn_css,
    input_css,
    table_css,
)

logger = logging.getLogger(__name__)

_INDENT = "    "  # 4 spaces for track nesting


class _NavidromeAlbumLoader(QObject):
    """Background worker to fetch the album list (no track details)."""

    finished = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(
        self,
        url: str,
        username: str,
        password: str,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self._url = url
        self._username = username
        self._password = password

    def run(self) -> None:
        try:
            from iopenpod.sync.navidrome_library import NavidromeClient

            client = NavidromeClient(self._url, self._username, self._password)
            albums = client.get_all_albums()
            # getAlbumList2 already returns songCount per album — no extra calls needed
            self.finished.emit(albums)
        except Exception as exc:
            logger.exception("Failed to load Navidrome albums")
            self.error.emit(str(exc))


class _NavidromeTrackLoader(QObject):
    """Background worker to fetch tracks for a single album."""

    finished = pyqtSignal(str, list)  # album_id, list of tracks
    error = pyqtSignal(str, str)  # album_id, error message

    def __init__(
        self,
        url: str,
        username: str,
        password: str,
        album_id: str,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self._url = url
        self._username = username
        self._password = password
        self._album_id = album_id

    def run(self) -> None:
        try:
            from iopenpod.sync.navidrome_library import NavidromeClient

            client = NavidromeClient(self._url, self._username, self._password)
            detail = client.get_album(self._album_id)
            songs = detail.get("song", [])
            self.finished.emit(self._album_id, songs)
        except Exception as exc:
            logger.exception("Failed to load tracks for album %s", self._album_id)
            self.error.emit(self._album_id, str(exc))


class NavidromeBrowseDialog(QDialog):
    """Modal dialog to browse Navidrome albums and select tracks for sync."""

    def __init__(
        self,
        settings_service: _SettingsService,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._settings_service = settings_service
        self._albums: list[dict] = []  # Full list of albums from Navidrome
        self._selected_ids: set[str] = set()
        self._album_rows: dict[int, int] = {}  # table row -> album index in self._albums
        self._expanded_albums: set[str] = set()  # album IDs that are expanded
        self._track_cache: dict[str, list[dict]] = {}  # album_id -> list of tracks (lazy-loaded)
        self._track_rows: dict[int, dict] = {}  # table row -> track info
        self._album_track_rows: dict[str, list[int]] = {}  # album_id -> list of track row indices
        self._table: QTableWidget | None = None
        self._search_input: QLineEdit | None = None
        self._selection_label: QLabel | None = None
        self._loading_label: QLabel | None = None
        self._loader_thread: QThread | None = None

        self.setWindowTitle("Browse Navidrome Library")
        self.setModal(True)
        self.setMinimumSize(860, 620)
        self.resize(980, 720)
        self.setAutoFillBackground(True)
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor(Colors.DIALOG_BG))
        self.setPalette(palette)

        self._build_ui()
        self._load_selected_ids()
        self._start_loading_albums()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(12)

        # Header
        header = QLabel("Select tracks from your Navidrome library to sync to the iPod.")
        header.setFont(QFont(FONT_FAMILY, Metrics.FONT_SM))
        header.setStyleSheet(f"color: {Colors.TEXT_SECONDARY};")
        header.setWordWrap(True)
        outer.addWidget(header)

        # Toolbar: search + select all / deselect all
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("Search albums...")
        self._search_input.setStyleSheet(input_css())
        self._search_input.textChanged.connect(self._apply_search_filter)
        toolbar.addWidget(self._search_input, 1)

        select_all_btn = QPushButton("Select All")
        select_all_btn.setStyleSheet(btn_css())
        select_all_btn.clicked.connect(self._select_all)
        toolbar.addWidget(select_all_btn)

        deselect_all_btn = QPushButton("Deselect All")
        deselect_all_btn.setStyleSheet(btn_css())
        deselect_all_btn.clicked.connect(self._deselect_all)
        toolbar.addWidget(deselect_all_btn)

        outer.addLayout(toolbar)

        self._loading_label = QLabel("Loading albums from Navidrome...")
        self._loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._loading_label.setStyleSheet(f"color: {Colors.TEXT_SECONDARY}; padding: 32px;")
        outer.addWidget(self._loading_label)

        # Album/track table
        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["", "Title", "Artist", "Duration"])
        self._table.setStyleSheet(table_css())
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.setShowGrid(False)
        header_view = self._table.horizontalHeader()
        header_view.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(0, 40)
        header_view.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header_view.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self._table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self._table.cellClicked.connect(self._on_cell_clicked)
        outer.addWidget(self._table, 1)

        # Selection summary
        self._selection_label = QLabel("0 tracks selected")
        self._selection_label.setStyleSheet(f"color: {Colors.TEXT_SECONDARY};")
        outer.addWidget(self._selection_label)

        # Buttons
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addStretch()

        cancel = QPushButton("Cancel")
        cancel.setStyleSheet(btn_css())
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)

        save = QPushButton("Save Selection")
        save.setStyleSheet(accent_btn_css())
        save.clicked.connect(self._save_and_accept)
        save.setDefault(True)
        buttons.addWidget(save)

        outer.addLayout(buttons)

    def _load_selected_ids(self) -> None:
        """Load previously selected IDs from settings."""
        try:
            s = self._settings_service.get_global_settings()
            raw = getattr(s, "navidrome_selected_ids", "").strip()
            if raw:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    self._selected_ids = set(parsed)
        except Exception:
            self._selected_ids = set()

    def _start_loading_albums(self) -> None:
        """Kick off background album loading (album list only, no tracks)."""
        s = self._settings_service.get_global_settings()
        url = s.navidrome_url.strip()
        username = s.navidrome_username.strip()
        password = s.navidrome_password

        self._loading_label.setVisible(True)
        self._table.setVisible(False)

        self._loader_thread = QThread(self)
        self._loader = _NavidromeAlbumLoader(url, username, password)
        self._loader.moveToThread(self._loader_thread)
        self._loader_thread.started.connect(self._loader.run)
        self._loader.finished.connect(self._on_albums_loaded)
        self._loader.error.connect(self._on_load_error)
        self._loader.finished.connect(self._loader_thread.quit)
        self._loader.error.connect(self._loader_thread.quit)
        self._loader_thread.finished.connect(self._loader_thread.deleteLater)
        self._loader_thread.start()

    def _on_albums_loaded(self, albums: list[dict]) -> None:
        # Sort albums by artist (case-insensitive), then by album title
        self._albums = sorted(
            albums,
            key=lambda a: (
                a.get("artist", "").lower(),
                a.get("name", a.get("title", "")).lower(),
            ),
        )

        # Pre-select tracks that are already cached on the iPod
        self._preselect_cached_tracks()

        self._loading_label.setVisible(False)
        self._table.setVisible(True)
        self._build_table_from_scratch()

    def _on_load_error(self, error: str) -> None:
        self._loading_label.setText(f"Error loading albums: {error}")
        self._loading_label.setStyleSheet(f"color: {Colors.DANGER}; padding: 32px;")

    def _preselect_cached_tracks(self) -> None:
        """Pre-select tracks that already exist in the local cache directory."""
        s = self._settings_service.get_global_settings()
        from iopenpod.infrastructure.settings_paths import default_navidrome_cache_dir

        cache_dir = s.navidrome_cache_dir.strip() or default_navidrome_cache_dir()
        if not os.path.isdir(cache_dir):
            return

        try:
            cached = set()
            for fname in os.listdir(cache_dir):
                fpath = os.path.join(cache_dir, fname)
                if not os.path.isfile(fpath):
                    continue
                stem, _ = os.path.splitext(fname)
                if stem:
                    cached.add(stem)
            # We don't have track IDs yet (lazy-loaded), so we can only pre-select
            # based on the cache overlay. Without album tracks loaded, we can't
            # match against the library. This will be refined when tracks are loaded.
            # For now: mark cached IDs as selected (they'll be reconciled on expand).
            # Any cached IDs that don't appear in any album are harmless noise.
            self._selected_ids.update(cached)
        except (OSError, PermissionError):
            logger.warning("Could not scan Navidrome cache dir for pre-selection", exc_info=True)

    def _build_table_from_scratch(self) -> None:
        """Populate the table with albums (and track rows for expanded albums)."""
        self._table.setRowCount(0)
        self._album_rows.clear()
        self._track_rows.clear()
        self._album_track_rows.clear()

        filter_text = self._search_input.text() if self._search_input else ""
        filter_lower = filter_text.strip().lower() if filter_text else ""

        row = 0
        for idx, album in enumerate(self._albums):
            album_id = album["id"]
            album_title = album.get("name", album.get("title", "Unknown Album"))
            album_artist = album.get("artist", "Unknown Artist")
            song_count = album.get("songCount", 0)

            # Apply search filter
            if filter_lower and filter_lower not in album_title.lower() and filter_lower not in album_artist.lower():
                continue

            # Album header row
            self._table.insertRow(row)

            check = QCheckBox()
            check.setChecked(self._all_album_tracks_selected(album))
            check.stateChanged.connect(lambda state, aid=album_id: self._on_album_check_changed(aid, state))
            self._table.setCellWidget(row, 0, check)

            expand_indicator = "▶ " if song_count > 0 else ""
            title_item = QTableWidgetItem(f"{expand_indicator}{album_title}")
            title_item.setFlags(title_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._table.setItem(row, 1, title_item)

            artist_item = QTableWidgetItem(album_artist)
            artist_item.setFlags(artist_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._table.setItem(row, 2, artist_item)

            dur_text = f"{song_count} tracks" if song_count else ""
            duration_item = QTableWidgetItem(dur_text)
            duration_item.setFlags(duration_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._table.setItem(row, 3, duration_item)

            self._album_rows[row] = idx
            row += 1

            # If this album is expanded and we have cached tracks, show them
            expanded = album_id in self._expanded_albums
            if expanded and album_id in self._track_cache:
                self._insert_album_tracks(album_id, row)
                row += len(self._track_cache[album_id])

        self._update_selection_label()

    def _insert_album_tracks(self, album_id: str, start_row: int) -> None:
        """Insert track rows for an album starting at start_row."""
        tracks = self._track_cache.get(album_id, [])
        track_rows = []
        for i, track in enumerate(tracks):
            r = start_row + i
            self._table.insertRow(r)

            song_id = track.get("id", "")
            check = QCheckBox()
            check.setChecked(song_id in self._selected_ids)
            check.stateChanged.connect(
                lambda state, sid=song_id: self._on_track_check_changed(sid, state)
            )
            self._table.setCellWidget(r, 0, check)

            title = track.get("title", "Untitled")
            track_num = track.get("track", "")
            track_label = f"{_INDENT}{track_num}. {title}" if track_num else f"{_INDENT}{title}"
            title_item = QTableWidgetItem(track_label)
            title_item.setFlags(title_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._table.setItem(r, 1, title_item)

            track_artist = track.get("artist", "")
            artist_item = QTableWidgetItem(track_artist)
            artist_item.setFlags(artist_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._table.setItem(r, 2, artist_item)

            duration_sec = track.get("duration", 0)
            duration_str = f"{int(duration_sec // 60)}:{int(duration_sec % 60):02d}" if duration_sec else ""
            dur_item = QTableWidgetItem(duration_str)
            dur_item.setFlags(dur_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._table.setItem(r, 3, dur_item)

            self._track_rows[r] = track
            track_rows.append(r)
        if track_rows:
            self._album_track_rows[album_id] = track_rows

    def _remove_album_tracks(self, album_id: str) -> None:
        """Remove track rows for an album given its album_id."""
        rows_to_remove = self._album_track_rows.get(album_id, [])
        for r in sorted(rows_to_remove, reverse=True):
            self._table.removeRow(r)
            if r in self._track_rows:
                del self._track_rows[r]
        if album_id in self._album_track_rows:
            del self._album_track_rows[album_id]
        # Update row indices for everything below the removed rows
        if rows_to_remove:
            self._shift_row_indices_after(min(rows_to_remove), -len(rows_to_remove))

    def _shift_row_indices_after(self, start_row: int, delta: int) -> None:
        """Adjust stored row indices after a given row by *delta*."""
        new_album_rows = {}
        for row, idx in self._album_rows.items():
            nr = row + delta if row > start_row else row
            new_album_rows[nr] = idx
        self._album_rows = new_album_rows

        new_track_rows = {}
        for row, track in self._track_rows.items():
            nr = row + delta if row > start_row else row
            new_track_rows[nr] = track
        self._track_rows = new_track_rows

        for aid in list(self._album_track_rows.keys()):
            self._album_track_rows[aid] = [
                r + delta if r > start_row else r for r in self._album_track_rows[aid]
            ]

    def _all_album_tracks_selected(self, album: dict) -> bool:
        album_id = album["id"]
        tracks = self._track_cache.get(album_id, [])
        if not tracks:
            return False
        return all(t["id"] in self._selected_ids for t in tracks if t.get("id"))

    def _on_album_check_changed(self, album_id: str, state: int) -> None:
        checked = state == Qt.CheckState.Checked.value
        tracks = self._track_cache.get(album_id, [])
        for track in tracks:
            sid = track.get("id")
            if sid:
                if checked:
                    self._selected_ids.add(sid)
                else:
                    self._selected_ids.discard(sid)
        self._update_selection_label()

    def _on_track_check_changed(self, song_id: str, state: int) -> None:
        checked = state == Qt.CheckState.Checked.value
        if checked:
            self._selected_ids.add(song_id)
        else:
            self._selected_ids.discard(song_id)
        self._update_selection_label()

    def _on_cell_clicked(self, row: int, col: int) -> None:
        """Handle cell clicks — expand/collapse on album title column."""
        if col != 1:
            return
        idx = self._album_rows.get(row)
        if idx is None:
            return
        album = self._albums[idx]
        album_id = album["id"]
        song_count = album.get("songCount", 0)
        if song_count < 1:
            return

        if album_id in self._expanded_albums:
            self._expanded_albums.discard(album_id)
            self._collapse_album(album_id)
        else:
            self._expanded_albums.add(album_id)
            self._expand_album(album_id)

    def _expand_album(self, album_id: str) -> None:
        """Expand an album. If tracks aren't cached yet, fetch them lazily."""
        # Already cached — insert immediately
        if album_id in self._track_cache:
            # Find the album header row
            start_row = self._find_album_row(album_id)
            if start_row is None:
                return
            self._insert_album_tracks(album_id, start_row + 1)
            num_tracks = len(self._track_cache[album_id])
            self._shift_row_indices_after(start_row, num_tracks)
            return

        # Not cached — show a loading indicator in the duration column
        header_row = self._find_album_row(album_id)
        if header_row is not None:
            dur_item = QTableWidgetItem("loading...")
            dur_item.setFlags(dur_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._table.setItem(header_row, 3, dur_item)

        # Fetch tracks in background
        s = self._settings_service.get_global_settings()
        url = s.navidrome_url.strip()
        username = s.navidrome_username.strip()
        password = s.navidrome_password

        loader = _NavidromeTrackLoader(
            url, username, password, album_id, parent=self
        )
        thread = QThread(self)
        loader.moveToThread(thread)
        thread.started.connect(loader.run)
        loader.finished.connect(lambda aid, songs: self._on_tracks_loaded(aid, songs))
        loader.error.connect(lambda aid, err: self._on_tracks_error(aid, err))
        loader.finished.connect(thread.quit)
        loader.error.connect(thread.quit)
        thread.finished.connect(thread.deleteLater)
        thread.start()

    def _on_tracks_loaded(self, album_id: str, songs: list[dict]) -> None:
        """Callback when tracks for an album have been fetched."""
        # Cache them
        self._track_cache[album_id] = songs

        # If the album is still expanded, insert rows
        if album_id in self._expanded_albums:
            header_row = self._find_album_row(album_id)
            if header_row is not None:
                # Restore the track count display
                song_count = len(songs)
                header_item = QTableWidgetItem(f"{song_count} tracks")
                header_item.setFlags(header_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self._table.setItem(header_row, 3, header_item)

                self._insert_album_tracks(album_id, header_row + 1)
                self._shift_row_indices_after(header_row, song_count)

    def _on_tracks_error(self, album_id: str, error: str) -> None:
        """Callback when track loading fails for an album."""
        self._expanded_albums.discard(album_id)
        header_row = self._find_album_row(album_id)
        if header_row is not None:
            err_item = QTableWidgetItem("error")
            err_item.setFlags(err_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._table.setItem(header_row, 3, err_item)
        logger.error("Failed to load tracks for album %s: %s", album_id, error)

    def _collapse_album(self, album_id: str) -> None:
        """Collapse an album by removing its track rows."""
        self._remove_album_tracks(album_id)

    def _find_album_row(self, album_id: str) -> int | None:
        """Find the table row for an album header given its ID."""
        for row, idx in self._album_rows.items():
            if idx < len(self._albums) and self._albums[idx]["id"] == album_id:
                return row
        return None

    def _apply_search_filter(self) -> None:
        self._build_table_from_scratch()

    def _select_all(self) -> None:
        """Select all tracks across all albums."""
        for album in self._albums:
            album_id = album["id"]
            for track in self._track_cache.get(album_id, []):
                sid = track.get("id")
                if sid:
                    self._selected_ids.add(sid)
        self._build_table_from_scratch()
        self._update_selection_label()

    def _deselect_all(self) -> None:
        """Deselect all tracks."""
        self._selected_ids.clear()
        self._build_table_from_scratch()
        self._update_selection_label()

    def _update_selection_label(self) -> None:
        count = len(self._selected_ids)
        self._selection_label.setText(f"{count} track{'s' if count != 1 else ''} selected")

    def _save_and_accept(self) -> None:
        """Save selected IDs to settings and close dialog."""
        try:
            s = self._settings_service.get_global_settings()
            s.navidrome_selected_ids = json.dumps(list(self._selected_ids))
            self._settings_service.save_global_settings(s)
            self.accept()
        except Exception:
            logger.exception("Failed to save Navidrome selection")
            self.accept()
