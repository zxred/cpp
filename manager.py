"""
Registry manager for `.def` entity definitions.

The `DefManager` recursively discovers all `.def` files under a given root
directory, parses them, and stores the resulting `EntityDef` objects in an
in-memory dictionary keyed by entity name.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, Iterable, List, Set

from .entity_def import EntityDef
from .parser import parse_def_file

logger = logging.getLogger(__name__)

# Default directory names that should never be scanned for `.def` files.
DEFAULT_IGNORED_DIRS: Set[str] = {
    "src",
    "__pycache__",
    "backup",
    "logs",
    ".git",
    "scripts",
}


class DefManager:
    """
    In-memory registry of entity definitions loaded from `.def` files.

    Order of insertion is preserved via `dict`, so iteration order matches the
    order in which files were discovered and parsed.

    The manager starts scanning from the provided application root directory
    and recursively walks all subdirectories while skipping known system/service
    folders (e.g. `src`, `__pycache__`, `.git`, etc.).
    """

    def __init__(
        self,
        root_path: Path | str,
        ignore_dirs: Iterable[str] | None = None,
    ) -> None:
        """
        Initialize the manager.

        Args:
            root_path: Application root directory to scan recursively.
            ignore_dirs: Optional iterable of directory names to skip during
                the recursive scan. If omitted, a sensible default set is used.
        """
        self.root_path = Path(root_path).resolve()
        self.ignore_dirs: Set[str] = set(ignore_dirs) if ignore_dirs else DEFAULT_IGNORED_DIRS
        self._entities: Dict[str, EntityDef] = {}

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------
    def _is_ignored(self, path: Path) -> bool:
        """
        Return True if *path* is located inside any ignored directory.

        The check is performed against path components relative to the root,
        so a file at ``root/src/foo.def`` is skipped even if ``src`` is not
        the immediate parent.
        """
        try:
            relative = path.relative_to(self.root_path)
        except ValueError:
            # Path is outside the root — do not scan it.
            return True

        return any(part in self.ignore_dirs for part in relative.parts)

    def load(self) -> None:
        """
        Recursively scan `root_path` for `.def` files and parse them.

        The registry is reset before loading. Corrupt or unsupported files are
        skipped with a warning. Files located inside ignored directories are
        not processed at all.
        """
        self._entities.clear()

        if not self.root_path.exists():
            logger.warning("Definition root path does not exist: %s", self.root_path)
            return

        if not self.root_path.is_dir():
            logger.warning("Definition root path is not a directory: %s", self.root_path)
            return

        # Sorting gives deterministic load order across runs.
        for def_file in sorted(self.root_path.rglob("*.def")):
            if self._is_ignored(def_file):
                logger.debug("Ignoring .def file in skipped directory: %s", def_file)
                continue

            # Print discovery info to stdout so it appears in the server console
            # even if the Python logging module is not configured.
            relative_path = def_file.relative_to(self.root_path)
            print(f"[DEF] {relative_path} - is found")
            logger.info("[DEF] %s - is found", relative_path)

            entity_name = def_file.stem
            if entity_name in self._entities:
                logger.warning(
                    "Duplicate entity name '%s' encountered at %s; overwriting previous definition.",
                    entity_name,
                    def_file,
                )

            entity_def = parse_def_file(def_file)
            if entity_def is not None:
                # Ensure the entity carries the resolved name from the file stem
                # (paranoid consistency check in case the parser changes).
                entity_def.name = entity_name
                self._entities[entity_name] = entity_def

        summary = (
            f"[DEF] Loaded {len(self._entities)} entity definition(s) from "
            f"{self.root_path} (ignored dirs: {', '.join(sorted(self.ignore_dirs))})"
        )
        print(summary)
        logger.info(summary)

    # ------------------------------------------------------------------
    # Getters
    # ------------------------------------------------------------------
    def get(self, name: str) -> EntityDef | None:
        """Return the `EntityDef` with the given name, or `None`."""
        return self._entities.get(name)

    def __getitem__(self, name: str) -> EntityDef:
        """Allow dictionary-style access: `manager["Avatar"]`."""
        return self._entities[name]

    def __contains__(self, name: str) -> bool:
        """Allow membership tests: `"Avatar" in manager`."""
        return name in self._entities

    def get_all(self) -> List[EntityDef]:
        """Return all loaded entity definitions as a list."""
        return list(self._entities.values())

    def names(self) -> Iterable[str]:
        """Return an iterable of all loaded entity names."""
        return self._entities.keys()

    def __len__(self) -> int:
        """Return the number of loaded entity definitions."""
        return len(self._entities)

    def __iter__(self) -> Iterable[EntityDef]:
        """Iterate over loaded entity definitions in load order."""
        return iter(self._entities.values())
