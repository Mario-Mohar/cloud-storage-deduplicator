"""OneDrive API client wrapper with pagination and error handling via Microsoft Graph."""

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Iterator, List, Optional

import requests

from .base_client import BaseStorageClient
from .models import DriveFile, OperationResult

logger = logging.getLogger(__name__)


def retry_with_backoff_requests(max_retries: int = 3, base_delay: float = 1.0):
    """Decorator for retry logic with exponential backoff for requests.

    Args:
        max_retries: Maximum number of retry attempts
        base_delay: Base delay between retries in seconds
    """
    def decorator(func):
        def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except requests.exceptions.HTTPError as e:
                    last_exception = e
                    status_code = e.response.status_code if e.response else 0

                    # Don't retry client errors (except rate limiting)
                    if 400 <= status_code < 500 and status_code != 429:
                        raise

                    # Calculate delay with exponential backoff
                    if status_code == 429:
                        # Use Retry-After header if available
                        retry_after = e.response.headers.get('Retry-After', base_delay * (2 ** attempt))
                        delay = float(retry_after)
                    else:
                        delay = base_delay * (2 ** attempt)

                    if attempt < max_retries:
                        logger.warning(
                            "Request failed (attempt %d/%d), retrying in %.1fs: %s",
                            attempt + 1, max_retries + 1, delay, e
                        )
                        time.sleep(delay)
                    else:
                        raise

            raise last_exception
        return wrapper
    return decorator


class OneDriveClient(BaseStorageClient):
    """High-level wrapper for Microsoft Graph OneDrive API operations."""

    GRAPH_API_ENDPOINT = "https://graph.microsoft.com/v1.0"

    def __init__(self, access_token: str, max_workers: int = 5, auth_instance=None) -> None:
        """Initialize OneDrive client.

        Args:
            access_token: Microsoft Graph API access token
            max_workers: Maximum number of concurrent operations
            auth_instance: Optional OneDriveAuth instance for token refresh
        """
        self.access_token = access_token
        self.max_workers = max_workers
        self._auth = auth_instance
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        })

    def _refresh_token_if_needed(self) -> None:
        """Refresh token if auth instance is available."""
        if self._auth:
            try:
                new_token = self._auth.authenticate()
                if new_token != self.access_token:
                    self.access_token = new_token
                    self._session.headers.update({
                        "Authorization": f"Bearer {new_token}"
                    })
            except Exception as e:
                logger.warning("Could not refresh token: %s", e)

    def _make_request(
        self,
        method: str,
        endpoint: str,
        **kwargs
    ) -> requests.Response:
        """Make authenticated request to Microsoft Graph API.

        Args:
            method: HTTP method (GET, POST, PATCH, DELETE)
            endpoint: API endpoint path
            **kwargs: Additional request parameters

        Returns:
            Response object

        Raises:
            requests.exceptions.HTTPError: If request fails
        """
        url = f"{self.GRAPH_API_ENDPOINT}{endpoint}"
        response = self._session.request(method, url, **kwargs)

        # If unauthorized, try to refresh token and retry once
        if response.status_code == 401 and self._auth:
            logger.info("Token expired, refreshing...")
            self._refresh_token_if_needed()
            response = self._session.request(method, url, **kwargs)

        response.raise_for_status()
        return response

    @retry_with_backoff_requests(max_retries=3)
    def _list_files_page(
        self,
        next_link: Optional[str] = None,
        page_size: int = 200
    ) -> Dict:
        """List files with pagination support.

        Args:
            next_link: URL for next page of results
            page_size: Number of files per page

        Returns:
            API response with files and @odata.nextLink
        """
        if next_link:
            # Use the full next link URL directly
            response = self._session.get(next_link)
            response.raise_for_status()
            return response.json()

        # Initial request - list all items in OneDrive root
        endpoint = "/me/drive/root/children"
        params = {
            "$top": min(page_size, 200),
            "$select": "id,name,file,folder,size,parentReference,createdDateTime,lastModifiedDateTime"
        }

        response = self._make_request("GET", endpoint, params=params)
        return response.json()

    @retry_with_backoff_requests(max_retries=3)
    def _list_folder_contents(
        self,
        folder_id: str,
        next_link: Optional[str] = None,
        page_size: int = 200
    ) -> Dict:
        """List contents of a specific folder.

        Args:
            folder_id: ID of folder to list
            next_link: URL for next page of results
            page_size: Number of files per page

        Returns:
            API response with items
        """
        if next_link:
            response = self._session.get(next_link)
            response.raise_for_status()
            return response.json()

        endpoint = f"/me/drive/items/{folder_id}/children"
        params = {
            "$top": min(page_size, 200),
            "$select": "id,name,file,folder,size,parentReference,createdDateTime,lastModifiedDateTime"
        }

        response = self._make_request("GET", endpoint, params=params)
        return response.json()

    def list_all_files(
        self,
        min_size: int = 0,
        progress_callback: Optional[callable] = None
    ) -> Iterator[DriveFile]:
        """List all files in OneDrive with recursive folder traversal.

        Args:
            min_size: Minimum file size in bytes to include
            progress_callback: Optional callback for progress updates

        Yields:
            DriveFile objects for each file
        """
        total_files = 0
        folders_to_process = ["root"]  # Start with root
        processed_folders = set()

        while folders_to_process:
            current_folder = folders_to_process.pop(0)

            if current_folder in processed_folders:
                continue
            processed_folders.add(current_folder)

            next_link = None

            while True:
                try:
                    if current_folder == "root":
                        response = self._list_files_page(next_link=next_link)
                    else:
                        response = self._list_folder_contents(current_folder, next_link=next_link)

                    items = response.get('value', [])

                    for item in items:
                        # If it's a folder, add to processing queue
                        if 'folder' in item:
                            folders_to_process.append(item['id'])
                            continue

                        # Skip if not a file
                        if 'file' not in item:
                            continue

                        drive_file = self._parse_file_data(item)

                        # Apply size filter
                        if drive_file.size is not None and drive_file.size < min_size:
                            continue

                        total_files += 1
                        if progress_callback:
                            progress_callback(total_files, drive_file)

                        yield drive_file

                    next_link = response.get('@odata.nextLink')
                    if not next_link:
                        break

                except requests.exceptions.HTTPError as e:
                    logger.error("Error listing files in folder %s: %s", current_folder, e)
                    break

        logger.info("Completed file listing: %d files from OneDrive", total_files)

    def _parse_file_data(self, item: Dict) -> DriveFile:
        """Parse file data from API response into DriveFile object.

        Args:
            item: File item from Microsoft Graph API

        Returns:
            DriveFile object
        """
        # Get file hash - OneDrive uses SHA1 or quickXorHash
        file_info = item.get('file', {})
        hashes = file_info.get('hashes', {})

        # Prefer SHA256, then SHA1, then quickXorHash
        checksum = (
            hashes.get('sha256Hash') or
            hashes.get('sha1Hash') or
            hashes.get('quickXorHash')
        )

        # Get parent folder ID
        parent_ref = item.get('parentReference', {})
        parents = [parent_ref.get('id')] if parent_ref.get('id') else None

        return DriveFile(
            id=item['id'],
            name=item.get('name', ''),
            mime_type=file_info.get('mimeType', 'application/octet-stream'),
            size=item.get('size'),
            md5_checksum=checksum,  # Using available hash
            parents=parents,
            created_time=item.get('createdDateTime'),
            modified_time=item.get('lastModifiedDateTime')
        )

    @retry_with_backoff_requests(max_retries=3)
    def create_folder(self, name: str, parent_id: Optional[str] = None) -> str:
        """Create a new folder in OneDrive.

        Args:
            name: Name of the folder to create
            parent_id: ID of parent folder (None for root)

        Returns:
            ID of created folder

        Raises:
            requests.exceptions.HTTPError: If folder creation fails
        """
        if parent_id:
            endpoint = f"/me/drive/items/{parent_id}/children"
        else:
            endpoint = "/me/drive/root/children"

        body = {
            "name": name,
            "folder": {},
            "@microsoft.graph.conflictBehavior": "rename"
        }

        logger.info("Creating folder '%s' in parent %s", name, parent_id or "root")
        response = self._make_request("POST", endpoint, json=body)
        result = response.json()
        folder_id = result.get('id')
        logger.info("Created folder '%s' with ID: %s", name, folder_id)

        return folder_id

    @retry_with_backoff_requests(max_retries=3)
    def move_file(self, file_id: str, target_folder_id: str) -> OperationResult:
        """Move a file to a different folder.

        Args:
            file_id: ID of file to move
            target_folder_id: ID of target folder

        Returns:
            OperationResult indicating success/failure
        """
        try:
            endpoint = f"/me/drive/items/{file_id}"
            body = {
                "parentReference": {
                    "id": target_folder_id
                }
            }

            self._make_request("PATCH", endpoint, json=body)

            logger.debug("Moved file %s to folder %s", file_id, target_folder_id)
            return OperationResult(
                success=True,
                file_id=file_id,
                operation="move"
            )

        except requests.exceptions.HTTPError as e:
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

        logger.info(
            "Moving %d files to folder %s with %d workers",
            len(file_ids), target_folder_id, self.max_workers
        )

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_file_id = {
                executor.submit(self.move_file, file_id, target_folder_id): file_id
                for file_id in file_ids
            }

            for future in as_completed(future_to_file_id):
                result = future.result()
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

    @retry_with_backoff_requests(max_retries=2)
    def get_folder_info(self, folder_id: str) -> Optional[Dict]:
        """Get information about a folder.

        Args:
            folder_id: ID of folder to check

        Returns:
            Folder metadata or None if not found
        """
        try:
            endpoint = f"/me/drive/items/{folder_id}"
            params = {"$select": "id,name,folder,parentReference"}

            response = self._make_request("GET", endpoint, params=params)
            item = response.json()

            if 'folder' in item:
                return {
                    'id': item['id'],
                    'name': item['name'],
                    'mimeType': 'application/vnd.onedrive.folder',
                    'parents': [item.get('parentReference', {}).get('id')]
                }
            else:
                logger.warning("ID %s is not a folder", folder_id)
                return None

        except requests.exceptions.HTTPError as e:
            if e.response and e.response.status_code == 404:
                logger.debug("Folder %s not found", folder_id)
                return None
            else:
                logger.error("Error getting folder info for %s: %s", folder_id, e)
                raise

    def get_file_parents(self, file_id: str) -> Optional[List[str]]:
        """The one folder the file sits in, or None if it is gone.

        OneDrive has no multi-parent model, so this list never holds more than
        one entry -- the signature matches Drive's so that `undo` does not have
        to know which provider it is talking to.
        """
        try:
            response = self._make_request(
                "GET",
                f"/me/drive/items/{file_id}",
                params={"$select": "id,parentReference"}
            )
            parent_id = response.json().get('parentReference', {}).get('id')
            return [parent_id] if parent_id else []
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
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

        Args:
            name: Name of folder to find/create
            parent_id: ID of parent folder (None for root)

        Returns:
            ID of found or created folder
        """
        try:
            # Search for existing folder by listing and filtering manually
            # (OData filter doesn't work well with OneDrive personal)
            if parent_id:
                endpoint = f"/me/drive/items/{parent_id}/children"
            else:
                endpoint = "/me/drive/root/children"

            params = {
                "$select": "id,name,folder",
                "$top": 200
            }

            response = self._make_request("GET", endpoint, params=params)
            items = response.json().get('value', [])

            # Find folder with matching name
            for item in items:
                if item.get('name') == name and 'folder' in item:
                    folder_id = item['id']
                    logger.info("Found existing folder '%s': %s", name, folder_id)
                    return folder_id

        except requests.exceptions.HTTPError as e:
            logger.warning("Error searching for folder '%s': %s", name, e)

        # Create new folder if not found
        return self.create_folder(name, parent_id)

    @property
    def provider_name(self) -> str:
        """Return the name of the storage provider."""
        return "OneDrive"
