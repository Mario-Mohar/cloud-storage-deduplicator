"""Tests for data models."""

from datetime import datetime

from drive_dedup.models import DriveFile, DuplicateGroup, LogEntry, ScanStats


class TestDriveFile:
    """Test DriveFile model."""

    def test_regular_file_creation(self):
        """Test creating a regular file with MD5."""
        file = DriveFile(
            id="file123",
            name="test.pdf",
            mime_type="application/pdf",
            size=1024,
            md5_checksum="abc123",
            parents=["folder1"]
        )

        assert file.id == "file123"
        assert file.name == "test.pdf"
        assert file.size == 1024
        assert file.has_md5 is True
        assert file.is_google_workspace_file is False

    def test_google_workspace_file(self):
        """Test Google Workspace file detection."""
        file = DriveFile(
            id="doc123",
            name="My Document",
            mime_type="application/vnd.google-apps.document",
            size=None,
            md5_checksum=None
        )

        assert file.is_google_workspace_file is True
        assert file.has_md5 is False

    def test_fallback_hash_generation(self):
        """Test fallback hash generation for Google Workspace files."""
        file = DriveFile(
            id="doc123",
            name="My Document",
            mime_type="application/vnd.google-apps.document",
            size=None
        )

        hash1 = file.get_fallback_hash()
        hash2 = file.get_fallback_hash()

        assert hash1 == hash2  # Should be deterministic
        assert len(hash1) == 32  # MD5 hash length
        assert hash1 != ""

    def test_comparison_key_with_md5(self):
        """Test comparison key when MD5 is available."""
        file = DriveFile(
            id="file123",
            name="test.pdf",
            mime_type="application/pdf",
            md5_checksum="abc123"
        )

        assert file.get_comparison_key() == "abc123"
        assert file.get_comparison_key(use_fallback=True) == "abc123"

    def test_comparison_key_fallback(self):
        """Test comparison key with fallback for Google Workspace files."""
        file = DriveFile(
            id="doc123",
            name="My Document",
            mime_type="application/vnd.google-apps.document"
        )

        # Without fallback, should return empty string
        assert file.get_comparison_key(use_fallback=False) == ""

        # With fallback, should return hash
        key = file.get_comparison_key(use_fallback=True)
        assert key != ""
        assert len(key) == 32

    def test_comparison_key_no_fallback_regular_file(self):
        """Test comparison key for regular file without MD5."""
        file = DriveFile(
            id="file123",
            name="test.pdf",
            mime_type="application/pdf",
            md5_checksum=None
        )

        # Should return empty string since it's not a Google Workspace file
        assert file.get_comparison_key(use_fallback=True) == ""


class TestDuplicateGroup:
    """Test DuplicateGroup model."""

    def test_duplicate_group_creation(self):
        """Test creating a duplicate group."""
        files = [
            DriveFile(
                id="file1",
                name="test1.pdf",
                mime_type="application/pdf",
                size=1024,
                created_time="2023-01-01T00:00:00.000Z"
            ),
            DriveFile(
                id="file2",
                name="test2.pdf",
                mime_type="application/pdf",
                size=1024,
                created_time="2023-01-02T00:00:00.000Z"
            )
        ]

        group = DuplicateGroup(files=files, comparison_key="abc123")

        assert len(group.files) == 2
        assert group.comparison_key == "abc123"
        assert group.kept_file == files[0]  # Older file should be kept
        assert group.duplicate_count == 1
        assert len(group.duplicate_files) == 1
        assert group.duplicate_files[0] == files[1]

    def test_duplicate_group_total_size(self):
        """Test total size calculation."""
        files = [
            DriveFile(id="file1", name="test1.pdf", mime_type="application/pdf", size=1024),
            DriveFile(id="file2", name="test2.pdf", mime_type="application/pdf", size=2048),
            DriveFile(id="file3", name="test3.pdf", mime_type="application/pdf", size=None)
        ]

        group = DuplicateGroup(files=files, comparison_key="abc123")

        assert group.total_size == 3072  # 1024 + 2048 + 0

    def test_duplicate_group_with_manual_kept_file(self):
        """Test duplicate group with manually specified kept file."""
        files = [
            DriveFile(id="file1", name="test1.pdf", mime_type="application/pdf"),
            DriveFile(id="file2", name="test2.pdf", mime_type="application/pdf"),
        ]

        group = DuplicateGroup(
            files=files,
            comparison_key="abc123",
            kept_file=files[1]
        )

        assert group.kept_file == files[1]
        assert group.duplicate_files == [files[0]]


class TestScanStats:
    """Test ScanStats model."""

    def test_scan_stats_initialization(self):
        """Test ScanStats initialization."""
        stats = ScanStats()

        assert stats.total_files_scanned == 0
        assert stats.files_with_md5 == 0
        assert stats.google_workspace_files == 0
        assert stats.duplicate_groups_found == 0
        assert stats.total_duplicates == 0
        assert stats.total_size_duplicates == 0
        assert stats.files_moved == 0
        assert stats.errors == 0
        assert stats.scan_duration_seconds == 0.0

    def test_add_file_with_md5(self):
        """Test adding file with MD5 to stats."""
        stats = ScanStats()
        file = DriveFile(
            id="file1",
            name="test.pdf",
            mime_type="application/pdf",
            md5_checksum="abc123"
        )

        stats.add_file(file)

        assert stats.total_files_scanned == 1
        assert stats.files_with_md5 == 1
        assert stats.google_workspace_files == 0

    def test_add_google_workspace_file(self):
        """Test adding Google Workspace file to stats."""
        stats = ScanStats()
        file = DriveFile(
            id="doc1",
            name="My Document",
            mime_type="application/vnd.google-apps.document"
        )

        stats.add_file(file)

        assert stats.total_files_scanned == 1
        assert stats.files_with_md5 == 0
        assert stats.google_workspace_files == 1

    def test_add_duplicate_group(self):
        """Test adding duplicate group to stats."""
        stats = ScanStats()
        files = [
            DriveFile(id="file1", name="test1.pdf", mime_type="application/pdf", size=1024),
            DriveFile(id="file2", name="test2.pdf", mime_type="application/pdf", size=1024),
        ]
        group = DuplicateGroup(files=files, comparison_key="abc123")

        stats.add_duplicate_group(group)

        assert stats.duplicate_groups_found == 1
        assert stats.total_duplicates == 1
        assert stats.total_size_duplicates == 1024


class TestLogEntry:
    """Test LogEntry model."""

    def test_log_entry_from_duplicate_group(self):
        """Test creating log entry from duplicate group."""
        files = [
            DriveFile(
                id="file1",
                name="test1.pdf",
                mime_type="application/pdf",
                size=1024,
                created_time="2023-01-01T00:00:00.000Z"
            ),
            DriveFile(
                id="file2",
                name="test2.pdf",
                mime_type="application/pdf",
                size=1024,
                created_time="2023-01-02T00:00:00.000Z"
            )
        ]
        group = DuplicateGroup(files=files, comparison_key="abc123")

        log_entry = LogEntry.from_duplicate_group(
            group=group,
            action="moved",
            target_folder_id="folder123",
            errors=["Error 1"]
        )

        assert log_entry.primary_id == "abc123"
        assert log_entry.kept_id == "file1"
        assert log_entry.duplicate_ids == ["file2"]
        assert log_entry.action == "moved"
        assert log_entry.move_target_folder_id == "folder123"
        assert log_entry.errors == ["Error 1"]
        assert log_entry.file_names == ["test1.pdf", "test2.pdf"]
        assert log_entry.total_size == 2048

    def test_log_entry_timestamp_format(self):
        """Test log entry timestamp format."""
        files = [
            DriveFile(id="file1", name="test.pdf", mime_type="application/pdf")
        ]
        group = DuplicateGroup(files=files, comparison_key="abc123")

        log_entry = LogEntry.from_duplicate_group(group, action="dry-run")

        # Should be valid ISO format
        datetime.fromisoformat(log_entry.timestamp)
