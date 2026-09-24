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


OWN_TAGS = {"TagSet": [{"Key": "managed-by", "Value": "iceberg-portal"}, {"Key": "database", "Value": "own-db"}]}


def test_delete_includes_versions_markers_objects_and_uploads(storage):
    storage.call = Mock(
        side_effect=[
            OWN_TAGS,
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
    storage.delete_bucket("own-bucket", "own-db")
    assert storage.call.call_args_list[2].kwargs["Delete"]["Objects"] == [
        {"Key": "old", "VersionId": "v1"},
        {"Key": "old", "VersionId": "v2"},
    ]
    assert storage.call.call_args_list[-1].args == ("delete_bucket",)
    assert any(c.args == ("abort_multipart_upload",) for c in storage.call.call_args_list)
    assert all(c.kwargs["Bucket"] == "own-bucket" for c in storage.call.call_args_list)


def test_partial_object_errors_stop_deletion(storage):
    storage.call = Mock(
        side_effect=[
            OWN_TAGS,
            {"Versions": [{"Key": "locked", "VersionId": "v1"}]},
            {"Errors": [{"Code": "AccessDenied"}]},
        ]
    )
    with pytest.raises(ServiceError, match="object versions"):
        storage.delete_bucket("own-bucket", "own-db")
    assert not any(c.args == ("delete_bucket",) for c in storage.call.call_args_list)


def test_delete_refuses_a_bucket_of_another_database(storage):
    # A catalog admin can point portal.bucket at another database's bucket.
    storage.call = Mock(return_value=OWN_TAGS)
    with pytest.raises(ServiceError, match="does not belong") as exc:
        storage.delete_bucket("own-bucket", "other-db")
    assert exc.value.status == 409
    assert [c.args for c in storage.call.call_args_list] == [("get_bucket_tagging",)]


def test_delete_refuses_an_untagged_bucket_and_skips_a_missing_one(storage):
    missing = ServiceError(404, "The storage operation failed.")
    storage.call = Mock(side_effect=[missing, {}])
    with pytest.raises(ServiceError, match="does not belong"):
        storage.delete_bucket("foreign-bucket", "own-db")
    storage.call = Mock(side_effect=[missing, missing])
    storage.delete_bucket("own-bucket", "own-db")
    assert [c.args for c in storage.call.call_args_list] == [("get_bucket_tagging",), ("head_bucket",)]


def test_owner_check_reports_a_missing_bucket_and_accepts_its_own(storage):
    missing = ServiceError(404, "The storage operation failed.")
    storage.call = Mock(side_effect=[missing, missing])
    assert storage.check_bucket_owner("own-bucket", "own-db") is False
    storage.call = Mock(return_value=OWN_TAGS)
    assert storage.check_bucket_owner("own-bucket", "own-db") is True


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
