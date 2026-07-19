# Selective Navidrome Sync Plan

## Goal
Implement selective browsing and syncing for the Navidrome integration in iOpenPod to eliminate the requirement of downloading the entire library to the local cache.

## Constraints & Preferences
- Preserve existing "download-all" functionality for backward compatibility.
- Use the Subsonic API for browsing (no cache needed) and download only selected tracks.
- Store user selections as JSON-encoded lists in `navidrome_selected_ids`.
- Maintain current cache structure (`{song_id}.{suffix}`).

## Design Overview

### 1. Settings Schema Update
Add a new setting to `AppSettings` in `settings_schema.py`:
```python
    navidrome_selected_ids: str = ""  # JSON string of selected song IDs
```

### 2. Settings Page Update
In `settingsPage.py`, add a "Browse Library" button in the Navidrome section that opens a dialog.

### 3. Navidrome Browse Dialog
Create `navidrome_browse_dialog.py` that:
- Connects to Navidrome via the Subsonic API.
- Allows browsing artists, albums, and songs.
- Lets users select songs (checkboxes) and confirm selection.
- Returns a list of selected song IDs.

### 4. NavidromeLibrary Changes
Add a new method `sync_selected(self, song_ids: list[str], progress_callback=None, is_cancelled=None)` that:
- Takes a list of song IDs.
- For each ID, downloads the song to the cache if not present (using existing `_download_file` logic).
- Skips already cached files (same as `sync`).
- Reports progress similarly.

### 5. Sync Session Integration
Modify `sync_session.py` to:
- Add a new `SyncPlanningMode` value? Actually, we already have `"full"` and `"selective"`.
- In `_build_diff_request`, when `intent.mode == "selective"` and we have Navidrome cache in folder list, we should:
  - Use the selected IDs from settings to limit the sync to only those tracks.
  - However, note that the current selective mode is for PC folders. We need to extend it to handle Navidrome selective sync.

Alternatively, we can handle Navidrome selective sync entirely within the `NavidromeLibrary` by having a separate mode that bypasses the `SyncDiffWorker` for the Navidrome part.

Given the current architecture, the `NavidromeLibrary.sync` is called by the `SyncDiffWorker` when the Navidrome cache is in the PC folders list. We want to avoid downloading the entire library.

Option: Instead of changing the sync session, we can change the `NavidromeLibrary` to have two modes:
- `sync()`: downloads all (current behavior)
- `sync_selected(ids)`: downloads only the given IDs.

Then, in the sync session, when we detect that we are in selective mode for Navidrome, we call `sync_selected` instead of `sync`.

But note: the sync session also does a PC library scan and then a diff. We want to avoid downloading the entire Navidrome library, but we still want to compare the selected tracks with the iPod.

So we can:
1. In the sync session, when planning, if we are in selective mode and the folder is a Navidrome cache, we do:
   - Call `NavidromeLibrary.sync_selected(selected_ids)` to populate the cache with only the selected tracks.
   - Then proceed with the normal `PCLibrary` scan and diff.

2. The `selected_ids` would come from the settings (`navidrome_selected_ids`).

However, the user might change the selection between runs. We should use the current selection from settings.

Alternatively, we can pass the selected IDs via the `SyncPlanningIntent`? But the intent is for PC folders.

Given the complexity, let's stick to using the settings for the selected IDs and have the `NavidromeLibrary` check for a selective sync request via a new method.

We'll change the `NavidromeLibrary` to have:
- `sync(selected_ids: Optional[List[str]] = None, ...)`: if `selected_ids` is provided, only sync those; else sync all.

Then, in the sync session, when building the diff request, we can pass the selected IDs from settings to the `NavidromeLibrary` instance.

But note: the `NavidromeLibrary` is created in the `_build_diff_request` method? Actually, no. The `NavidromeLibrary` is used by the `SyncDiffWorker` in the `run` method.

Let's look at `jobs.py` (SyncDiffWorker) to see how it uses the NavidromeLibrary.

We might need to adjust the `SyncDiffRequest` to include the selected IDs for Navidrome.

Alternatively, we can set the selected IDs in the settings and have the `NavidromeLibrary` read them from there? But the library instance is created in the worker thread, and we don't have access to the settings service there.

Better to pass the selected IDs via the `SyncDiffRequest`.

Let's examine the existing code in `sync_session.py` and `jobs.py`.

But due to time, let's outline the steps we will take and then implement.

## Implementation Steps

1. **Update Settings Schema**
   - Add `navidrome_selected_ids` to `AppSettings` in `settings_schema.py`.

2. **Update Settings Page**
   - In `settingsPage.py`, add a "Browse Library" button in the Navidrome group box.
   - Connect it to a slot that opens `NavidromeBrowseDialog`.
   - On acceptance, update the setting with the selected IDs (as JSON string).

3. **Create Navidrome Browse Dialog**
   - New file: `src/iopenpod/gui/widgets/navidrome_browse_dialog.py`
   - Use `NavidromeClient` to fetch artists, albums, songs.
   - Present a tree view or list view with checkboxes for songs.
   - Allow user to select songs and accept.

4. **Modify NavidromeLibrary**
   - In `navidrome_library.py`, change the `sync` method to accept an optional `song_ids` parameter.
   - If `song_ids` is provided, only iterate over those songs (after fetching their metadata? Actually we need the song metadata to get the suffix and size for caching check).
   - We can fetch the song metadata for each ID via `get_song` (which is already used in `get_all_songs` via albums). But note: `get_all_songs` fetches by iterating albums. We can change to use `getSongList` in batches? Or we can call `get_song` for each ID? That might be many requests.

   Alternatively, we can fetch all songs once and then filter by ID? But that defeats the purpose of reducing data transfer.

   We need a way to get minimal metadata (id, suffix, size) for a list of song IDs without getting all songs.

   The Subsonic API has `getSong` for a single song, and `getSongList` for multiple (paginated). We can use `getSongList` to get multiple songs at once.

   Let's change the `NavidromeLibrary` to have a method `get_songs_by_ids(ids: list[str])` that uses `getSongList` in batches.

   Then, in `sync_selected`, we:
   - Fetch the metadata for the given IDs (in batches).
   - Then for each song, check cache and download if needed.

5. **Update Sync Session and Jobs**
   - In `sync_session.py`, when building the diff request for a Navidrome cache folder, we need to pass the selected IDs (from settings) to the worker.
   - We'll add a field to `SyncDiffRequest` for `navidrome_selected_ids` (list of strings).
   - In `SyncDiffWorker`, when creating the `NavidromeLibrary` instance, we pass the selected IDs to the `sync` method (if in selective mode?).

   But note: the sync session doesn't know if we are in selective mode for Navidrome only. We are using the existing `SyncPlanningMode` which is for PC folders.

   We can introduce a new mode for Navidrome? Or we can say: if the user has set `navidrome_selected_ids` (non-empty), then we treat it as selective for Navidrome.

   However, the user might want to do a full Navidrome sync even if they have a selection set (maybe they want to update the selection). So we should not rely on the setting being non-empty.

   Alternatively, we can add a new setting: `navidrome_sync_mode` with values "full" or "selected".

   Let's keep it simple: we will use the setting `navidrome_selected_ids` to indicate that we want to sync only those IDs. If the string is empty, we sync all.

   Then, in the sync session, we always pass the selected IDs (which might be empty meaning all) to the `NavidromeLibrary.sync` method.

   This way, the existing behavior is preserved when the setting is empty.

6. **Update Dev Log**
   - Create `DEVLOG.md` and update as we complete each step.

## Detailed Steps

### Step 1: Settings Schema
Edit `src/iopenpod/infrastructure/settings_schema.py`:
- Add field to `AppSettings`:
  ```python
      navidrome_selected_ids: str = ""  # JSON string of selected song IDs
  ```

### Step 2: Settings Page
Edit `src/iopenpod/gui/widgets/settingsPage.py`:
- In the `NavidromeGroupBox` (or wherever the Navidrome fields are), add a QPushButton "Browse Library".
- Connect its clicked signal to a slot that opens `NavidromeBrowseDialog`.
- On dialog acceptance, get the selected IDs (as a list), convert to JSON string, and update the setting via `settings_service`.

### Step 3: Create Navidrome Browse Dialog
Create `src/iopenpod/gui/widgets/navidrome_browse_dialog.py`:
- Use `QDialog` with a layout.
- Use a `QTreeWidget` or `QListWidget` to show artists -> albums -> songs.
- Use `NavidromeClient` to fetch data (artists, then albums for each artist, then songs for each album).
- Implement checkboxes for songs.
- On accept, collect checked song IDs and return them.

### Step 4: Modify NavidromeLibrary
Edit `src/iopenpod/sync/navidrome_library.py`:
- Change `sync` method to accept an optional `song_ids: list[str] | None = None`.
- If `song_ids` is None, behave as before (get all songs).
- If `song_ids` is provided, we need to get the metadata for those songs.
  - We can use `getSongList` in batches (e.g., 500 at a time) to get the song list and then filter by ID? Actually, `getSongList` returns a list of songs, but we can't filter by ID in the API call? We can get all songs and then filter, but that's what we are trying to avoid.
  - Alternatively, we can call `getSong` for each ID. This is one request per song, which might be slow but acceptable for a selected subset (hopefully not thousands).
  - Let's use `getSong` for each ID for simplicity, and if performance becomes an issue, we can switch to batching.

  We'll create a helper method `_get_songs_by_ids(self, ids: list[str]) -> list[dict]` that returns the song metadata for the given IDs.

- Then, in the sync loop, iterate over the fetched songs (either all or the selected ones).

### Step 5: Update Sync Session and Jobs
Edit `src/iopenpod/application/sync_session.py`:
- In `_build_diff_request`, after getting the `navidrome_cache_dir` and credentials, we will:
  - Get the selected IDs from settings (if the folder is a Navidrome cache).
  - We need to pass these IDs to the worker. We'll add a field to `SyncDiffRequest`.

Edit `src/iopenpod/application/jobs.py`:
- Add a field `navidrome_selected_ids: list[str] | None = None` to `SyncDiffRequest`.
- In `SyncDiffWorker.run`, when creating the `NavidromeLibrary` instance, we will call its `sync` method with the selected IDs (if provided).

But note: the `SyncDiffWorker` already has access to the settings via the request? Actually, the request has the settings passed in? Let's look.

In `sync_session.py`, `_build_diff_request` creates a `SyncDiffRequest` and passes settings to it? Actually, the `SyncDiffRequest` is built with many fields from settings, but not the settings object itself.

We are already passing `navidrome_url`, `navidrome_username`, `navidrome_password`, and `navidrome_cache_dir` from settings. We can add `navidrome_selected_ids` similarly.

So in `_build_diff_request`, we can do:
```python
        navidrome_selected_ids = []
        if settings.navidrome_selected_ids:
            try:
                navidrome_selected_ids = json.loads(settings.navidrome_selected_ids)
            except json.JSONDecodeError:
                navidrome_selected_ids = []
        # ... then add to the request
```

Then in `SyncDiffWorker.run`, we have access to `self.request.navidrome_selected_ids`.

We will then call:
```python
        library = NavidromeLibrary(
            url=self.request.navidrome_url,
            username=self.request.navidrome_username,
            password=self.request.navidrome_password,
            cache_dir=self.request.navidrome_cache_dir,
        )
        library.sync(
            song_ids=self.request.navidrome_selected_ids if self.request.navidrome_selected_ids else None,
            progress_callback=progress_callback,
            is_cancelled=self.isInterruptionRequested,
        )
```

But note: the `sync` method of `NavidromeLibrary` currently doesn't take `song_ids`. We will change it to take an optional `song_ids`.

### Step 6: Update Dev Log
Create `DEVLOG.md` and record each step.

Let's start by creating the plan and dev log files.