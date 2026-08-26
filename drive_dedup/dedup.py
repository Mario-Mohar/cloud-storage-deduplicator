"""Core duplicate detection and management functionality."""

import json
import logging
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Set

from .drive_client import GoogleDriveClient
from .models import DriveFile, DuplicateGroup, LogEntry, ScanStats

logger = logging.getLogger(__name__)


class DuplicateDetector:
    """Handles duplicate file detection and management operations."""

    def __init__(
        self,
        drive_client: GoogleDriveClient,
        min_file_size: int = 0,
        use_fallback_hash: bool = False
    ) -> None:
        """Initialize duplicate detector.

        Args:
            drive_client: Google Drive client instance
            min_file_size: Minimum file size in bytes to consider
            use_fallback_hash: Whether to use fallback hash for Google Workspace files
        """
        self.drive_client = drive_client
        self.min_file_size = min_file_size
        self.use_fallback_hash = use_fallback_hash
        self.stats = ScanStats()

    def scan_for_duplicates(
        self,
        progress_callback: Optional[callable] = None
    ) -> List[DuplicateGroup]:
        """Scan Google Drive for duplicate files.

        Args:
            progress_callback: Optional callback for progress updates

        Returns:
            List of duplicate groups found
        """
        logger.info("Starting duplicate scan with min_file_size=%d bytes", self.min_file_size)
        scan_start = datetime.now()

        # Collect all files and group by comparison key
        file_groups: Dict[str, List[DriveFile]] = defaultdict(list)
        files_processed = 0

        def file_progress_callback(count: int, file: DriveFile) -> None:
            nonlocal files_processed
            files_processed = count
            self.stats.add_file(file)

            if progress_callback:
                progress_callback(count, file)

            if count % 1000 == 0:
                logger.info("Processed %d files so far", count)

        # Get all files from Drive
        try:
            for drive_file in self.drive_client.list_all_files(
                min_size=self.min_file_size,
                progress_callback=file_progress_callback
            ):
                comparison_key = drive_file.get_comparison_key(self.use_fallback_hash)

                if comparison_key:  # Only group files that can be compared
                    file_groups[comparison_key].append(drive_file)
                else:
                    logger.debug(
                        "Skipping file %s (%s) - no comparison key available",
                        drive_file.name, drive_file.id
                    )

        except Exception as e:
            logger.error("Error during file scanning: %s", e)
            raise

        # Find duplicate groups (groups with more than one file)
        duplicate_groups = []
        for comparison_key, files in file_groups.items():
            if len(files) > 1:
                group = DuplicateGroup(files=files, comparison_key=comparison_key)
                duplicate_groups.append(group)
                self.stats.add_duplicate_group(group)
                logger.debug(
                    "Found duplicate group with %d files: %s",
                    len(files), [f.name for f in files]
                )

        # Update statistics
        scan_end = datetime.now()
        self.stats.scan_duration_seconds = (scan_end - scan_start).total_seconds()

        logger.info(
            "Duplicate scan completed in %.1f seconds: "
            "scanned %d files, found %d duplicate groups with %d total duplicates",
            self.stats.scan_duration_seconds,
            self.stats.total_files_scanned,
            self.stats.duplicate_groups_found,
            self.stats.total_duplicates
        )

        return duplicate_groups

    def move_duplicates(
        self,
        duplicate_groups: List[DuplicateGroup],
        target_folder_id: str,
        dry_run: bool = True
    ) -> List[LogEntry]:
        """Move duplicate files to target folder.

        Args:
            duplicate_groups: List of duplicate groups to process
            target_folder_id: ID of folder to move duplicates to
            dry_run: If True, only simulate the operation

        Returns:
            List of log entries for the operations
        """
        log_entries = []
        total_duplicates = sum(group.duplicate_count for group in duplicate_groups)

        if dry_run:
            logger.info(
                "DRY RUN: Would move %d duplicate files from %d groups to folder %s",
                total_duplicates, len(duplicate_groups), target_folder_id
            )
        else:
            logger.info(
                "Moving %d duplicate files from %d groups to folder %s",
                total_duplicates, len(duplicate_groups), target_folder_id
            )

        for i, group in enumerate(duplicate_groups, 1):
            if group.duplicate_count == 0:
                continue

            logger.info(
                "Processing group %d/%d: %d duplicates of '%s'",
                i, len(duplicate_groups), group.duplicate_count,
                group.kept_file.name if group.kept_file else "unknown"
            )

            errors = []
            moved_count = 0

            if not dry_run:
                # Move duplicate files
                file_ids_to_move = [f.id for f in group.duplicate_files]
                move_results = self.drive_client.move_files_batch(
                    file_ids_to_move, target_folder_id
                )

                # Check for errors
                for result in move_results:
                    if result.success:
                        moved_count += 1
                    else:
                        errors.append(result.error_message or "Unknown error")
                        self.stats.errors += 1

                self.stats.files_moved += moved_count

            # Create log entry
            action = "dry-run" if dry_run else "moved"
            log_entry = LogEntry.from_duplicate_group(
                group=group,
                action=action,
                target_folder_id=target_folder_id,
                errors=errors if errors else None
            )
            log_entries.append(log_entry)

            if errors:
                logger.warning(
                    "Group %d had %d errors during move operation",
                    i, len(errors)
                )

        logger.info(
            "Completed %s operation: %d files processed, %d moved successfully, %d errors",
            "dry-run" if dry_run else "move",
            total_duplicates,
            self.stats.files_moved,
            self.stats.errors
        )

        return log_entries

    def generate_report(
        self,
        duplicate_groups: List[DuplicateGroup],
        log_entries: List[LogEntry]
    ) -> str:
        """Generate human-readable report of duplicate detection results.

        Args:
            duplicate_groups: List of duplicate groups found
            log_entries: List of log entries from operations

        Returns:
            Formatted report string
        """
        report_lines = [
            "=" * 60,
            "DUPLICATE DETECTION REPORT",
            "=" * 60,
            f"Scan completed at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"Scan duration: {self.stats.scan_duration_seconds:.1f} seconds",
            "",
            "SUMMARY:",
            f"  Total files scanned: {self.stats.total_files_scanned:,}",
            f"  Files with MD5 checksum: {self.stats.files_with_md5:,}",
            f"  Google Workspace files: {self.stats.google_workspace_files:,}",
            f"  Duplicate groups found: {self.stats.duplicate_groups_found:,}",
            f"  Total duplicate files: {self.stats.total_duplicates:,}",
            f"  Size of duplicates: {self._format_size(self.stats.total_size_duplicates)}",
            f"  Files moved: {self.stats.files_moved:,}",
            f"  Errors encountered: {self.stats.errors:,}",
            ""
        ]

        if duplicate_groups:
            report_lines.extend([
                "DUPLICATE GROUPS:",
                "-" * 40
            ])

            for i, group in enumerate(duplicate_groups, 1):
                if group.duplicate_count == 0:
                    continue

                report_lines.extend([
                    f"Group {i}: {group.duplicate_count} "
                    f"{'duplicate' if group.duplicate_count == 1 else 'duplicates'}",
                    f"  Kept file: {group.kept_file.name} ({group.kept_file.id})",
                    f"  File size: {self._format_size(group.kept_file.size or 0)}",
                    f"  MIME type: {group.kept_file.mime_type}",
                    f"  Comparison key: {group.comparison_key[:16]}...",
                    "  Duplicate files:"
                ])

                for dup_file in group.duplicate_files:
                    report_lines.append(f"    - {dup_file.name} ({dup_file.id})")

                report_lines.append("")

        if self.stats.errors > 0:
            report_lines.extend([
                "ERRORS:",
                "-" * 40
            ])

            for entry in log_entries:
                if entry.errors:
                    report_lines.append(f"Group {entry.primary_id[:8]}:")
                    for error in entry.errors:
                        report_lines.append(f"  - {error}")

            report_lines.append("")

        report_lines.extend([
            "=" * 60,
            "END OF REPORT",
            "=" * 60
        ])

        return "\n".join(report_lines)

    def save_json_log(
        self,
        log_entries: List[LogEntry],
        log_file_path: str
    ) -> None:
        """Save log entries to JSON Lines file.

        Args:
            log_entries: List of log entries to save
            log_file_path: Path to save log file
        """
        log_path = Path(log_file_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        with open(log_path, 'w', encoding='utf-8') as f:
            for entry in log_entries:
                json.dump(entry.__dict__, f, ensure_ascii=False)
                f.write('\n')

        logger.info("Saved JSON log with %d entries to: %s", len(log_entries), log_path)

    def save_report(
        self,
        report: str,
        report_file_path: str
    ) -> None:
        """Save human-readable report to file.

        Args:
            report: Report content to save
            report_file_path: Path to save report file
        """
        report_path = Path(report_file_path)
        report_path.parent.mkdir(parents=True, exist_ok=True)

        with open(report_path, 'w', encoding='utf-8') as f:
            f.write(report)

        logger.info("Saved report to: %s", report_path)

    def _format_size(self, size_bytes: Optional[int]) -> str:
        """Format file size in human-readable format."""
        if size_bytes is None or size_bytes == 0:
            return "0 B"

        units = ["B", "KB", "MB", "GB", "TB"]
        unit_index = 0
        size = float(size_bytes)

        while size >= 1024 and unit_index < len(units) - 1:
            size /= 1024
            unit_index += 1

        if unit_index == 0:
            return f"{int(size)} {units[unit_index]}"
        else:
            return f"{size:.1f} {units[unit_index]}"

    def get_stats(self) -> ScanStats:
        """Get current scan statistics.

        Returns:
            ScanStats object with current statistics
        """
        return self.stats