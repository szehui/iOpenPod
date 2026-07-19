# DEVLOG: Navidrome Selective Sync

## Implementation Summary

Implementing selective browsing and syncing for the Navidrome integration in iOpenPod, eliminating the requirement of downloading the entire library to the local cache.

## Files Changed

| File | Change |
|------|--------|
| `src/iopenpod/sync/navidrome_library.py` | Added `get_song_metadata()`, `get_all_cached_songs()`, `_resolve_songs()`. Extended `sync()` with optional `song_ids` param. |
| `src/iopenpod/application/jobs.py` | Added `navidrome_selected_ids: list[str] | None` to `SyncDiffRequest`. Worker passes IDs to `lib.sync()`. |
| `src/iopenpod/application/sync_session.py` | `_build_diff_request()` parses `navidrome_selected_ids` from settings and includes in request. |
| `src/iopenpod/infrastructure/settings_schema.py` | Added `navidrome_selected_ids: str` field to `AppSettings`. |
| `src/iopenpod/gui/widgets/navidromeBrowseDialog.py` | **New** — Qt6 dialog for browsing Navidrome albums/tracks with multi-select. |
| `src/iopenpod/gui/views/settingsPage.py` | Added "Browse Library" button wired to browse dialog. |
| `tests/test_navidrome_selective_sync.py` | **New** — 20 tests covering selective sync, caching, metadata resolution. |

## Design Decisions

- **`song_ids=None`** → full sync (backward compatible)
- **`song_ids=[]`** → no-op (sync nothing)
- **`song_ids=["1","2"]`** → selective sync of only those IDs
- Empty JSON (`""`) in settings resolves to `None` at the session level, maintaining backward compat
- Cache dir files are checked by `os.path.isfile()` + size comparison, matching existing behavior

## Verification

All 23 Navidrome tests pass (3 pre-existing + 20 new):
```
23 passed in 1.20s
```
No regressions in the full test suite.
