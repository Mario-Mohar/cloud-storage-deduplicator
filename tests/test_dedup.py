"""Tests for duplicate detection logic."""

import json
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from drive_dedup.dedup import DuplicateDetector
from drive_dedup.models import DriveFile, DuplicateGroup, LogEntry


def _yield_files_like_client(files):
    """Mirror the list_all_files() contract of the real clients.

    drive_client.py and onedrive_client.py call
    progress_callback(running_index, file) for every file they yield.
    DuplicateDetector counts its statistics through exactly that callback,
    so the mock has to drive it too.
    """
    def _side_effect(*args, **kwargs):
        progress_callback = kwargs.get("progress_callback")
        for index, drive_file in enumerate(files, start=1):
            if progress_callback:
                progress_callback(index, drive_file)
            yield drive_file
    return _side_effect


class TestDuplicateDetector:
    """Test DuplicateDetector class."""

    @pytest.fixture
    def mock_drive_client(self):
        """Create a mock drive client."""
        return Mock()

    @pytest.fixture
    def detector(self, mock_drive_client):
        """Create a DuplicateDetector instance with mock client."""
        return DuplicateDetector(
            drive_client=mock_drive_client,
            min_file_size=100,
            use_fallback_hash=False
        )

    def test_detector_initialization(self, mock_drive_client):
        """Test detector initialization."""
        detector = DuplicateDetector(
            drive_client=mock_drive_client,
            min_file_size=1024,
            use_fallback_hash=True
        )

        assert detector.drive_client == mock_drive_client
        assert detector.min_file_size == 1024
        assert detector.use_fallback_hash is True
        assert detector.stats.total_files_scanned == 0

    def test_scan_for_duplicates_with_real_duplicates(self, detector, mock_drive_client):
        """Test scanning that finds actual duplicates."""
        # Mock files with same MD5
        files = [
            DriveFile(
                id="file1",
                name="document1.pdf",
                mime_type="application/pdf",
                size=2048,
                md5_checksum="abc123",
                created_time="2023-01-01T00:00:00.000Z"
            ),
            DriveFile(
                id="file2",
                name="document2.pdf",
                mime_type="application/pdf",
                size=2048,
                md5_checksum="abc123",
                created_time="2023-01-02T00:00:00.000Z"
            ),
            DriveFile(
                id="file3",
                name="unique.pdf",
                mime_type="application/pdf",
                size=1024,
                md5_checksum="def456"
            )
        ]

        mock_drive_client.list_all_files.side_effect = _yield_files_like_client(files)

        with patch('drive_dedup.dedup.datetime') as mock_datetime:
            mock_datetime.now.return_value = datetime(2023, 1, 1, 12, 0, 0)
            duplicate_groups = detector.scan_for_duplicates()

        # Should find one duplicate group
        assert len(duplicate_groups) == 1
        group = duplicate_groups[0]
        assert group.comparison_key == "abc123"
        assert len(group.files) == 2
        assert group.kept_file.id == "file1"  # Older file
        assert group.duplicate_files[0].id == "file2"

        # Check stats
        assert detector.stats.total_files_scanned == 3
        assert detector.stats.duplicate_groups_found == 1
        assert detector.stats.total_duplicates == 1

    def test_scan_for_duplicates_no_duplicates(self, detector, mock_drive_client):
        """Test scanning that finds no duplicates."""
        files = [
            DriveFile(
                id="file1",
                name="document1.pdf",
                mime_type="application/pdf",
                size=2048,
                md5_checksum="abc123"
            ),
            DriveFile(
                id="file2",
                name="document2.pdf",
                mime_type="application/pdf",
                size=1024,
                md5_checksum="def456"
            )
        ]

        mock_drive_client.list_all_files.side_effect = _yield_files_like_client(files)

        duplicate_groups = detector.scan_for_duplicates()

        assert len(duplicate_groups) == 0
        assert detector.stats.duplicate_groups_found == 0
        assert detector.stats.total_duplicates == 0

    def test_scan_with_google_workspace_files_no_fallback(self, detector, mock_drive_client):
        """Test scanning with Google Workspace files when fallback is disabled."""
        files = [
            DriveFile(
                id="doc1",
                name="My Document",
                mime_type="application/vnd.google-apps.document",
                size=None,
                md5_checksum=None
            ),
            DriveFile(
                id="doc2",
                name="My Document Copy",
                mime_type="application/vnd.google-apps.document",
                size=None,
                md5_checksum=None
            )
        ]

        mock_drive_client.list_all_files.side_effect = _yield_files_like_client(files)

        duplicate_groups = detector.scan_for_duplicates()

        # Should find no duplicates since fallback is disabled
        assert len(duplicate_groups) == 0
        assert detector.stats.google_workspace_files == 2

    def test_scan_with_google_workspace_files_with_fallback(self, mock_drive_client):
        """Test scanning with Google Workspace files when fallback is enabled."""
        detector = DuplicateDetector(
            drive_client=mock_drive_client,
            min_file_size=0,
            use_fallback_hash=True
        )

        files = [
            DriveFile(
                id="doc1",
                name="My Document",
                mime_type="application/vnd.google-apps.document",
                size=None,
                md5_checksum=None,
                created_time="2023-01-01T00:00:00.000Z"
            ),
            DriveFile(
                id="doc2",
                name="My Document",
                mime_type="application/vnd.google-apps.document",
                size=None,
                md5_checksum=None,
                created_time="2023-01-02T00:00:00.000Z"
            )
        ]

        mock_drive_client.list_all_files.side_effect = _yield_files_like_client(files)

        duplicate_groups = detector.scan_for_duplicates()

        # Should find duplicates using fallback hash
        assert len(duplicate_groups) == 1
        group = duplicate_groups[0]
        assert len(group.files) == 2
        assert group.kept_file.id == "doc1"  # Older file

    def test_move_duplicates_dry_run(self, detector, mock_drive_client):
        """Test moving duplicates in dry-run mode."""
        files = [
            DriveFile(id="file1", name="original.pdf", mime_type="application/pdf"),
            DriveFile(id="file2", name="duplicate.pdf", mime_type="application/pdf")
        ]
        group = DuplicateGroup(files=files, comparison_key="abc123")

        log_entries = detector.move_duplicates(
            duplicate_groups=[group],
            target_folder_id="folder123",
            dry_run=True
        )

        # Should not call move operations
        mock_drive_client.move_files_batch.assert_not_called()

        # Should create log entry
        assert len(log_entries) == 1
        assert log_entries[0].action == "dry-run"
        assert log_entries[0].duplicate_ids == ["file2"]

    def test_move_duplicates_live_mode(self, detector, mock_drive_client):
        """Test moving duplicates in live mode."""
        from drive_dedup.models import OperationResult

        files = [
            DriveFile(id="file1", name="original.pdf", mime_type="application/pdf"),
            DriveFile(id="file2", name="duplicate.pdf", mime_type="application/pdf")
        ]
        group = DuplicateGroup(files=files, comparison_key="abc123")

        # Mock successful move
        mock_drive_client.move_files_batch.return_value = [
            OperationResult(success=True, file_id="file2", operation="move")
        ]

        log_entries = detector.move_duplicates(
            duplicate_groups=[group],
            target_folder_id="folder123",
            dry_run=False
        )

        # Should call move operations
        mock_drive_client.move_files_batch.assert_called_once_with(
            ["file2"], "folder123"
        )

        # Should create log entry
        assert len(log_entries) == 1
        assert log_entries[0].action == "moved"
        assert detector.stats.files_moved == 1

    def test_move_duplicates_with_errors(self, detector, mock_drive_client):
        """Test moving duplicates with some errors."""
        from drive_dedup.models import OperationResult

        files = [
            DriveFile(id="file1", name="original.pdf", mime_type="application/pdf"),
            DriveFile(id="file2", name="duplicate1.pdf", mime_type="application/pdf"),
            DriveFile(id="file3", name="duplicate2.pdf", mime_type="application/pdf")
        ]
        group = DuplicateGroup(files=files, comparison_key="abc123")

        # Mock mixed results
        mock_drive_client.move_files_batch.return_value = [
            OperationResult(success=True, file_id="file2", operation="move"),
            OperationResult(
                success=False,
                file_id="file3",
                operation="move",
                error_message="Permission denied"
            )
        ]

        log_entries = detector.move_duplicates(
            duplicate_groups=[group],
            target_folder_id="folder123",
            dry_run=False
        )

        assert len(log_entries) == 1
        assert log_entries[0].errors == ["Permission denied"]
        assert detector.stats.files_moved == 1
        assert detector.stats.errors == 1

    def test_generate_report(self, detector):
        """Test report generation."""
        # Create some mock data
        files = [
            DriveFile(
                id="file1",
                name="original.pdf",
                mime_type="application/pdf",
                size=1024
            ),
            DriveFile(
                id="file2",
                name="duplicate.pdf",
                mime_type="application/pdf",
                size=1024
            )
        ]
        group = DuplicateGroup(files=files, comparison_key="abc123")
        log_entry = LogEntry.from_duplicate_group(group, action="moved")

        # Set some stats
        detector.stats.total_files_scanned = 100
        detector.stats.duplicate_groups_found = 1
        detector.stats.total_duplicates = 1
        detector.stats.scan_duration_seconds = 45.5

        report = detector.generate_report([group], [log_entry])

        assert "DUPLICATE DETECTION REPORT" in report
        assert "Total files scanned: 100" in report
        assert "Duplicate groups found: 1" in report
        assert "Scan duration: 45.5 seconds" in report
        assert "original.pdf" in report
        assert "duplicate.pdf" in report

    def test_save_json_log(self, detector):
        """Test saving JSON log to file."""
        with tempfile.TemporaryDirectory() as temp_dir:
            log_file = Path(temp_dir) / "test.jsonl"

            files = [
                DriveFile(id="file1", name="original.pdf", mime_type="application/pdf"),
                DriveFile(id="file2", name="duplicate.pdf", mime_type="application/pdf")
            ]
            group = DuplicateGroup(files=files, comparison_key="abc123")
            log_entries = [LogEntry.from_duplicate_group(group, action="moved")]

            detector.save_json_log(log_entries, str(log_file))

            # Verify file was created and contains valid JSON
            assert log_file.exists()
            content = log_file.read_text()
            log_data = json.loads(content.strip())

            assert log_data["primary_id"] == "abc123"
            assert log_data["action"] == "moved"
            assert log_data["duplicate_ids"] == ["file2"]

    def test_save_report(self, detector):
        """Test saving report to file."""
        with tempfile.TemporaryDirectory() as temp_dir:
            report_file = Path(temp_dir) / "test_report.txt"
            report_content = "Test report content"

            detector.save_report(report_content, str(report_file))

            assert report_file.exists()
            assert report_file.read_text() == report_content

    def test_progress_callback(self, detector, mock_drive_client):
        """Test that progress callback is called during scanning."""
        files = [
            DriveFile(
                id="file1",
                name="test1.pdf",
                mime_type="application/pdf",
                size=1024,
                md5_checksum="abc123"
            ),
            DriveFile(
                id="file2",
                name="test2.pdf",
                mime_type="application/pdf",
                size=2048,
                md5_checksum="def456"
            )
        ]

        mock_drive_client.list_all_files.side_effect = _yield_files_like_client(files)

        progress_calls = []

        def progress_callback(count, file):
            progress_calls.append((count, file.id))

        detector.scan_for_duplicates(progress_callback)

        # Progress callback should be called for each file
        assert len(progress_calls) == 2
        assert progress_calls[0] == (1, "file1")
        assert progress_calls[1] == (2, "file2")

    def test_format_size_method(self, detector):
        """Test the _format_size helper method."""
        assert detector._format_size(0) == "0 B"
        assert detector._format_size(None) == "0 B"
        assert detector._format_size(1024) == "1.0 KB"
        assert detector._format_size(1048576) == "1.0 MB"
        assert detector._format_size(1073741824) == "1.0 GB"
        assert detector._format_size(512) == "512 B"