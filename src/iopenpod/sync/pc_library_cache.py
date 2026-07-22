"""Cache for PC library scan results to avoid re-reading metadata on every sync."""

import json
import time
from pathlib import Path


class PCLibraryCache:
    """Manages caching of PC library scan results."""

    def __init__(self, cache_dir: str):
        self.cache_dir = Path(cache_dir)
        self.cache_file = self.cache_dir / "pc-library-index.json"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        # In-memory cache structure: {root_path: {rel_path: (mtime, size, pctrack_dict)}}
        self._cache: dict[str, dict[str, tuple[float, int, dict]]] = {}
        self._loaded = False

    def load(self) -> bool:
        """Load cache from disk. Returns True if loaded successfully."""
        if self._loaded:
            return True

        if not self.cache_file.exists():
            return False

        try:
            with open(self.cache_file, encoding="utf-8") as f:
                data = json.load(f)

            # Validate version
            if data.get("version") != 2:
                return False

            self._cache = {}
            for root_str, root_data in data.get("roots", {}).items():
                root_path = Path(root_str)
                if not root_path.exists():
                    continue  # Skip roots that no longer exist
                self._cache[str(root_path)] = {}
                for rel_str, file_data in root_data.get("files", {}).items():
                    mtime = file_data.get("mtime")
                    size = file_data.get("size")
                    pctrack_dict = file_data.get("pctrack")
                    if mtime is None or size is None or pctrack_dict is None:
                        continue
                    self._cache[str(root_path)][rel_str] = (mtime, size, pctrack_dict)
            self._loaded = True
            return True
        except (json.JSONDecodeError, OSError, KeyError):
            return False

    def save(self) -> None:
        """Save current cache to disk."""
        data = {
            "version": 2,
            "generated_at": time.time(),
            "roots": {},
        }
        for root_str, files in self._cache.items():
            root_data = {"files": {}}
            for rel_str, (mtime, size, pctrack_dict) in files.items():
                root_data["files"][rel_str] = {
                    "mtime": mtime,
                    "size": size,
                    "pctrack": pctrack_dict,
                }
            data["roots"][root_str] = root_data

        try:
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, sort_keys=True)
        except OSError:
            pass  # Best effort; if we can't write, we'll just use memory cache for this run.

    def get(self, root_path: Path, rel_path: str) -> tuple[float, int, dict] | None:
        """Get cached (mtime, size, pctrack_dict) for a file, or None if not cached."""
        if not self._loaded:
            self.load()
        root_str = str(root_path)
        if root_str not in self._cache:
            return None
        return self._cache[root_str].get(rel_path)

    def put(self, root_path: Path, rel_path: str, mtime: float, size: int, pctrack_dict: dict) -> None:
        """Store or update a file's cache entry."""
        if not self._loaded:
            self.load()
        root_str = str(root_path)
        if root_str not in self._cache:
            self._cache[root_str] = {}
        self._cache[root_str][rel_path] = (mtime, size, pctrack_dict)

    def delete(self, root_path: Path, rel_path: str) -> None:
        """Remove a file from the cache."""
        if not self._loaded:
            self.load()
        root_str = str(root_path)
        if root_str in self._cache:
            self._cache[root_str].pop(rel_path, None)

    def clear(self) -> None:
        """Clear the entire cache."""
        self._cache.clear()
        if self.cache_file.exists():
            try:
                self.cache_file.unlink()
            except OSError:
                pass

    def get_all_cached_files(self, root_path: Path) -> dict:
        """Return a dict of relative_path -> (mtime, size, pctrack_dict) for a given root."""
        if not self._loaded:
            self.load()
        return self._cache.get(str(root_path), {}).copy()

    def prune_to(self, seen_files_by_root: dict[str, set[str]]) -> None:
        """Remove cache entries for files that no longer exist on disk.

        Args:
            seen_files_by_root: Mapping of root_path -> set of relative paths still present.
        """
        for root_str in list(self._cache.keys()):
            still_present = seen_files_by_root.get(root_str, set())
            for rel_path in list(self._cache[root_str].keys()):
                if rel_path not in still_present:
                    del self._cache[root_str][rel_path]
