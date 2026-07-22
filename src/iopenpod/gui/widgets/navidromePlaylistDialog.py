"""NavidromePlaylistDialog — browse and select Navidrome playlists for sync."""

from __future__ import annotations

import json
import logging
from typing import Any

from PyQt6.QtCore import QObject, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
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
    table_css,
)

logger = logging.getLogger(__name__)


class _PlaylistListLoader(QObject):
    """Background worker to fetch the playlist list from Navidrome."""

    finished = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, url: str, username: str, password: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._url = url
        self._username = username
        self._password = password

    def run(self) -> None:
        try:
            from iopenpod.sync.navidrome_library import NavidromeClient

            client = NavidromeClient(self._url, self._username, self._password)
            playlists = client.get_playlists()
            self.finished.emit(playlists)
        except Exception as e:
            logger.exception("Failed to fetch playlists from Navidrome")
            self.error.emit(str(e))


class NavidromePlaylistDialog(QDialog):
    """A dialog for selecting which Navidrome playlists to sync to the iPod."""

    def __init__(self, settings_service: _SettingsService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings_service = settings_service
        self._playlists: list[dict[str, Any]] = []
        self._checkboxes: dict[str, QCheckBox] = {}  # playlist_id -> checkbox

        self._init_ui()

        # Load current selection from settings
        settings = self._settings_service.get_effective_settings()
        raw = getattr(settings, "navidrome_selected_playlist_ids", "")
        if raw:
            try:
                self._selected_ids = set(json.loads(raw))
            except (json.JSONDecodeError, TypeError):
                self._selected_ids = set()
        else:
            self._selected_ids = set()

        # Start loading
        self._load_playlists()

    def _init_ui(self) -> None:
        self.setWindowTitle("Select Navidrome Playlists")
        self.setMinimumSize(600, 400)
        self.setStyleSheet(f"background: {Colors.DIALOG_BG}; color: {Colors.TEXT_PRIMARY};")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        # Title
        title = QLabel("Select playlists to sync to your iPod")
        title.setFont(QFont(FONT_FAMILY, Metrics.FONT_LG))
        title.setStyleSheet(f"color: {Colors.TEXT_PRIMARY}; font-weight: 600;")
        layout.addWidget(title)

        subtitle = QLabel(
            "Checked playlists will be synced. Their songs will also be "
            "automatically downloaded if not already selected in Browse Library."
        )
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet(f"color: {Colors.TEXT_SECONDARY}; font-size: {Metrics.FONT_SM}px;")
        layout.addWidget(subtitle)

        # Loading label
        self._loading_label = QLabel("Loading playlists...")
        self._loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._loading_label.setStyleSheet(f"color: {Colors.TEXT_SECONDARY}; padding: 40px;")
        layout.addWidget(self._loading_label)

        # Table
        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["Sync", "Playlist Name", "Tracks", "Owner"])
        self._table.setStyleSheet(table_css())
        self._table.horizontalHeader().setStretchLastSection(False)
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setShowGrid(False)
        self._table.setVisible(False)
        layout.addWidget(self._table)

        # Button row
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self._select_all_btn = QPushButton("Select All")
        self._select_all_btn.setFont(QFont(FONT_FAMILY, Metrics.FONT_SM))
        self._select_all_btn.setStyleSheet(btn_css("secondary", "sm"))
        self._select_all_btn.clicked.connect(self._select_all)
        self._select_all_btn.setVisible(False)
        btn_row.addWidget(self._select_all_btn)

        self._clear_btn = QPushButton("Clear")
        self._clear_btn.setFont(QFont(FONT_FAMILY, Metrics.FONT_SM))
        self._clear_btn.setStyleSheet(btn_css("secondary", "sm"))
        self._clear_btn.clicked.connect(self._clear_all)
        self._clear_btn.setVisible(False)
        btn_row.addWidget(self._clear_btn)

        btn_row.addStretch()

        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.setFont(QFont(FONT_FAMILY, Metrics.FONT_SM))
        self._cancel_btn.setStyleSheet(btn_css("secondary", "sm"))
        self._cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(self._cancel_btn)

        self._save_btn = QPushButton("Save")
        self._save_btn.setFont(QFont(FONT_FAMILY, Metrics.FONT_SM))
        self._save_btn.setStyleSheet(accent_btn_css("sm"))
        self._save_btn.clicked.connect(self._save)
        self._save_btn.setVisible(False)
        btn_row.addWidget(self._save_btn)

        layout.addLayout(btn_row)

    def _load_playlists(self) -> None:
        """Fetch the playlist list from Navidrome in a background thread."""
        settings = self._settings_service.get_effective_settings()
        url = getattr(settings, "navidrome_url", "").strip()
        username = getattr(settings, "navidrome_username", "").strip()
        password = getattr(settings, "navidrome_password", "")

        if not url or not username or not password:
            self._loading_label.setText(
                "Navidrome not configured. Go to Settings > Navidrome to connect first."
            )
            return

        self._loader = _PlaylistListLoader(url, username, password)
        self._loader_thread = QThread()
        self._loader.moveToThread(self._loader_thread)
        self._loader_thread.started.connect(self._loader.run)
        self._loader.finished.connect(self._on_playlists_loaded)
        self._loader.error.connect(self._on_load_error)
        self._loader.finished.connect(self._loader_thread.quit)
        self._loader.error.connect(self._loader_thread.quit)
        self._loader_thread.finished.connect(self._loader.deleteLater)
        self._loader_thread.start()

    def _on_playlists_loaded(self, playlists: list[dict[str, Any]]) -> None:
        """Populate the table with fetched playlists."""
        self._playlists = playlists
        self._loading_label.setVisible(False)
        self._table.setVisible(True)
        self._select_all_btn.setVisible(True)
        self._clear_btn.setVisible(True)
        self._save_btn.setVisible(True)

        self._table.setRowCount(len(playlists))

        for row, pl in enumerate(playlists):
            pl_id = pl.get("id", "")
            pl_name = pl.get("name", "Unknown")
            song_count = pl.get("songCount", 0)
            owner = pl.get("owner", pl.get("username", ""))
            is_public = pl.get("public", False)

            # Checkbox column
            cb = QCheckBox()
            cb.setChecked(pl_id in self._selected_ids)
            cb.toggled.connect(lambda checked, pid=pl_id: self._on_check_toggled(pid, checked))
            self._checkboxes[pl_id] = cb

            cb_widget = QWidget()
            cb_layout = QHBoxLayout(cb_widget)
            cb_layout.setContentsMargins(8, 0, 0, 0)
            cb_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cb_layout.addWidget(cb)
            self._table.setCellWidget(row, 0, cb_widget)

            # Name column
            name_item = QTableWidgetItem(pl_name)
            name_item.setFont(QFont(FONT_FAMILY, Metrics.FONT_SM))
            name_item.setForeground(Qt.GlobalColor.white)
            if is_public:
                name_item.setToolTip("Public playlist")
            self._table.setItem(row, 1, name_item)

            # Track count
            count_item = QTableWidgetItem(str(song_count))
            count_item.setFont(QFont(FONT_FAMILY, Metrics.FONT_SM))
            count_item.setForeground(Qt.GlobalColor.lightGray)
            count_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row, 2, count_item)

            # Owner
            owner_item = QTableWidgetItem(owner if owner else "—")
            owner_item.setFont(QFont(FONT_FAMILY, Metrics.FONT_SM))
            owner_item.setForeground(Qt.GlobalColor.lightGray)
            owner_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row, 3, owner_item)

        self._table.resizeRowsToContents()

    def _on_load_error(self, error_msg: str) -> None:
        self._loading_label.setText(f"Failed to load playlists: {error_msg}")
        self._loading_label.setStyleSheet(f"color: {Colors.DANGER}; padding: 40px;")

    def _on_check_toggled(self, playlist_id: str, checked: bool) -> None:
        if checked:
            self._selected_ids.add(playlist_id)
        else:
            self._selected_ids.discard(playlist_id)

    def _select_all(self) -> None:
        for _pl_id, cb in self._checkboxes.items():
            cb.setChecked(True)

    def _clear_all(self) -> None:
        for _pl_id, cb in self._checkboxes.items():
            cb.setChecked(False)

    def _save(self) -> None:
        """Persist the selected playlist IDs to settings."""
        settings = self._settings_service.get_effective_settings()
        settings.navidrome_selected_playlist_ids = json.dumps(sorted(self._selected_ids))
        self._settings_service.save_global_settings(settings)
        count = len(self._selected_ids)
        logger.info("Saved %d selected Navidrome playlist(s)", count)
        self.accept()
