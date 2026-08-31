"""Pytest configuration and shared fixtures."""

from unittest.mock import Mock

import pytest

from drive_dedup.models import DriveFile


@pytest.fixture
def sample_drive_file():
    """Create a sample DriveFile for testing."""
    return DriveFile(
        id="1ABC123xyz",
        name="test_document.pdf",
        mime_type="application/pdf",
        size=1048576,  # 1MB
        md5_checksum="d41d8cd98f00b204e9800998ecf8427e",
        parents=["0BwwA4oUTeiV1TGRPeTVjaWRDY1E"],
        created_time="2023-01-01T12:00:00.000Z",
        modified_time="2023-01-02T12:00:00.000Z"
    )


@pytest.fixture
def sample_google_workspace_file():
    """Create a sample Google Workspace file for testing."""
    return DriveFile(
        id="1DEF456abc",
        name="My Document",
        mime_type="application/vnd.google-apps.document",
        size=None,
        md5_checksum=None,
        parents=["0BwwA4oUTeiV1TGRPeTVjaWRDY1E"],
        created_time="2023-01-01T12:00:00.000Z",
        modified_time="2023-01-02T12:00:00.000Z"
    )


@pytest.fixture
def mock_google_drive_service():
    """Create a mock Google Drive service."""
    service = Mock()

    # Mock the files() resource
    files_resource = Mock()
    service.files.return_value = files_resource

    # Mock common methods
    files_resource.list.return_value.execute.return_value = {
        'files': [],
        'nextPageToken': None
    }
    files_resource.get.return_value.execute.return_value = {}
    files_resource.create.return_value.execute.return_value = {'id': 'new_folder_id'}
    files_resource.update.return_value.execute.return_value = {}

    return service


@pytest.fixture
def duplicate_files_pair():
    """Create a pair of duplicate files for testing."""
    file1 = DriveFile(
        id="file1",
        name="original.pdf",
        mime_type="application/pdf",
        size=2048,
        md5_checksum="same_checksum_123",
        created_time="2023-01-01T12:00:00.000Z"
    )

    file2 = DriveFile(
        id="file2",
        name="duplicate.pdf",
        mime_type="application/pdf",
        size=2048,
        md5_checksum="same_checksum_123",
        created_time="2023-01-02T12:00:00.000Z"
    )

    return [file1, file2]


@pytest.fixture
def unique_files():
    """Create a list of unique files (no duplicates) for testing."""
    return [
        DriveFile(
            id="unique1",
            name="file1.pdf",
            mime_type="application/pdf",
            size=1024,
            md5_checksum="checksum1"
        ),
        DriveFile(
            id="unique2",
            name="file2.pdf",
            mime_type="application/pdf",
            size=2048,
            md5_checksum="checksum2"
        ),
        DriveFile(
            id="unique3",
            name="file3.pdf",
            mime_type="application/pdf",
            size=4096,
            md5_checksum="checksum3"
        )
    ]
