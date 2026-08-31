"""Authentication module for Microsoft OneDrive API access via Microsoft Graph."""

import json
import logging
import secrets
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import List, Optional
from urllib.parse import parse_qs, urlencode, urlparse

import requests

from .base_client import BaseStorageAuth

logger = logging.getLogger(__name__)




class OAuthCallbackHandler(BaseHTTPRequestHandler):
    """HTTP handler to receive OAuth callback."""

    auth_code = None
    error = None

    def do_GET(self):
        """Handle GET request from OAuth redirect."""
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        if 'code' in params:
            OAuthCallbackHandler.auth_code = params['code'][0]
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            self.wfile.write(b"""
                <html><body style="font-family: Arial; text-align: center; padding-top: 50px;">
                <h1>Authentifizierung erfolgreich!</h1>
                <p>Du kannst dieses Fenster jetzt schliessen.</p>
                <script>window.close();</script>
                </body></html>
            """)
        elif 'error' in params:
            OAuthCallbackHandler.error = params.get('error_description', params['error'])[0]
            self.send_response(400)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            error_msg = params.get('error_description', ['Unknown error'])[0]
            self.wfile.write(f"""
                <html><body style="font-family: Arial; text-align: center; padding-top: 50px;">
                <h1>Authentifizierung fehlgeschlagen</h1>
                <p>{error_msg}</p>
                </body></html>
            """.encode())
        else:
            self.send_response(400)
            self.end_headers()

    def log_message(self, format, *args):
        """Suppress HTTP server logs."""
        pass


class OneDriveAuth(BaseStorageAuth):
    """Handles OneDrive API authentication using Microsoft Identity Platform (OAuth 2.0).

    Requires your own Azure AD app registration (public client, no client
    secret). Pass the Application (client) ID via client_id. See the
    "OneDrive setup" section of the README.
    """

    DEFAULT_SCOPES = [
        "Files.ReadWrite.All",
        "User.Read",
        "offline_access"
    ]

    GRAPH_API_ENDPOINT = "https://graph.microsoft.com/v1.0"

    # Different auth endpoints for different account types
    AUTH_ENDPOINTS = {
        "common": "https://login.microsoftonline.com/common/oauth2/v2.0",      # Both
        "consumers": "https://login.microsoftonline.com/consumers/oauth2/v2.0", # Personal only
        "organizations": "https://login.microsoftonline.com/organizations/oauth2/v2.0"  # Work only
    }

    def __init__(
        self,
        client_id: Optional[str] = None,
        token_file: str = "onedrive_token.json",
        scopes: Optional[List[str]] = None,
        redirect_port: int = 8400,
        account_type: str = "common"
    ) -> None:
        """Initialize OneDrive authentication handler.

        Args:
            client_id: Application (client) ID of your own Azure AD public
                      client app. Required.
            token_file: Path to store/load access token
            scopes: List of OAuth scopes to request
            redirect_port: Local port for OAuth callback server
            account_type: Account type - 'common' (both), 'consumers' (personal), 'organizations' (work)
        """
        if not client_id:
            raise ValueError(
                "OneDrive requires your own Azure AD client ID. Register a "
                "public client app (redirect URI http://localhost:8400) and "
                "pass its Application (client) ID via --client-id. See the "
                "'OneDrive setup' section of the README."
            )

        self.client_id = client_id
        self.token_file = Path(token_file)
        self.scopes = scopes or self.DEFAULT_SCOPES
        self.redirect_port = redirect_port
        self.redirect_uri = f"http://localhost:{redirect_port}"
        self.account_type = account_type
        self.auth_endpoint = self.AUTH_ENDPOINTS.get(account_type, self.AUTH_ENDPOINTS["common"])

        self._access_token: Optional[str] = None
        self._refresh_token: Optional[str] = None
        self._token_expiry: Optional[float] = None

        # Load existing tokens
        self._load_tokens()

    def _load_tokens(self) -> None:
        """Load tokens from file if available."""
        if self.token_file.exists():
            try:
                data = json.loads(self.token_file.read_text())
                self._access_token = data.get('access_token')
                self._refresh_token = data.get('refresh_token')
                self._token_expiry = data.get('expiry')
                logger.info("Loaded existing tokens from %s", self.token_file)
            except Exception as e:
                logger.warning("Failed to load tokens: %s", e)

    def _save_tokens(self, access_token: str, refresh_token: str, expires_in: int) -> None:
        """Save tokens to file."""
        try:
            data = {
                'access_token': access_token,
                'refresh_token': refresh_token,
                'expiry': time.time() + expires_in - 60  # 60 second buffer
            }
            self.token_file.write_text(json.dumps(data, indent=2))
            logger.info("Saved tokens to %s", self.token_file)
        except Exception as e:
            logger.warning("Failed to save tokens: %s", e)

    def _is_token_valid(self) -> bool:
        """Check if current access token is still valid."""
        if not self._access_token or not self._token_expiry:
            return False
        return time.time() < self._token_expiry

    def authenticate(self) -> str:
        """Authenticate and return valid access token.

        Returns:
            Valid Microsoft Graph access token

        Raises:
            Exception: If authentication fails
        """
        # Return cached token if valid
        if self._is_token_valid():
            return self._access_token

        # Try to refresh token
        if self._refresh_token:
            try:
                self._refresh_access_token()
                if self._is_token_valid():
                    return self._access_token
            except Exception as e:
                logger.warning("Token refresh failed: %s", e)

        # Need new authentication
        self._authenticate_interactive()

        if not self._access_token:
            raise Exception("Failed to acquire access token")

        return self._access_token

    def _refresh_access_token(self) -> None:
        """Refresh the access token using refresh token."""
        logger.info("Refreshing access token...")

        data = {
            'client_id': self.client_id,
            'grant_type': 'refresh_token',
            'refresh_token': self._refresh_token,
            'scope': ' '.join(self.scopes)
        }

        response = requests.post(
            f"{self.auth_endpoint}/token",
            data=data
        )

        if response.status_code == 200:
            result = response.json()
            self._access_token = result['access_token']
            self._refresh_token = result.get('refresh_token', self._refresh_token)
            self._token_expiry = time.time() + result.get('expires_in', 3600) - 60
            self._save_tokens(
                self._access_token,
                self._refresh_token,
                result.get('expires_in', 3600)
            )
            logger.info("Token refreshed successfully")
        else:
            error = response.json()
            raise Exception(f"Token refresh failed: {error.get('error_description', error.get('error'))}")

    def _authenticate_interactive(self) -> None:
        """Perform interactive authentication via browser."""
        print("\n" + "=" * 60)
        print("OneDrive Authentifizierung")
        print("=" * 60)
        print("\nEin Browser-Fenster wird geoeffnet...")
        print("Bitte melde dich mit deinem Microsoft-Konto an.")
        print("=" * 60 + "\n")

        # Generate state for CSRF protection
        state = secrets.token_urlsafe(32)

        # Build authorization URL
        auth_params = {
            'client_id': self.client_id,
            'response_type': 'code',
            'redirect_uri': self.redirect_uri,
            'scope': ' '.join(self.scopes),
            'state': state,
            'response_mode': 'query'
        }

        auth_url = f"{self.auth_endpoint}/authorize?{urlencode(auth_params)}"

        # Reset handler state
        OAuthCallbackHandler.auth_code = None
        OAuthCallbackHandler.error = None

        # Start local server for callback
        server = HTTPServer(('localhost', self.redirect_port), OAuthCallbackHandler)
        server.timeout = 120  # 2 minute timeout

        # Open browser
        try:
            webbrowser.open(auth_url)
        except Exception:
            print(f"\nKonnte Browser nicht oeffnen. Bitte oeffne diese URL manuell:\n{auth_url}\n")

        # Wait for callback
        print("Warte auf Authentifizierung...")

        while OAuthCallbackHandler.auth_code is None and OAuthCallbackHandler.error is None:
            server.handle_request()

        server.server_close()

        if OAuthCallbackHandler.error:
            raise Exception(f"Authentication failed: {OAuthCallbackHandler.error}")

        if not OAuthCallbackHandler.auth_code:
            raise Exception("No authorization code received")

        # Exchange code for tokens
        self._exchange_code_for_tokens(OAuthCallbackHandler.auth_code)

    def _exchange_code_for_tokens(self, auth_code: str) -> None:
        """Exchange authorization code for access and refresh tokens."""
        data = {
            'client_id': self.client_id,
            'grant_type': 'authorization_code',
            'code': auth_code,
            'redirect_uri': self.redirect_uri,
            'scope': ' '.join(self.scopes)
        }

        response = requests.post(
            f"{self.auth_endpoint}/token",
            data=data
        )

        if response.status_code == 200:
            result = response.json()
            self._access_token = result['access_token']
            self._refresh_token = result.get('refresh_token')
            self._token_expiry = time.time() + result.get('expires_in', 3600) - 60

            if self._refresh_token:
                self._save_tokens(
                    self._access_token,
                    self._refresh_token,
                    result.get('expires_in', 3600)
                )

            print("\nAuthentifizierung erfolgreich!\n")
            logger.info("Authentication completed successfully")
        else:
            error = response.json()
            raise Exception(f"Token exchange failed: {error.get('error_description', error.get('error'))}")

    def get_service(self) -> str:
        """Get authenticated access token for Microsoft Graph API.

        Returns:
            Access token string
        """
        return self.authenticate()

    def get_access_token(self) -> str:
        """Get authenticated access token (alias for get_service).

        Returns:
            Access token string
        """
        return self.authenticate()

    def revoke_credentials(self) -> None:
        """Delete stored credentials."""
        self._access_token = None
        self._refresh_token = None
        self._token_expiry = None

        if self.token_file.exists():
            try:
                self.token_file.unlink()
                logger.info("Deleted token file: %s", self.token_file)
            except Exception as e:
                logger.warning("Failed to delete token file: %s", e)

    @property
    def graph_endpoint(self) -> str:
        """Return the Microsoft Graph API endpoint."""
        return self.GRAPH_API_ENDPOINT
