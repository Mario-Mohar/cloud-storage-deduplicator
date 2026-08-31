"""Authentication module for Google Drive API access."""

import logging
from pathlib import Path
from typing import List, Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import Resource, build

from .base_client import BaseStorageAuth

logger = logging.getLogger(__name__)


class GoogleDriveAuth(BaseStorageAuth):
    """Handles Google Drive API authentication using OAuth 2.0."""

    DEFAULT_SCOPES = [
        "https://www.googleapis.com/auth/drive"
    ]

    def __init__(
        self,
        credentials_file: str = "credentials.json",
        token_file: str = "token.json",
        scopes: Optional[List[str]] = None
    ) -> None:
        """Initialize authentication handler.

        Args:
            credentials_file: Path to OAuth 2.0 credentials file from Google Cloud Console
            token_file: Path to store/load access token
            scopes: List of OAuth scopes to request
        """
        self.credentials_file = Path(credentials_file)
        self.token_file = Path(token_file)
        self.scopes = scopes or self.DEFAULT_SCOPES
        self._credentials: Optional[Credentials] = None

    def authenticate(self) -> Credentials:
        """Authenticate and return valid credentials.

        Returns:
            Valid Google OAuth 2.0 credentials

        Raises:
            FileNotFoundError: If credentials file is missing
            Exception: If authentication fails
        """
        if self._credentials and self._credentials.valid:
            return self._credentials

        creds = None

        # Load existing token if available
        if self.token_file.exists():
            try:
                creds = Credentials.from_authorized_user_file(
                    str(self.token_file), self.scopes
                )
                logger.info("Loaded existing credentials from %s", self.token_file)
            except Exception as e:
                logger.warning("Failed to load existing token: %s", e)

        # Refresh expired credentials or get new ones
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                    logger.info("Refreshed expired credentials")
                except Exception as e:
                    logger.warning("Failed to refresh credentials: %s", e)
                    creds = None

            if not creds:
                creds = self._get_new_credentials()

        # Save credentials for next run
        try:
            with open(self.token_file, 'w') as token:
                token.write(creds.to_json())
            logger.info("Saved credentials to %s", self.token_file)
        except Exception as e:
            logger.warning("Failed to save credentials: %s", e)

        self._credentials = creds
        return creds

    def _get_new_credentials(self) -> Credentials:
        """Get new credentials via OAuth flow.

        Returns:
            New Google OAuth 2.0 credentials

        Raises:
            FileNotFoundError: If credentials file is missing
            Exception: If OAuth flow fails
        """
        if not self.credentials_file.exists():
            raise FileNotFoundError(
                f"Credentials file not found: {self.credentials_file}\n"
                "Please download it from Google Cloud Console:\n"
                "1. Go to https://console.cloud.google.com/\n"
                "2. Create/select a project\n"
                "3. Enable Google Drive API\n"
                "4. Create OAuth 2.0 credentials (Desktop application)\n"
                "5. Download and save as 'credentials.json'"
            )

        try:
            flow = InstalledAppFlow.from_client_secrets_file(
                str(self.credentials_file), self.scopes
            )
            creds = flow.run_local_server(port=0)
            logger.info("Obtained new credentials via OAuth flow")
            return creds
        except Exception as e:
            logger.error("OAuth flow failed: %s", e)
            raise

    def get_drive_service(self) -> Resource:
        """Get authenticated Google Drive service.

        Returns:
            Google Drive API service resource
        """
        creds = self.authenticate()
        service = build('drive', 'v3', credentials=creds)
        logger.info("Created Google Drive service")
        return service

    def get_service(self) -> Resource:
        """Get authenticated service (alias for get_drive_service).

        Returns:
            Google Drive API service resource
        """
        return self.get_drive_service()

    def revoke_credentials(self) -> None:
        """Revoke and delete stored credentials."""
        if self._credentials:
            try:
                self._credentials.revoke(Request())
                logger.info("Revoked credentials")
            except Exception as e:
                logger.warning("Failed to revoke credentials: %s", e)

        if self.token_file.exists():
            try:
                self.token_file.unlink()
                logger.info("Deleted token file: %s", self.token_file)
            except Exception as e:
                logger.warning("Failed to delete token file: %s", e)

        self._credentials = None
