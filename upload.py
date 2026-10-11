"""videos.insert through Google's resumable upload protocol.

1. POST /upload/youtube/v3/videos?uploadType=resumable with the JSON metadata
   opens an upload session; Google answers its URI in Location. No video
   exists yet, so this step is safely retried.
2. PUT the file to that URI in chunks (multiples of 256 KiB) with Content-Range.
   Google answers 308 with the received Range until the last chunk, then 200 or
   201 with the video resource.
3. After a lost connection, a 5xx or a 429, the block asks the session for its
   status (PUT with Content-Range: bytes */size) and resumes from the received
   offset, at most max_retries times in a row. The upload is uncertain only when
   the final chunk was sent and no answer could be obtained.

The session URI must be on the configured API origin: the access token and
the file are never sent to another host. The file is read by offset from one
open descriptor and must not change during the upload.
"""

import re
from urllib.parse import parse_qs, urlsplit

from . import logic

RANGE = re.compile(r"bytes=0-([0-9]{1,20})\Z")


def session_target(location, origin):
    """The path and query of the upload session URI, refused unless it is on the API origin."""
    try:
        parsed, expected = urlsplit(location or ""), urlsplit(origin)
        same = (parsed.scheme == expected.scheme and (parsed.hostname or "").lower() == (expected.hostname or "").lower()
                and (parsed.port or (443 if parsed.scheme == "https" else 80))
                == (expected.port or (443 if expected.scheme == "https" else 80)))
    except ValueError:
        same = False
    if (not same or parsed.username is not None or parsed.path != logic.UPLOAD_PATH or parsed.fragment
            or not parse_qs(parsed.query).get("upload_id") or len(location) > 4096
            or any(ord(char) <= 32 or ord(char) >= 127 for char in location)):
        logic.fail("protocol", "Google answered an upload session URI outside the API origin; nothing was uploaded.")
    return parsed.path + "?" + parsed.query


def received(header, size):
    """Bytes Google holds, from a 308 Range header (none means nothing yet)."""
    if header is None:
        return 0
    match = RANGE.fullmatch(header.strip())
    if not match or int(match.group(1)) >= size:
        logic.fail("protocol", "Google answered an invalid upload Range; the upload was abandoned.")
    return int(match.group(1)) + 1


def initiate(session, request, size, media):
    """Open the resumable session; returns its path on the API origin."""
    query = {"uploadType": "resumable", "part": "snippet,status",
             "notifySubscribers": "true" if request["notify_subscribers"] else "false"}
    outcome = session.call("POST", logic.UPLOAD_PATH, query, logic.upload_metadata(request), auth="oauth",
                           cost=logic.COSTS["videos.insert"], mutation=False, replayable=True, full=True,
                           extra_headers={"X-Upload-Content-Length": str(size), "X-Upload-Content-Type": media})
    return session_target(outcome["headers"].get("location"), session.config["api_base_url"])


def wait(session, seconds, final_sent):
    """Pause before resuming; a cancellation after the final chunk was sent leaves the upload uncertain."""
    try:
        session.pause(seconds)
    except logic.YouTubeError as exc:
        if final_sent:
            logic.fail(exc.code, logic.MESSAGES["cancelled"], uncertain=True)
        raise


def transfer(session, target, source, media):
    """Send the chunks, resume after interruptions and return (video resource, chunks sent, resumes)."""
    config = session.config
    origin, size, step = config["api_base_url"], source.size, config["upload_chunk_bytes"]
    offset, failures, chunks, resumes, final_sent, query = 0, 0, 0, 0, False, False
    budget = 2 * (-(-size // step)) + 3 * config["max_retries"] + 4
    while budget > 0:
        budget -= 1
        headers = session.headers("oauth")
        query = query or offset >= size  # Everything is held but not finalized: ask for the outcome.
        if query:
            headers["Content-Range"] = "bytes */" + str(size)
            data = b""
        else:
            end = min(offset + step, size)
            if end == size:
                source.verify()  # A file changed on disk is abandoned before its last chunk can create a video.
            data = source.read(offset, end - offset)
            headers["Content-Type"] = media
            headers["Content-Range"] = f"bytes {offset}-{end - 1}/{size}"
            final_sent = final_sent or end == size
            chunks += 1
        query = False
        try:
            outcome = session.send(origin, "PUT", target, headers, data, mutation=final_sent, replayable=True,
                                   limit=config["max_response_bytes"])
        except logic.YouTubeError as exc:
            failures += 1
            if exc.code != "network" or failures > config["max_retries"]:
                raise
            wait(session, min(2 ** (failures - 1), 8), final_sent)
            query, resumes = True, resumes + 1
            continue
        status = outcome["status"]
        if status in (200, 201):
            if outcome["body_error"] or not isinstance(outcome["body"], dict):
                logic.fail("protocol", "Google completed the upload with an unreadable answer; check the channel's videos.",
                           uncertain=True)
            return outcome["body"], chunks, resumes
        if status == 308:
            offset, failures = received(outcome["headers"].get("range"), size), 0
            continue
        if status in (404, 410):
            logic.fail("upload_expired", http_status=status)
        if status == 429 or status >= 500:
            failures += 1
            delay = outcome["retry_after"] if outcome["retry_after"] is not None else min(2 ** (failures - 1), 8)
            if failures > config["max_retries"] or delay > config["max_retry_wait_sec"]:
                logic.provider_failure(status, outcome["body"], session.secrets, mutation=final_sent,
                                       retry_after=outcome["retry_after"])
            wait(session, delay, final_sent)
            query, resumes = True, resumes + 1
            continue
        logic.provider_failure(status, outcome["body"], session.secrets, mutation=False, retry_after=outcome["retry_after"])
    logic.fail("protocol", "The upload made no progress; it was abandoned.", uncertain=final_sent)


def upload_video(session, request, config, source):
    """Upload one local video (privacy private unless asked otherwise); returns the result fields."""
    request = {**request, "title": request["title"] or logic.upload_title(source.name)}
    media = logic.upload_type(source.name)
    target = initiate(session, request, source.size, media)
    body, chunks, resumes = transfer(session, target, source, media)
    video = logic.normalize_video(body)
    return {"video_id": video["id"], "video": video, "file_name": source.name, "size": source.size, "mime_type": media,
            "privacy": request["privacy"], "chunks": chunks, "resumed": resumes}
