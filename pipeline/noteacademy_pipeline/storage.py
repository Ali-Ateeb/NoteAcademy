"""Put question crops where the app can serve them from.

The crop is the display artifact — text extraction loses the diagrams, graphs
and circuit symbols that make a physics question answerable — so until the crops
are reachable, a correctly ingested question is still a blank card. This module
is the step between `question_assets.storage_key` and something a browser can
load.

Supabase Storage, over its REST API, because the project already exists and its
service role key is the only credential needed: no second account, no S3 keys to
mint, nothing new to rotate. The bucket is private and the app hands out
short-lived signed URLs. `storage_key` stays exactly what the database already
records, so nothing about the schema or the loader changes if this is swapped
for R2 or S3 later — that is what keeping the key and the URL separate buys.
"""

from __future__ import annotations

import logging
import mimetypes
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


class StorageError(RuntimeError):
    pass


@dataclass(frozen=True)
class SupabaseStorage:
    url: str
    service_role_key: str
    bucket: str

    @classmethod
    def from_settings(cls, settings) -> SupabaseStorage | None:
        """Build a client, or None when storage is not configured.

        None rather than an exception: uploading is an optional stage, and an
        ingestion run without storage configured should still load the questions
        and say what it skipped.
        """
        if not (settings.supabase_url and settings.supabase_service_role_key):
            return None
        return cls(
            url=settings.supabase_url.rstrip("/"),
            service_role_key=settings.supabase_service_role_key,
            bucket=settings.storage_bucket or "noteacademy-papers",
        )

    def _request(self, method: str, path: str, **kwargs) -> bytes:
        request = urllib.request.Request(
            f"{self.url}/storage/v1/{path}", method=method, **kwargs
        )
        request.add_header("apikey", self.service_role_key)
        request.add_header("Authorization", f"Bearer {self.service_role_key}")
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            detail = error.read().decode(errors="replace")[:300]
            raise StorageError(f"{method} {path} -> {error.code}: {detail}") from error
        except urllib.error.URLError as error:
            raise StorageError(f"{method} {path} -> {error.reason}") from error

    def upload(self, key: str, path: Path) -> None:
        """Upload one file, replacing whatever is at that key.

        Upsert, because ingestion runs are re-run constantly and a re-render at
        a different DPI is the same crop of the same question, not a new one.
        """
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self._request(
            "POST",
            f"object/{self.bucket}/{key}",
            data=path.read_bytes(),
            headers={"Content-Type": content_type, "x-upsert": "true"},
        )


def upload_crops(storage: SupabaseStorage, pairs: list[tuple[str, Path]]) -> int:
    """Upload (storage_key, local file) pairs. Returns how many were sent.

    A missing local file is skipped rather than fatal: the crops directory is
    working data, and a paper loaded on another machine will not have it.
    """
    sent = 0
    for key, path in pairs:
        if not path.is_file():
            log.warning("no local file for %s (expected %s)", key, path)
            continue
        storage.upload(key, path)
        sent += 1
    return sent
