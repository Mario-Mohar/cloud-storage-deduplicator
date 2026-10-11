"""Tests for the Google Drive client."""

from drive_dedup.drive_client import GoogleDriveClient


def test_listing_only_asks_for_owned_files(mock_google_drive_service):
    client = GoogleDriveClient(mock_google_drive_service)

    client.list_files_page()

    query = mock_google_drive_service.files.return_value.list.call_args.kwargs["q"]
    assert "trashed=false" in query
    assert "'me' in owners" in query
