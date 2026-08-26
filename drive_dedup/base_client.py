"""Abstract base classes for cloud storage providers."""

from abc import ABC, abstractmethod
from typing import Dict, Iterator, List, Optional

from .models import DriveFile, OperationResult


class BaseStorageAuth(ABC):
    """Abstract base class for cloud storage authentication."""

    @abstractmethod
    def authenticate(self):
        """Authenticate and return valid credentials.

        Returns:
            Valid credentials for the cloud provider
        """
        pass

    @abstractmethod
    def get_service(self):
        """Get authenticated service/client.

        Returns:
            Authenticated service or client object
        """
        pass

    @abstractmethod
    def revoke_credentials(self) -> None:
        """Revoke and delete stored credentials."""
        pass


class BaseStorageClient(ABC):
    """Abstract base class for cloud storage operations."""

    @abstractmethod
    def list_all_files(
        self,
        min_size: int = 0,
        progress_callback: Optional[callable] = None
    ) -> Iterator[DriveFile]:
        """List all files in storage with pagination.

        Args:
            min_size: Minimum file size in bytes to include
            progress_callback: Optional callback for progress updates

        Yields:
            DriveFile objects for each file
        """
        pass

    @abstractmethod
    def create_folder(self, name: str, parent_id: Optional[str] = None) -> str:
        """Create a new folder.

        Args:
            name: Name of the folder to create
            parent_id: ID of parent folder (None for root)

        Returns:
            ID of created folder
        """
        pass

    @abstractmethod
    def move_file(self, file_id: str, target_folder_id: str) -> OperationResult:
        """Move a file to a different folder.

        Args:
            file_id: ID of file to move
            target_folder_id: ID of target folder

        Returns:
            OperationResult indicating success/failure
        """
        pass

    @abstractmethod
    def move_files_batch(
        self,
        file_ids: List[str],
        target_folder_id: str
    ) -> List[OperationResult]:
        """Move multiple files concurrently.

        Args:
            file_ids: List of file IDs to move
            target_folder_id: ID of target folder

        Returns:
            List of OperationResult objects
        """
        pass

    @abstractmethod
    def get_folder_info(self, folder_id: str) -> Optional[Dict]:
        """Get information about a folder.

        Args:
            folder_id: ID of folder to check

        Returns:
            Folder metadata or None if not found
        """
        pass

    @abstractmethod
    def find_or_create_folder(
        self,
        name: str,
        parent_id: Optional[str] = None
    ) -> str:
        """Find existing folder by name or create new one.

        Args:
            name: Name of folder to find/create
            parent_id: ID of parent folder (None for root)

        Returns:
            ID of found or created folder
        """
        pass

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Return the name of the storage provider."""
        pass
