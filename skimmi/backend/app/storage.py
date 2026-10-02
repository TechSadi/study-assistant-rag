"""Where the original uploaded files are kept.

* "supabase" (production): a private Supabase Storage bucket, called from the
  backend with the service role key through Storage's REST API. The bucket has
  no public URLs and no access policies, so browsers can't read it directly;
  files are only reached through the API, after an ownership check.
* "local" (tests, offline development): files under config.UPLOAD_DIR.

Objects are stored at `<user id>/<document id>.<extension>`: both parts are
UUIDs generated or verified by the backend, so the user's file name never ends
up in a path.
"""

import logging
from functools import lru_cache
from pathlib import Path

import httpx

from . import config, database, extract

log = logging.getLogger("skimmi.storage")

CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".epub": "application/epub+zip",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
}


class StorageError(Exception):
    """The file store couldn't be reached or refused the request. The API answers 503."""


class NotStored(StorageError):
    """There is no stored file at that path."""


def object_path(user_id, doc_id, extension):
    user_id, doc_id = database.valid_id(user_id), database.valid_id(doc_id)
    if not user_id or not doc_id or extension not in extract.FILE_TYPES:
        raise ValueError("Invalid storage path")
    return f"{user_id}/{doc_id}{extension}"


class LocalStorage:
    def __init__(self, root):
        self.root = Path(root)

    def _file(self, path):
        file = (self.root / path).resolve()
        if self.root.resolve() not in file.parents:
            raise ValueError("Invalid storage path")
        return file

    def put(self, path, data, content_type):
        file = self._file(path)
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(data)

    def get(self, path):
        try:
            return self._file(path).read_bytes()
        except FileNotFoundError:
            raise NotStored(path)

    def delete(self, path):
        self._file(path).unlink(missing_ok=True)

    def ensure_ready(self):
        self.root.mkdir(parents=True, exist_ok=True)


class SupabaseStorage:
    def __init__(self, url, service_key, bucket):
        self.bucket = bucket
        # New-style secret keys (sb_secret_...) go only in `apikey`; the legacy
        # service_role key is a JWT and is also sent as the bearer token.
        headers = {"apikey": service_key}
        if service_key.startswith("eyJ"):
            headers["Authorization"] = f"Bearer {service_key}"
        self.client = httpx.Client(
            base_url=f"{url}/storage/v1",
            headers=headers,
            timeout=config.HTTP_TIMEOUT,
            transport=httpx.HTTPTransport(retries=2),  # retries failed connections only
        )

    def _request(self, method, url, **kwargs):
        try:
            response = self.client.request(method, url, **kwargs)
        except httpx.HTTPError as e:
            log.warning("Supabase Storage request failed: %s", type(e).__name__)
            raise StorageError() from e
        if response.status_code == 404 or (
            response.status_code == 400 and "not_found" in response.text.lower().replace(" ", "_")
        ):
            raise NotStored(url)
        if response.status_code >= 400:
            # The body may explain (bad key, bucket missing); log it, don't show it.
            log.warning("Supabase Storage %s %s -> %s %s", method, url.split("/")[1], response.status_code,
                        response.text[:200])
            raise StorageError()
        return response

    def put(self, path, data, content_type):
        self._request(
            "POST", f"/object/{self.bucket}/{path}", content=data,
            headers={"Content-Type": content_type, "x-upsert": "false"},
        )

    def get(self, path):
        return self._request("GET", f"/object/{self.bucket}/{path}").content

    def delete(self, path):
        try:
            self._request("DELETE", f"/object/{self.bucket}/{path}")
        except NotStored:
            pass

    def ensure_ready(self):
        """Create the private bucket on first start; warn loudly if it is public."""
        try:
            bucket = self._request("GET", f"/bucket/{self.bucket}").json()
        except NotStored:
            self._request("POST", "/bucket", json={
                "id": self.bucket,
                "name": self.bucket,
                "public": False,
                "file_size_limit": config.MAX_UPLOAD_MB * 1024 * 1024,
            })
            log.info("Created the private storage bucket %s", self.bucket)
            return
        if bucket.get("public"):
            log.error("Storage bucket %s is PUBLIC. Make it private in the Supabase dashboard.", self.bucket)


@lru_cache(maxsize=1)
def get_storage():
    if config.STORAGE_BACKEND == "local":
        return LocalStorage(config.UPLOAD_DIR)
    if config.STORAGE_BACKEND != "supabase":
        raise RuntimeError(f"Unknown SKIMMI_STORAGE_BACKEND: {config.STORAGE_BACKEND}")
    if not (config.SUPABASE_URL and config.SUPABASE_SERVICE_ROLE_KEY):
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set for Supabase Storage "
            "(or set SKIMMI_STORAGE_BACKEND=local for offline development)."
        )
    return SupabaseStorage(config.SUPABASE_URL, config.SUPABASE_SERVICE_ROLE_KEY, config.STORAGE_BUCKET)
