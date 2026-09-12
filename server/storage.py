"""RustFS S3 and signed admin API. Policies contain only the user's team buckets."""

import json
import secrets
from urllib.parse import quote

import boto3
import httpx
from botocore.auth import S3SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.config import Config
from botocore.credentials import Credentials
from botocore.exceptions import BotoCoreError, ClientError

from .models import ServiceError


def bucket_name(database):
    return f"db-{database.replace('_', '-')[:40].rstrip('-')}-{secrets.token_hex(6)}"


def bucket_policy(buckets):
    resources = [arn for b in sorted(set(buckets)) for arn in (f"arn:aws:s3:::{b}", f"arn:aws:s3:::{b}/*")]
    # IAM policies must have a statement even when a team has no databases yet.
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow" if resources else "Deny",
                "Action": ["s3:*"],
                "Resource": resources or ["arn:aws:s3:::*", "arn:aws:s3:::*/*"],
            }
        ],
    }


class RustFSStorage:
    def __init__(self, env):
        self.endpoint = env.get("S3_ADMIN_ENDPOINT", "http://localhost:9000")
        self.public_endpoint = env.get("S3_ENDPOINT", "http://localhost:9000")
        self.region = env.get("AWS_REGION", "us-east-1")
        self.credentials = Credentials(env.get("RUSTFS_ACCESS_KEY", ""), env.get("RUSTFS_SECRET_KEY", ""))
        self.http = httpx.Client(timeout=15)
        self.s3 = boto3.client(
            "s3",
            endpoint_url=self.endpoint,
            region_name=self.region,
            aws_access_key_id=self.credentials.access_key,
            aws_secret_access_key=self.credentials.secret_key,
            config=Config(
                s3={"addressing_style": "path"},
                connect_timeout=5,
                read_timeout=15,
                retries={"max_attempts": 0},
            ),
        )

    def close(self):
        self.http.close()
        self.s3.close()

    def admin(self, path, method="GET", data=None):
        body = json.dumps(data) if data is not None else None
        request = AWSRequest(
            method=method,
            url=f"{self.endpoint}/rustfs/admin/v3/{path}",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        S3SigV4Auth(self.credentials, "s3", self.region).add_auth(request)
        try:
            response = self.http.request(method, request.url, headers=dict(request.headers), content=body)
        except httpx.HTTPError as exc:
            raise ServiceError(503, "The storage provider is unavailable.") from exc
        if response.is_error:
            raise ServiceError(
                404 if response.status_code == 404 else 502,
                f"Storage administration failed (HTTP {response.status_code}).",
            )
        return response.json() if response.content else None

    def call(self, operation, **kwargs):
        try:
            return getattr(self.s3, operation)(**kwargs)
        except ClientError as exc:
            status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            raise ServiceError(404 if status == 404 else 502, "The storage operation failed.") from exc
        except BotoCoreError as exc:
            raise ServiceError(503, "The storage provider is unavailable.") from exc

    def tag_bucket(self, bucket, database, team):
        self.call(
            "put_bucket_tagging",
            Bucket=bucket,
            Tagging={
                "TagSet": [
                    {"Key": "managed-by", "Value": "iceberg-portal"},
                    {"Key": "database", "Value": database},
                    {"Key": "team", "Value": team},
                ]
            },
        )

    def create_bucket(self, database, team):
        bucket = bucket_name(database)
        try:
            self.call("head_bucket", Bucket=bucket)
        except ServiceError as exc:
            if exc.status != 404:
                raise
        else:
            raise ServiceError(409, "The bucket name already exists. Please try again.")
        extra = (
            {}
            if self.region == "us-east-1"
            else {"CreateBucketConfiguration": {"LocationConstraint": self.region}}
        )
        self.call("create_bucket", Bucket=bucket, **extra)
        try:
            self.tag_bucket(bucket, database, team)
        except Exception:
            self.delete_empty_bucket(bucket)
            raise
        return bucket

    def delete_empty_bucket(self, bucket):
        try:
            self.call("delete_bucket", Bucket=bucket)
        except ServiceError as exc:
            if exc.status != 404:
                raise

    def delete_bucket(self, bucket):
        # Includes all object versions and delete markers, not just currently visible keys.
        try:
            while True:
                page = self.call("list_object_versions", Bucket=bucket, MaxKeys=1000)
                objects = [
                    {"Key": v["Key"], "VersionId": v["VersionId"]}
                    for v in page.get("Versions", []) + page.get("DeleteMarkers", [])
                ]
                if not objects:
                    break
                result = self.call("delete_objects", Bucket=bucket, Delete={"Objects": objects})
                if result.get("Errors"):
                    raise ServiceError(502, "Some object versions could not be deleted. Please try again.")
            while True:
                page = self.call("list_objects_v2", Bucket=bucket, MaxKeys=1000)
                objects = [{"Key": v["Key"]} for v in page.get("Contents", [])]
                if not objects:
                    break
                result = self.call("delete_objects", Bucket=bucket, Delete={"Objects": objects})
                if result.get("Errors"):
                    raise ServiceError(502, "Some objects could not be deleted. Please try again.")
            while True:
                uploads = self.call("list_multipart_uploads", Bucket=bucket).get("Uploads", [])
                if not uploads:
                    break
                for upload in uploads:
                    self.call(
                        "abort_multipart_upload",
                        Bucket=bucket,
                        Key=upload["Key"],
                        UploadId=upload["UploadId"],
                    )
        except ServiceError as exc:
            if exc.status != 404:
                raise
        self.delete_empty_bucket(bucket)

    def set_user_buckets(self, key, buckets):
        self.admin(f"add-canned-policy?name=portal-{quote(key)}", "PUT", bucket_policy(buckets))

    def create_user(self, key, buckets):
        secret = secrets.token_urlsafe(32)
        self.set_user_buckets(key, buckets)
        try:
            self.admin(f"add-user?accessKey={quote(key)}", "PUT", {"secretKey": secret, "status": "enabled"})
            self.admin("idp/builtin/policy/attach", "POST", {"policies": [f"portal-{key}"], "user": key})
        except Exception:
            self.delete_user(key)
            raise
        return {
            "accessKeyId": key,
            "secretAccessKey": secret,
            "buckets": buckets,
            "endpoint": self.public_endpoint,
        }

    def delete_user(self, key):
        for path in (f"remove-user?accessKey={quote(key)}", f"remove-canned-policy?name=portal-{quote(key)}"):
            try:
                self.admin(path, "DELETE")
            except ServiceError as exc:
                if exc.status != 404:
                    raise
