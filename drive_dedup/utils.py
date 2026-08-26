"""Utility functions for the Google Drive deduplicator."""

import logging
import time
from functools import wraps
from typing import Any, Callable, TypeVar

from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)

F = TypeVar('F', bound=Callable[..., Any])


def retry_with_backoff(
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    backoff_factor: float = 2.0
) -> Callable[[F], F]:
    """Decorator for retrying functions with exponential backoff.

    Args:
        max_retries: Maximum number of retry attempts
        base_delay: Initial delay in seconds
        max_delay: Maximum delay in seconds
        backoff_factor: Multiplier for delay between retries

    Returns:
        Decorated function with retry logic
    """
    def decorator(func: F) -> F:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            delay = base_delay
            last_exception = None

            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except HttpError as e:
                    last_exception = e
                    status_code = e.resp.status

                    # Don't retry on client errors (4xx) except rate limits
                    if 400 <= status_code < 500 and status_code not in (429, 403):
                        logger.error(
                            "Client error %d in %s, not retrying: %s",
                            status_code, func.__name__, e
                        )
                        raise

                    if attempt < max_retries:
                        if status_code == 429:
                            logger.warning(
                                "Rate limit hit in %s (attempt %d/%d), "
                                "waiting %.1f seconds",
                                func.__name__, attempt + 1, max_retries + 1, delay
                            )
                        elif status_code == 403:
                            logger.warning(
                                "Quota exceeded in %s (attempt %d/%d), "
                                "waiting %.1f seconds",
                                func.__name__, attempt + 1, max_retries + 1, delay
                            )
                        else:
                            logger.warning(
                                "HTTP error %d in %s (attempt %d/%d), "
                                "waiting %.1f seconds",
                                status_code, func.__name__, attempt + 1,
                                max_retries + 1, delay
                            )

                        time.sleep(delay)
                        delay = min(delay * backoff_factor, max_delay)
                    else:
                        logger.error(
                            "Max retries exceeded in %s: %s",
                            func.__name__, e
                        )
                        raise
                except Exception as e:
                    last_exception = e
                    if attempt < max_retries:
                        logger.warning(
                            "Error in %s (attempt %d/%d), waiting %.1f seconds: %s",
                            func.__name__, attempt + 1, max_retries + 1, delay, e
                        )
                        time.sleep(delay)
                        delay = min(delay * backoff_factor, max_delay)
                    else:
                        logger.error("Max retries exceeded in %s: %s", func.__name__, e)
                        raise

            # This should never be reached, but just in case
            if last_exception:
                raise last_exception

        return wrapper  # type: ignore
    return decorator


def format_file_size(size_bytes: int) -> str:
    """Format file size in human-readable format.

    Args:
        size_bytes: Size in bytes

    Returns:
        Formatted size string (e.g., "1.2 MB")
    """
    if size_bytes == 0:
        return "0 B"

    units = ["B", "KB", "MB", "GB", "TB"]
    unit_index = 0
    size = float(size_bytes)

    while size >= 1024 and unit_index < len(units) - 1:
        size /= 1024
        unit_index += 1

    if unit_index == 0:
        return f"{int(size)} {units[unit_index]}"
    else:
        return f"{size:.1f} {units[unit_index]}"


def validate_folder_id(folder_id: str) -> bool:
    """Validate Google Drive folder ID format.

    Args:
        folder_id: Folder ID to validate

    Returns:
        True if format appears valid
    """
    # Google Drive IDs are typically 33-44 characters long and alphanumeric
    return (
        isinstance(folder_id, str) and
        10 <= len(folder_id) <= 50 and
        folder_id.replace('-', '').replace('_', '').isalnum()
    )


def setup_logging(
    log_level: str = "INFO",
    log_file: str = None,
    format_string: str = None
) -> None:
    """Set up logging configuration.

    Args:
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR).
            Unknown values fall back to INFO.
        log_file: Optional log file path
        format_string: Optional custom format string
    """
    if format_string is None:
        format_string = (
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )

    resolved_level = getattr(logging, log_level.upper(), None)
    if not isinstance(resolved_level, int):
        resolved_level = logging.INFO

    logging.basicConfig(
        level=resolved_level,
        format=format_string,
        handlers=[
            logging.StreamHandler(),
        ]
    )

    if log_file:
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(logging.Formatter(format_string))
        logging.getLogger().addHandler(file_handler)

    # Reduce noise from Google API client
    logging.getLogger('googleapiclient.discovery_cache').setLevel(logging.ERROR)
    logging.getLogger('google.auth.transport.requests').setLevel(logging.WARNING)