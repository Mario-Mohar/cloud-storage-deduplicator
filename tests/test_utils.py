"""Tests for utility functions."""

import logging
import time
from unittest.mock import Mock, patch

import pytest
from googleapiclient.errors import HttpError

from drive_dedup.utils import retry_with_backoff, format_file_size, validate_folder_id, setup_logging


class TestRetryWithBackoff:
    """Test retry_with_backoff decorator."""

    def test_successful_function_no_retry(self):
        """Test function that succeeds on first try."""
        @retry_with_backoff(max_retries=3)
        def success_func():
            return "success"

        result = success_func()
        assert result == "success"

    def test_function_succeeds_after_retries(self):
        """Test function that succeeds after some retries."""
        call_count = 0

        @retry_with_backoff(max_retries=3, base_delay=0.01)
        def flaky_func():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise Exception("Temporary error")
            return "success"

        result = flaky_func()
        assert result == "success"
        assert call_count == 3

    def test_function_fails_after_max_retries(self):
        """Test function that fails even after max retries."""
        @retry_with_backoff(max_retries=2, base_delay=0.01)
        def always_fails():
            raise Exception("Permanent error")

        with pytest.raises(Exception, match="Permanent error"):
            always_fails()

    def test_http_error_rate_limit(self):
        """Test handling of HTTP 429 rate limit errors."""
        call_count = 0

        @retry_with_backoff(max_retries=2, base_delay=0.01)
        def rate_limited_func():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                # Mock HTTP 429 error
                resp = Mock()
                resp.status = 429
                raise HttpError(resp=resp, content=b'Rate limit exceeded')
            return "success"

        result = rate_limited_func()
        assert result == "success"
        assert call_count == 3

    def test_http_error_client_error_no_retry(self):
        """Test that client errors (4xx except 429, 403) are not retried."""
        @retry_with_backoff(max_retries=3, base_delay=0.01)
        def client_error_func():
            resp = Mock()
            resp.status = 404
            raise HttpError(resp=resp, content=b'Not found')

        with pytest.raises(HttpError):
            client_error_func()

    def test_http_error_quota_exceeded(self):
        """Test handling of HTTP 403 quota exceeded errors."""
        call_count = 0

        @retry_with_backoff(max_retries=2, base_delay=0.01)
        def quota_exceeded_func():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                resp = Mock()
                resp.status = 403
                raise HttpError(resp=resp, content=b'Quota exceeded')
            return "success"

        result = quota_exceeded_func()
        assert result == "success"
        assert call_count == 3

    def test_backoff_delay_calculation(self):
        """Test that backoff delay increases correctly."""
        delays = []

        @retry_with_backoff(max_retries=3, base_delay=0.1, backoff_factor=2.0)
        def failing_func():
            delays.append(time.time())
            raise Exception("Test error")

        with pytest.raises(Exception):
            failing_func()

        # Should have 4 calls (initial + 3 retries)
        assert len(delays) == 4

        # Check that delays increase (allowing some tolerance for timing)
        time_diffs = [delays[i+1] - delays[i] for i in range(len(delays)-1)]
        assert time_diffs[0] >= 0.08  # First retry delay ~0.1s
        assert time_diffs[1] >= 0.18  # Second retry delay ~0.2s
        assert time_diffs[2] >= 0.38  # Third retry delay ~0.4s

    def test_max_delay_cap(self):
        """Test that delay is capped at max_delay."""
        delays = []

        @retry_with_backoff(max_retries=5, base_delay=10.0, max_delay=15.0, backoff_factor=2.0)
        def failing_func():
            delays.append(time.time())
            raise Exception("Test error")

        start_time = time.time()
        with pytest.raises(Exception):
            failing_func()
        total_time = time.time() - start_time

        # With max_delay=15.0, total time should be less than if delay kept growing
        # (10 + 15 + 15 + 15 + 15 = 70s vs 10 + 20 + 40 + 80 + 160 = 310s)
        assert total_time < 100  # Much less than uncapped growth


class TestFormatFileSize:
    """Test format_file_size function."""

    def test_zero_bytes(self):
        """Test formatting zero bytes."""
        assert format_file_size(0) == "0 B"

    def test_bytes(self):
        """Test formatting bytes."""
        assert format_file_size(500) == "500 B"
        assert format_file_size(1023) == "1023 B"

    def test_kilobytes(self):
        """Test formatting kilobytes."""
        assert format_file_size(1024) == "1.0 KB"
        assert format_file_size(1536) == "1.5 KB"
        assert format_file_size(2048) == "2.0 KB"

    def test_megabytes(self):
        """Test formatting megabytes."""
        assert format_file_size(1048576) == "1.0 MB"
        assert format_file_size(1572864) == "1.5 MB"

    def test_gigabytes(self):
        """Test formatting gigabytes."""
        assert format_file_size(1073741824) == "1.0 GB"
        assert format_file_size(2147483648) == "2.0 GB"

    def test_terabytes(self):
        """Test formatting terabytes."""
        assert format_file_size(1099511627776) == "1.0 TB"

    def test_large_values(self):
        """Test formatting very large values."""
        # Should cap at TB
        assert format_file_size(1125899906842624) == "1024.0 TB"


class TestValidateFolderId:
    """Test validate_folder_id function."""

    def test_valid_folder_ids(self):
        """Test valid folder ID formats."""
        valid_ids = [
            "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms",
            "1ABC123xyz",
            "0B1234567890abcdef",
            "folder-id_with-dashes",
            "a" * 33,  # 33 characters
            "a" * 44,  # 44 characters
        ]

        for folder_id in valid_ids:
            assert validate_folder_id(folder_id), f"Should be valid: {folder_id}"

    def test_invalid_folder_ids(self):
        """Test invalid folder ID formats."""
        invalid_ids = [
            "",  # Empty string
            "a",  # Too short
            "a" * 9,  # Too short
            "a" * 51,  # Too long
            "folder id with spaces",  # Contains spaces
            "folder@id",  # Invalid character
            "folder#id",  # Invalid character
            None,  # Not a string
            123,  # Not a string
        ]

        for folder_id in invalid_ids:
            assert not validate_folder_id(folder_id), f"Should be invalid: {folder_id}"

    def test_edge_cases(self):
        """Test edge cases for folder ID validation."""
        # Minimum valid length
        assert validate_folder_id("a" * 10)
        assert not validate_folder_id("a" * 9)

        # Maximum valid length
        assert validate_folder_id("a" * 50)
        assert not validate_folder_id("a" * 51)

        # Valid characters
        assert validate_folder_id("ABC123xyz-_")
        assert not validate_folder_id("ABC123xyz@")


class TestSetupLogging:
    """Test setup_logging function."""

    def test_basic_logging_setup(self):
        """Test basic logging setup."""
        with patch('logging.basicConfig') as mock_basic_config:
            setup_logging()

            mock_basic_config.assert_called_once()
            call_args = mock_basic_config.call_args
            assert call_args[1]['level'] == logging.INFO
            assert 'format' in call_args[1]
            assert 'handlers' in call_args[1]

    def test_custom_log_level(self):
        """Test setting custom log level."""
        with patch('logging.basicConfig') as mock_basic_config:
            setup_logging(log_level="DEBUG")

            call_args = mock_basic_config.call_args
            assert call_args[1]['level'] == logging.DEBUG

    def test_invalid_log_level(self):
        """An unknown level must not crash; it falls back to INFO."""
        with patch('logging.basicConfig') as mock_basic_config:
            setup_logging(log_level="INVALID")

            call_args = mock_basic_config.call_args
            assert call_args[1]['level'] == logging.INFO

    def test_file_handler_added(self):
        """Test that file handler is added when log_file is specified."""
        with patch('logging.basicConfig') as mock_basic_config, \
             patch('logging.FileHandler') as mock_file_handler, \
             patch('logging.getLogger') as mock_get_logger:

            mock_logger = Mock()
            mock_get_logger.return_value = mock_logger
            mock_handler = Mock()
            mock_file_handler.return_value = mock_handler

            setup_logging(log_file="test.log")

            mock_file_handler.assert_called_once_with("test.log")
            mock_handler.setFormatter.assert_called_once()
            mock_logger.addHandler.assert_called_once_with(mock_handler)

    def test_google_loggers_configured(self):
        """Test that Google API client loggers are configured to reduce noise."""
        with patch('logging.basicConfig'), \
             patch('logging.getLogger') as mock_get_logger:

            mock_logger = Mock()
            mock_get_logger.return_value = mock_logger

            setup_logging()

            # Should be called for the noisy Google loggers
            expected_calls = [
                'googleapiclient.discovery_cache',
                'google.auth.transport.requests'
            ]

            for logger_name in expected_calls:
                mock_get_logger.assert_any_call(logger_name)