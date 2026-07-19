"""
NavidromeBrowseDialog — browse albums/tracks on the Navidrome server
and select which tracks to sync to the iPod.
"""
from __future__ import annotations

import json
import logging

from PyQt6.QtCore import QObject, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPalette
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
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
    """Background worker to fetch albums from the Navidrome API."""

    finished = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(
        self,
        url: str,
        username: str,
        password: str,
        cache_dir: str,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self._url = url
        self._username = username
        self._password = password
        self._cache_dir = cache_dir

    def run(self) -> None:
        try:
            from iopenpod.sync.navidrome_library import NavidromeClient
            client = NavidromeClient(self._url, self._username, self._password)
            albums = client.get_all_albums()
            # Enrich albums with their track counts
            enriched = []
            for album in albums:
                try:
                    detail = client.get_album(album["id"])
                    songs = detail.get("song", [])
                    album["_track_count"] = len(songs)
                    album["_tracks"] = songs
                except Exception:
                    album["_track_count"] = 0
                    album["_tracks"] = []
                enriched.append(album)
            self.finished.emit(enriched)
        except Exception as exc:
            logger.exception("Failed to load Navidrome albums")
            self.error.emit(str(exc))


class NavidromeBrowseDialog(QDialog):
    """Modal dialog to browse Navidrome albums and select tracks for sync."""

    def __init__(
        self,
        settings_service: _SettingsService,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._settings_service = settings_service
        self._albums: list[dict] = []
        self._selected_ids: set[str] = set()
        self._album_rows: dict[int, str] = {}  # table row -> album_id
        self._expanded_albums: set[str] = set()
        self._track_rows: dict[int, dict] = {}  # table row -> track info
        self._album_start_rows: dict[str, int] = {}  # album_id -> first row in table
        self._table: QTableWidget | None = None
        self._search_input: QLineEdit | None = None
        self._selection_label: QLabel | None = None
        self._loading_label: QLabel | None = None
        self._loader: _NavidromeAlbumLoader | None = None
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

        # Album/ track table
        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(["", "Title", "Duration"])
        self._table.setStyleSheet(table_css())
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.setShowGrid(False)
        header_view = self._table.horizontalHeader()
        header_view.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(0, 40)
        header_view.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header_view.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
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
        """Kick off background album loading."""
        s = self._settings_service.get_global_settings()
        url = s.navidrome_url.strip()
        username = s.navidrome_username.strip()
        password = s.navidrome_password
        from iopenpod.infrastructure.settings_paths import default_navidrome_cache_dir

        cache_dir = s.navidrome_cache_dir.strip() or default_navidrome_cache_dir()

        self._loading_label.setVisible(True)
        self._table.setVisible(False)

        self._loader_thread = QThread(self)
        self._loader = _NavidromeAlbumLoader(url, username, password, cache_dir)
        self._loader.moveToThread(self._loader_thread)
        self._loader_thread.started.connect(self._loader.run)
        self._loader.finished.connect(self._on_albums_loaded)
        self._loader.error.connect(self._on_load_error)
        self._loader.finished.connect(self._loader_thread.quit)
        self._loader.error.connect(self._loader_thread.quit)
        self._loader_thread.finished.connect(self._loader_thread.deleteLater)
        self._loader_thread.start()

    def _on_albums_loaded(self, albums: list[dict]) -> None:
        self._albums = albums
        self._loading_label.setVisible(False)
        self._table.setVisible(True)
        self._rebuild_table()

    def _on_load_error(self, error: str) -> None:
        self._loading_label.setText(f"Error loading albums: {error}")
        self._loading_label.setStyleSheet(f"color: {Colors.DANGER}; padding: 32px;")

    def _rebuild_table(self, filter_text: str = "") -> None:
        """Populate the table with albums and optionally their tracks."""
        self._table.setRowCount(0)
        self._album_rows.clear()
        self._track_rows.clear()
        self._album_start_rows.clear()

        filter_lower = filter_text.strip().lower() if filter_text else ""

        row = 0
        for album in self._albums:
            album_id = album["id"]
            album_title = album.get("title", "Unknown Album")
            album_artist = album.get("artist", "Unknown Artist")
            track_count = album.get("_track_count", 0)

            # Apply search filter
            if filter_lower and filter_lower not in album_title.lower() and filter_lower not in album_artist.lower():
                continue

            self._album_start_rows[album_id] = row

            # Album header row
            self._table.insertRow(row)

            # Checkbox column — check to select ALL tracks in the album
            check = QCheckBox()
            check.setChecked(self._all_album_tracks_selected(album))
            check.stateChanged.connect(lambda state, aid=album_id: self._on_album_check_changed(aid, state))
            self._table.setCellWidget(row, 0, check)

            title_widget = self._make_album_title_widget(album_title, album_artist, track_count)
            self._table.setCellWidget(row, 1, title_widget)

            duration_item = QTableWidgetItem(f"{track_count} tracks")
            duration_item.setFlags(duration_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._table.setItem(row, 2, duration_item)

            self._album_rows[row] = album_id
            row += 1

            # Expand tracks if this album was expanded
            expanded = album_id in self._expanded_albums
            if expanded:
                self._insert_album_tracks(album, row)
                tracks = album.get("_tracks", [])
                row += len(tracks)

        self._update_selection_label()

    def _make_album_title_widget(self, title: str, artist: str, track_count: int) -> QWidget:
        w = QWidget()
        layout = QHBoxLayout(w)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        expand_btn = QPushButton("▶" if track_count > 0 else "")
        expand_btn.setFixedSize(24, 24)
        expand_btn.setStyleSheet(
            "QPushButton { background: transparent; border: none; "
            f"color: {Colors.TEXT_SECONDARY}; font-size: 10px; }} "
            "QPushButton:hover { color: #aaa; }"
        )
        expand_btn.clicked.connect(self._on_expand_clicked)
        layout.addWidget(expand_btn)

        text = QLabel(f"<b>{title}</b> — {artist}")
        text.setStyleSheet(f"color: {Colors.TEXT_PRIMARY}; background: transparent;")
        layout.addWidget(text, 1)

        return w

    def _insert_album_tracks(self, album: dict, start_row: int) -> None:
        tracks = album.get("_tracks", [])
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

            duration_sec = track.get("duration", 0)
            duration_str = f"{int(duration_sec // 60)}:{int(duration_sec % 60):02d}" if duration_sec else ""
            dur_item = QTableWidgetItem(duration_str)
            dur_item.setFlags(dur_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._table.setItem(r, 2, dur_item)

            self._track_rows[r] = track

    def _all_album_tracks_selected(self, album: dict) -> bool:
        tracks = album.get("_tracks", [])
        if not tracks:
            return False
        return all(t["id"] in self._selected_ids for t in tracks if t.get("id"))

    def _on_album_check_changed(self, album_id: str, state: int) -> None:
        checked = state == Qt.CheckState.Checked.value
        album = next((a for a in self._albums if a["id"] == album_id), None)
        if not album:
            return
        for track in album.get("_tracks", []):
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
        """Handle cell clicks — expand/collapse on album rows."""
        if col != 1:
            return
        album_id = self._album_rows.get(row)
        if not album_id:
            return
        album = next((a for a in self._albums if a["id"] == album_id), None)
        if not album or not album.get("_tracks"):
            return

        # Toggle expand
        if album_id in self._expanded_albums:
            self._expanded_albums.discard(album_id)
        else:
            self._expanded_albums.add(album_id)
        self._rebuild_table(self._search_input.text() if self._search_input else "")

    def _on_expand_clicked(self) -> None:
        """Handled via _on_cell_clicked — button clicks cascade to row click."""
        pass

    def _apply_search_filter(self) -> None:
        text = self._search_input.text() if self._search_input else ""
        self._rebuild_table(text)

    def _select_all(self) -> None:
        """Select all tracks across all albums."""
        for album in self._albums:
            for track in album.get("_tracks", []):
                sid = track.get("id")
                if sid:
                    self._selected_ids.add(sid)
        self._rebuild_table(self._search_input.text() if self._search_input else "")

    def _deselect_all(self) -> None:
        """Deselect all tracks."""
        self._selected_ids.clear()
        self._rebuild_table(self._search_input.text() if self._search_input else "")

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
        except Exception as exc:
            logger.exception("Failed to save Navidrome selection")
            # Still accept — the user picked; we logged the error
            self.accept()
