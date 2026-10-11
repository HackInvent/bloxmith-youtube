"""FB2/FB3/FB4/FB5/FB6/FB7: loopback YouTube Data API protocol, resumable uploads, watch, errors, cancellation
and both runtimes.

Every provider exchange targets the loopback double in youtube_fixture.py. The
wallet and its fixture credentials exist only in disposable test storage.
"""

import json
from pathlib import Path
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / "tests"), str(Path(__file__).resolve().parent)]
from blocs.youtube import files, logic, operations, storage, transport, watcher
from blocs.youtube.block import YouTubeBlock
from block_test_packages import install_test_package, prepare_release_run, surface_payload
from youtube_fixture import (ACCESS, CHANNEL, COMMENTED, GRANT, HANDLE, INJECTION, KEY, MISSING, PLAYLIST, PRIVATE,
                             REFRESHED, SILENT, UPLOADS, VIDEOS, error, settings, youtube_api)
from ui_smoke_common import (create_project_api, create_run_api, data_edge, display_node, graph_payload, graph_storage_dir,
                             http_json, isolated_server, stop_run_api, text_node, wait_for_run_predicate,
                             wait_for_run_terminal)

OAUTH = {"access_token": ACCESS}
QUOTA = {"status": 403, "body": error(403, "The request cannot be completed because you have exceeded your quota.",
                                      "quotaExceeded", "PERMISSION_DENIED")}
RATE = {"status": 429, "body": error(429, "Too many requests.", "rateLimitExceeded", "RESOURCE_EXHAUSTED")}
EVERYTHING = {"allow_writes": True, "allow_delete": True, "allowed_operations": list(logic.OPERATIONS)}


def run(config, value, *, grant=OAUTH, key=KEY, cancel=lambda: False, storage=None, port="request", clock=None):
    """Execute one event through the real local checks, credential choice, session and operations."""
    request = logic.parse_request(value, config) if port == "request" else logic.file_request(value, config)
    kind = logic.credential_kind(request, config)
    source = files.open_upload(request["path"], config) if request["operation"] == "upload_video" else None
    credential = {"grant": grant} if kind == "oauth" else {"api_key": key}
    session = transport.Session(config, cancel, timeout=config["upload_timeout_sec"] if source else None, **credential)
    try:
        return operations.run(session, request, config, auth=kind, storage=storage, source=source, clock=clock)
    finally:
        if source is not None:
            source.close()


def failure(call, code):
    try:
        call()
    except logic.YouTubeError as exc:
        assert exc.code == code, (code, exc.code, str(exc))
        text = json.dumps(exc.envelope())
        assert ACCESS not in text and REFRESHED not in text and KEY not in text and GRANT["client_secret"] not in text
        return exc
    raise AssertionError("Expected " + code)


def titles(values):
    return [item["title"] for item in values["items"]]


def read_protocol():
    """# - FB2 - # - FB6 - search, details, channel uploads, playlists and comments; exact page tokens; quota estimates."""
    with youtube_api() as api:
        config = logic.configuration(settings(api, channel=HANDLE))
        found = run(config, "cat")
        assert titles(found) == ["Cat agility training", "Cat food review", "Cat versus laser", "Cat nap compilation",
                                 "Cat café visit"]
        result = found["result"]
        assert {key: result[key] for key in ("operation", "q", "type", "order", "count", "truncated", "pages", "requests",
                                             "quota_units")} == {
            "operation": "search", "q": "cat", "type": "video", "order": "relevance", "count": 5, "truncated": False,
            "pages": 1, "requests": 1, "quota_units": 100}
        call = api.calls[-1]
        assert call["key"] == KEY and call["auth"] is None and call["accept_encoding"] == "identity"
        assert call["query"]["part"] == "snippet" and call["query"]["maxResults"] == "50" and "key" not in call["query"]
        first = found["items"][0]
        assert first["kind"] == "video" and first["id"] == VIDEOS[0] and first["url"].endswith("v=" + VIDEOS[0])
        assert first["thumbnail_url"].endswith("hqdefault.jpg") and first["channel_id"] == CHANNEL
        dated = run(config, {"q": "cat", "order": "date", "published_after": "2026-01-01T02:30:00Z", "limit": 2})
        assert titles(dated) == ["Cat café visit", "Cat nap compilation"] and dated["result"]["truncated"]
        assert api.calls[-1]["query"]["publishedAfter"] == "2026-01-01T02:30:00Z" and api.calls[-1]["query"]["order"] == "date"
        # search_max_pages bounds the 100-unit pages; the token resumes exactly.
        small = logic.configuration(settings(api, page_size=2, search_max_pages=2))
        page = run(small, {"q": "cat", "limit": 50})
        assert page["result"]["pages"] == 2 and page["result"]["stop_reason"] == "max_pages" and page["result"]["quota_units"] == 200
        rest = run(small, {"q": "cat", "page_token": page["result"]["next_page_token"]})
        assert titles(page) + titles(rest) == titles(found) and rest["result"]["next_page_token"] is None
        assert [item["kind"] for item in run(config, {"q": "cat", "type": "channel"})["items"]] == ["channel"]
        assert run(config, {"q": "cat", "type": "playlist"})["items"][0]["id"] == PLAYLIST
        # Video details: durations in seconds, hidden likes, missing IDs reported, one request for up to 50 IDs.
        details = run(config, {"operation": "video_details", "video_ids": [VIDEOS[1], VIDEOS[3], MISSING, PRIVATE],
                               "request_id": "d-1"})
        assert [(item["id"], item["duration_seconds"], item["like_count"]) for item in details["items"]] == [
            (VIDEOS[1], 3723, 200), (VIDEOS[3], 45, None)]
        assert details["result"]["missing"] == [MISSING, PRIVATE] and details["result"]["quota_units"] == 1
        assert api.calls[-1]["query"]["part"] == "snippet,contentDetails,statistics,status"
        # Channel uploads by handle, ID, URL and mine (mine always uses the OAuth grant).
        uploads = run(config, {"operation": "channel_uploads", "limit": 3})
        assert titles(uploads) == ["Cat café visit", "Mountain bike trail", "Rust ownership explained"]
        assert uploads["result"]["uploads_playlist_id"] == UPLOADS and uploads["result"]["quota_units"] == 2
        assert api.calls[-2]["query"]["forHandle"] == HANDLE and api.calls[-1]["query"]["playlistId"] == UPLOADS
        listed = run(logic.configuration({**config, "operation": "channel_uploads"}), CHANNEL)
        assert api.calls[-2]["query"]["id"] == CHANNEL and listed["result"]["count"] == 12
        mine = run(config, {"operation": "channel_uploads", "channel": "mine", "limit": 50})
        assert api.calls[-2]["query"]["mine"] == "true" and api.calls[-1]["auth"] == "Bearer " + ACCESS
        assert mine["result"]["count"] == 13 and "Private draft" in titles(mine), "the owner sees private uploads"
        failure(lambda: run(config, {"operation": "channel_uploads", "channel": "@nobody-here"}), "not_found")
        # Playlist items: duplicates kept, exact continuation with a small page size.
        paged = logic.configuration(settings(api, page_size=3, list_limit=10))
        first_page = run(paged, {"operation": "playlist_items", "playlist_id": PLAYLIST, "limit": 3})
        assert [item["video_id"] for item in first_page["items"]] == [VIDEOS[2], VIDEOS[5], VIDEOS[2]]
        assert [item["position"] for item in first_page["items"]] == [0, 1, 2]
        second = run(paged, {"operation": "playlist_items", "playlist_id": PLAYLIST,
                             "page_token": first_page["result"]["next_page_token"]})
        assert [item["video_id"] for item in second["items"]] == [VIDEOS[8]] and not second["result"]["truncated"]
        failure(lambda: run(config, {"operation": "playlist_items", "playlist_id": "PLnope"}), "not_found")
        # Comments: plain text, newest first, untrusted, search terms, disabled comments.
        threads = run(config, {"operation": "comments", "video_id": COMMENTED, "limit": 20})
        assert threads["result"]["count"] == 7 and threads["items"][0]["text"] == "Where is this?"
        assert all(item["untrusted_content"] for item in threads["items"])
        assert INJECTION in [item["text"] for item in threads["items"]], "comment text is returned as data"
        assert api.calls[-1]["query"]["textFormat"] == "plainText" and api.calls[-1]["query"]["order"] == "time"
        assert [item["text"] for item in run(config, {"operation": "comments", "video_id": COMMENTED,
                                                       "search_terms": "cat"})["items"]] == ["Cat café looks nice",
                                                                                             "Loved the cat"]
        assert run(config, {"operation": "comments", "video_id": COMMENTED, "order": "relevance"})["items"][0]["like_count"] == 6
        disabled = failure(lambda: run(config, {"operation": "comments", "video_id": SILENT}), "comments_disabled")
        assert disabled.http_status == 403 and disabled.provider_reason == "commentsDisabled"
        assert run(config, {"operation": "comments", "video_id": VIDEOS[4]})["result"]["count"] == 0
        failure(lambda: run(config, {"operation": "comments", "video_id": MISSING}), "not_found")
        # read_credential oauth reads with the grant; a refresh-token grant is exchanged once per activation.
        owner = logic.configuration(settings(api, read_credential="oauth"))
        before = len(api.calls)
        assert run(owner, {"operation": "video_details", "video_ids": [PRIVATE]}, grant=GRANT)["items"][0]["privacy_status"] \
            == "private"
        assert api.kinds()[before:] == ["token", "videos"] and api.calls[-1]["auth"] == "Bearer " + REFRESHED
        refused = failure(lambda: run(owner, "cat", grant={**GRANT, "refresh_token": "revoked"}), "auth_failed")
        assert refused.provider_status == "invalid_grant"
        invalid = failure(lambda: run(config, "cat", key="-".join(("wrong", "fixture", "key"))), "key_invalid")
        assert invalid.http_status == 400 and invalid.provider_status == "INVALID_ARGUMENT"
    print("[ok] YouTube search, details, channel uploads, playlists, comments, tokens and quota estimates", flush=True)


def change_protocol():
    """# - FB3 - playlist insertions and removals, video updates by read-modify-write; videos are never deleted."""
    with youtube_api() as api:
        config = logic.configuration(settings(api, **EVERYTHING, upload_root="/srv/videos"))
        added = run(config, {"operation": "playlist_add", "playlist_id": PLAYLIST, "video_id": VIDEOS[0], "position": 1,
                             "request_id": "a-1"})["result"]
        assert added["request_id"] == "a-1" and added["item"]["video_id"] == VIDEOS[0] and added["item"]["position"] == 1
        assert added["quota_units"] == 50 and api.calls[-1]["auth"] == "Bearer " + ACCESS and api.calls[-1]["key"] is None
        assert api.calls[-1]["body"] == {"snippet": {"playlistId": PLAYLIST, "position": 1,
                                                     "resourceId": {"kind": "youtube#video", "videoId": VIDEOS[0]}}}
        assert [row["video"] for row in api.playlists[PLAYLIST]] == [VIDEOS[2], VIDEOS[0], VIDEOS[5], VIDEOS[2], VIDEOS[8]]
        removed = run(config, {"operation": "playlist_remove", "playlist_item_id": added["item"]["id"]})["result"]
        assert removed["removed"] is True and removed["quota_units"] == 50 and api.kinds()[-1] == "delete"
        looked = run(config, {"operation": "playlist_remove", "playlist_id": PLAYLIST, "video_id": VIDEOS[2]})["result"]
        assert looked["matches"] == 2 and looked["quota_units"] == 51 and api.kinds()[-2:] == ["items", "delete"]
        assert [row["video"] for row in api.playlists[PLAYLIST]] == [VIDEOS[5], VIDEOS[2], VIDEOS[8]]
        assert all(ident in api.videos for ident in VIDEOS), "removing a playlist item never deletes a video"
        assert "DELETE" not in [call["method"] for call in api.calls if call["path"].endswith("/videos")]
        failure(lambda: run(config, {"operation": "playlist_remove", "playlist_id": PLAYLIST, "video_id": VIDEOS[11]}),
                "not_found")
        failure(lambda: run(config, {"operation": "playlist_remove", "playlist_item_id": "UExub3RoZXJlMDAx"}), "not_found")
        failure(lambda: run(config, {"operation": "playlist_add", "playlist_id": PLAYLIST, "video_id": MISSING}),
                "not_found")
        # update_video carries the current snippet and status: only the requested fields change.
        video = api.videos[VIDEOS[0]]
        video["status"].update(embeddable=False, license="creativeCommon")
        updated = run(config, {"operation": "update_video", "video_id": VIDEOS[0], "title": "Cat agility, part 2",
                               "privacy": "unlisted"})["result"]
        assert updated["changed"] == ["privacy", "title"] and updated["previous"] == {"title": "Cat agility training",
                                                                                     "privacy": "public"}
        assert api.kinds()[-2:] == ["videos", "update"] and api.calls[-1]["query"]["part"] == "snippet,status"
        assert updated["quota_units"] == 51 and updated["video"]["title"] == "Cat agility, part 2"
        assert video["snippet"]["description"] == "Description of Cat agility training." and video["snippet"]["tags"] == [
            "cats", "pets"], "fields not named in the request keep their values"
        assert video["status"]["embeddable"] is False and video["status"]["license"] == "creativeCommon"
        assert video["snippet"]["defaultLanguage"] == "en" and video["status"]["privacyStatus"] == "unlisted"
        run(config, {"operation": "update_video", "video_id": VIDEOS[0], "tags": []})
        assert api.calls[-1]["query"]["part"] == "snippet" and "status" not in api.calls[-1]["body"]
        assert "tags" not in video["snippet"] or video["snippet"]["tags"] == []
        failure(lambda: run(config, {"operation": "update_video", "video_id": MISSING, "title": "x"}), "not_found")
        before = len(api.calls)
        failure(lambda: run({**config, "allow_writes": False}, {"operation": "update_video", "video_id": VIDEOS[0],
                                                                "title": "x"}), "writes_disabled")
        failure(lambda: run({**config, "allow_delete": False}, {"operation": "playlist_remove",
                                                                "playlist_item_id": "UExub3RoZXJlMDAx"}), "delete_disabled")
        assert len(api.calls) == before
    print("[ok] YouTube playlist changes and video updates", flush=True)


def upload_protocol():
    """# - FB4 - resumable uploads: chunks, Range answers, resumption, lost final answers, refusals and cancellation."""
    with youtube_api() as api, tempfile.TemporaryDirectory() as folder:
        root = Path(folder).resolve()
        clip = bytes(range(256)) * 2734 + b"end"  # 699907 bytes: three 256 KiB chunks.
        (root / "holiday clip.mp4").write_bytes(clip)
        (root / "big.mkv").write_bytes(b"x" * 1048577)
        config = logic.configuration(settings(api, **EVERYTHING, upload_root=str(root), upload_chunk_bytes=262144,
                                              max_upload_bytes=1048576, max_retry_wait_sec=2))
        done = run(config, "holiday clip.mp4", port="file")["result"]
        session = api.sessions["fx000001"]
        assert bytes(session["data"]) == clip and session["type"] == "video/mp4" and session["notify"] == "false"
        assert session["metadata"] == {"snippet": {"title": "holiday clip", "description": "", "categoryId": "22"},
                                       "status": {"privacyStatus": "private", "selfDeclaredMadeForKids": False}}
        assert api.kinds()[-4:] == ["initiate", "chunk", "chunk", "chunk"]
        assert [call["range"] for call in api.calls[-3:]] == ["bytes 0-262143/699907", "bytes 262144-524287/699907",
                                                              "bytes 524288-699906/699907"]
        assert all(call["auth"] == "Bearer " + ACCESS for call in api.calls[-4:])
        assert (done["chunks"], done["resumed"], done["size"], done["privacy"], done["quota_units"]) == (3, 0, len(clip),
                                                                                                         "private", 1600)
        assert done["video"]["id"] == done["video_id"] and api.videos[done["video_id"]]["status"]["privacyStatus"] == "private"
        assert done["video"]["upload_status"] == "uploaded" and done["file_name"] == "holiday clip.mp4"
        # A request may name the title, tags, category, privacy and audience; subscribers may be notified.
        named = run(config, {"operation": "upload_video", "path": str(root / "holiday clip.mp4"), "title": "Holiday",
                             "description": "Summer\nfun", "tags": ["summer", "beach day"], "category_id": "19",
                             "privacy": "unlisted", "made_for_kids": True, "notify_subscribers": True})["result"]
        metadata = api.sessions["fx000002"]["metadata"]
        assert metadata["snippet"] == {"title": "Holiday", "description": "Summer\nfun", "categoryId": "19",
                                       "tags": ["summer", "beach day"]}
        assert metadata["status"] == {"privacyStatus": "unlisted", "selfDeclaredMadeForKids": True}
        assert api.sessions["fx000002"]["notify"] == "true" and named["privacy"] == "unlisted"
        # A connection lost after Google stored a chunk: ask for the status, resume from its Range.
        api.script["chunk"] = [None, "lost_after"]
        resumed = run(config, "holiday clip.mp4", port="file")["result"]
        assert resumed["resumed"] == 1 and bytes(api.sessions["fx000003"]["data"]) == clip
        assert api.kinds()[-5:] == ["initiate", "chunk", "chunk", "chunk", "chunk"]
        assert api.calls[-2]["range"] == "bytes */699907" and api.calls[-1]["range"] == "bytes 524288-699906/699907"
        # The final answer is lost after the video was created: the status query recovers it, nothing is uploaded twice.
        api.script["chunk"] = [None, None, "lost_after"]
        recovered = run(config, "holiday clip.mp4", port="file")["result"]
        assert recovered["resumed"] == 1 and api.calls[-1]["range"] == "bytes */699907"
        assert recovered["video_id"] == api.sessions["fx000004"]["video"]
        # 503 on a chunk, then success; 429 with Retry-After is honoured.
        api.script["chunk"] = [{"status": 503, "body": error(503, "Backend Error", "backendError")},
                               {**RATE, "headers": {"Retry-After": "1"}}]
        started = time.monotonic()
        assert run(config, "holiday clip.mp4", port="file")["result"]["resumed"] == 2 and time.monotonic() - started >= 1
        # Refusals: an expired session, a foreign session URI, a refused initiation and an oversized file.
        api.script["chunk"] = [{"status": 404, "body": error(404, "Not Found", "notFound")}]
        failure(lambda: run(config, "holiday clip.mp4", port="file"), "upload_expired")
        api.foreign_location = True
        before = len(api.calls)
        failure(lambda: run(config, "holiday clip.mp4", port="file"), "protocol")
        assert api.kinds()[before:] == ["initiate"], "no byte or token goes to another origin"
        api.foreign_location = False
        api.script["initiate"] = [{"status": 403, "body": error(403, "Upload limit exceeded.", "uploadLimitExceeded")}]
        assert not failure(lambda: run(config, "holiday clip.mp4", port="file"), "upload_limit").uncertain
        before = len(api.calls)
        failure(lambda: run(config, "big.mkv", port="file"), "file_too_large")
        assert len(api.calls) == before
        # Cancellation during a middle chunk: nothing was created.
        api.script["chunk"] = [None, "hold"]
        started = time.monotonic()
        cancelled = failure(lambda: run(config, "holiday clip.mp4", port="file",
                                        cancel=lambda: api.holding > 0 and time.monotonic() - started > 0.3), "cancelled")
        assert not cancelled.uncertain and time.monotonic() - started < 5
        api.release.set()
        time.sleep(0.2)
        api.release.clear()
        # Cancellation while the final chunk is held: the outcome is uncertain.
        api.script["chunk"] = [None, None, "hold"]
        uncertain = failure(lambda: run(config, "holiday clip.mp4", port="file",
                                        cancel=lambda: api.holding > 0), "cancelled")
        assert uncertain.uncertain
        api.release.set()
        time.sleep(0.2)
        assert not [thread for thread in threading.enumerate() if thread.name == "youtube-http"]
    print("[ok] YouTube resumable uploads, resumption, refusals and cancellation", flush=True)


def error_protocol():
    """# - FB6 - 401/403/404/429/5xx, quota, lost connections, uncertain changes, cancellation, deadline and TLS."""
    with youtube_api() as api:
        config = logic.configuration(settings(api, **EVERYTHING, upload_root="/srv/videos", max_retry_wait_sec=2))
        add = {"operation": "playlist_add", "playlist_id": PLAYLIST, "video_id": VIDEOS[0]}
        expired = failure(lambda: run(config, add, grant={"access_token": "expired-fixture-credential"}), "unauthorized")
        assert expired.provider_status == "UNAUTHENTICATED" and not expired.uncertain
        api.script["search"] = [QUOTA]
        quota = failure(lambda: run(config, "cat"), "quota_exceeded")
        assert quota.attempts == 1 and quota.provider_reason == "quotaExceeded", "a daily quota is never retried"
        api.script["search"] = [{**RATE, "headers": {"Retry-After": "1"}}]
        started = time.monotonic()
        assert run(config, "cat")["result"]["quota_units"] == 200 and time.monotonic() - started >= 1
        api.script["search"] = [{"status": 403, "body": error(403, "Rate limit", "userRateLimitExceeded")}]
        assert run(config, "cat")["result"]["requests"] == 2
        api.script["search"] = [{**RATE, "headers": {"Retry-After": "30"}}]
        limited = failure(lambda: run(config, "cat"), "rate_limited")
        assert limited.retry_after == 30 and limited.attempts == 1
        api.script["videos"] = ["html502", "lost"]
        before = len(api.calls)
        assert run(config, {"operation": "video_details", "video_id": VIDEOS[0]})["result"]["count"] == 1
        assert api.kinds()[before:] == ["videos"] * 3
        api.script["update"] = [{"status": 503, "body": error(503, "Backend Error", "backendError")}]
        before = len(api.calls)
        run(config, {"operation": "update_video", "video_id": VIDEOS[0], "title": "Retried"})
        assert api.kinds()[before:] == ["videos", "update", "update"], "video updates are idempotent and retried"
        for scripted, code in ((RATE, None), ({"status": 503, "body": error(503, "Backend Error", "backendError")},
                                              "provider_unavailable"), ("html502", "provider_unavailable"),
                               ("lost", "network"), ("oversized", "protocol"), ("gzip", "protocol")):
            api.script["insert"] = [scripted]
            before = len(api.playlists[PLAYLIST])
            calls = len(api.calls)
            if code is None:
                assert run(config, add)["result"]["item"]["video_id"] == VIDEOS[0]
                assert api.kinds()[calls:] == ["insert", "insert"], "a rate-limited insertion was not processed"
                continue
            outcome = failure(lambda: run(logic.configuration({**config, "max_response_bytes": 65536}), add), code)
            assert outcome.uncertain and api.kinds()[calls:].count("insert") == 1, scripted
        api.script["items"] = ["oversized"]
        assert not failure(lambda: run(logic.configuration({**config, "max_response_bytes": 65536, "max_retries": 0}),
                                       {"operation": "playlist_items", "playlist_id": PLAYLIST}), "protocol").uncertain
        # Cancellation and the deadline shut the socket down while Google holds the request.
        api.script["insert"] = ["hold"]
        started = time.monotonic()
        cancelled = failure(lambda: run(config, add, cancel=lambda: time.monotonic() - started > 0.4), "cancelled")
        assert cancelled.uncertain and time.monotonic() - started < 2.5
        api.release.set()
        time.sleep(0.2)
        api.release.clear()
        api.script["search"] = ["hold"]
        started = time.monotonic()
        quiet = failure(lambda: run(config, "cat", cancel=lambda: time.monotonic() - started > 0.4), "cancelled")
        assert not quiet.uncertain and time.monotonic() - started < 2.5
        api.release.set()
        time.sleep(0.2)
        api.release.clear()
        api.script["delete"] = ["hold"]
        started = time.monotonic()
        timed = failure(lambda: run(logic.configuration({**config, "timeout_sec": 5}),
                                    {"operation": "playlist_remove", "playlist_item_id": "UExub3RoZXJlMDAx"}), "timeout")
        assert timed.uncertain and 4 <= time.monotonic() - started < 8
        api.release.set()
        time.sleep(0.3)
        assert not [thread for thread in threading.enumerate() if thread.name == "youtube-http"]
        assert not failure(lambda: run(logic.configuration(settings(api, api_base_url="http://127.0.0.1:9", max_retries=0)),
                                       "cat"), "network").uncertain
        failure(lambda: run(config, "cat", cancel=lambda: True), "cancelled")
    with youtube_api(tls=True) as api:
        config = logic.configuration(settings(api))
        assert run(config, "cat")["result"]["count"] == 5
        before = len(api.calls)
        untrusted = failure(lambda: run(logic.configuration(settings(api, ca_file="")), "cat"), "tls")
        assert not untrusted.uncertain and len(api.calls) == before
    print("[ok] YouTube errors, retries, quota, uncertainty, cancellation, deadline and TLS", flush=True)


def watcher_for(config, directory, emitted, *, key=KEY, refuse=False, stop=lambda: False, wall=time.time):
    """A watcher whose store is opened like run() does, so cycles can be driven one by one."""
    def emit(kind, value, stats):
        if refuse and kind == "changed":
            raise RuntimeError("runtime_listeners.queue_full")
        emitted.append((kind, value, stats))

    secrets = {config["api_key_ref"]: key, config["secret_ref"]: json.dumps(OAUTH)}
    instance = watcher.Watcher(config, resolve_secret=lambda ref: secrets[ref], storage_dir=lambda: directory, emit=emit,
                               stop=stop, wall=wall)
    instance.store = storage.WatchStore(directory)
    assert instance.store.acquire()
    instance.document = instance.store.load()
    return instance


def delivered(emitted):
    return [value["items"][0]["video_id"] for kind, value, _stats in emitted if kind == "changed"]


def watch_protocol():
    """# - FB5 - single checks and the listener: baseline, new uploads once, throttling, restarts, emit_existing,
    refusal, errors, busy lock and Stop."""
    with youtube_api() as api, tempfile.TemporaryDirectory() as folder:
        directory = Path(folder).resolve()
        node = directory / "node"
        node.mkdir()
        config = logic.configuration(settings(api, channel=HANDLE, watch_max_items=2))
        now = [2000000000]
        clock = lambda: now[0]  # noqa: E731 - a settable test clock
        check = {"operation": "watch_uploads"}
        first = run(config, check, storage=lambda: node, clock=clock)
        assert "changed" not in first and first["result"]["baseline"] and first["result"]["count"] == 0
        assert first["result"]["quota_units"] == 2 and api.kinds()[-2:] == ["channels", "items"]
        state = json.loads((node / storage.STATE_NAME).read_text())
        entry = list(state["watches"].values())[0]
        assert entry["playlist_id"] == UPLOADS and len(entry["seen"]) == 12 and entry["seen"][-1] == VIDEOS[-1]
        fresh = api.publish("Brand new cat video")
        before = len(api.calls)
        throttled = run(config, check, storage=lambda: node, clock=clock)["result"]
        assert throttled["throttled"] and throttled["next_check_in_sec"] == 900 and len(api.calls) == before
        assert throttled["quota_units"] == 0, "a throttled check sends nothing"
        now[0] += 900
        second = run(config, check, storage=lambda: node, clock=clock)
        assert [item["video_id"] for item in second["changed"]["items"]] == [fresh]
        assert second["changed"]["channel"] == HANDLE and second["changed"]["playlist_id"] == UPLOADS
        assert second["result"]["count"] == 1 and api.kinds()[before:] == ["items"], "the uploads playlist is remembered"
        api.publish("Private one", privacy="private")
        newer = [api.publish(f"Burst {index}") for index in range(3)]
        now[0] += 900
        third = run(config, check, storage=lambda: node, clock=clock)
        assert [item["video_id"] for item in third["changed"]["items"]] == newer[:2], "oldest first, watch_max_items"
        now[0] += 900
        assert [item["video_id"] for item in run(config, check, storage=lambda: node, clock=clock)["changed"]["items"]] == [
            newer[2]], "the rest waits for the next check; private uploads stay invisible to an API key"
        now[0] += 900
        assert "changed" not in run(config, check, storage=lambda: node, clock=clock)
        holder = storage.WatchStore(node)
        assert holder.acquire()
        failure(lambda: run(config, {**check, "channel": CHANNEL}, storage=lambda: node, clock=clock), "busy")
        holder.close()
        failure(lambda: run(config, check, storage=None), "storage")
        # emit_existing: the first check also emits the newest uploads, bounded; with mine the grant sees private ones.
        eager = logic.configuration(settings(api, channel="mine", emit_existing=True, watch_max_items=3))
        shown = run(eager, check, storage=lambda: node, clock=clock)
        assert [item["video_id"] for item in shown["changed"]["items"]] == list(reversed(api.uploads[:3]))
        assert api.calls[-1]["auth"] == "Bearer " + ACCESS and not shown["result"]["baseline"]
        # The listener: the same rules, one emission per upload, and a restart never replays.
        listener_config = logic.configuration(settings(api, channel=CHANNEL, watch_enabled=True, watch_max_items=5))
        listen_dir = directory / "listener"
        listen_dir.mkdir()
        emitted = []
        listener = watcher_for(listener_config, listen_dir, emitted)
        listener.cycle()
        assert delivered(emitted) == [] and emitted[-1][0] == "status" and emitted[-1][2]["state"] == "listening"
        one, two = api.publish("Listener one"), api.publish("Listener two")
        listener.cycle()
        assert delivered(emitted) == [one, two]
        envelope = [value for kind, value, _stats in emitted if kind == "changed"][0]
        assert envelope["operation"] == "watch_uploads" and envelope["count"] == 1 and envelope["request_id"] is None
        assert envelope["items"][0]["title"] == "Listener one" and envelope["channel_id"] == CHANNEL
        listener.cycle()
        assert delivered(emitted) == [one, two], "nothing is emitted twice"
        assert emitted[-1][2]["emitted"] == 2 and emitted[-1][2]["quota_units"] == 4
        listener.store.close()
        offline = api.publish("While stopped")
        emitted.clear()
        restarted = watcher_for(listener_config, listen_dir, emitted)
        restarted.cycle()
        assert delivered(emitted) == [offline]
        refused_video = api.publish("Refused once")
        restarted.store.close()
        blocked = watcher_for(listener_config, listen_dir, [], refuse=True)
        try:
            blocked.cycle()
            raise AssertionError("A refused hand-off must halt the listener")
        except watcher.Halt:
            pass
        blocked.store.close()
        emitted.clear()
        again = watcher_for(listener_config, listen_dir, emitted)
        again.cycle()
        assert delivered(emitted) == [refused_video]
        again.store.close()
        # Errors are reported once per distinct code; the wallet value never appears.
        emitted.clear()
        wrong = "-".join(("revoked", "fixture", "key"))
        failing = watcher_for(listener_config, listen_dir, emitted, key=wrong)
        failing.cycle()
        failing.cycle()
        errors = [value for kind, value, _stats in emitted if kind == "error"]
        assert [item["code"] for item in errors] == ["key_invalid"] and errors[0]["operation"] == "watch_uploads"
        assert failing.stats["errors"] == 2 and wrong not in json.dumps(errors)
        failing.store.close()
        # The listener waits for the interval after a recent stored check, and Stop ends it promptly, even mid-request.
        stop = threading.Event()
        holder = storage.WatchStore(listen_dir)
        assert holder.acquire()
        emitted.clear()
        busy = watcher.Watcher(listener_config, resolve_secret=lambda ref: KEY, storage_dir=lambda: listen_dir,
                               emit=lambda kind, value, stats: emitted.append((kind, value, stats)), stop=stop.is_set)
        thread = threading.Thread(target=busy.run)
        thread.start()
        deadline = time.monotonic() + 5
        while not emitted and time.monotonic() < deadline:
            time.sleep(0.05)
        assert emitted and emitted[0][1]["code"] == "busy"
        stop.set()
        thread.join(2)
        assert not thread.is_alive()
        holder.close()
        stop.clear()
        before = len(api.calls)
        recent = watcher.Watcher(listener_config, resolve_secret=lambda ref: KEY, storage_dir=lambda: listen_dir,
                                 emit=lambda *args: None, stop=stop.is_set)
        thread = threading.Thread(target=recent.run)
        thread.start()
        time.sleep(0.6)
        assert len(api.calls) == before, "a check stored less than watch_interval_sec ago is not repeated at start"
        stop.set()
        thread.join(2)
        assert not thread.is_alive()
        stop.clear()
        fresh_dir = directory / "held"
        fresh_dir.mkdir()
        api.script["channels"] = ["hold"]
        live = watcher.Watcher(listener_config, resolve_secret=lambda ref: KEY, storage_dir=lambda: fresh_dir,
                               emit=lambda *args: None, stop=stop.is_set)
        thread = threading.Thread(target=live.run)
        thread.start()
        deadline = time.monotonic() + 10
        while api.holding < 1 and time.monotonic() < deadline:
            time.sleep(0.05)
        assert api.holding == 1, "held channel request not reached"
        started = time.monotonic()
        stop.set()
        thread.join(2)
        assert not thread.is_alive() and time.monotonic() - started < 1.0, "Stop ends a watch within one second"
        api.release.set()
    print("[ok] YouTube watch checks, throttling, emit_existing, listener, restart, refusal, errors and Stop", flush=True)


def value(data, port):
    raw = data.get("output_values", {}).get("tube:" + str(port), {}).get("value")
    return json.loads(raw) if raw else None


def wallet(server, grant):
    http_json(server.base_url, "/api/application/secrets/init", method="POST", payload={"password": "disposable-youtube-fixture"})
    for name, secret in (("youtube_key_fixture", KEY), ("youtube_fixture", json.dumps(grant))):
        http_json(server.base_url, "/api/application/secrets", method="POST", payload={"name": name, "value": secret})


def clean(data):
    text = json.dumps(data)
    assert ACCESS not in text and REFRESHED not in text and KEY not in text and GRANT["client_secret"] not in text


def graph_runs():
    """# - FB7 - # - FB5 - real graphs in One Shot and Active Runtime for bundled, managed and linked packages, with Stop."""
    block = YouTubeBlock()
    for mode in ("centralized", "zeromq_active"):
        for origin in (None, "managed", "linked"):
            full = origin is None
            with youtube_api() as api, isolated_server() as server:
                videos = server.root_dir / "fixture-videos"
                videos.mkdir()
                (videos / "graph clip.mp4").write_bytes(b"graph video " * 30000)
                (server.root_dir / "outside.mp4").write_bytes(b"outside the upload root")
                overrides = settings(api, **EVERYTHING, upload_root=str(videos), upload_chunk_bytes=262144, timeout_sec=20,
                                     channel=HANDLE, watch_enabled=mode == "zeromq_active")
                node = block.build_node_payload(node_id="tube", position={"x": 380, "y": 160}, config_overrides=overrides)
                if origin:
                    model = install_test_package(server, "youtube", origin=origin)
                    node["block_version"] = model["version"]
                    for surface in ("modal", "inspector_panel", "node_card"):
                        html = surface_payload(server, model, node, surface=surface)["html"]
                        assert "{{" not in html and KEY not in html
                        if surface != "node_card":
                            assert 'data-block-config-field="allowed_operations"' in html
                wallet(server, GRANT if full else OAUTH)
                sinks = [display_node("listed", "Items", 760, 20), display_node("summary", "Result", 760, 160),
                         display_node("news", "Changed", 760, 300), display_node("problem", "Problem", 760, 440)]
                edges = [data_edge("o1", "tube", 1, "listed", 1), data_edge("o2", "tube", 2, "summary", 1),
                         data_edge("o3", "tube", 3, "news", 1), data_edge("o4", "tube", 4, "problem", 1)]

                def graph(request="", video=""):
                    return graph_payload("YouTube", [text_node("req", "Request", request, 60, 100),
                                                     text_node("src", "Video file", video, 60, 320), node, *sinks],
                                         [data_edge("in", "req", 1, "tube", 1), data_edge("up", "src", 1, "tube", 2), *edges])

                if mode == "centralized":
                    def once(document, expect="success"):
                        project = create_project_api(server, document=document)["project"]
                        created = create_run_api(server, document, project_id=project["graph_id"], runtime_mode=mode)
                        data = wait_for_run_terminal(server, created["run_id"], timeout_sec=40)
                        assert data["status"] == expect, (origin, data.get("status"), data.get("logs"))
                        clean(data)
                        return data, project

                    data, _ = once(graph(json.dumps({"request_id": "q-1", "q": "cat", "limit": 2})))
                    assert value(data, 2)["request_id"] == "q-1" and value(data, 2)["quota_units"] == 100
                    assert [item["title"] for item in value(data, 1)] == ["Cat agility training", "Cat food review"]
                    assert value(data, 4) is None and data["results"]["tube"]["youtube"]["quota_units"] == 100
                    # Both inputs wired: the details request and the upload each run exactly once.
                    before = len(api.sessions)
                    data, project = once(graph(json.dumps({"operation": "video_details", "video_ids": VIDEOS[:2]}),
                                               str(videos / "graph clip.mp4")))
                    assert len(api.sessions) == before + 1 and value(data, 1)[0]["duration_seconds"] == 253
                    uploads = [call for call in api.calls if call["kind"] == "initiate"]
                    assert len(uploads) == 1, api.kinds()
                    # watch_uploads in One Shot: one check, stored in the node storage.
                    data, project = once(graph(json.dumps({"operation": "watch_uploads"})))
                    assert value(data, 2)["baseline"] is True and value(data, 3) is None
                    stored = graph_storage_dir(server, project) / "instances" / "1" / "blocs" / "tube" / storage.STATE_NAME
                    assert stored.is_file() and KEY not in stored.read_text()
                    if full:
                        before = len(api.calls)
                        data, _ = once(graph("", str(server.root_dir / "outside.mp4")))
                        assert value(data, 4)["code"] == "path_denied" and len(api.calls) == before
                        assert data["results"]["tube"]["youtube"]["error_codes"] == [value(data, 4)["code"]]
                        http_json(server.base_url, "/api/application/secrets/lock", method="POST", payload={})
                        data, _ = once(graph("cat"))
                        assert value(data, 4)["code"] == "secret" and len(api.calls) == before
                        http_json(server.base_url, "/api/application/secrets/unlock", method="POST",
                                  payload={"password": "disposable-youtube-fixture"})
                        # Stop while Google holds a playlist insertion: the run ends promptly and nothing is retried.
                        api.script["insert"] = ["hold"]
                        document = graph(json.dumps({"operation": "playlist_add", "playlist_id": PLAYLIST,
                                                     "video_id": VIDEOS[0]}))
                        project = create_project_api(server, document=document)["project"]
                        rid = create_run_api(server, document, project_id=project["graph_id"], runtime_mode=mode)["run_id"]
                        deadline = time.monotonic() + 20
                        while api.holding < 1 and time.monotonic() < deadline:
                            time.sleep(0.05)
                        assert api.holding == 1, "held insertion not reached"
                        held, started = len(api.calls), time.monotonic()
                        stop_run_api(server, rid)
                        stopped = wait_for_run_terminal(server, rid, timeout_sec=20)
                        assert time.monotonic() - started < 10, stopped.get("logs")
                        assert stopped["status"] == "cancelled", (stopped["status"], value(stopped, 4), stopped.get("logs"))
                        assert value(stopped, 2) is None and len(api.calls) == held, "a stopped insertion is never replayed"
                        assert "shutdown_timeout" not in json.dumps(stopped.get("logs"))
                        api.release.set()
                else:
                    document = graph()
                    project = create_project_api(server, document=document)["project"]
                    node_dir = graph_storage_dir(server, project) / "instances" / "1" / "blocs" / "tube"
                    node_dir.mkdir(parents=True, exist_ok=True)
                    # A stored check from a previous Run: uploads published since then are emitted at start.
                    digest = logic.watch_digest(HANDLE, "api_key")
                    seen = list(reversed([ident for ident in api.uploads if ident != PRIVATE]))
                    (node_dir / storage.STATE_NAME).write_text(json.dumps({"schema_version": 1, "watches": {digest: {
                        "playlist_id": UPLOADS, "seen": seen, "last_poll": int(time.time()) - 100000}}}))
                    arrived = api.publish("Live upload " + str(origin))

                    def publish(rid, source, text):
                        http_json(server.base_url, f"/api/runs/{rid}/active/control", method="POST", payload={
                            "action": "publish_output", "node_id": source, "port_id": 1, "value": text,
                            "content_type": "text/plain"})

                    def wait(rid, predicate, label):
                        data = wait_for_run_predicate(server, rid, lambda item: item.get("status") == "failed" or predicate(item),
                                                      label, timeout_sec=30)
                        assert data.get("status") != "failed", data.get("logs")
                        clean(data)
                        return data

                    def stop(rid):
                        started = time.monotonic()
                        stop_run_api(server, rid)
                        stopped = wait_for_run_terminal(server, rid, timeout_sec=20)
                        assert time.monotonic() - started < 10, stopped.get("logs")
                        assert "shutdown_timeout" not in json.dumps(stopped.get("logs"))
                        return stopped

                    rid = prepare_release_run(server, project["graph_id"], document)["run_id"]
                    http_json(server.base_url, f"/api/runs/{rid}/play", method="POST", payload={})
                    try:
                        data = wait(rid, lambda item: (value(item, 3) or {}).get("operation") == "watch_uploads",
                                    "watch missing")
                        watched = value(data, 3)
                        assert [item["video_id"] for item in watched["items"]] == [arrived]
                        assert watched["items"][0]["title"] == "Live upload " + str(origin)
                        assert set(api.kinds()) <= {"items"}, api.kinds()
                        publish(rid, "req", json.dumps({"request_id": "live-1", "q": "cat", "limit": 3}))
                        data = wait(rid, lambda item: (value(item, 2) or {}).get("request_id") == "live-1", "search missing")
                        assert value(data, 2)["count"] == 3 and len(value(data, 1)) == 3
                        publish(rid, "src", str(videos / "graph clip.mp4"))
                        data = wait(rid, lambda item: (value(item, 2) or {}).get("operation") == "upload_video",
                                    "upload missing")
                        assert value(data, 2)["privacy"] == "private" and value(data, 2)["chunks"] == 2
                        if full:
                            publish(rid, "req", json.dumps({"request_id": "live-2", "operation": "comments",
                                                            "video_id": SILENT}))
                            data = wait(rid, lambda item: (value(item, 4) or {}).get("request_id") == "live-2",
                                        "error missing")
                            assert value(data, 4)["code"] == "comments_disabled" and value(data, 4)["quota_units"] == 1
                            api.script["search"] = ["hold"]
                            publish(rid, "req", json.dumps({"request_id": "live-3", "q": "cat"}))
                            deadline = time.monotonic() + 20
                            while api.holding < 1 and time.monotonic() < deadline:
                                time.sleep(0.05)
                            assert api.holding == 1, "held search not reached"
                    finally:
                        stop(rid)
                        api.release.set()
                    state = json.loads((node_dir / storage.STATE_NAME).read_text())
                    assert arrived in state["watches"][digest]["seen"], "the emitted upload is remembered"
                logs = "\n".join(server.logs)
                assert ACCESS not in logs and REFRESHED not in logs and KEY not in logs and GRANT["client_secret"] not in logs
            print("[ok] YouTube graph " + mode + " " + str(origin), flush=True)


def main():
    read_protocol()
    change_protocol()
    upload_protocol()
    error_protocol()
    watch_protocol()
    graph_runs()


if __name__ == "__main__":
    main()
