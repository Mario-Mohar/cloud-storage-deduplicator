"""Google Drive API client wrapper with pagination and error handling."""

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Dict, Iterator, List, Optional

from googleapiclient.discovery import Resource
from googleapiclient.errors import HttpError

from .base_client import BaseStorageClient
from .models import DriveFile, OperationResult
from .utils import retry_with_backoff

logger = logging.getLogger(__name__)


class AmbiguousFolderError(ValueError):
    """More than one of the user's folders matches the requested name."""


def _escape_query_value(value: str) -> str:
    """Escape a string for use inside a quoted Drive query literal."""
    return value.replace('\\', '\\\\').replace("'", "\\'")


class GoogleDriveClient(BaseStorageClient):
    """High-level wrapper for Google Drive API operations."""

    def __init__(
        self,
        service: Resource,
        max_workers: int = 5,
        service_factory: Optional[Callable[[], Resource]] = None
    ) -> None:
        """Initialize Drive client.

        Args:
            service: Authenticated Google Drive service
            max_workers: Maximum number of concurrent operations
            service_factory: Builds a fresh service for a worker thread.
                A service wraps one httplib2.Http, which is not thread-safe,
                so concurrent moves need one each. Without a factory the
                moves run one at a time on `service`.
        """
        self.service = service
        self.max_workers = max_workers
        self._service_factory = service_factory
        self._owner_thread = threading.get_ident()
        self._local = threading.local()

    def _thread_service(self) -> Resource:
        """The service this thread may use without sharing a connection."""
        if (self._service_factory is None
                or threading.get_ident() == self._owner_thread):
            return self.service
        service = getattr(self._local, 'service', None)
        if service is None:
            service = self._service_factory()
            self._local.service = service
        return service

    @retry_with_backoff(max_retries=3)
    def list_files_page(
        self,
        page_token: Optional[str] = None,
        page_size: int = 1000,
        fields: str = "nextPageToken,files(id,name,mimeType,size,md5Checksum,parents,createdTime,modifiedTime)"
    ) -> Dict:
        """List files with pagination support.

        Args:
            page_token: Token for next page of results
            page_size: Number of files per page (max 1000)
            fields: Fields to retrieve from API

        Returns:
            API response with files and nextPageToken
        """
        # Only non-deleted files the user owns. Files shared with the user
        # belong to someone else, and moving one would move it for its
        # owner too, so they are never considered.
        query = "trashed=false and 'me' in owners"

        request_params = {
            'q': query,
            'pageSize': min(page_size, 1000),
            'fields': fields
        }

        if page_token:
            request_params['pageToken'] = page_token

        logger.debug("Requesting files page with params: %s", request_params)
        response = self.service.files().list(**request_params).execute()
        logger.debug("Retrieved %d files from API", len(response.get('files', [])))

        return response

    def list_all_files(
        self,
        min_size: int = 0,
        progress_callback: Optional[callable] = None
    ) -> Iterator[DriveFile]:
        """List all files in Drive with pagination.

        Args:
            min_size: Minimum file size in bytes to include
            progress_callback: Optional callback for progress updates

        Yields:
            DriveFile objects for each file
        """
        page_token = None
        total_files = 0
        total_pages = 0

        while True:
            try:
                response = self.list_files_page(page_token=page_token)
                files_data = response.get('files', [])
                total_pages += 1

                logger.info(
                    "Processing page %d with %d files",
                    total_pages, len(files_data)
                )

                for file_data in files_data:
                    drive_file = self._parse_file_data(file_data)

                    # Apply size filter
                    if drive_file.size is not None and drive_file.size < min_size:
                        continue

                    total_files += 1
                    if progress_callback:
                        progress_callback(total_files, drive_file)

                    yield drive_file

                page_token = response.get('nextPageToken')
                if not page_token:
                    break

            except HttpError as e:
                logger.error("Error listing files: %s", e)
                raise

        logger.info(
            "Completed file listing: %d files from %d pages",
            total_files, total_pages
        )

    def _parse_file_data(self, file_data: Dict) -> DriveFile:
        """Parse file data from API response into DriveFile object.

        Args:
            file_data: File data from Google Drive API

        Returns:
            DriveFile object
        """
        size = file_data.get('size')
        if size is not None:
            try:
                size = int(size)
            except (ValueError, TypeError):
                size = None

        return DriveFile(
            id=file_data['id'],
            name=file_data.get('name', ''),
            mime_type=file_data.get('mimeType', ''),
            size=size,
            md5_checksum=file_data.get('md5Checksum'),
            parents=file_data.get('parents'),
            created_time=file_data.get('createdTime'),
            modified_time=file_data.get('modifiedTime')
        )

    @retry_with_backoff(max_retries=3)
    def create_folder(self, name: str, parent_id: Optional[str] = None) -> str:
        """Create a new folder in Google Drive.

        Args:
            name: Name of the folder to create
            parent_id: ID of parent folder (None for root)

        Returns:
            ID of created folder

        Raises:
            HttpError: If folder creation fails
        """
        metadata = {
            'name': name,
            'mimeType': 'application/vnd.google-apps.folder'
        }

        if parent_id:
            metadata['parents'] = [parent_id]

        logger.info("Creating folder '%s' in parent %s", name, parent_id or "root")
        folder = self.service.files().create(body=metadata, fields='id').execute()
        folder_id = folder.get('id')
        logger.info("Created folder '%s' with ID: %s", name, folder_id)

        return folder_id

    @retry_with_backoff(max_retries=3)
    def _move_file_request(self, file_id: str, target_folder_id: str) -> None:
        """Move one file, letting HttpError through so it can be retried."""
        service = self._thread_service()

        # Get current parents
        file_metadata = service.files().get(
            fileId=file_id, fields='parents'
        ).execute()
        # A retry after a move that did go through finds the file already in
        # the target; removing the target again would orphan it.
        previous_parents = ','.join(
            p for p in file_metadata.get('parents', []) if p != target_folder_id
        )

        # Move file by updating parents
        service.files().update(
            fileId=file_id,
            addParents=target_folder_id,
            removeParents=previous_parents
        ).execute()

    def move_file(self, file_id: str, target_folder_id: str) -> OperationResult:
        """Move a file to a different folder.

        Rate limits (429/403) are retried with backoff by the inner request;
        only an error that outlasts the retries becomes a failed result.

        Args:
            file_id: ID of file to move
            target_folder_id: ID of target folder

        Returns:
            OperationResult indicating success/failure
        """
        try:
            self._move_file_request(file_id, target_folder_id)

            logger.debug("Moved file %s to folder %s", file_id, target_folder_id)
            return OperationResult(
                success=True,
                file_id=file_id,
                operation="move"
            )

        except HttpError as e:
            error_msg = f"Failed to move file {file_id}: {e}"
            logger.error(error_msg)
            return OperationResult(
                success=False,
                file_id=file_id,
                operation="move",
                error_message=error_msg
            )

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
        results = []

        if not file_ids:
            return results

        # Without a way to give each worker its own connection, the moves
        # must not run side by side on the shared one.
        workers = self.max_workers if self._service_factory else 1

        logger.info(
            "Moving %d files to folder %s with %d workers",
            len(file_ids), target_folder_id, workers
        )

        with ThreadPoolExecutor(max_workers=workers) as executor:
            # Submit all move operations
            future_to_file_id = {
                executor.submit(self.move_file, file_id, target_folder_id): file_id
                for file_id in file_ids
            }

            # Collect results as they complete
            for future in as_completed(future_to_file_id):
                file_id = future_to_file_id[future]
                try:
                    result = future.result()
                except Exception as e:
                    # One broken move must not end the run and lose the log
                    # of the moves that already happened.
                    result = OperationResult(
                        success=False,
                        file_id=file_id,
                        operation="move",
                        error_message=f"Failed to move file {file_id}: {e}"
                    )
                results.append(result)

                if result.success:
                    logger.debug("Successfully moved file %s", result.file_id)
                else:
                    logger.warning("Failed to move file %s: %s",
                                   result.file_id, result.error_message)

        successful_moves = sum(1 for r in results if r.success)
        logger.info(
            "Batch move completed: %d/%d files moved successfully",
            successful_moves, len(file_ids)
        )

        return results

    @retry_with_backoff(max_retries=2)
    def get_folder_info(self, folder_id: str) -> Optional[Dict]:
        """Get information about a folder.

        Args:
            folder_id: ID of folder to check

        Returns:
            Folder metadata or None if not found
        """
        try:
            folder = self.service.files().get(
                fileId=folder_id,
                fields='id,name,mimeType,parents'
            ).execute()

            if folder.get('mimeType') == 'application/vnd.google-apps.folder':
                return folder
            else:
                logger.warning("ID %s is not a folder", folder_id)
                return None

        except HttpError as e:
            if e.resp.status == 404:
                logger.debug("Folder %s not found", folder_id)
                return None
            else:
                logger.error("Error getting folder info for %s: %s", folder_id, e)
                raise

    def get_file_parents(self, file_id: str) -> Optional[List[str]]:
        """Folders the file currently sits in, or None if it is gone.

        Drive allows several parents, so this is a list. A file that has been
        deleted since the run is not an error worth stopping an undo for.
        """
        try:
            metadata = self.service.files().get(
                fileId=file_id, fields='parents'
            ).execute()
            return list(metadata.get('parents') or [])
        except HttpError as e:
            if e.resp.status == 404:
                logger.debug("File %s not found", file_id)
                return None
            logger.error("Error getting parents for %s: %s", file_id, e)
            raise

    def find_or_create_folder(
        self,
        name: str,
        parent_id: Optional[str] = None
    ) -> str:
        """Find existing folder by name or create new one.

        Only folders the user owns are considered, and without a parent only
        those directly in My Drive: a same-named folder someone else shared
        would make the moved duplicates visible to them.

        Args:
            name: Name of folder to find/create
            parent_id: ID of parent folder (None for root)

        Returns:
            ID of found or created folder

        Raises:
            AmbiguousFolderError: If more than one owned folder matches
        """
        # Search for existing folder
        parent = parent_id or 'root'
        query = (
            f"name='{_escape_query_value(name)}'"
            " and mimeType='application/vnd.google-apps.folder'"
            " and trashed=false and 'me' in owners"
            f" and '{_escape_query_value(parent)}' in parents"
        )

        folders = []
        try:
            results = self.service.files().list(
                q=query,
                fields='files(id,name)'
            ).execute()
            folders = results.get('files', [])

        except HttpError as e:
            logger.warning("Error searching for folder '%s': %s", name, e)

        if len(folders) > 1:
            ids = ', '.join(f['id'] for f in folders)
            raise AmbiguousFolderError(
                f"{len(folders)} folders named '{name}' found in "
                f"{'My Drive' if parent_id is None else 'folder ' + parent_id}"
                f" ({ids}). Rename or remove all but one, or pass the one "
                "you want with --move-folder-id."
            )
        if folders:
            folder_id = folders[0]['id']
            logger.info("Found existing folder '%s': %s", name, folder_id)
            return folder_id

        # Create new folder if not found
        return self.create_folder(name, parent_id)

    @property
    def provider_name(self) -> str:
        """Return the name of the storage provider."""
        return "Google Drive"
