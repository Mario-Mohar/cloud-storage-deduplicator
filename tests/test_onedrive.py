"""Tests for OneDrive authentication and client."""

import pytest
from unittest.mock import Mock, patch, MagicMock
import requests

from drive_dedup.onedrive_client import OneDriveClient
from drive_dedup.models import DriveFile, OperationResult


@pytest.fixture
def mock_onedrive_response():
    """Create a mock OneDrive API response."""
    return {
        'value': [
            {
                'id': 'file1',
                'name': 'document.pdf',
                'file': {
                    'mimeType': 'application/pdf',
                    'hashes': {
                        'sha1Hash': 'abc123def456'
                    }
                },
                'size': 1048576,
                'parentReference': {'id': 'parent1'},
                'createdDateTime': '2023-01-01T12:00:00Z',
                'lastModifiedDateTime': '2023-01-02T12:00:00Z'
            },
            {
                'id': 'file2',
                'name': 'spreadsheet.xlsx',
                'file': {
                    'mimeType': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    'hashes': {
                        'sha1Hash': 'xyz789abc'
                    }
                },
                'size': 2097152,
                'parentReference': {'id': 'parent1'},
                'createdDateTime': '2023-02-01T12:00:00Z',
                'lastModifiedDateTime': '2023-02-02T12:00:00Z'
            }
        ]
    }


@pytest.fixture
def mock_onedrive_folder_response():
    """Create a mock OneDrive folder response."""
    return {
        'value': [
            {
                'id': 'folder1',
                'name': 'My Folder',
                'folder': {'childCount': 5},
                'parentReference': {'id': 'root'}
            }
        ]
    }


@pytest.fixture
def onedrive_client():
    """Create an OneDrive client with mock access token."""
    with patch.object(requests.Session, 'request') as mock_request:
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {'value': []}
        mock_response.raise_for_status = Mock()
        mock_request.return_value = mock_response

        client = OneDriveClient(access_token="mock_token", max_workers=2)
        return client


class TestOneDriveClient:
    """Tests for OneDriveClient class."""

    def test_client_initialization(self):
        """Test client is initialized with correct settings."""
        client = OneDriveClient(access_token="test_token", max_workers=3)

        assert client.access_token == "test_token"
        assert client.max_workers == 3
        assert client.provider_name == "OneDrive"

    def test_parse_file_data(self, onedrive_client, mock_onedrive_response):
        """Test parsing file data from API response."""
        file_data = mock_onedrive_response['value'][0]

        drive_file = onedrive_client._parse_file_data(file_data)

        assert drive_file.id == 'file1'
        assert drive_file.name == 'document.pdf'
        assert drive_file.mime_type == 'application/pdf'
        assert drive_file.size == 1048576
        assert drive_file.md5_checksum == 'abc123def456'  # Using SHA1 as fallback
        assert drive_file.parents == ['parent1']
        assert drive_file.created_time == '2023-01-01T12:00:00Z'
        assert drive_file.modified_time == '2023-01-02T12:00:00Z'

    def test_parse_file_data_without_hash(self, onedrive_client):
        """Test parsing file data when no hash is available."""
        file_data = {
            'id': 'file_no_hash',
            'name': 'no_hash.txt',
            'file': {'mimeType': 'text/plain'},
            'size': 100
        }

        drive_file = onedrive_client._parse_file_data(file_data)

        assert drive_file.id == 'file_no_hash'
        assert drive_file.md5_checksum is None

    @patch.object(requests.Session, 'request')
    def test_create_folder(self, mock_request, onedrive_client):
        """Test folder creation."""
        mock_response = Mock()
        mock_response.status_code = 201
        mock_response.json.return_value = {'id': 'new_folder_id', 'name': '_duplicates'}
        mock_response.raise_for_status = Mock()
        mock_request.return_value = mock_response

        folder_id = onedrive_client.create_folder('_duplicates')

        assert folder_id == 'new_folder_id'

    @patch.object(requests.Session, 'request')
    def test_move_file_success(self, mock_request, onedrive_client):
        """Test successful file move."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {'id': 'file1'}
        mock_response.raise_for_status = Mock()
        mock_request.return_value = mock_response

        result = onedrive_client.move_file('file1', 'target_folder')

        assert isinstance(result, OperationResult)
        assert result.success is True
        assert result.file_id == 'file1'
        assert result.operation == 'move'

    @patch.object(requests.Session, 'request')
    def test_move_file_failure(self, mock_request, onedrive_client):
        """Test failed file move."""
        mock_response = Mock()
        mock_response.status_code = 404
        mock_response.raise_for_status.side_effect = requests.exceptions.HTTPError("Not found")
        mock_request.return_value = mock_response

        result = onedrive_client.move_file('nonexistent', 'target_folder')

        assert isinstance(result, OperationResult)
        assert result.success is False
        assert result.file_id == 'nonexistent'
        assert result.error_message is not None

    @patch.object(requests.Session, 'request')
    def test_get_folder_info_exists(self, mock_request, onedrive_client):
        """Test getting folder info when folder exists."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'id': 'folder1',
            'name': 'My Folder',
            'folder': {'childCount': 5},
            'parentReference': {'id': 'root'}
        }
        mock_response.raise_for_status = Mock()
        mock_request.return_value = mock_response

        folder_info = onedrive_client.get_folder_info('folder1')

        assert folder_info is not None
        assert folder_info['id'] == 'folder1'
        assert folder_info['name'] == 'My Folder'

    @patch.object(requests.Session, 'request')
    def test_get_folder_info_not_found(self, mock_request, onedrive_client):
        """Test getting folder info when folder doesn't exist."""
        mock_response = Mock()
        mock_response.status_code = 404
        mock_response.raise_for_status.side_effect = requests.exceptions.HTTPError(
            response=Mock(status_code=404)
        )
        mock_request.return_value = mock_response

        folder_info = onedrive_client.get_folder_info('nonexistent')

        assert folder_info is None

    @patch.object(requests.Session, 'request')
    def test_get_folder_info_is_file(self, mock_request, onedrive_client):
        """Test getting folder info when ID refers to a file."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'id': 'file1',
            'name': 'document.pdf',
            'file': {'mimeType': 'application/pdf'}
        }
        mock_response.raise_for_status = Mock()
        mock_request.return_value = mock_response

        folder_info = onedrive_client.get_folder_info('file1')

        assert folder_info is None


class TestOneDriveAuth:
    """Tests for OneDriveAuth class."""

    def test_auth_requires_client_id(self):
        """Without a client ID, initialization must fail with a clear error."""
        import pytest
        from drive_dedup.onedrive_auth import OneDriveAuth

        with pytest.raises(ValueError, match="client ID"):
            OneDriveAuth(token_file="test_token.json")

    def test_auth_initialization_with_custom_client_id(self):
        """Test auth initialization with custom client ID."""
        from drive_dedup.onedrive_auth import OneDriveAuth

        auth = OneDriveAuth(
            client_id="custom_client_id",
            token_file="test_token.json"
        )

        assert auth.client_id == "custom_client_id"

    def test_graph_endpoint(self):
        """Test graph endpoint property."""
        from drive_dedup.onedrive_auth import OneDriveAuth

        auth = OneDriveAuth(client_id="custom_client_id")

        assert auth.graph_endpoint == "https://graph.microsoft.com/v1.0"


    def test_token_validity_check(self):
        """Test token validity checking."""
        from drive_dedup.onedrive_auth import OneDriveAuth
        import time

        auth = OneDriveAuth(client_id="custom_client_id")

        # No token - should be invalid
        assert auth._is_token_valid() is False

        # Set expired token
        auth._access_token = "test_token"
        auth._token_expiry = time.time() - 100  # Expired
        assert auth._is_token_valid() is False

        # Set valid token
        auth._token_expiry = time.time() + 3600  # Valid for 1 hour
        assert auth._is_token_valid() is True


class TestOneDriveDriveFileCompatibility:
    """Tests to ensure OneDrive files work with existing DriveFile model."""

    def test_onedrive_file_comparison_key(self):
        """Test that OneDrive files can generate comparison keys."""
        file = DriveFile(
            id="onedrive_file_1",
            name="test.pdf",
            mime_type="application/pdf",
            size=1024,
            md5_checksum="sha1_hash_value",  # OneDrive uses SHA1
            created_time="2023-01-01T12:00:00Z"
        )

        key = file.get_comparison_key()
        assert key == "sha1_hash_value"

    def test_onedrive_file_fallback_hash(self):
        """Test fallback hash for OneDrive files without checksums."""
        file = DriveFile(
            id="onedrive_file_2",
            name="test.pdf",
            mime_type="application/pdf",
            size=1024,
            md5_checksum=None,
            created_time="2023-01-01T12:00:00Z"
        )

        fallback = file.get_fallback_hash()
        assert fallback is not None
        assert len(fallback) == 32  # MD5 hex length
