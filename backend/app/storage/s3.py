"""S3-compatible object storage (AWS S3, Cloudflare R2, Backblaze B2, Supabase Storage, MinIO, ...).
The bucket stays PRIVATE: nothing is public, and the browser never receives storage credentials. Files are only reachable through
backend endpoints that check ownership first (or a short-lived signed link created after that check)."""
import logging
import mimetypes
import os
import uuid

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from ..config import Settings
from ..security import sanitize_filename
from .base import AREAS, Storage

log = logging.getLogger("dreamcast.storage")
_MISSING = {"404", "NoSuchKey", "NotFound"}


class S3Storage(Storage):
    def __init__(self, settings: Settings):
        s = settings
        self.bucket = s.s3_bucket
        self.prefix = (s.s3_prefix.strip("/") + "/") if s.s3_prefix.strip("/") else ""
        self.signed_seconds = s.s3_signed_url_seconds
        self.client = boto3.client(
            "s3", region_name=s.s3_region or None, endpoint_url=s.s3_endpoint_url or None,
            aws_access_key_id=s.s3_access_key_id, aws_secret_access_key=s.s3_secret_access_key,
            config=Config(signature_version="s3v4", s3={"addressing_style": s.s3_addressing_style}, retries={"max_attempts": 3, "mode": "standard"},
                          connect_timeout=10, read_timeout=60))

    # --- keys: the app's opaque key ("generated/<project>/<file>") is stored in PostgreSQL; the bucket key adds the optional prefix
    def _k(self, key: str) -> str:
        if not key or key.startswith("/") or ".." in key.split("/"):
            raise ValueError("Invalid storage key")
        return f"{self.prefix}{key}"

    def _new_key(self, area: str, project_id: str, filename: str) -> str:
        if area not in AREAS:
            raise ValueError("Unknown storage area")
        return f"{area}/{project_id}/{uuid.uuid4().hex[:12]}_{sanitize_filename(filename)}"

    @staticmethod
    def _ctype(name: str) -> str:
        return mimetypes.guess_type(name)[0] or "application/octet-stream"

    def save(self, area: str, project_id: str, filename: str, data: bytes) -> str:
        key = self._new_key(area, project_id, filename)
        self.client.put_object(Bucket=self.bucket, Key=self._k(key), Body=data, ContentType=self._ctype(filename))
        return key

    def save_file(self, area: str, project_id: str, filename: str, source_path: str) -> str:
        key = self._new_key(area, project_id, filename)
        self.client.upload_file(source_path, self.bucket, self._k(key), ExtraArgs={"ContentType": self._ctype(filename)})
        try:
            os.unlink(source_path)            # same "move" behaviour as local storage: the temp file is gone once it is safely uploaded
        except OSError:
            pass
        return key

    def copy(self, key: str, area: str, project_id: str, filename: str) -> str:
        new_key = self._new_key(area, project_id, filename)
        self.client.copy_object(Bucket=self.bucket, Key=self._k(new_key), CopySource={"Bucket": self.bucket, "Key": self._k(key)})
        return new_key

    def read(self, key: str) -> bytes:
        try:
            return self.client.get_object(Bucket=self.bucket, Key=self._k(key))["Body"].read()
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in _MISSING:
                raise FileNotFoundError(key) from e
            raise

    def size(self, key: str) -> int:
        return int(self.client.head_object(Bucket=self.bucket, Key=self._k(key))["ContentLength"])

    def read_range(self, key: str, start: int, end: int) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=self._k(key), Range=f"bytes={start}-{end}")["Body"].read()

    def iter_range(self, key: str, start: int, end: int, chunk: int = 1024 * 1024):
        body = self.client.get_object(Bucket=self.bucket, Key=self._k(key), Range=f"bytes={start}-{end}")["Body"]
        try:
            yield from body.iter_chunks(chunk)
        finally:
            body.close()

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=self._k(key))
            return True
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in _MISSING:
                return False
            raise

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=self._k(key))

    def _list(self, prefix: str):
        pages = self.client.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=self._k(prefix) if prefix else self.prefix)
        for page in pages:
            yield from page.get("Contents", [])

    def delete_prefix(self, prefix: str) -> None:
        batch: list[dict] = []
        for obj in self._list(prefix.rstrip("/") + "/"):
            batch.append({"Key": obj["Key"]})
            if len(batch) == 1000:
                self.client.delete_objects(Bucket=self.bucket, Delete={"Objects": batch})
                batch = []
        if batch:
            self.client.delete_objects(Bucket=self.bucket, Delete={"Objects": batch})

    def usage_bytes(self, prefix: str = "") -> int:
        return sum(int(o["Size"]) for o in self._list(prefix.rstrip("/") + "/" if prefix else ""))

    def generate_signed_url(self, key: str, expires_seconds: int | None = None, filename: str | None = None) -> str | None:
        params = {"Bucket": self.bucket, "Key": self._k(key)}
        if filename:
            params["ResponseContentDisposition"] = f'attachment; filename="{sanitize_filename(filename)}"'
        return self.client.generate_presigned_url("get_object", Params=params, ExpiresIn=expires_seconds or self.signed_seconds)

    def ping(self) -> tuple[bool, str]:
        try:
            self.client.head_bucket(Bucket=self.bucket)
            return True, f"bucket '{self.bucket}' reachable"
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            log.warning("object storage check failed: %s", code)
            return False, {"403": "access denied: check the access key and bucket permissions", "404": "bucket not found: check the bucket name and region/endpoint",
                           "NoSuchBucket": "bucket not found: check the bucket name and region/endpoint"}.get(code, f"error {code or 'unknown'}")
        except BotoCoreError as e:
            log.warning("object storage check failed: %s", type(e).__name__)
            return False, "cannot reach the storage endpoint: check S3_ENDPOINT_URL and S3_REGION"
