"""Tests for the Google Drive client."""

import threading
from unittest.mock import Mock, patch

import pytest
from googleapiclient.errors import HttpError

from drive_dedup import utils as utils_module
from drive_dedup.drive_client import GoogleDriveClient


def test_listing_only_asks_for_owned_files(mock_google_drive_service):
    client = GoogleDriveClient(mock_google_drive_service)

    client.list_files_page()

    query = mock_google_drive_service.files.return_value.list.call_args.kwargs["q"]
    assert "trashed=false" in query
    assert "'me' in owners" in query


class FakeRequest:
    """Stands in for a googleapiclient request: execute() runs `action`."""

    def __init__(self, action):
        self._action = action

    def execute(self):
        return self._action()


class FakeFiles:
    """The files() resource of one fake service, recording who calls it."""

    def __init__(self, service):
        self._service = service

    def get(self, fileId, fields):
        return FakeRequest(lambda: self._service.on_get(fileId))

    def update(self, fileId, addParents, removeParents):
        return FakeRequest(
            lambda: self._service.on_update(fileId, addParents, removeParents)
        )


class FakeService:
    """A Drive service whose calls note the thread they came from.

    A real service wraps one httplib2.Http, which breaks when two threads use
    it at once, so a service touched from two threads is the bug.
    """

    def __init__(self, fail_updates=0, fail_with=None):
        self.threads = set()
        self.updates = []
        self._fail_updates = fail_updates
        self._fail_with = fail_with
        self._lock = threading.Lock()

    def files(self):
        with self._lock:
            self.threads.add(threading.get_ident())
        return FakeFiles(self)

    def on_get(self, file_id):
        return {'parents': ['OLD']}

    def on_update(self, file_id, add, remove):
        with self._lock:
            if self._fail_updates:
                self._fail_updates -= 1
                raise self._fail_with()
            self.updates.append((file_id, add, remove))
        return {}


def http_error(status):
    resp = Mock()
    resp.status = status
    resp.reason = "error"
    return HttpError(resp=resp, content=b'{}')


class TestMoveRetries:
    """Issue #12: a rate limit is retried, not turned straight into a failure."""

    @pytest.mark.parametrize("status", [429, 403])
    def test_rate_limited_move_is_retried(self, status):
        service = FakeService(fail_updates=2, fail_with=lambda: http_error(status))
        client = GoogleDriveClient(service)

        with patch.object(utils_module.time, "sleep") as sleep:
            result = client.move_file("f1", "DUPS")

        assert result.success is True
        assert service.updates == [("f1", "DUPS", "OLD")]
        assert [c.args[0] for c in sleep.call_args_list] == [1.0, 2.0]

    def test_gives_up_with_a_failed_result_after_the_retries(self):
        service = FakeService(fail_updates=99, fail_with=lambda: http_error(429))
        client = GoogleDriveClient(service)

        with patch.object(utils_module.time, "sleep") as sleep:
            result = client.move_file("f1", "DUPS")

        assert result.success is False
        assert "f1" in result.error_message
        assert sleep.call_count == 3

    def test_not_found_is_not_retried(self):
        service = FakeService(fail_updates=99, fail_with=lambda: http_error(404))
        client = GoogleDriveClient(service)

        with patch.object(utils_module.time, "sleep") as sleep:
            result = client.move_file("f1", "DUPS")

        assert result.success is False
        sleep.assert_not_called()

