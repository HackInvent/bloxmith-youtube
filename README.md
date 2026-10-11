# YouTube

Search videos, read details and manage playlists and uploads on YouTube. The
block searches YouTube, reads video details (durations in seconds, statistics,
privacy), lists a channel's uploads, playlist items and comment threads, adds
videos to playlists and removes playlist items, updates a video's title,
description, tags, category or privacy, uploads local videos through Google's
resumable protocol, and can watch a channel for new uploads in Active Runtime.
Public reads use a YouTube Data API key; owner operations use an OAuth grant.
Both stay in the BloxSmith wallet, remote access stays off until you enable it,
and changes and playlist removals each have their own switch. Every result
reports the estimated quota units it used.

<!-- block-metadata:start -->
[![Block version: 0.1.0](https://img.shields.io/badge/block-0.1.0-blue)](model.json)
[![BloxSmith compatibility: 1.0.9](https://img.shields.io/badge/BloxSmith-1.0.9-brightgreen)](compatibility.json)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)

Verified BloxSmith versions: **1.0.9** (bundled-block tests; see [test evidence](compatibility.json)).
<!-- block-metadata:end -->

[![YouTube — search, read and manage videos](media/thumbnail.webp)](media/cover.png)

## When to use it

- Find recent videos on a topic (`search` with `order: date` and
  `published_after`), then feed their titles and descriptions to a summary or
  classification block.
- Read the duration, view and like counts of known videos (`video_details`).
- React to each new upload of a channel (watch mode, Active Runtime), for
  example to post it to a chat or to add it to a playlist.
- Collect the comments of a video for moderation or analysis.
- Publish a rendered video file as a private upload, then make it public later
  with `update_video`.

## Ports

| Direction | Port | Value |
| --- | --- | --- |
| Input | `request` | A JSON object with the operation parameters, or plain text (see below) |
| Input | `file` | A local video path (`file/path` or text) to upload, or a JSON object `{path, title, description, tags, privacy, ...}` |
| Output | `items` | Listings (`search`, `video_details`, `channel_uploads`, `playlist_items`, `comments`): a JSON array of normalized items |
| Output | `result` | Every successful operation: a JSON summary with identifiers, counts, page token, `requests` and `quota_units` |
| Output | `changed` | `watch_uploads` and the watch: the new uploads of the channel, oldest first |
| Output | `error` | Machine `code`, English `message`, `uncertain` flag, provider details and `quota_units` |

Both inputs are optional and each fresh value runs exactly one operation. When
both inputs are wired in One Shot, each value is processed once per run.
Business failures are published on `error` and the run continues; they never
stop an Active Runtime worker.

### `request` input

| Field | Operations | Meaning |
| --- | --- | --- |
| `operation` | all | One of the operations below; defaults to the `operation` setting and must be allowed |
| `request_id` | all | Optional correlation ID (1–128 of `A-Za-z0-9._:-`), echoed in `result` and `error` |
| `q` | `search` | Search text (YouTube supports `-word` and `a|b`); `search` needs `q` or `channel_id` |
| `type` | `search` | `video` (default), `channel` or `playlist` |
| `order` | `search`, `comments` | `search`: `relevance` (default), `date`, `rating`, `title`, `viewCount`, `videoCount`; `comments`: `time` (default) or `relevance` |
| `published_after`, `published_before` | `search` | A date (`2026-01-31`, midnight UTC) or an RFC 3339 date-time |
| `channel_id` | `search` | Restrict the search to one channel (`UC...` ID) |
| `region_code`, `safe_search` | `search` | ISO country code such as `FR`; `moderate`, `none` or `strict` |
| `video_ids` / `video_id` | `video_details` | Up to 50 video IDs or URLs (array, or comma/space-separated text), or one `video_id` |
| `channel` | `channel_uploads`, `watch_uploads` | A channel ID (`UC...`), an `@handle`, a channel URL or `mine`; defaults to the `channel` setting |
| `playlist_id` | `playlist_items`, `playlist_add`, `playlist_remove` | A playlist ID (`PL...`, `UU...`) or a URL with `list=` |
| `video_id` | `comments`, `playlist_add`, `playlist_remove`, `update_video` | An 11-character video ID or a `youtube.com` / `youtu.be` URL |
| `search_terms` | `comments` | Keep only comment threads containing this text |
| `position` | `playlist_add` | Zero-based position in a manually sorted playlist; default the end |
| `playlist_item_id` | `playlist_remove` | The `id` of a playlist item (from `playlist_items`); or give `playlist_id` with `video_id` instead |
| `path` | `upload_video` | A local file inside `upload_root` (absolute, or relative to it) |
| `title`, `description`, `tags`, `category_id`, `privacy` | `upload_video`, `update_video` | Title (1–100 characters), description (up to 5000 bytes), tags (array or comma-separated, 500-character budget), category ID such as `22`, `private` / `unlisted` / `public`. YouTube refuses `<` and `>` in titles and descriptions |
| `made_for_kids`, `notify_subscribers` | `upload_video` | Audience declaration and subscriber notification; default the settings |
| `limit`, `page_token` | listings | Items to return, 1 to `list_limit`; the `next_page_token` of a previous result |

The operations are `search`, `video_details`, `channel_uploads`,
`playlist_items`, `comments`, `watch_uploads`, `playlist_add`,
`playlist_remove`, `upload_video` and `update_video`. Unknown fields are refused.

Text that does not start with `{` fills the main field of the default operation:
the search text for `search`, the video IDs or URLs for `video_details`, the
channel for `channel_uploads` and `watch_uploads`, the playlist for
`playlist_items`, the video for `comments` and the local path for
`upload_video`. The other operations need JSON.

### `file` input

A plain path uploads that file with the defaults: title from the file name,
privacy `default_privacy` (`private`), category `default_category_id`. A JSON
object adds `title`, `description`, `tags`, `category_id`, `privacy`,
`made_for_kids`, `notify_subscribers` and `request_id`, for example
`{"path": "render.mp4", "title": "Weekly update", "privacy": "unlisted"}`. The
file must be inside `upload_root`. It needs `allow_writes` and `upload_video`
among the allowed operations.

### `items` output

A JSON array. Videos (`video_details`) carry `id`, `url`, `title`,
`description`, `channel_id`, `channel_title`, `published_at`, `tags`,
`category_id`, `default_language`, `live_broadcast_content`, `thumbnail_url`,
`duration` (ISO 8601, for example `PT4M13S`), `duration_seconds` (`253`; `null`
for a form without a fixed length such as months), `definition`, `dimension`,
`caption`, `licensed_content`, `view_count`, `like_count` (`null` when hidden),
`comment_count`, `privacy_status`, `upload_status`, `embeddable`, `license`,
`made_for_kids` and `publish_at`. Search results carry `kind` (`video`,
`channel` or `playlist`), `id`, `url`, `title`, `description`, `channel_id`,
`channel_title`, `published_at`, `live_broadcast_content` and `thumbnail_url`.
Playlist items (`channel_uploads`, `playlist_items`) carry the item `id` (used
to remove it), `video_id`, `url`, `playlist_id`, `position`, `title`,
`description`, `added_at`, `video_published_at`, `channel_id`,
`video_owner_channel_id`, `privacy_status` and `thumbnail_url`. Comment threads
carry `id`, `comment_id`, `video_id`, `author`, `author_channel_id`, `text`
(plain text), `like_count`, `published_at`, `updated_at`, `reply_count`,
`can_reply` and `untrusted_content: true`.

Wire `items` to a Filter or a Split / Aggregate block to handle items one by one.

### `result` output

```json
{"operation": "search", "request_id": "q-1", "q": "lofi", "type": "video", "order": "date",
 "count": 20, "truncated": true, "stop_reason": "limit", "next_page_token": "CBQQAA",
 "pages": 1, "total_results": 1000000, "requests": 1, "quota_units": 100}
```

Listings add `next_page_token` (resumes exactly after the last returned item),
`stop_reason` (`end`, `limit`, `max_pages`, `output_limit`) and `total_results`
(YouTube's estimate). `video_details` adds `missing` (IDs not found or not
visible). `channel_uploads` adds `channel_id`, `channel_title` and
`uploads_playlist_id`. `playlist_add` returns the new `item`;
`playlist_remove` returns `playlist_item_id`, `removed` and, after a lookup by
video, `matches`. `update_video` returns `changed`, the `previous` values and
the updated `video`. `upload_video` returns `video_id`, `video`, `file_name`,
`size`, `mime_type`, `privacy`, `chunks` and `resumed`. `watch_uploads`
returns `count`, `baseline`, `throttled`, `next_check_in_sec`, `channel_id` and
`playlist_id`.

### `changed` output

```json
{"operation": "watch_uploads", "request_id": null, "channel": "@example", "channel_id": "UC...",
 "playlist_id": "UU...", "count": 1, "items": [{"id": "...", "video_id": "...", "title": "New video", "...": "..."}]}
```

The items are playlist items as above, oldest first. The watch emits one
envelope per new upload; a `watch_uploads` request emits one envelope with all
the uploads it found.

### `error` output

`{"code", "message", "uncertain", "operation", "request_id", "video_id",
"playlist_id", "channel", "input"}` plus, when Google provides them,
`http_status`, `retry_after`, `provider_status`, `provider_reason`,
`provider_message` (bounded, credentials removed), `attempts`, and `requests`
and `quota_units` once a request was sent. Watch errors carry
`"operation": "watch_uploads"` and `"source": "watch"`. `uncertain: true` means
a change may have been applied: check the playlist or the channel before
sending it again.

| Code | Meaning |
| --- | --- |
| `config`, `disabled`, `secret`, `api_key_required`, `oauth_required`, `invalid_input`, `request_too_large` | Refused locally; nothing sent |
| `operation_not_allowed`, `writes_disabled`, `delete_disabled` | The operation is not enabled in the properties |
| `upload_root_required`, `path`, `path_denied`, `file_too_large`, `file_changed`, `storage` | Local file or storage refused; no video created |
| `busy`, `state_invalid` | Watch: another check holds the node storage, or the stored state is unreadable |
| `auth_failed` | The refresh-token grant was refused by Google's token endpoint |
| `key_invalid` | Google refused the API key |
| `bad_request` (400), `unauthorized` (401), `forbidden` (403), `not_found` (404), `rate_limited` (429 or 403 rate limit), `quota_exceeded` (daily quota), `comments_disabled`, `upload_limit`, `provider_error` (other 4xx) | Google refused the request |
| `upload_expired` | The upload session expired or was refused; no video was created |
| `provider_unavailable` (5xx), `network`, `timeout`, `cancelled`, `protocol` | Transport outcome; `uncertain` tells whether a change may have been applied |
| `tls` | Certificate verification failed; nothing sent |

## Settings

| Setting | Default | Meaning |
| --- | --- | --- |
| `enabled` | `false` | Remote access switch. While off, nothing reaches Google and the wallet is not read |
| `api_key_ref` | empty | Wallet reference holding the API key, such as `secret://workspace/youtube_api_key` |
| `secret_ref` | empty | Wallet reference holding the OAuth grant, such as `secret://workspace/youtube` |
| `read_credential` | `api_key` | Credential for reads: `api_key` (public data) or `oauth` (the grant, including the owner's private and unlisted videos) |
| `operation` | `search` | Operation used when a request names none; always allowed |
| `allowed_operations` | the six reads | Other operations a request may select |
| `channel` | empty | Default channel (`UC...`, `@handle`, channel URL or `mine`) for `channel_uploads`, `watch_uploads` and the watch |
| `list_limit` | 50 | Maximum items returned by one listing, 1–1000 |
| `allow_writes` | `false` | Required for `playlist_add`, `upload_video`, `update_video` and the `file` input |
| `allow_delete` | `false` | Required for `playlist_remove` |
| `upload_root` | empty | Local folder videos may be uploaded from; required for `upload_video` |
| `max_upload_bytes` | 268435456 | Upload size limit, 1 MiB–256 GiB (default 256 MiB) |
| `default_privacy` | `private` | Privacy of uploads when a request gives none |
| `default_category_id` | `22` | Category of uploads when a request gives none (22 is People & Blogs) |
| `made_for_kids` | `false` | Audience declaration (`selfDeclaredMadeForKids`) of uploads |
| `notify_subscribers` | `false` | Let YouTube notify subscribers about an upload |
| `watch_enabled` | `false` | Watch new uploads of `channel` in Active Runtime |
| `watch_interval_sec` | 900 | Time between two checks, 300–86400 seconds; also the minimum spacing of `watch_uploads` |
| `watch_max_items` | 20 | New uploads emitted per check, 1–50; the rest wait for the next check |
| `emit_existing` | `false` | Emit the newest existing uploads (up to `watch_max_items`) at the first check |
| `page_size` | 50 | Items requested per page, 1–50 |
| `max_pages` | 5 | Pages per listing, comments request or playlist lookup, 1–50 |
| `search_max_pages` | 1 | Pages per search (100 quota units each), 1–10 |
| `timeout_sec` | 120 | Deadline for one operation or one watch check, token exchange and retries included, 5–900 |
| `upload_timeout_sec` | 3600 | Deadline for one upload, every chunk and resumption included, 60–43200 |
| `upload_chunk_bytes` | 8388608 | Bytes per upload request, a multiple of 256 KiB, up to 256 MiB |
| `max_retries` | 2 | Retries, 0–5 (see Limits) |
| `max_retry_wait_sec` | 20 | Longest wait before a retry, 1–120 |
| `max_request_bytes` | 65536 | Bound for one incoming request, 1 KiB–1 MiB |
| `max_response_bytes` | 4194304 | Bound for each JSON response from Google, 64 KiB–32 MiB |
| `api_base_url` | `https://www.googleapis.com` | YouTube API and upload origin; plain HTTP only for a loopback test host |
| `oauth_base_url` | `https://oauth2.googleapis.com` | Token origin for refresh-token grants; plain HTTP only for a loopback test host |
| `ca_file` | empty | Optional absolute PEM file for a privately signed HTTPS origin |

The settings are validated on Apply and again before a Run starts.
`playlist_add`, `upload_video` and `update_video` in `operation` or
`allowed_operations` are refused unless `allow_writes` is on; `playlist_remove`
needs `allow_delete`. With remote access enabled, reads with the API key need
`api_key_ref`; owner operations, OAuth reads and `channel: mine` need
`secret_ref`. Restart a loaded Run to use changed settings.

## Quota

A Google Cloud project gets 10,000 YouTube Data API units per day by default;
the quota resets at midnight Pacific Time. Every `result` reports
`quota_units`, an estimate from Google's published cost table, counted per
request that reached Google (retries included); the run metadata adds them up.

| Request | Units |
| --- | --- |
| `search.list` (each page of `search`) | 100 |
| `videos.list`, `channels.list`, `playlistItems.list`, `commentThreads.list` (each page) | 1 |
| `playlistItems.insert`, `playlistItems.delete`, `videos.update` | 50 |
| `videos.insert` (`upload_video`, counted once per upload session) | 1600 |

A `search` therefore costs 100 units per page: keep `search_max_pages` at 1 and
prefer `channel_uploads` (2 units) to follow a channel. `quota_exceeded` is not
retried. The cost table belongs to Google and can change; check your project's
quota page for the actual usage.

## Watch

With `watch_enabled`, `enabled`, a `channel` and an Active Runtime Run, the
block checks the channel's uploads playlist every `watch_interval_sec` seconds
(never more often than every 300 seconds) and emits **each new upload** on
`changed`. In One Shot (or on demand), a `watch_uploads` request performs one
check with the same stored state and returns; a second check within
`watch_interval_sec` answers `throttled: true` without contacting YouTube.

- A check reads one page of 50 uploads, newest first, and the next page only
  while the whole page is new (at most 4 pages, 200 uploads).
- A video is new when its ID is not among the last 1000 IDs already seen. New
  uploads are emitted oldest first, at most `watch_max_items` per check.
- The first check of a channel only records the current uploads, unless
  `emit_existing` asks for the newest `watch_max_items` of them.
- With the API key, only public videos are visible; with `mine` (or
  `read_credential: oauth`) the owner's private and unlisted uploads are too.
  A scheduled video becomes visible to an API key when it is published.

The uploads playlist ID, the seen IDs and the time of the last check are stored
in `youtube-watch.json` in the node storage, keyed by a digest of the channel
and the credential kind: no key, token or video text is written. A restarted
Run resumes from there, so an upload published while it was stopped is emitted
once; a check made less than `watch_interval_sec` ago is not repeated at start.
Each upload is recorded as seen just before it is handed to the runtime (at
most once at the Stop boundary); if the runtime refuses it, the record is
restored. While the listener runs, `watch_uploads` requests on the same node
answer `busy`.

Watch counters (`cycles`, `emitted`, `errors`, `quota_units`, `state`) appear in
the run metadata. A failure is reported once on `error` and retried at the next
check. A check costs 1 unit per page (plus 1 for the first channel lookup).

## Authentication

Store the credentials in an unlocked wallet and configure only their
references:

- **API key** (public reads): the key text, or `{"api_key": "YOUR_API_KEY"}`,
  under for example `secret://workspace/youtube_api_key`. Create it in Google
  Cloud with the YouTube Data API v3 enabled, and restrict it to that API. The
  key is sent in the `x-goog-api-key` header, never in a URL.
- **OAuth grant** (owner operations, private data, `mine`): the same operator
  model as the Google Drive, Google Sheets and Gmail blocks, so one grant with
  the right scopes can serve several Google blocks:

```json
{"client_id": "YOUR_CLIENT_ID", "client_secret": "YOUR_CLIENT_SECRET", "refresh_token": "YOUR_REFRESH_TOKEN"}
```

or `{"access_token": "YOUR_ACCESS_TOKEN"}`, or the bare access token text. These
are placeholders, not credentials. With the refresh-token form, the block
exchanges the refresh token at `https://oauth2.googleapis.com/token` once per
activation (once per check for the watch) and keeps the short-lived access
token only in memory. Service-account keys are not supported: YouTube does not
accept them for channel operations.

Choose the narrowest YouTube scopes for the workflow:

| Scope | Allows |
| --- | --- |
| `https://www.googleapis.com/auth/youtube.readonly` | OAuth reads: `search`, `video_details`, `channel_uploads` (with `mine`), `playlist_items` (private playlists), the watch |
| `https://www.googleapis.com/auth/youtube.upload` | `upload_video` only |
| `https://www.googleapis.com/auth/youtube` | `playlist_add`, `playlist_remove`, `update_video`, and every read |
| `https://www.googleapis.com/auth/youtube.force-ssl` | `comments` with the OAuth grant (with the API key, public comments need no scope) |

Uploads from an API project that Google has not audited are locked to private
viewing, whatever `privacy` asks; this is a YouTube policy, not a block limit.

## Example

1. Create an API key with the YouTube Data API v3 and store it in the wallet as
   `youtube_api_key`.
2. Set `enabled = true` and `api_key_ref = secret://workspace/youtube_api_key`.
3. Wire a Text block containing `lofi beats` to `request` and a Display to
   `items`: One Shot returns up to 50 matching videos for 100 quota units.
4. Send `{"operation": "video_details", "video_ids": ["<id>"]}` to read their
   durations and view counts.
5. To follow a channel, set `channel = @example`, `watch_enabled = true` and run
   in Active Runtime: each new upload arrives on `changed`.
6. To publish a rendered video, store an OAuth grant with the `youtube.upload`
   scope as `youtube`, set `secret_ref`, enable `allow_writes`, set
   `upload_root`, add `upload_video` to `allowed_operations`, and wire the
   renderer's `file/path` output to `file`. The video is uploaded as private.

## Limits and warnings

- Reads and `update_video` (idempotent) are retried after HTTP 429, a 403
  rate-limit reason, 5xx or a lost connection. `playlist_add` and
  `playlist_remove` are retried only after a rate-limit answer (Google did not
  process them); a change that fails after it was sent is reported with
  `uncertain: true` and never replayed. `Retry-After` is honoured up to
  `max_retry_wait_sec`, within the deadline. The daily quota is never retried.
- Uploads use the resumable protocol: after a lost connection, a 5xx or a 429,
  the block asks the session how many bytes Google holds and resumes from there,
  at most `max_retries` times in a row. An upload is uncertain only when the
  last chunk was sent and no answer could be obtained. A file that changes on
  disk during the upload is abandoned before its last chunk (`file_changed`).
  Interrupted sessions are not resumed by a later Run.
- `update_video` reads the video's snippet and status, then writes them back
  with only the requested fields changed, because YouTube replaces whole parts
  and clears what is omitted. A change made by someone else between the read
  and the write can be overwritten. Localizations, recording details and
  thumbnails are not changed.
- Nothing is ever deleted: `playlist_remove` takes an item out of a playlist and
  the video stays on YouTube. There is no video, playlist or comment deletion,
  no comment posting and no rating.
- `search` costs 100 quota units per page and YouTube's `total_results` is an
  estimate. Search, `video_details` and comments see public data only with the
  API key.
- A watch backlog larger than 200 uploads between two checks is not fully
  emitted. Livestreams and premieres appear in the uploads playlist like other
  videos.
- No HTTP proxy support; the host needs direct outbound HTTPS to the Google origins.
- Titles, descriptions, tags and comments are untrusted public data. Do not feed
  them to tools that execute commands; an LLM reading them can be prompt-injected.

## Dependencies

Python standard library only (`http.client`, `ssl`, `json`); no
`requirements.txt`.

## Security

- The wallet value is read at run time, only after settings, the request and the
  local file are validated, and only the credential the operation needs. Keys
  and tokens never appear in settings, outputs, logs, run metadata, the watch
  state or error messages; provider messages are bounded and credentials removed.
- Uploads are read only from `upload_root` (symbolic links leaving it, devices
  and pipes are refused). The upload session URI must be on the API origin, so
  the token and the file are never sent to another host. Error messages never
  include local paths.
- TLS certificates and host names are always verified; there is no insecure mode.
- Redirects, cookies, proxies and compressed responses are not accepted.
- `api_base_url` and `oauth_base_url` other than the official origins receive the
  credentials: change them only for a trusted gateway or a local test double.
- The block opens no listening port; the watch polls outbound only.

## YouTube Data API usage

YouTube Data API **v3** on `https://www.googleapis.com`:

- `GET /youtube/v3/search` (`part=snippet`, `q`, `type`, `order`, `publishedAfter`,
  `publishedBefore`, `channelId`, `regionCode`, `safeSearch`, `maxResults`, `pageToken`)
- `GET /youtube/v3/videos` (`part=snippet,contentDetails,statistics,status`, `id`)
  and `PUT /youtube/v3/videos` (`part=snippet` and/or `status`)
- `GET /youtube/v3/channels` (`part=snippet,contentDetails`, `id`, `forHandle` or `mine`)
- `GET /youtube/v3/playlistItems` (`part=snippet,contentDetails,status`, `playlistId`),
  `POST /youtube/v3/playlistItems?part=snippet` and `DELETE /youtube/v3/playlistItems?id=...`
- `GET /youtube/v3/commentThreads` (`part=snippet`, `videoId`, `order`, `searchTerms`,
  `textFormat=plainText`)
- `POST /upload/youtube/v3/videos?uploadType=resumable&part=snippet,status&notifySubscribers=...`
  (`X-Upload-Content-Length`, `X-Upload-Content-Type`, JSON metadata), then `PUT`
  to the session URI with `Content-Range` (308 with `Range` until complete,
  `bytes */size` for the status)

OAuth 2.0 refresh-token grant: `POST https://oauth2.googleapis.com/token`
(`grant_type=refresh_token`). Errors follow the documented
`{"error": {"code", "message", "errors": [{"reason"}], "status", "details"}}`
envelope.

**Real-provider qualification is pending.** All tests use a loopback double that
reproduces these documented request and response shapes; no Google account,
key, token or channel was contacted. In particular, the `x-goog-api-key` header
with the YouTube API, the exact 308/`Range` behaviour of Google's upload
servers, uploads of several gigabytes, the quota costs and the visibility of
scheduled or premiere videos have not been verified against YouTube itself.

## Framing decisions

- Two credentials: an API key (`api_key_ref`) for public reads and an OAuth grant
  (`secret_ref`) for owner operations; `read_credential` chooses which one reads,
  and `mine` always uses the grant. The grant has the same shape as the other
  Google blocks.
- The framing's `playlist_items` operation is split into `playlist_items`
  (list), `playlist_add` (`allow_writes`) and `playlist_remove`
  (`allow_delete`), so the operation allowlist can grant each one separately.
  A removal by `playlist_id` and `video_id` removes the first matching item.
- No video, playlist or comment is ever deleted: YouTube has no trash, so the
  block offers no deletion at all; `playlist_remove` only edits a playlist.
- Listings emit a plain array on `items` and the summary on `result`, like the
  Google Drive block; the new uploads of a watch go to `changed`.
- `update_video` is a read-modify-write of whole parts and returns the previous
  values, because `videos.update` clears omitted fields.
- Uploads use Google's resumable protocol in 256 KiB-multiple chunks read from
  one open file, with a separate `upload_timeout_sec`; the default size bound
  is 256 MiB and the default privacy is `private`.
- The watch polls the uploads playlist (1 unit per page) rather than `search`
  (100 units). The minimum interval is 300 s, the default 900 s; seen IDs are
  persisted per channel and credential kind. `watch_uploads` gives One Shot
  graphs one check with the same state and the same minimum spacing.
- Video URLs (`watch`, `youtu.be`, `shorts`, `embed`, `live`), playlist URLs and
  channel URLs are accepted where IDs are expected; they are parsed, never
  fetched.
- Comments are read as plain text and marked `untrusted_content`; replies are
  counted, not listed.

## Tests

`tests/F5.01_youtube.py` checks settings, both credential kinds, request
contracts (URLs, tags budget, titles, gates before the wallet), resource
normalization (durations, hidden counts), the read-modify-write body of
`update_video`, provider error mapping, upload session and Range checks, watch
state validation and locking, upload-root confinement and block hooks without a
socket. `tests/F5.02_youtube_runtime.py` runs the real client against the
loopback double (`tests/youtube_fixture.py`, HTTP and HTTPS): searches with
exact page tokens and quota estimates, video details, channel uploads by handle,
ID and `mine`, playlists, comments (including disabled comments), playlist
insertions and removals, read-modify-write updates, resumable uploads (lost
chunks, a lost final answer, 503 and 429 resumptions, expired and foreign
sessions, cancellation), 400/401/403/404/429/5xx, quota, lost connections,
oversized and compressed bodies, cancellation and the deadline, single watch
checks (baseline, throttling, `emit_existing`, `busy`) and the listener
(restart, refused hand-off, errors, Stop within one second), then real graphs in
One Shot and Active Runtime for bundled, managed and linked packages, including
Stop while an insertion is pending and a watch emission at Run start.
`tests/F8.01_youtube_ui.py` covers the English and French forms at desktop and
mobile widths. They run in the private BloxSmith workspace against a pinned
framework; the initial development version does not imply a release.

## License

Block code: Apache-2.0. YouTube is an external service governed by Google's
terms. No Google SDK, logo, account data or credential is bundled.
