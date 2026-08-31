"""Data models for Google Drive file metadata and operations."""

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class DriveFile:
    """Represents a Google Drive file with relevant metadata."""

    id: str
    name: str
    mime_type: str
    size: Optional[int] = None
    md5_checksum: Optional[str] = None
    parents: Optional[List[str]] = None
    created_time: Optional[str] = None
    modified_time: Optional[str] = None

    @property
    def is_google_workspace_file(self) -> bool:
        """Check if file is a Google Workspace document."""
        workspace_mimes = {
            'application/vnd.google-apps.document',
            'application/vnd.google-apps.spreadsheet',
            'application/vnd.google-apps.presentation',
            'application/vnd.google-apps.drawing',
            'application/vnd.google-apps.form',
            'application/vnd.google-apps.site'
        }
        return self.mime_type in workspace_mimes

    @property
    def has_md5(self) -> bool:
        """Check if file has MD5 checksum available."""
        return bool(self.md5_checksum)

    def get_fallback_hash(self) -> str:
        """Generate fallback hash from name and size for Google Workspace files."""
        content = f"{self.name}:{self.size or 0}:{self.mime_type}"
        return hashlib.md5(content.encode()).hexdigest()

    def get_comparison_key(self, use_fallback: bool = False) -> str:
        """Get key for duplicate comparison.

        Args:
            use_fallback: Use name+size hash for Google Workspace files

        Returns:
            Comparison key (MD5 or fallback hash)
        """
        if self.has_md5:
            return self.md5_checksum
        elif use_fallback and self.is_google_workspace_file:
            return self.get_fallback_hash()
        else:
            return ""  # Cannot compare without hash


@dataclass
class DuplicateGroup:
    """Represents a group of duplicate files."""

    files: List[DriveFile]
    comparison_key: str
    kept_file: Optional[DriveFile] = None

    def __post_init__(self) -> None:
        """Set the kept file after initialization."""
        if self.files and not self.kept_file:
            # Keep the oldest file (by creation time) or first if times unavailable
            self.kept_file = min(
                self.files,
                key=lambda f: f.created_time or "0000-00-00T00:00:00.000Z"
            )

    @property
    def duplicate_files(self) -> List[DriveFile]:
        """Get files to be marked as duplicates (excluding kept file)."""
        return [f for f in self.files if f != self.kept_file]

    @property
    def total_size(self) -> int:
        """Get total size of all files in group."""
        return sum(f.size or 0 for f in self.files)

    @property
    def duplicate_count(self) -> int:
        """Get number of duplicate files (excluding kept file)."""
        return len(self.files) - 1 if self.kept_file else len(self.files)


@dataclass
class ScanStats:
    """Statistics from a duplicate scan operation."""

    total_files_scanned: int = 0
    files_with_md5: int = 0
    google_workspace_files: int = 0
    duplicate_groups_found: int = 0
    total_duplicates: int = 0
    total_size_duplicates: int = 0
    files_moved: int = 0
    errors: int = 0
    scan_duration_seconds: float = 0.0

    def add_file(self, file: DriveFile) -> None:
        """Add a file to the scan statistics."""
        self.total_files_scanned += 1
        if file.has_md5:
            self.files_with_md5 += 1
        if file.is_google_workspace_file:
            self.google_workspace_files += 1

    def add_duplicate_group(self, group: DuplicateGroup) -> None:
        """Add a duplicate group to the statistics."""
        if group.duplicate_count > 0:
            self.duplicate_groups_found += 1
            self.total_duplicates += group.duplicate_count
            self.total_size_duplicates += sum(
                f.size or 0 for f in group.duplicate_files
            )


@dataclass
class OperationResult:
    """Result of a file operation (move, etc.)."""

    success: bool
    file_id: str
    operation: str
    error_message: Optional[str] = None
    timestamp: Optional[datetime] = None

    def __post_init__(self) -> None:
        """Set timestamp if not provided."""
        if not self.timestamp:
            self.timestamp = datetime.now()


# Bumped when the shape of a log record changes in a way `undo` cares about.
# Version 1 recorded only which files were moved and where to, never where they
# came from -- such a log cannot be undone, and `undo` says so plainly instead
# of failing obscurely.
LOG_VERSION = 2


@dataclass
class LogEntry:
    """Structured log entry for JSON logging."""

    timestamp: str
    primary_id: str
    kept_id: str
    duplicate_ids: List[str]
    action: str  # "moved" or "dry-run"
    move_target_folder_id: Optional[str] = None
    errors: Optional[List[str]] = None
    file_names: Optional[List[str]] = None
    total_size: Optional[int] = None
    # Per moved file, so a run can be reversed: where it went is already known
    # from move_target_folder_id, but where it came from was nowhere in the
    # record. `duplicate_ids` stays alongside it -- readers of older logs keep
    # working, and the two are written from the same list.
    moved: Optional[List[Dict]] = None
    version: int = LOG_VERSION

    @classmethod
    def from_duplicate_group(
        cls,
        group: DuplicateGroup,
        action: str,
        target_folder_id: Optional[str] = None,
        errors: Optional[List[str]] = None
    ) -> "LogEntry":
        """Create log entry from duplicate group."""
        duplicates = group.duplicate_files
        return cls(
            timestamp=datetime.now().isoformat(),
            primary_id=group.comparison_key,
            kept_id=group.kept_file.id if group.kept_file else "",
            duplicate_ids=[f.id for f in duplicates],
            action=action,
            move_target_folder_id=target_folder_id,
            errors=errors,
            file_names=[f.name for f in group.files],
            total_size=group.total_size,
            moved=[
                {"id": f.id, "name": f.name, "from": list(f.parents or [])}
                for f in duplicates
            ]
        )
