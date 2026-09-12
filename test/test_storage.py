import re
from unittest.mock import Mock

import httpx
import pytest
from botocore.stub import Stubber

from server.models import ServiceError
from server.polaris import PolarisProvider
from server.storage import RustFSStorage, bucket_name, bucket_policy


@pytest.fixture
def storage():
    storage = RustFSStorage({"RUSTFS_ACCESS_KEY": "test-key", "RUSTFS_SECRET_KEY": "test-secret"})
    yield storage
    storage.close()


def test_bucket_names_and_policy_scope():
    for name in ("analytics_dev", "analytics-dev", "a" * 48, "abc---"):
        assert re.fullmatch(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]", bucket_name(name))
    assert bucket_name("analytics_dev") != bucket_name("analytics-dev")
    statement = bucket_policy(["own-bucket"])["Statement"][0]
    assert statement["Effect"] == "Allow"
    assert statement["Resource"] == ["arn:aws:s3:::own-bucket", "arn:aws:s3:::own-bucket/*"]
    assert bucket_policy([])["Statement"][0]["Effect"] == "Deny"


def test_existing_bucket_is_never_adopted(storage):
    with Stubber(storage.s3) as stub:
        stub.add_response("head_bucket", {})
        with pytest.raises(ServiceError, match="already exists"):
            storage.create_bucket("analytics", "team-test")
        stub.assert_no_pending_responses()


def test_admin_requests_have_s3_payload_signature_and_sanitize_errors(storage):
    def handle(request):
        assert request.headers["x-amz-content-sha256"]
        assert request.headers["authorization"].startswith("AWS4-HMAC-SHA256 ")
        return httpx.Response(500, json={"secret": "must-not-leak"})

    storage.http.close()
    storage.http = httpx.Client(transport=httpx.MockTransport(handle))
    with pytest.raises(ServiceError) as exc:
        storage.set_user_buckets("test-key", [])
    assert "must-not-leak" not in str(exc.value)


def test_delete_includes_versions_markers_objects_and_uploads(storage):
    storage.call = Mock(
        side_effect=[
            {
                "Versions": [{"Key": "old", "VersionId": "v1"}],
                "DeleteMarkers": [{"Key": "old", "VersionId": "v2"}],
            },
            {},
            {},
            {"Contents": [{"Key": "current"}]},
            {},
            {},
            {"Uploads": [{"Key": "partial", "UploadId": "upload-1"}]},
            {},
            {},
            {},
        ]
    )
    storage.delete_bucket("own-bucket")
    assert storage.call.call_args_list[1].kwargs["Delete"]["Objects"] == [
        {"Key": "old", "VersionId": "v1"},
        {"Key": "old", "VersionId": "v2"},
    ]
    assert storage.call.call_args_list[-1].args == ("delete_bucket",)
    assert any(c.args == ("abort_multipart_upload",) for c in storage.call.call_args_list)
    assert all(c.kwargs["Bucket"] == "own-bucket" for c in storage.call.call_args_list)


def test_partial_object_errors_stop_deletion(storage):
    storage.call = Mock(
        side_effect=[
            {"Versions": [{"Key": "locked", "VersionId": "v1"}]},
            {"Errors": [{"Code": "AccessDenied"}]},
        ]
    )
    with pytest.raises(ServiceError, match="object versions"):
        storage.delete_bucket("own-bucket")
    assert not any(c.args == ("delete_bucket",) for c in storage.call.call_args_list)


def test_provider_errors_do_not_leak_credentials(storage):
    provider = PolarisProvider({"POLARIS_URL": "http://polaris"}, storage)
    provider.access_token = lambda: "test-token"
    provider.http.close()
    provider.http = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(500, json={"secret": "sensitive"}))
    )
    try:
        with pytest.raises(ServiceError) as exc:
            provider.management("/catalogs")
        assert exc.value.status == 502
        assert "sensitive" not in str(exc.value)
    finally:
        provider.http.close()
