"""Pure YouTube Data API v3 contracts: settings, requests, resources, quota estimates, errors and watch state.

No network, filesystem, framework or third-party import happens here. Every
function is deterministic, so the policy can be checked directly by the tests.
"""

from collections.abc import Mapping
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import math
import mimetypes
import re
from urllib.parse import parse_qs, urlsplit

OFFICIAL_API = "https://www.googleapis.com"
OFFICIAL_OAUTH = "https://oauth2.googleapis.com"
API_ROOT = "/youtube/v3"
UPLOAD_PATH = "/upload/youtube/v3/videos"
WATCH_URL = "https://www.youtube.com/watch?v="
OPERATIONS = ("search", "video_details", "channel_uploads", "playlist_items", "comments", "watch_uploads",
              "playlist_add", "playlist_remove", "upload_video", "update_video")
READS = ("search", "video_details", "channel_uploads", "playlist_items", "comments", "watch_uploads")
WRITES = frozenset(("playlist_add", "upload_video", "update_video"))
DELETES = frozenset(("playlist_remove",))
OAUTH_ONLY = WRITES | DELETES
LISTINGS = ("search", "channel_uploads", "playlist_items", "comments")
CHANNEL_OPERATIONS = frozenset(("channel_uploads", "watch_uploads"))
# Estimated quota units per request, from the YouTube Data API v3 quota cost table.
COSTS = {"search.list": 100, "videos.list": 1, "channels.list": 1, "playlistItems.list": 1,
         "commentThreads.list": 1, "playlistItems.insert": 50, "playlistItems.delete": 50,
         "videos.insert": 1600, "videos.update": 50}
PRIVACY = ("private", "unlisted", "public")
SEARCH_TYPES = ("video", "channel", "playlist")
SEARCH_ORDERS = ("relevance", "date", "rating", "title", "viewCount", "videoCount")
COMMENT_ORDERS = ("time", "relevance")
SAFE_SEARCH = ("moderate", "none", "strict")
READ_CREDENTIALS = ("api_key", "oauth")
CHUNK_UNIT = 262144
UPLOAD_LIMIT = 274877906944  # YouTube accepts files up to 256 GB.
OUTPUT_LIMIT = 16777216
SEEN_LIMIT = 1000
WATCH_PAGES = 4
WATCH_PAGE_SIZE = 50
TITLE_LIMIT = 100
DESCRIPTION_BYTES = 5000
TAGS_LIMIT = 500
YOUTUBE_HOSTS = frozenset(("youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"))

DEFAULTS = {
    "enabled": False, "api_key_ref": "", "secret_ref": "", "read_credential": "api_key",
    "operation": "search", "allowed_operations": list(READS), "channel": "",
    "allow_writes": False, "allow_delete": False,
    "upload_root": "", "max_upload_bytes": 268435456, "default_privacy": "private", "default_category_id": "22",
    "made_for_kids": False, "notify_subscribers": False,
    "list_limit": 50, "page_size": 50, "max_pages": 5, "search_max_pages": 1,
    "watch_enabled": False, "watch_interval_sec": 900, "watch_max_items": 20, "emit_existing": False,
    "timeout_sec": 120, "upload_timeout_sec": 3600, "upload_chunk_bytes": 8388608,
    "max_request_bytes": 65536, "max_response_bytes": 4194304, "max_retries": 2, "max_retry_wait_sec": 20,
    "api_base_url": OFFICIAL_API, "oauth_base_url": OFFICIAL_OAUTH, "ca_file": "",
}
FRAMEWORK_KEYS = {"execution", "runtime_path", "runtime_path_label"}
BOUNDS = {
    "max_upload_bytes": (1048576, UPLOAD_LIMIT), "list_limit": (1, 1000), "page_size": (1, 50),
    "max_pages": (1, 50), "search_max_pages": (1, 10), "watch_interval_sec": (300, 86400),
    "watch_max_items": (1, 50), "timeout_sec": (5, 900), "upload_timeout_sec": (60, 43200),
    "upload_chunk_bytes": (CHUNK_UNIT, 268435456), "max_request_bytes": (1024, 1048576),
    "max_response_bytes": (65536, 33554432), "max_retries": (0, 5), "max_retry_wait_sec": (1, 120),
}
CHOICES = {"read_credential": READ_CREDENTIALS, "default_privacy": PRIVACY}
SECRET_REF = re.compile(r"secret://(?:workspace/[A-Za-z0-9_.-]{1,80}|project/[A-Za-z0-9_.-]{1,80}/[A-Za-z0-9_.-]{1,80})\Z")
VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}\Z")
CHANNEL_ID = re.compile(r"UC[A-Za-z0-9_-]{22}\Z")
HANDLE = re.compile(r"@[\w.-]{3,30}\Z")
PLAYLIST_ID = re.compile(r"[A-Za-z0-9_-]{2,64}\Z")
ITEM_ID = re.compile(r"[A-Za-z0-9_=-]{8,256}\Z")
RESOURCE_ID = re.compile(r"[A-Za-z0-9_.=-]{1,256}\Z")
CATEGORY_ID = re.compile(r"[0-9]{1,3}\Z")
REGION = re.compile(r"[A-Z]{2}\Z")
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
TOKEN_TEXT = re.compile(r"[\x21-\x7e]{1,8192}\Z")
KEY_TEXT = re.compile(r"[\x21-\x7e]{8,512}\Z")
PAGE_TOKEN = re.compile(r"[\x21-\x7e]{1,4096}\Z")
HOST_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
DATE_ONLY = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
DATE_TIME = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?(?:Z|[+-][0-9]{2}:[0-9]{2})\Z")
DURATION = re.compile(r"P(?:([0-9]{1,6})W)?(?:([0-9]{1,6})D)?(?:T(?:([0-9]{1,6})H)?(?:([0-9]{1,6})M)?(?:([0-9]{1,9})S)?)?\Z")
FIELDS = {"operation", "request_id"}
PAGED = {"limit", "page_token"}
REQUEST_KEYS = {
    "search": FIELDS | PAGED | {"q", "type", "order", "published_after", "published_before", "channel_id",
                                "region_code", "safe_search"},
    "video_details": FIELDS | {"video_ids", "video_id"},
    "channel_uploads": FIELDS | PAGED | {"channel"},
    "playlist_items": FIELDS | PAGED | {"playlist_id"},
    "comments": FIELDS | PAGED | {"video_id", "order", "search_terms"},
    "watch_uploads": FIELDS | {"channel"},
    "playlist_add": FIELDS | {"playlist_id", "video_id", "position"},
    "playlist_remove": FIELDS | {"playlist_item_id", "playlist_id", "video_id"},
    "upload_video": FIELDS | {"path", "title", "description", "tags", "category_id", "privacy", "made_for_kids",
                              "notify_subscribers"},
    "update_video": FIELDS | {"video_id", "title", "description", "tags", "category_id", "privacy"},
}
UPDATABLE = ("title", "description", "tags", "category_id", "privacy")
REASONS = {"rateLimitExceeded": "rate_limited", "userRateLimitExceeded": "rate_limited",
           "quotaExceeded": "quota_exceeded", "dailyLimitExceeded": "quota_exceeded",
           "commentsDisabled": "comments_disabled", "uploadLimitExceeded": "upload_limit", "keyInvalid": "key_invalid"}
DETAIL_REASONS = {"API_KEY_INVALID": "key_invalid", "RATE_LIMIT_EXCEEDED": "rate_limited"}

MESSAGES = {
    "config": "Invalid YouTube settings.",
    "disabled": "Remote access is disabled; enable the block explicitly before contacting YouTube.",
    "secret": "Unlock the wallet and store the API key under api_key_ref, or the OAuth grant (an access token, or "
              "client_id/client_secret/refresh_token JSON) under secret_ref.",
    "api_key_required": "This read uses the API key, but api_key_ref is empty; nothing was sent.",
    "oauth_required": "This operation needs the OAuth grant (owner access), but secret_ref is empty; nothing was sent.",
    "invalid_input": "The request does not follow the documented contract; nothing was sent.",
    "request_too_large": "The request exceeds max_request_bytes; nothing was sent.",
    "operation_not_allowed": "This operation is not listed in allowed_operations; nothing was sent.",
    "writes_disabled": "Changes are disabled; enable allow_writes in the properties; nothing was uploaded or changed.",
    "delete_disabled": "Removing playlist items is disabled; enable allow_delete in the properties; nothing was removed.",
    "upload_root_required": "Set upload_root, the local folder videos may be uploaded from; nothing was sent.",
    "path": "The local file cannot be read as a regular file; nothing was sent.",
    "path_denied": "The local file is outside upload_root; nothing was sent.",
    "file_too_large": "The file is larger than max_upload_bytes; nothing was uploaded.",
    "file_changed": "The local file changed while it was uploaded; the upload was abandoned and no video was created.",
    "storage": "Block storage is unavailable; watch_uploads and the watch need a project-scoped run.",
    "busy": "Another watch of this node holds its storage; try again after the current check.",
    "state_invalid": "The stored watch state is unreadable; nothing was emitted. Remove youtube-watch.json from the node "
                     "storage to restart.",
    "not_found": "The video, channel, playlist or playlist item was not found (HTTP 404), or is not visible to this "
                 "credential.",
    "comments_disabled": "Comments are disabled for this video (HTTP 403 commentsDisabled).",
    "key_invalid": "Google refused the API key; check the wallet value and the key restrictions.",
    "auth_failed": "Google refused to issue an access token from the wallet grant; check the refresh token and its scopes.",
    "bad_request": "YouTube rejected the request (HTTP 400); nothing was changed.",
    "unauthorized": "YouTube rejected the credential (HTTP 401); refresh or replace the wallet secret.",
    "forbidden": "The credential has no access to this resource, lacks a YouTube scope, or the API is not enabled (HTTP 403).",
    "rate_limited": "YouTube rate limit reached (HTTP 429 or 403 rate limit); retry after the indicated delay.",
    "quota_exceeded": "The daily YouTube Data API quota of this Google Cloud project is exhausted; it resets at "
                      "midnight Pacific Time. Nothing was changed.",
    "upload_limit": "The channel reached its YouTube upload limit; nothing was uploaded.",
    "upload_expired": "The upload session expired or was refused before completion; no video was created.",
    "provider_error": "YouTube returned an unexpected error status; nothing is assumed changed.",
    "provider_unavailable": "YouTube is temporarily unavailable (HTTP 5xx); nothing was changed.",
    "provider_unavailable_uncertain": "YouTube answered HTTP 5xx after the change was sent; the change is uncertain.",
    "network": "The connection to Google failed before the request was complete; nothing was changed.",
    "network_uncertain": "The connection failed after the change was sent; the change is uncertain.",
    "tls": "TLS verification failed for the Google API origin; nothing was sent.",
    "timeout": "The operation exceeded its deadline before any change was complete; nothing was changed.",
    "timeout_uncertain": "The operation exceeded its deadline after the change was sent; the change is uncertain.",
    "cancelled": "The operation was cancelled after the change was sent; the change is uncertain.",
    "cancelled_before_send": "The operation was cancelled before any change was complete; nothing was changed.",
    "protocol": "Google returned an invalid, compressed or oversized response.",
    "internal": "The YouTube operation failed without a complete result.",
}


class YouTubeError(ValueError):
    """A content-free diagnostic with a stable machine code and an English message."""

    def __init__(self, code, detail=None, *, uncertain=False, http_status=None, retry_after=None,
                 provider_status=None, provider_reason=None, provider_message=None, retryable=False):
        super().__init__(detail or MESSAGES.get(code, MESSAGES["internal"]))
        self.code, self.uncertain, self.retryable = code, bool(uncertain), bool(retryable)
        self.http_status, self.retry_after = http_status, retry_after
        self.provider_status, self.provider_reason, self.provider_message = provider_status, provider_reason, provider_message
        self.attempts = 0

    def envelope(self, **context):
        """Return the public error document; callers add identifiers only, never content or credentials."""
        result = {"code": self.code, "message": str(self), "uncertain": self.uncertain}
        for key, value in (("http_status", self.http_status), ("retry_after", self.retry_after),
                           ("provider_status", self.provider_status), ("provider_reason", self.provider_reason),
                           ("provider_message", self.provider_message)):
            if value is not None:
                result[key] = value
        if self.attempts:
            result["attempts"] = self.attempts
        for key, value in context.items():
            if value is not None:
                result[key] = value
        return result


def fail(code, detail=None, **extra):
    """Raise a structured failure."""
    raise YouTubeError(code, detail, **extra)


def thaw(value):
    """Turn immutable preparation snapshots back into plain JSON containers."""
    if isinstance(value, Mapping):
        return {key: thaw(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [thaw(item) for item in value]
    return value


def encoded(value, limit, code="invalid_input"):
    """Serialize strict finite JSON with bounded depth, element count and bytes."""
    pending, count = [(value, 0)], 0
    while pending:
        item, depth = pending.pop()
        count += 1
        if depth > 24 or count > 200000:
            fail(code, "JSON exceeds its depth or element limit.")
        if isinstance(item, dict):
            if any(type(key) is not str for key in item):
                fail(code, "JSON object keys must be strings.")
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, (list, tuple)):
            pending.extend((child, depth + 1) for child in item)
        elif item is not None and type(item) not in (str, int, float, bool):
            fail(code, "A strict JSON value is required.")
        elif type(item) is float and not math.isfinite(item):
            fail(code, "Non-finite numbers are not accepted.")
    try:
        data = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        fail(code, "Finite UTF-8 JSON is required.")
    if len(data) > limit:
        fail("request_too_large" if code == "invalid_input" else code, "JSON exceeds its byte limit.")
    return data


def strict_json(text, limit, code="invalid_input"):
    """Parse JSON text, rejecting duplicate keys, non-finite numbers and oversized input."""
    if isinstance(text, bytes):
        try:
            text = text.decode("utf-8", errors="strict")
        except UnicodeError:
            fail(code, "UTF-8 JSON is required.")
    if not isinstance(text, str):
        fail(code, "JSON text is required.")
    if len(text.encode("utf-8", errors="replace")) > limit:
        fail("request_too_large" if code == "invalid_input" else code, "JSON exceeds its byte limit.")

    def pairs(items):
        result = {}
        for key, item in items:
            if key in result:
                fail(code, "Duplicate JSON property.")
            result[key] = item
        return result

    def constant(_name):
        fail(code, "Non-finite numbers are not accepted.")

    try:
        value = json.loads(text, object_pairs_hook=pairs, parse_constant=constant)
    except YouTubeError:
        raise
    except (ValueError, RecursionError):
        fail(code, "Malformed JSON.")
    encoded(value, limit, code)
    return value


def boolean(value, name, code="config"):
    if type(value) is not bool:
        fail(code, f"{name} must be a boolean.")
    return value


def integer(value, name, low, high, code="config"):
    if type(value) is not int or not low <= value <= high:
        fail(code, f"{name} must be an integer between {low} and {high}.")
    return value


def path_text(value, name):
    if not isinstance(value, str) or len(value) > 4096 or any(ord(char) < 32 or ord(char) == 127 for char in value):
        fail("config", f"{name} must be a bounded path without control characters.")
    value = value.strip()
    if value and (not value.startswith("/") or ".." in value.split("/")):
        fail("config", f"{name} must be empty or an absolute path without '..' components.")
    return value


def is_loopback(host):
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def api_origin(value, name):
    """Accept an HTTPS origin; plain HTTP only for a loopback host such as a local test double."""
    if not isinstance(value, str) or not 1 <= len(value) <= 2048 or any(ord(char) <= 32 or ord(char) >= 127 for char in value):
        fail("config", f"{name} must be an ASCII URL without spaces.")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        fail("config", f"{name} has an invalid host or port.")
    host = (parsed.hostname or "").lower()
    if (parsed.scheme not in ("https", "http") or not host or parsed.username is not None
            or parsed.password is not None or parsed.query or parsed.fragment or "?" in value or "#" in value
            or parsed.path not in ("", "/") or "\\" in value or "%" in host):
        fail("config", f"{name} must be a bare https:// origin without credentials, path, query or fragment.")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if len(host) > 253 or host.endswith(".") or any(not HOST_LABEL.fullmatch(label) for label in host.split(".")):
            fail("config", f"{name} has an invalid host name.")
    if port is not None and not 1 <= port <= 65535:
        fail("config", f"{name} has an invalid port.")
    if parsed.scheme == "http" and not is_loopback(host):
        fail("config", f"Plain HTTP is accepted for {name} only with a loopback test host; use HTTPS.")
    netloc = "[" + host + "]" if ":" in host else host
    return parsed.scheme + "://" + netloc + (":" + str(port) if port is not None else "")


def wallet_ref(value, name):
    if not isinstance(value, str) or len(value) > 256:
        fail("config", f"{name} must be a wallet reference such as secret://workspace/youtube.")
    value = value.strip()
    if value and not SECRET_REF.fullmatch(value):
        fail("config", f"{name} must be a wallet reference such as secret://workspace/youtube, never a key or token.")
    return value


def operations_list(value):
    if (not isinstance(value, list) or not 1 <= len(value) <= len(OPERATIONS)
            or any(item not in OPERATIONS for item in value) or len(set(value)) != len(value)):
        fail("config", "allowed_operations must list distinct values among " + ", ".join(OPERATIONS) + ".")
    return value


def configuration(raw):
    """Pure settings validation; no wallet, filesystem or network access."""
    raw = thaw(raw)
    if not isinstance(raw, Mapping):
        fail("config", "Settings must be a JSON object.")
    unknown = set(raw) - set(DEFAULTS) - FRAMEWORK_KEYS
    if unknown:
        fail("config", "Unknown setting: " + sorted(unknown)[0][:64] + ". Credentials belong in the wallet.")
    config = {**{key: thaw(value) for key, value in DEFAULTS.items()},
              **{key: value for key, value in raw.items() if key in DEFAULTS}}
    for key in ("enabled", "allow_writes", "allow_delete", "made_for_kids", "notify_subscribers", "watch_enabled",
                "emit_existing"):
        boolean(config[key], key)
    for key, (low, high) in BOUNDS.items():
        integer(config[key], key, low, high)
    for key, allowed in CHOICES.items():
        if config[key] not in allowed:
            fail("config", f"{key} must be one of: " + ", ".join(allowed) + ".")
    if config["upload_chunk_bytes"] % CHUNK_UNIT:
        fail("config", "upload_chunk_bytes must be a multiple of 262144 bytes (256 KiB).")
    config["api_key_ref"] = wallet_ref(config["api_key_ref"], "api_key_ref")
    config["secret_ref"] = wallet_ref(config["secret_ref"], "secret_ref")
    if not isinstance(config["default_category_id"], str) or not CATEGORY_ID.fullmatch(config["default_category_id"].strip()):
        fail("config", "default_category_id must be a YouTube video category ID such as 22.")
    config["default_category_id"] = config["default_category_id"].strip()
    if config["operation"] not in OPERATIONS:
        fail("config", "operation must be one of: " + ", ".join(OPERATIONS) + ".")
    config["allowed_operations"] = operations_list(config["allowed_operations"])
    if not isinstance(config["channel"], str) or len(config["channel"]) > 2048:
        fail("config", "channel must be a channel ID (UC...), an @handle, a channel URL or mine.")
    config["channel"] = channel_ref(config["channel"], "channel", "config") if config["channel"].strip() else ""
    config["upload_root"] = path_text(config["upload_root"], "upload_root")
    config["ca_file"] = path_text(config["ca_file"], "ca_file")
    config["api_base_url"] = api_origin(config["api_base_url"], "api_base_url")
    config["oauth_base_url"] = api_origin(config["oauth_base_url"], "oauth_base_url")
    selectable = set(config["allowed_operations"]) | {config["operation"]}
    if selectable & WRITES and not config["allow_writes"]:
        fail("config", "playlist_add, upload_video and update_video need allow_writes; enable it or remove them.")
    if selectable & DELETES and not config["allow_delete"]:
        fail("config", "playlist_remove needs allow_delete; enable it or remove it from the operations.")
    if config["watch_enabled"] and not config["channel"]:
        fail("config", "The watch needs channel, the channel whose new uploads are watched.")
    if config["enabled"]:
        # Channel reads of "mine" always use the OAuth grant; other reads use read_credential.
        plain_reads = bool(selectable & (set(READS) - CHANNEL_OPERATIONS))
        channel_reads = bool(selectable & CHANNEL_OPERATIONS) or config["watch_enabled"]
        mine = channel_reads and config["channel"] == "mine"
        reads = plain_reads or (channel_reads and not mine)
        if reads and config["read_credential"] == "api_key" and not config["api_key_ref"]:
            fail("config", "Reads use the API key: set api_key_ref, or choose read_credential oauth.")
        if (selectable & OAUTH_ONLY or (reads and config["read_credential"] == "oauth") or mine) \
                and not config["secret_ref"]:
            fail("config", "Owner operations, OAuth reads and channel mine need secret_ref, the wallet OAuth grant.")
        if "upload_video" in selectable and not config["upload_root"]:
            fail("config", "upload_video needs upload_root, the local folder videos may be uploaded from.")
    return config


def selectable_operations(config):
    return [name for name in OPERATIONS if name == config["operation"] or name in config["allowed_operations"]]


def gate(operation, config):
    """The allowlist and the two change switches, checked before the wallet is read."""
    if operation not in selectable_operations(config):
        fail("operation_not_allowed")
    if operation in WRITES and not config["allow_writes"]:
        fail("writes_disabled")
    if operation in DELETES and not config["allow_delete"]:
        fail("delete_disabled")


def credential_kind(request, config):
    """api_key or oauth: owner operations and channel mine always use the OAuth grant."""
    if request["operation"] in OAUTH_ONLY or request.get("channel") == "mine":
        kind = "oauth"
    else:
        kind = config["read_credential"]
    if kind == "oauth" and not config["secret_ref"]:
        fail("oauth_required")
    if kind == "api_key" and not config["api_key_ref"]:
        fail("api_key_required")
    return kind


def credentials(value):
    """Validate the OAuth wallet value: a bare access token, {access_token} or a refresh-token grant."""
    secret = value
    if isinstance(secret, str):
        text = secret.strip()
        if text.startswith("{"):
            try:
                secret = strict_json(text, 32768, "secret")
            except YouTubeError:
                fail("secret")
        else:
            secret = {"access_token": text}
    if not isinstance(secret, Mapping):
        fail("secret")
    keys = set(secret)
    if keys not in ({"access_token"}, {"client_id", "client_secret", "refresh_token"}):
        fail("secret")
    for item in secret.values():
        if not isinstance(item, str) or not TOKEN_TEXT.fullmatch(item):
            fail("secret")
    return dict(secret)


def api_key(value):
    """Validate the API key wallet value: bare text or {"api_key": "..."}."""
    key = value
    if isinstance(key, str) and key.strip().startswith("{"):
        try:
            key = strict_json(key.strip(), 4096, "secret")
        except YouTubeError:
            fail("secret")
    if isinstance(key, Mapping):
        if set(key) != {"api_key"}:
            fail("secret")
        key = key["api_key"]
    if not isinstance(key, str) or not KEY_TEXT.fullmatch(key.strip()):
        fail("secret")
    return key.strip()


def redact(text, secrets):
    """Mask every credential value wherever it could appear."""
    text = "" if text is None else str(text)
    for secret in secrets:
        if secret and len(secret) >= 4:
            text = text.replace(secret, "***")
    return text


def clean(value, limit):
    """Bounded single-line provider text with control characters removed."""
    if not isinstance(value, str):
        return None
    value = "".join(" " if ord(char) < 32 or ord(char) == 127 else char for char in value).strip()
    return value[:limit] if value else None


# ---------------------------------------------------------------------------
# Request values


def identifier(value):
    if value is None:
        return None
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        fail("invalid_input", "request_id needs 1-128 ASCII letters, digits or ._:- characters.")
    return value


def youtube_url(text):
    """Split a youtube.com or youtu.be URL; None for anything else (never fetched)."""
    if not text.startswith(("https://", "http://")):
        return None
    try:
        parsed = urlsplit(text)
        host = (parsed.hostname or "").lower()
    except ValueError:
        return None
    if host not in YOUTUBE_HOSTS or parsed.username is not None or parsed.password is not None:
        return None
    return host, parsed.path, parse_qs(parsed.query)


def video_id(value, name="video_id", code="invalid_input"):
    """An 11-character video ID, or the ID inside a watch, youtu.be, shorts, embed or live URL."""
    text = value.strip() if isinstance(value, str) else None
    if text is not None and len(text) <= 2048:
        if VIDEO_ID.fullmatch(text):
            return text
        parts = youtube_url(text)
        if parts:
            host, path, query = parts
            segments = [segment for segment in path.split("/") if segment]
            candidate = ""
            if host == "youtu.be" and segments:
                candidate = segments[0]
            elif path == "/watch":
                candidate = (query.get("v") or [""])[0]
            elif len(segments) >= 2 and segments[0] in ("shorts", "embed", "live"):
                candidate = segments[1]
            if VIDEO_ID.fullmatch(candidate):
                return candidate
    fail(code, f"{name} must be an 11-character YouTube video ID or a youtube.com / youtu.be video URL.")


def playlist_id(value, name="playlist_id", code="invalid_input"):
    """A playlist ID (PL..., UU..., OLAK5uy_...), or the list parameter of a youtube.com URL."""
    text = value.strip() if isinstance(value, str) else None
    if text is not None and len(text) <= 2048:
        if PLAYLIST_ID.fullmatch(text):
            return text
        parts = youtube_url(text)
        if parts:
            candidate = (parts[2].get("list") or [""])[0]
            if PLAYLIST_ID.fullmatch(candidate):
                return candidate
    fail(code, f"{name} must be a YouTube playlist ID or a youtube.com URL with a list parameter.")


def channel_ref(value, name="channel", code="invalid_input"):
    """mine, a UC... channel ID, an @handle, or a youtube.com/channel/UC... or youtube.com/@handle URL."""
    text = value.strip() if isinstance(value, str) else None
    if text is not None and len(text) <= 2048:
        if text == "mine" or CHANNEL_ID.fullmatch(text) or HANDLE.fullmatch(text):
            return text
        parts = youtube_url(text)
        if parts and parts[0] != "youtu.be":
            segments = [segment for segment in parts[1].split("/") if segment]
            if len(segments) >= 2 and segments[0] == "channel" and CHANNEL_ID.fullmatch(segments[1]):
                return segments[1]
            if segments and HANDLE.fullmatch(segments[0]):
                return segments[0]
    fail(code, f"{name} must be a channel ID (UC followed by 22 characters), an @handle, a channel URL or mine.")


def video_ids(value):
    """1-50 distinct video IDs from a JSON array or a comma, space or newline separated text."""
    items = value
    if isinstance(value, str):
        items = [part for part in re.split(r"[\s,]+", value) if part]
    if not isinstance(items, list) or not 1 <= len(items) <= 50:
        fail("invalid_input", "video_ids must name 1 to 50 videos.")
    result = []
    for item in items:
        ident = video_id(item, "video_ids")
        if ident not in result:
            result.append(ident)
    return result


def single_line(value, name, low, high):
    if not isinstance(value, str) or not low <= len(value.strip()) <= high:
        fail("invalid_input", f"{name} must be {low}-{high} characters.")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        fail("invalid_input", f"{name} cannot contain control characters.")
    return value.strip()


def title_text(value, name="title"):
    """YouTube titles: 1-100 characters, no < or >, no control characters."""
    text = single_line(value, name, 1, TITLE_LIMIT)
    if "<" in text or ">" in text:
        fail("invalid_input", f"YouTube refuses < and > in {name}.")
    return text


def description_text(value):
    """YouTube descriptions: at most 5000 UTF-8 bytes, no < or >; line breaks and tabs are kept."""
    if not isinstance(value, str) or len(value.encode("utf-8", errors="replace")) > DESCRIPTION_BYTES:
        fail("invalid_input", "description must be text of at most 5000 UTF-8 bytes.")
    if any((ord(char) < 32 and char not in "\n\r\t") or ord(char) == 127 for char in value):
        fail("invalid_input", "description cannot contain control characters other than line breaks and tabs.")
    if "<" in value or ">" in value:
        fail("invalid_input", "YouTube refuses < and > in description.")
    return value.replace("\r\n", "\n")


def tags_weight(tags):
    """Characters YouTube counts for tags: commas between tags and quotes around tags with spaces."""
    return sum(len(tag) + (2 if " " in tag else 0) for tag in tags) + max(0, len(tags) - 1)


def tag_list(value):
    """A JSON array, or comma-separated text, of tags within YouTube's 500-character budget."""
    items = [part.strip() for part in value.split(",")] if isinstance(value, str) else value
    if not isinstance(items, list) or len(items) > 100:
        fail("invalid_input", "tags must be a JSON array or comma-separated text of at most 100 tags.")
    result = []
    for item in items:
        if not isinstance(item, str):
            fail("invalid_input", "Each tag must be text.")
        if not item.strip() and isinstance(value, str):
            continue
        tag = single_line(item, "Each tag", 1, 100)
        if any(char in tag for char in ",<>\""):
            fail("invalid_input", "Tags cannot contain commas, quotes, < or >.")
        if tag not in result:
            result.append(tag)
    if tags_weight(result) > TAGS_LIMIT:
        fail("invalid_input", "tags exceed YouTube's 500-character limit (commas and quotes around tags with spaces count).")
    return result


def category(value):
    if not isinstance(value, str) or not CATEGORY_ID.fullmatch(value.strip()):
        fail("invalid_input", "category_id must be a YouTube video category ID such as 22.")
    return value.strip()


def privacy(value):
    if value not in PRIVACY:
        fail("invalid_input", "privacy must be private, unlisted or public.")
    return value


def timestamp(value, name):
    """RFC 3339 date-time, or a date taken as midnight UTC; returned in canonical UTC form."""
    if not isinstance(value, str) or len(value) > 40:
        fail("invalid_input", f"{name} must be a date such as 2026-01-31 or an RFC 3339 date-time.")
    text = value.strip()
    if DATE_ONLY.fullmatch(text):
        text += "T00:00:00Z"
    if not DATE_TIME.fullmatch(text):
        fail("invalid_input", f"{name} must be a date such as 2026-01-31 or an RFC 3339 date-time.")
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        fail("invalid_input", f"{name} is not a valid calendar date-time.")
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") + "Z"


def local_path(value):
    """A local path; surrounding whitespace such as a trailing newline from a text source is ignored."""
    value = value.strip() if isinstance(value, str) else value
    if (not isinstance(value, str) or not value or len(value.encode("utf-8", errors="replace")) > 4096
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        fail("invalid_input", "path must be a local file path without control characters.")
    return value


def document(value, config):
    """Read a JSON object, or return the text of a plain value (None when it is JSON)."""
    if isinstance(value, Mapping):
        result = thaw(value)
        encoded(result, config["max_request_bytes"])
        return result, None
    if isinstance(value, (list, tuple)):
        fail("invalid_input", "The request must be one JSON object, not an array.")
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8", errors="strict")
        except UnicodeError:
            fail("invalid_input", "The request must be UTF-8 text or JSON.")
    if not isinstance(value, str):
        fail("invalid_input", "The request must be UTF-8 text or JSON.")
    if len(value.encode("utf-8", errors="replace")) > config["max_request_bytes"]:
        fail("request_too_large")
    text = value.strip()
    if text.startswith("{") or text.startswith("["):
        parsed = strict_json(text, config["max_request_bytes"])
        if not isinstance(parsed, dict):
            fail("invalid_input", "The request must be one JSON object, not an array.")
        return parsed, None
    return {}, text


# Plain text on the request input fills this field of the default operation.
TEXT_FIELDS = {"search": "q", "video_details": "video_ids", "channel_uploads": "channel", "playlist_items": "playlist_id",
               "comments": "video_id", "watch_uploads": "channel", "upload_video": "path"}


def page_fields(data, request, config):
    limit = data.get("limit", config["list_limit"])
    request["limit"] = integer(limit, "limit", 1, config["list_limit"], "invalid_input")
    token = data.get("page_token")
    if token is not None and (not isinstance(token, str) or not PAGE_TOKEN.fullmatch(token)):
        fail("invalid_input", "page_token must be the next_page_token text of a previous result.")
    request["page_token"] = token


def request_channel(data, config):
    if data.get("channel") is not None:
        return channel_ref(data["channel"])
    if not config["channel"]:
        fail("invalid_input", "Name a channel (UC... ID, @handle, channel URL or mine), or set the channel setting.")
    return config["channel"]


def parse_request(value, config):
    """Return one normalized operation request; enforce the allowlist and switches before any wallet access."""
    data, text = document(value, config)
    operation = data.get("operation", config["operation"])
    if operation not in OPERATIONS:
        fail("invalid_input", "operation must be one of: " + ", ".join(OPERATIONS) + ".")
    request = {"operation": operation, "request_id": identifier(data.get("request_id"))}
    gate(operation, config)
    unknown = set(data) - REQUEST_KEYS[operation]
    if unknown:
        fail("invalid_input", "Unknown field for " + operation + ": " + sorted(unknown)[0][:64] + ".")
    if text:
        if operation not in TEXT_FIELDS:
            fail("invalid_input", operation + " needs a JSON object; plain text is accepted only for "
                 + ", ".join(TEXT_FIELDS) + ".")
        data = {TEXT_FIELDS[operation]: text}
    if operation in LISTINGS:
        page_fields(data, request, config)
    if operation == "search":
        if data.get("q") is None and data.get("channel_id") is None:
            fail("invalid_input", "search needs q (search text) or channel_id.")
        if data.get("q") is not None:
            request["q"] = single_line(data["q"], "q", 1, 500)
        kind = data.get("type", "video")
        if kind not in SEARCH_TYPES:
            fail("invalid_input", "type must be video, channel or playlist.")
        order = data.get("order", "relevance")
        if order not in SEARCH_ORDERS:
            fail("invalid_input", "order must be one of: " + ", ".join(SEARCH_ORDERS) + ".")
        request.update(type=kind, order=order)
        for key in ("published_after", "published_before"):
            if data.get(key) is not None:
                request[key] = timestamp(data[key], key)
        if data.get("channel_id") is not None:
            ident = channel_ref(data["channel_id"], "channel_id")
            if not CHANNEL_ID.fullmatch(ident):
                fail("invalid_input", "channel_id must be a UC... channel ID; use channel_uploads for a handle.")
            request["channel_id"] = ident
        if data.get("region_code") is not None:
            if not isinstance(data["region_code"], str) or not REGION.fullmatch(data["region_code"].strip().upper()):
                fail("invalid_input", "region_code must be an ISO 3166-1 alpha-2 country code such as FR.")
            request["region_code"] = data["region_code"].strip().upper()
        if data.get("safe_search") is not None:
            if data["safe_search"] not in SAFE_SEARCH:
                fail("invalid_input", "safe_search must be moderate, none or strict.")
            request["safe_search"] = data["safe_search"]
    elif operation == "video_details":
        if (data.get("video_ids") is None) == (data.get("video_id") is None):
            fail("invalid_input", "video_details needs video_ids (or a single video_id), not both.")
        request["video_ids"] = video_ids(data["video_ids"]) if data.get("video_ids") is not None \
            else [video_id(data["video_id"])]
    elif operation in CHANNEL_OPERATIONS:
        request["channel"] = request_channel(data, config)
    elif operation == "playlist_items":
        if data.get("playlist_id") is None:
            fail("invalid_input", "playlist_items needs playlist_id.")
        request["playlist_id"] = playlist_id(data["playlist_id"])
    elif operation == "comments":
        if data.get("video_id") is None:
            fail("invalid_input", "comments needs video_id.")
        request["video_id"] = video_id(data["video_id"])
        order = data.get("order", "time")
        if order not in COMMENT_ORDERS:
            fail("invalid_input", "order must be time or relevance.")
        request["order"] = order
        if data.get("search_terms") is not None:
            request["search_terms"] = single_line(data["search_terms"], "search_terms", 1, 200)
    elif operation == "playlist_add":
        if data.get("playlist_id") is None or data.get("video_id") is None:
            fail("invalid_input", "playlist_add needs playlist_id and video_id.")
        request["playlist_id"] = playlist_id(data["playlist_id"])
        request["video_id"] = video_id(data["video_id"])
        request["position"] = None if data.get("position") is None else integer(
            data["position"], "position", 0, 100000, "invalid_input")
    elif operation == "playlist_remove":
        if data.get("playlist_item_id") is not None:
            if data.get("playlist_id") is not None or data.get("video_id") is not None:
                fail("invalid_input", "Give playlist_item_id alone, or playlist_id with video_id.")
            item = data["playlist_item_id"]
            if not isinstance(item, str) or not ITEM_ID.fullmatch(item.strip()):
                fail("invalid_input", "playlist_item_id must be the id of a playlist item (from playlist_items).")
            request["playlist_item_id"] = item.strip()
        else:
            if data.get("playlist_id") is None or data.get("video_id") is None:
                fail("invalid_input", "playlist_remove needs playlist_item_id, or playlist_id with video_id.")
            request["playlist_id"] = playlist_id(data["playlist_id"])
            request["video_id"] = video_id(data["video_id"])
    elif operation == "upload_video":
        if data.get("path") is None:
            fail("invalid_input", "upload_video needs path, a local file inside upload_root (or send it on the file input).")
        request.update(upload_fields(data, config))
    else:
        if data.get("video_id") is None:
            fail("invalid_input", "update_video needs video_id.")
        request["video_id"] = video_id(data["video_id"])
        changes = {key: data[key] for key in UPDATABLE if data.get(key) is not None}
        if not changes:
            fail("invalid_input", "update_video needs at least one of: " + ", ".join(UPDATABLE) + ".")
        request["changes"] = video_changes(changes)
    return request


def video_changes(data):
    result = {}
    if "title" in data:
        result["title"] = title_text(data["title"])
    if "description" in data:
        result["description"] = description_text(data["description"])
    if "tags" in data:
        result["tags"] = tag_list(data["tags"])
    if "category_id" in data:
        result["category_id"] = category(data["category_id"])
    if "privacy" in data:
        result["privacy"] = privacy(data["privacy"])
    return result


def upload_fields(data, config):
    """Upload metadata from a request; the title defaults to the file name later."""
    result = {"path": local_path(data["path"]), "title": None, "description": "", "tags": [],
              "category_id": config["default_category_id"], "privacy": config["default_privacy"],
              "made_for_kids": config["made_for_kids"], "notify_subscribers": config["notify_subscribers"]}
    result.update(video_changes({key: data[key] for key in UPDATABLE if data.get(key) is not None}))
    for key in ("made_for_kids", "notify_subscribers"):
        if data.get(key) is not None:
            result[key] = boolean(data[key], key, "invalid_input")
    return result


def file_request(value, config):
    """A file/path event uploads that file; a JSON object adds title, description, tags or privacy."""
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8", errors="strict")
        except UnicodeError:
            fail("invalid_input", "The file input must be a UTF-8 path or a JSON object.")
    if not isinstance(value, (str, Mapping)):
        fail("invalid_input", "The file input must be a UTF-8 path or a JSON object.")
    gate("upload_video", config)
    data = {"path": value}
    if isinstance(value, Mapping) or value.strip().startswith("{"):
        data, _ = document(value, config)
        unknown = set(data) - (REQUEST_KEYS["upload_video"] - {"operation"})
        if unknown:
            fail("invalid_input", "Unknown field for upload_video: " + sorted(unknown)[0][:64] + ".")
        if data.get("path") is None:
            fail("invalid_input", "A JSON object on the file input needs path.")
    request = {"operation": "upload_video", "request_id": identifier(data.get("request_id"))}
    request.update(upload_fields(data, config))
    return request


def upload_title(name):
    """A title from a local file name: its stem, cleaned for YouTube's rules."""
    stem = name.rsplit(".", 1)[0] if "." in name.strip(".") else name
    text = "".join(" " if ord(char) < 32 or ord(char) == 127 or char in "<>" else char for char in stem)
    text = " ".join(text.split())[:TITLE_LIMIT].strip()
    return text or "Untitled upload"


def upload_type(name):
    """The X-Upload-Content-Type of a local file: its video MIME type, or video/* when unknown."""
    guess, _ = mimetypes.guess_type(name, strict=False)
    if isinstance(guess, str) and guess.startswith("video/") and re.fullmatch(r"video/[A-Za-z0-9.+-]{1,100}", guess):
        return guess
    return "video/*"


def upload_metadata(request):
    """The videos.insert body: snippet and status (privacy defaults to private)."""
    snippet = {"title": request["title"], "description": request["description"], "categoryId": request["category_id"]}
    if request["tags"]:
        snippet["tags"] = list(request["tags"])
    return {"snippet": snippet, "status": {"privacyStatus": request["privacy"],
                                           "selfDeclaredMadeForKids": request["made_for_kids"]}}


SNIPPET_KEEP = ("defaultLanguage", "defaultAudioLanguage")
STATUS_KEEP = ("embeddable", "license", "publicStatsViewable", "selfDeclaredMadeForKids", "containsSyntheticMedia")


def update_body(current, changes):
    """videos.update replaces whole parts: carry the current mutable values and apply only the changes.

    Returns (parts, body, previous): the parts sent, the resource body and the replaced values.
    """
    snippet = current.get("snippet") if isinstance(current.get("snippet"), dict) else {}
    status = current.get("status") if isinstance(current.get("status"), dict) else {}
    body, parts, previous = {"id": current["id"]}, [], {}
    if set(changes) & {"title", "description", "tags", "category_id"}:
        if not isinstance(snippet.get("title"), str) or not isinstance(snippet.get("categoryId"), str):
            fail("protocol", "YouTube returned the video without its title or category; nothing was changed.")
        part = {"title": snippet["title"], "categoryId": snippet["categoryId"]}
        if isinstance(snippet.get("description"), str):
            part["description"] = snippet["description"]
        if isinstance(snippet.get("tags"), list):
            part["tags"] = [tag for tag in snippet["tags"] if isinstance(tag, str)]
        for key in SNIPPET_KEEP:
            if isinstance(snippet.get(key), str):
                part[key] = snippet[key]
        for name, key in (("title", "title"), ("description", "description"), ("tags", "tags"),
                          ("category_id", "categoryId")):
            if name in changes:
                previous[name] = part.get(key)
                part[key] = changes[name]
        body["snippet"] = part
        parts.append("snippet")
    if "privacy" in changes:
        if status.get("privacyStatus") not in PRIVACY:
            fail("protocol", "YouTube returned the video without its privacy status; nothing was changed.")
        part = {"privacyStatus": changes["privacy"]}
        for key in STATUS_KEEP:
            if type(status.get(key)) is bool or (key == "license" and isinstance(status.get(key), str)):
                part[key] = status[key]
        if changes["privacy"] == "private" and isinstance(status.get("publishAt"), str):
            part["publishAt"] = status["publishAt"]
        previous["privacy"] = status["privacyStatus"]
        body["status"] = part
        parts.append("status")
    return parts, body, previous


# ---------------------------------------------------------------------------
# Provider resources


def optional_text(item, key, limit=65536):
    value = item.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > limit:
        fail("protocol")
    return value


def section(item, key):
    value = item.get(key, {})
    if not isinstance(value, dict):
        fail("protocol")
    return value


def count(value):
    """YouTube statistics are decimal strings; hidden counts are absent."""
    if value is None:
        return None
    if isinstance(value, str) and value.isdigit() and len(value) <= 20:
        return int(value)
    if type(value) is int and value >= 0:
        return value
    fail("protocol")


def flag(value):
    if value is None or type(value) is bool:
        return value
    if value in ("true", "false"):
        return value == "true"
    fail("protocol")


def thumbnail(snippet):
    """The largest thumbnail URL YouTube lists."""
    thumbs = snippet.get("thumbnails", {})
    if not isinstance(thumbs, dict):
        fail("protocol")
    for size in ("maxres", "standard", "high", "medium", "default"):
        entry = thumbs.get(size)
        if isinstance(entry, dict) and isinstance(entry.get("url"), str) and entry["url"].startswith("https://") \
                and len(entry["url"]) <= 2048:
            return entry["url"]
    return None


def duration_seconds(value):
    """ISO 8601 duration (PT1H2M3S, P1DT2H, P0D) to seconds; None for an unknown form."""
    if not isinstance(value, str) or value in ("P", "PT") or value.endswith("T"):
        return None
    match = DURATION.fullmatch(value)
    if not match:
        return None
    weeks, days, hours, minutes, seconds = (int(part) if part else 0 for part in match.groups())
    return (((weeks * 7 + days) * 24 + hours) * 60 + minutes) * 60 + seconds


def resource_id(item, key="id", pattern=RESOURCE_ID):
    value = item.get(key)
    if not isinstance(value, str) or not pattern.fullmatch(value):
        fail("protocol")
    return value


def text_list(value, limit=500):
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > limit or any(not isinstance(item, str) for item in value):
        fail("protocol")
    return list(value)


def normalize_video(item):
    """One videos resource (snippet, contentDetails, statistics, status) in the public shape."""
    if not isinstance(item, dict):
        fail("protocol")
    ident = resource_id(item, "id", VIDEO_ID)
    snippet, details = section(item, "snippet"), section(item, "contentDetails")
    stats, status = section(item, "statistics"), section(item, "status")
    duration = optional_text(details, "duration", 64)
    return {"id": ident, "url": WATCH_URL + ident, "title": optional_text(snippet, "title"),
            "description": optional_text(snippet, "description"), "channel_id": optional_text(snippet, "channelId", 64),
            "channel_title": optional_text(snippet, "channelTitle"), "published_at": optional_text(snippet, "publishedAt", 64),
            "tags": text_list(snippet.get("tags")), "category_id": optional_text(snippet, "categoryId", 16),
            "default_language": optional_text(snippet, "defaultLanguage", 32),
            "live_broadcast_content": optional_text(snippet, "liveBroadcastContent", 32), "thumbnail_url": thumbnail(snippet),
            "duration": duration, "duration_seconds": duration_seconds(duration),
            "definition": optional_text(details, "definition", 16), "dimension": optional_text(details, "dimension", 16),
            "caption": flag(details.get("caption")), "licensed_content": flag(details.get("licensedContent")),
            "view_count": count(stats.get("viewCount")), "like_count": count(stats.get("likeCount")),
            "comment_count": count(stats.get("commentCount")),
            "privacy_status": optional_text(status, "privacyStatus", 32),
            "upload_status": optional_text(status, "uploadStatus", 32), "embeddable": flag(status.get("embeddable")),
            "license": optional_text(status, "license", 32), "made_for_kids": flag(status.get("madeForKids")),
            "publish_at": optional_text(status, "publishAt", 64)}


def normalize_search(item):
    """One search result: a video, channel or playlist with its snippet."""
    if not isinstance(item, dict):
        fail("protocol")
    ident = section(item, "id")
    snippet = section(item, "snippet")
    kind = ident.get("kind")
    keys = {"youtube#video": ("video", "videoId"), "youtube#channel": ("channel", "channelId"),
            "youtube#playlist": ("playlist", "playlistId")}
    if kind not in keys:
        fail("protocol")
    name, key = keys[kind]
    value = resource_id(ident, key)
    url = {"video": WATCH_URL + value, "channel": "https://www.youtube.com/channel/" + value,
           "playlist": "https://www.youtube.com/playlist?list=" + value}[name]
    return {"kind": name, "id": value, "url": url, "title": optional_text(snippet, "title"),
            "description": optional_text(snippet, "description"), "channel_id": optional_text(snippet, "channelId", 64),
            "channel_title": optional_text(snippet, "channelTitle"), "published_at": optional_text(snippet, "publishedAt", 64),
            "live_broadcast_content": optional_text(snippet, "liveBroadcastContent", 32),
            "thumbnail_url": thumbnail(snippet)}


def normalize_playlist_item(item):
    """One playlistItems resource: the item ID (used to remove it) and the video it points to."""
    if not isinstance(item, dict):
        fail("protocol")
    ident = resource_id(item, "id", ITEM_ID)
    snippet, details, status = section(item, "snippet"), section(item, "contentDetails"), section(item, "status")
    resource = section(snippet, "resourceId")
    video = details.get("videoId", resource.get("videoId"))
    if not isinstance(video, str) or not VIDEO_ID.fullmatch(video):
        fail("protocol")
    position = snippet.get("position")
    if position is not None and (type(position) is not int or position < 0):
        fail("protocol")
    return {"id": ident, "video_id": video, "url": WATCH_URL + video, "playlist_id": optional_text(snippet, "playlistId", 64),
            "position": position, "title": optional_text(snippet, "title"),
            "description": optional_text(snippet, "description"), "added_at": optional_text(snippet, "publishedAt", 64),
            "video_published_at": optional_text(details, "videoPublishedAt", 64),
            "channel_id": optional_text(snippet, "channelId", 64), "channel_title": optional_text(snippet, "channelTitle"),
            "video_owner_channel_id": optional_text(snippet, "videoOwnerChannelId", 64),
            "video_owner_channel_title": optional_text(snippet, "videoOwnerChannelTitle"),
            "privacy_status": optional_text(status, "privacyStatus", 32), "thumbnail_url": thumbnail(snippet)}


def normalize_comment(item):
    """One commentThreads resource: the top-level comment, flagged as untrusted public text."""
    if not isinstance(item, dict):
        fail("protocol")
    ident = resource_id(item, "id")
    snippet = section(item, "snippet")
    top = section(snippet, "topLevelComment")
    comment = section(top, "snippet")
    author = section(comment, "authorChannelId")
    replies = snippet.get("totalReplyCount", 0)
    if type(replies) is not int or replies < 0:
        fail("protocol")
    return {"id": ident, "comment_id": optional_text(top, "id", 256), "video_id": optional_text(snippet, "videoId", 64),
            "channel_id": optional_text(snippet, "channelId", 64),
            "author": optional_text(comment, "authorDisplayName"), "author_channel_id": optional_text(author, "value", 64),
            "text": optional_text(comment, "textDisplay", 100000), "like_count": count(comment.get("likeCount")),
            "published_at": optional_text(comment, "publishedAt", 64), "updated_at": optional_text(comment, "updatedAt", 64),
            "reply_count": replies, "can_reply": flag(snippet.get("canReply")), "untrusted_content": True}


def normalize_channel(item):
    """One channels resource: its ID, title and uploads playlist."""
    if not isinstance(item, dict):
        fail("protocol")
    ident = resource_id(item, "id", CHANNEL_ID)
    snippet, details = section(item, "snippet"), section(item, "contentDetails")
    related = section(details, "relatedPlaylists")
    uploads = related.get("uploads")
    if not isinstance(uploads, str) or not PLAYLIST_ID.fullmatch(uploads):
        fail("protocol")
    return {"id": ident, "title": optional_text(snippet, "title"), "custom_url": optional_text(snippet, "customUrl", 256),
            "uploads_playlist_id": uploads}


def page(body, size):
    """Validate one list response; return (items, next page token, total results or None)."""
    if not isinstance(body, dict):
        fail("protocol")
    items = body.get("items", [])
    token = body.get("nextPageToken")
    info = body.get("pageInfo", {})
    if (not isinstance(items, list) or len(items) > size or not isinstance(info, dict)
            or (token is not None and (not isinstance(token, str) or not PAGE_TOKEN.fullmatch(token)))):
        fail("protocol")
    total = info.get("totalResults")
    if total is not None and (type(total) is not int or total < 0):
        fail("protocol")
    return items, token or None, total


# ---------------------------------------------------------------------------
# Watch state


def watch_digest(channel, kind):
    """One stored entry per watched channel and credential kind (an API key sees public uploads only)."""
    material = json.dumps(["uploads", channel.lower() if channel.startswith("@") else channel, kind],
                          separators=(",", ":"))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def watch_entry(value):
    if not isinstance(value, dict) or set(value) != {"playlist_id", "seen", "last_poll"}:
        fail("state_invalid")
    playlist = value["playlist_id"]
    if playlist is not None and (not isinstance(playlist, str) or not PLAYLIST_ID.fullmatch(playlist)):
        fail("state_invalid")
    items = value["seen"]
    if (not isinstance(items, list) or len(items) > SEEN_LIMIT
            or any(not isinstance(item, str) or not VIDEO_ID.fullmatch(item) for item in items)):
        fail("state_invalid")
    if type(value["last_poll"]) is not int or value["last_poll"] < 0:
        fail("state_invalid")
    return value


def watch_document(value):
    """Validate the stored watch document; it holds playlist and video IDs and times only, never credentials."""
    if (not isinstance(value, dict) or value.get("schema_version") != 1 or set(value) != {"schema_version", "watches"}
            or not isinstance(value["watches"], dict) or len(value["watches"]) > 8):
        fail("state_invalid")
    for key, entry in value["watches"].items():
        if not isinstance(key, str) or not re.fullmatch(r"[0-9a-f]{32}", key):
            fail("state_invalid")
        watch_entry(entry)
    return value


def bounded_ids(items, limit=SEEN_LIMIT):
    """Keep the newest `limit` distinct IDs in order."""
    result, seen = [], set()
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result[-limit:]


def store_entry(document, digest, entry, keep=8):
    """Return a new document with this watch entry; the oldest other entries beyond `keep` are dropped."""
    watches = {key: value for key, value in document["watches"].items() if key != digest}
    while len(watches) >= keep:
        watches.pop(min(watches, key=lambda key: watches[key]["last_poll"]))
    watches[digest] = {"playlist_id": entry["playlist_id"], "seen": bounded_ids(entry["seen"]),
                       "last_poll": int(entry["last_poll"])}
    return {"schema_version": 1, "watches": watches}


# ---------------------------------------------------------------------------
# Provider errors


def map_status(status):
    return {400: "bad_request", 401: "unauthorized", 403: "forbidden", 404: "not_found",
            429: "rate_limited"}.get(status, "provider_unavailable" if status >= 500 else "provider_error")


def provider_failure(status, body, secrets, *, mutation, retry_after=None):
    """Raise the coded error for a non-2xx response ({"error": {code, message, errors, status, details}})."""
    detail = body.get("error") if isinstance(body, dict) else None
    provider_status = clean(detail.get("status"), 64) if isinstance(detail, dict) else None
    errors = detail.get("errors") if isinstance(detail, dict) else None
    first = errors[0] if isinstance(errors, list) and errors and isinstance(errors[0], dict) else {}
    reason = clean(first.get("reason"), 64)
    details = detail.get("details") if isinstance(detail, dict) else None
    info = [clean(entry.get("reason"), 64) for entry in details if isinstance(entry, dict)
            and str(entry.get("@type", "")).endswith("google.rpc.ErrorInfo")] if isinstance(details, list) else []
    message = clean(redact(detail.get("message"), secrets), 200) if isinstance(detail, dict) else None
    code = map_status(status)
    if status in (400, 403, 429):
        mapped = [DETAIL_REASONS[item] for item in info if item in DETAIL_REASONS]
        if reason in REASONS:
            code = REASONS[reason]
        elif mapped:
            code = mapped[0]
    if status in (400, 403) and reason is None and info:
        reason = info[0]
    uncertain = mutation and code == "provider_unavailable"
    fail(code, MESSAGES["provider_unavailable_uncertain"] if uncertain else None, uncertain=uncertain, http_status=status,
         retry_after=retry_after if code == "rate_limited" else None, provider_status=provider_status,
         provider_reason=reason, provider_message=message)
