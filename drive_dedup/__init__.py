"""Cloud Storage Deduplicator - A tool for finding and managing duplicate files in Google Drive and OneDrive."""

__version__ = "2.0.0"
__author__ = "Cloud Storage Deduplicator"

from .auth import GoogleDriveAuth
from .base_client import BaseStorageAuth, BaseStorageClient
from .dedup import DuplicateDetector
from .drive_client import GoogleDriveClient
from .models import DriveFile, DuplicateGroup, LogEntry, OperationResult, ScanStats
from .onedrive_auth import OneDriveAuth
from .onedrive_client import OneDriveClient

__all__ = [
    "GoogleDriveAuth",
    "OneDriveAuth",
    "BaseStorageAuth",
    "BaseStorageClient",
    "GoogleDriveClient",
    "OneDriveClient",
    "DuplicateDetector",
    "DriveFile",
    "DuplicateGroup",
    "ScanStats",
    "OperationResult",
    "LogEntry",
]
