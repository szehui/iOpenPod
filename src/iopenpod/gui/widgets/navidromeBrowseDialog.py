"""NavidromeBrowseDialog — browse Navidrome albums grouped by artist
with lazy-loaded track details.
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

_ALBUM_INDENT = "    "
_TRACK_INDENT = "        "


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
            self.finished.emit(albums)
        except Exception as exc:
            logger.exception("Failed to load Navidrome albums")
            self.error.emit(str(exc))


class _NavidromeTrackLoader(QObject):
    """Background worker to fetch tracks for a single album."""

    finished = pyqtSignal(str, list)
    error = pyqtSignal(str, str)

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
    """Modal dialog to browse Navidrome albums grouped by artist,
    expand albums to select individual tracks.
    """

    def __init__(
        self,
        settings_service: _SettingsService,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._settings_service = settings_service
        self._albums: list[dict] = []
        self._artist_groups: list[dict] = []  # [{name, album_indices: [int]}]
        self._selected_ids: set[str] = set()
        self._expanded_artists: set[str] = set()
        self._expanded_albums: set[str] = set()
        self._loading_albums: set[str] = set()
        self._track_cache: dict[str, list[dict]] = {}

        # Row tracking for click handling (rebuilt each time)
        self._artist_rows: dict[int, str] = {}
        self._album_rows: dict[int, int] = {}

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

    # ---- UI construction ----

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(12)

        header = QLabel("Select tracks from your Navidrome library to sync to the iPod.")
        header.setFont(QFont(FONT_FAMILY, Metrics.FONT_SM))
        header.setStyleSheet(f"color: {Colors.TEXT_SECONDARY};")
        header.setWordWrap(True)
        outer.addWidget(header)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("Search albums or artists...")
        self._search_input.setStyleSheet(input_css())
        self._search_input.textChanged.connect(self._on_search_changed)
        toolbar.addWidget(self._search_input, 1)

        expand_all_btn = QPushButton("Expand All")
        expand_all_btn.setStyleSheet(btn_css())
        expand_all_btn.clicked.connect(self._expand_all)
        toolbar.addWidget(expand_all_btn)

        collapse_all_btn = QPushButton("Collapse All")
        collapse_all_btn.setStyleSheet(btn_css())
        collapse_all_btn.clicked.connect(self._collapse_all)
        toolbar.addWidget(collapse_all_btn)

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

        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["", "Title", "Year", "Tracks"])
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

        self._selection_label = QLabel("0 tracks selected")
        self._selection_label.setStyleSheet(f"color: {Colors.TEXT_SECONDARY};")
        outer.addWidget(self._selection_label)

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

    # ---- Data loading ----

    def _load_selected_ids(self) -> None:
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
        self._albums = sorted(
            albums,
            key=lambda a: (
                a.get("artist", "").lower(),
                a.get("name", a.get("title", "")).lower(),
            ),
        )
        self._build_artist_groups()
        self._preselect_cached_tracks()
        self._preload_saved_albums()
        self._loading_label.setVisible(False)
        self._table.setVisible(True)
        self._build_table_from_scratch()

    def _build_artist_groups(self) -> None:
        self._artist_groups = []
        for idx, album in enumerate(self._albums):
            artist = album.get("artist", "Unknown Artist")
            if not self._artist_groups or self._artist_groups[-1]["name"] != artist:
                self._artist_groups.append({"name": artist, "album_indices": []})
            self._artist_groups[-1]["album_indices"].append(idx)

    def _on_load_error(self, error: str) -> None:
        self._loading_label.setText(f"Error loading albums: {error}")
        self._loading_label.setStyleSheet(f"color: {Colors.DANGER}; padding: 32px;")

    def _preselect_cached_tracks(self) -> None:
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
            self._selected_ids.update(cached)
        except (OSError, PermissionError):
            logger.warning("Could not scan Navidrome cache dir", exc_info=True)

    def _preload_saved_albums(self) -> None:
        """Pre-load tracks for albums that had saved selections."""
        s = self._settings_service.get_global_settings()
        raw = getattr(s, "navidrome_selected_album_ids", "").strip()
        if not raw:
            return
        try:
            album_ids = json.loads(raw)
            if not isinstance(album_ids, list) or not album_ids:
                return
        except Exception:
            return

        url = s.navidrome_url.strip()
        username = s.navidrome_username.strip()
        password = s.navidrome_password
        if not url or not username or not password:
            return

        try:
            from iopenpod.sync.navidrome_library import NavidromeClient
            client = NavidromeClient(url, username, password)
        except Exception:
            logger.warning("Could not create Navidrome client for album pre-load", exc_info=True)
            return

        for album_id in album_ids:
            if album_id in self._track_cache:
                continue
            try:
                detail = client.get_album(album_id)
                tracks = detail.get("song", [])
                if isinstance(tracks, dict):
                    tracks = [tracks]
                self._track_cache[album_id] = tracks
            except Exception:
                logger.debug("Could not pre-load album %s", album_id, exc_info=True)
        self._build_table_from_scratch()

    # ---- Table building ----

    def _build_table_from_scratch(self) -> None:
        self._table.setRowCount(0)
        self._artist_rows.clear()
        self._album_rows.clear()

        filter_text = self._search_input.text().strip().lower() if self._search_input else ""

        row = 0
        for group in self._artist_groups:
            artist_name = group["name"]

            # Filter albums within this group
            matched = []
            for idx in group["album_indices"]:
                album = self._albums[idx]
                title = album.get("name", album.get("title", "")).lower()
                artist = album.get("artist", "").lower()
                if not filter_text or filter_text in title or filter_text in artist:
                    matched.append(idx)

            if not matched:
                continue

            # ---- Artist header row ----
            self._table.insertRow(row)

            artist_check = QCheckBox()
            artist_check.stateChanged.connect(
                lambda state, name=artist_name: self._on_artist_check_changed(name, state)
            )
            self._set_artist_check_state(artist_check, matched)
            self._table.setCellWidget(row, 0, artist_check)

            is_expanded = artist_name in self._expanded_artists
            indicator = "▼ " if is_expanded else "▶ "
            name_item = QTableWidgetItem(f"{indicator}{artist_name}")
            name_item.setData(Qt.ItemDataRole.FontRole, self._bold_font())
            name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._table.setItem(row, 1, name_item)

            blank = QTableWidgetItem("")
            blank.setFlags(blank.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._table.setItem(row, 2, blank)

            col3 = QTableWidgetItem(f"{len(matched)} albums")
            col3.setFlags(col3.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._table.setItem(row, 3, col3)

            self._artist_rows[row] = artist_name
            row += 1

            # ---- Album rows (if artist expanded) ----
            if is_expanded:
                for idx in matched:
                    row = self._insert_album_row(row, idx)

        self._update_selection_label()

    def _bold_font(self) -> QFont:
        f = QFont(FONT_FAMILY, Metrics.FONT_SM)
        f.setBold(True)
        return f

    def _set_artist_check_state(self, check: QCheckBox, album_indices: list[int]) -> None:
        any_tracks = False
        all_selected = True
        for idx in album_indices:
            album_id = self._albums[idx]["id"]
            tracks = self._track_cache.get(album_id, [])
            if tracks:
                any_tracks = True
                if not all(t.get("id") in self._selected_ids for t in tracks if t.get("id")):
                    all_selected = False
                    break
        check.setChecked(any_tracks and all_selected)

    def _insert_album_row(self, row: int, album_idx: int) -> int:
        """Insert one album row at *row*. Returns the next available row."""
        album = self._albums[album_idx]
        album_id = album["id"]
        album_title = album.get("name", album.get("title", "Unknown Album"))
        song_count = album.get("songCount", 0)

        self._table.insertRow(row)

        check = QCheckBox()
        check.setChecked(self._all_album_tracks_selected(album_id))
        check.stateChanged.connect(
            lambda state, aid=album_id: self._on_album_check_changed(aid, state)
        )
        self._table.setCellWidget(row, 0, check)

        is_expanded = album_id in self._expanded_albums
        if is_expanded and album_id in self._track_cache:
            exp_ind = "▼ "
        elif song_count > 0:
            exp_ind = "▶ "
        else:
            exp_ind = ""
        title_item = QTableWidgetItem(f"{_ALBUM_INDENT}{exp_ind}{album_title}")
        title_item.setFlags(title_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self._table.setItem(row, 1, title_item)

        year = album.get("year", "")
        year_item = QTableWidgetItem(str(year) if year else "")
        year_item.setFlags(year_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self._table.setItem(row, 2, year_item)

        if album_id in self._loading_albums:
            dur_text = "loading..."
        elif song_count:
            dur_text = f"{song_count} tracks"
        else:
            dur_text = ""
        dur_item = QTableWidgetItem(dur_text)
        dur_item.setFlags(dur_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self._table.setItem(row, 3, dur_item)

        self._album_rows[row] = album_idx
        row += 1

        # Track rows (if album expanded and cached)
        if is_expanded and album_id in self._track_cache:
            row = self._insert_track_rows(album_id, row)

        return row

    def _insert_track_rows(self, album_id: str, start_row: int) -> int:
        """Insert track rows for album_id. Returns next available row."""
        tracks = self._track_cache.get(album_id, [])
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
            label = f"{_TRACK_INDENT}{track_num}. {title}" if track_num else f"{_TRACK_INDENT}{title}"
            title_item = QTableWidgetItem(label)
            title_item.setFlags(title_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._table.setItem(r, 1, title_item)

            track_artist = track.get("artist", "")
            artist_item = QTableWidgetItem(track_artist)
            artist_item.setFlags(artist_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._table.setItem(r, 2, artist_item)

            duration_sec = track.get("duration", 0)
            duration_str = (
                f"{int(duration_sec // 60)}:{int(duration_sec % 60):02d}"
                if duration_sec
                else ""
            )
            dur_item = QTableWidgetItem(duration_str)
            dur_item.setFlags(dur_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._table.setItem(r, 3, dur_item)

        return start_row + len(tracks)

    # ---- Selection state helpers ----

    def _all_album_tracks_selected(self, album_id: str) -> bool:
        tracks = self._track_cache.get(album_id, [])
        if not tracks:
            return False
        return all(t.get("id") in self._selected_ids for t in tracks if t.get("id"))

    def _update_selection_label(self) -> None:
        count = len(self._selected_ids)
        self._selection_label.setText(f"{count} track{'s' if count != 1 else ''} selected")

    # ---- Click handling ----

    def _on_cell_clicked(self, row: int, col: int) -> None:
        if col != 1:
            return

        # Artist header row?
        artist_name = self._artist_rows.get(row)
        if artist_name is not None:
            self._toggle_artist(artist_name)
            return

        # Album header row?
        album_idx = self._album_rows.get(row)
        if album_idx is not None:
            self._toggle_album(album_idx)

    def _toggle_artist(self, artist_name: str) -> None:
        if artist_name in self._expanded_artists:
            self._expanded_artists.discard(artist_name)
            group = next(g for g in self._artist_groups if g["name"] == artist_name)
            for idx in group["album_indices"]:
                self._expanded_albums.discard(self._albums[idx]["id"])
        else:
            self._expanded_artists.add(artist_name)
        self._build_table_from_scratch()

    def _toggle_album(self, album_idx: int) -> None:
        album = self._albums[album_idx]
        album_id = album["id"]
        if album.get("songCount", 0) < 1:
            return

        if album_id in self._expanded_albums:
            self._expanded_albums.discard(album_id)
            self._build_table_from_scratch()
        else:
            self._expanded_albums.add(album_id)
            if album_id in self._track_cache:
                self._build_table_from_scratch()
            else:
                self._loading_albums.add(album_id)
                self._build_table_from_scratch()
                self._fetch_album_tracks(album_id)

    # ---- Track fetching ----

    def _fetch_album_tracks(self, album_id: str) -> None:
        s = self._settings_service.get_global_settings()
        loader = _NavidromeTrackLoader(
            s.navidrome_url.strip(),
            s.navidrome_username.strip(),
            s.navidrome_password,
            album_id,
            parent=self,
        )
        thread = QThread(self)
        loader.moveToThread(thread)
        thread.started.connect(loader.run)
        loader.finished.connect(self._on_tracks_loaded)
        loader.error.connect(self._on_tracks_error)
        loader.finished.connect(thread.quit)
        loader.error.connect(thread.quit)
        thread.finished.connect(thread.deleteLater)
        thread.start()

    def _on_tracks_loaded(self, album_id: str, songs: list[dict]) -> None:
        self._track_cache[album_id] = songs
        self._loading_albums.discard(album_id)
        if album_id in self._expanded_albums:
            self._build_table_from_scratch()

    def _on_tracks_error(self, album_id: str, error: str) -> None:
        self._loading_albums.discard(album_id)
        self._expanded_albums.discard(album_id)
        self._build_table_from_scratch()
        logger.error("Failed to load tracks for album %s: %s", album_id, error)

    # ---- Checkbox changes ----

    def _on_artist_check_changed(self, artist_name: str, state: int) -> None:
        checked = state == Qt.CheckState.Checked.value
        group = next((g for g in self._artist_groups if g["name"] == artist_name), None)
        if group is None:
            return
        for idx in group["album_indices"]:
            album_id = self._albums[idx]["id"]
            for track in self._track_cache.get(album_id, []):
                sid = track.get("id")
                if sid:
                    if checked:
                        self._selected_ids.add(sid)
                    else:
                        self._selected_ids.discard(sid)
        self._update_selection_label()

    def _on_album_check_changed(self, album_id: str, state: int) -> None:
        checked = state == Qt.CheckState.Checked.value
        for track in self._track_cache.get(album_id, []):
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

    # ---- Toolbar actions ----

    def _on_search_changed(self) -> None:
        self._build_table_from_scratch()

    def _expand_all(self) -> None:
        for group in self._artist_groups:
            self._expanded_artists.add(group["name"])
        self._build_table_from_scratch()

    def _collapse_all(self) -> None:
        self._expanded_artists.clear()
        self._expanded_albums.clear()
        self._build_table_from_scratch()

    def _select_all(self) -> None:
        for album in self._albums:
            album_id = album["id"]
            for track in self._track_cache.get(album_id, []):
                sid = track.get("id")
                if sid:
                    self._selected_ids.add(sid)
        self._build_table_from_scratch()

    def _deselect_all(self) -> None:
        self._selected_ids.clear()
        self._build_table_from_scratch()

    # ---- Persistence ----

    def _save_and_accept(self) -> None:
        try:
            s = self._settings_service.get_global_settings()
            s.navidrome_selected_ids = json.dumps(list(self._selected_ids))

            # Save which albums have selected tracks so we can pre-load them
            # on the next dialog open and show correct checkbox states
            album_ids: set[str] = set()
            for album_id, tracks in self._track_cache.items():
                if any(t.get("id") in self._selected_ids for t in tracks if t.get("id")):
                    album_ids.add(album_id)
            s.navidrome_selected_album_ids = json.dumps(sorted(album_ids))

            self._settings_service.save_global_settings(s)
            self.accept()
        except Exception:
            logger.exception("Failed to save Navidrome selection")
            self.accept()
