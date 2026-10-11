"""FB1/FB2/FB3/FB4/FB5/FB6/FB7/FB8: pure settings, credentials, requests, resources, updates, errors, uploads,
watch state, local files and block hooks.

No socket is opened and no provider is contacted; the wallet resolver is a local double.
"""

import json
import os
from pathlib import Path
import sys
import tempfile
from types import MappingProxyType, SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / "tests"), str(Path(__file__).resolve().parent)]
from blocs.youtube import files, logic, storage, upload, watcher
from blocs.youtube.block import YouTubeBlock
from bloxsmith_app.block_api import BlockRuntimeContext, BlockRuntimePreparationContext
from youtube_fixture import ACCESS, CHANNEL, GRANT, KEY, KEY_REF, REF

VIDEO = "dQw4w9WgXcQ"
BASE = {"enabled": True, "api_key_ref": KEY_REF}
OWNER = {**BASE, "secret_ref": REF}
EVERYTHING = {**OWNER, "allow_writes": True, "allow_delete": True, "allowed_operations": list(logic.OPERATIONS),
              "upload_root": "/srv/videos"}


def rejected(call, code=None):
    """A contract violation must raise a coded error, never fall back silently."""
    try:
        call()
    except logic.YouTubeError as exc:
        assert code is None or exc.code == code, (code, exc.code, str(exc))
        text = str(exc) + json.dumps(exc.envelope())
        assert ACCESS not in text and KEY not in text and GRANT["client_secret"] not in text
        return exc
    raise AssertionError("Invalid value accepted")


def settings_contract():
    """# - FB1 - remote access, changes, removals and the watch stay off by default; every setting is bounded."""
    defaults = logic.configuration({})
    assert defaults["enabled"] is False and defaults["allow_writes"] is False and defaults["allow_delete"] is False
    assert defaults["watch_enabled"] is False and defaults["emit_existing"] is False and defaults["read_credential"] == "api_key"
    assert defaults["api_base_url"] == logic.OFFICIAL_API == "https://www.googleapis.com"
    assert defaults["oauth_base_url"] == "https://oauth2.googleapis.com" and defaults["default_privacy"] == "private"
    assert defaults["max_upload_bytes"] == 268435456 and defaults["watch_interval_sec"] == 900
    assert logic.selectable_operations(defaults) == list(logic.READS)
    assert set(YouTubeBlock().default_config()) == set(logic.DEFAULTS)
    config = logic.configuration({**EVERYTHING, "execution": {"inhibited": False},
                                  "channel": " https://www.youtube.com/@FixtureChannel/videos "})
    assert config["channel"] == "@FixtureChannel" and logic.selectable_operations(config) == list(logic.OPERATIONS)
    assert logic.configuration({**BASE, "channel": "https://youtube.com/channel/" + CHANNEL})["channel"] == CHANNEL
    frozen = logic.configuration(MappingProxyType({**OWNER, "allow_writes": True, "allowed_operations": ("playlist_add",)}))
    assert logic.selectable_operations(frozen) == ["search", "playlist_add"]
    # Credentials follow the operations: the API key for reads, the OAuth grant for owner operations and mine.
    assert logic.configuration({"enabled": True, "secret_ref": REF, "read_credential": "oauth"})["api_key_ref"] == ""
    assert logic.configuration({"enabled": True, "secret_ref": REF, "operation": "channel_uploads",
                                "allowed_operations": ["channel_uploads", "watch_uploads"], "channel": "mine",
                                "watch_enabled": True})["channel"] == "mine"
    for raw in ({"enabled": True}, {**BASE, "api_key_ref": KEY}, {**BASE, "api_key_ref": "env://YOUTUBE_KEY"},
                {**BASE, "secret_ref": ACCESS}, {**BASE, "api_key": KEY}, {**BASE, "access_token": ACCESS},
                {**BASE, "operation": "delete_video"}, {**BASE, "operation": "upload_video"},
                {**BASE, "allowed_operations": ["playlist_add"]}, {**BASE, "allowed_operations": ["playlist_remove"]},
                {**BASE, "allow_writes": True, "allowed_operations": ["playlist_add"]},
                {**OWNER, "allow_writes": True, "allowed_operations": ["upload_video"]},
                {**BASE, "allowed_operations": []}, {**BASE, "allowed_operations": ["search", "search"]},
                {**BASE, "allowed_operations": "search"}, {**BASE, "read_credential": "oauth"},
                {"enabled": True, "secret_ref": REF}, {**BASE, "channel": "mine", "watch_enabled": True},
                {**BASE, "watch_enabled": True}, {**BASE, "channel": "UCshort"}, {**BASE, "channel": "@a"},
                {**BASE, "channel": "https://evil.example/@handle"}, {**BASE, "channel": 5},
                {**BASE, "default_privacy": "secret"}, {**BASE, "default_category_id": "music"},
                {**BASE, "upload_chunk_bytes": 262145}, {**BASE, "upload_root": "relative/dir"},
                {**BASE, "upload_root": "/srv/../etc"}, {**BASE, "enabled": "yes"}, {**BASE, "allow_writes": 1},
                {**BASE, "ca_file": "ca.pem"}, {**BASE, "api_base_url": "http://www.googleapis.com"},
                {**BASE, "api_base_url": "https://x.test/youtube/v3"}, {**BASE, "api_base_url": "https://u:p@x.test"},
                {**BASE, "api_base_url": "ftp://x.test"}, {**BASE, "oauth_base_url": "http://example.test:8080"}):
        rejected(lambda raw=raw: logic.configuration(raw), "config")
    for key, (low, high) in logic.BOUNDS.items():
        for bad in (low - 1, high + 1, True, float(low), str(low)):
            rejected(lambda key=key, bad=bad: logic.configuration({**BASE, key: bad}), "config")
        good = (low, high) if key != "upload_chunk_bytes" else (low, high - high % logic.CHUNK_UNIT)
        assert [logic.configuration({**BASE, key: value})[key] for value in good] == list(good)
    assert logic.BOUNDS["watch_interval_sec"][0] == 300, "the watch never polls more often than every 300 s"
    origins = {"https://www.googleapis.com/": "https://www.googleapis.com", "http://127.0.0.1:8081": "http://127.0.0.1:8081",
               "http://localhost:9": "http://localhost:9", "http://[::1]:7": "http://[::1]:7"}
    for raw, expected in origins.items():
        assert logic.configuration({**BASE, "api_base_url": raw, "oauth_base_url": raw})["api_base_url"] == expected


def credential_contract():
    """# - FB1 - # - FB6 - wallet values: an API key, a bare token, access_token JSON or a refresh grant; never echoed."""
    assert logic.api_key(" " + KEY + "\n") == KEY and logic.api_key({"api_key": KEY}) == KEY
    assert logic.api_key(json.dumps({"api_key": KEY})) == KEY
    for bad in ("", "short", None, 12, {"key": KEY}, {"api_key": KEY, "extra": 1}, "a b c d e f g h", '{"api_key": ',
                {"api_key": 5}):
        rejected(lambda bad=bad: logic.api_key(bad), "secret")
    assert logic.credentials(" " + ACCESS + "\n") == {"access_token": ACCESS}
    assert logic.credentials({"access_token": ACCESS}) == {"access_token": ACCESS}
    assert logic.credentials(json.dumps(GRANT)) == GRANT
    for bad in ("", None, 12, {"access_token": ""}, {"access_token": "a b"}, {**GRANT, "extra": "x"},
                {"client_id": "a", "refresh_token": "b"}, '{"access_token": ', json.dumps({"access_token": 5}),
                {"type": "service_account", "private_key": "-----BEGIN KEY-----"}):
        error = rejected(lambda bad=bad: logic.credentials(bad), "secret")
        assert "BEGIN" not in json.dumps(error.envelope())
    text = logic.redact("key=" + KEY + " Bearer " + ACCESS + " " + GRANT["client_secret"],
                        [KEY, ACCESS, GRANT["client_secret"], ""])
    assert KEY not in text and ACCESS not in text and text.count("***") == 3
    reader = logic.configuration(BASE)
    owner = logic.configuration(EVERYTHING)
    assert logic.credential_kind({"operation": "search"}, reader) == "api_key"
    assert logic.credential_kind({"operation": "update_video"}, owner) == "oauth"
    assert logic.credential_kind({"operation": "channel_uploads", "channel": "mine"}, owner) == "oauth"
    rejected(lambda: logic.credential_kind({"operation": "channel_uploads", "channel": "mine"}, reader), "oauth_required")
    rejected(lambda: logic.credential_kind({"operation": "search"}, {**owner, "api_key_ref": ""}), "api_key_required")


def request_contract():
    """# - FB2 - # - FB3 - # - FB4 - # - FB5 - one explicit request; the allowlist and switches apply before the wallet."""
    reader = logic.configuration({**BASE, "channel": "@fixturechannel", "list_limit": 40})
    every = logic.configuration(EVERYTHING)
    assert logic.parse_request("  lofi beats \n", reader) == {"operation": "search", "request_id": None, "limit": 40,
                                                              "page_token": None, "q": "lofi beats", "type": "video",
                                                              "order": "relevance"}
    found = logic.parse_request({"q": "cats", "type": "channel", "order": "date", "published_after": "2026-01-31",
                                 "published_before": "2026-02-01T10:00:00+02:00", "channel_id": CHANNEL,
                                 "region_code": "fr", "safe_search": "strict", "limit": 5, "page_token": "CAUQAA",
                                 "request_id": "r-1"}, reader)
    assert (found["published_after"], found["published_before"], found["region_code"], found["limit"]) == \
        ("2026-01-31T00:00:00Z", "2026-02-01T08:00:00Z", "FR", 5)
    assert logic.parse_request({"channel_id": CHANNEL}, reader)["channel_id"] == CHANNEL
    details = logic.parse_request({"operation": "video_details", "video_ids": "https://youtu.be/" + VIDEO + "?t=3, "
                                   "https://www.youtube.com/watch?v=abcdefghij0&list=PLx " + VIDEO}, reader)
    assert details["video_ids"] == [VIDEO, "abcdefghij0"]
    for url in ("https://m.youtube.com/watch?v=" + VIDEO, "https://www.youtube.com/shorts/" + VIDEO,
                "https://www.youtube.com/embed/" + VIDEO, "https://www.youtube.com/live/" + VIDEO + "?si=x"):
        assert logic.video_id(url) == VIDEO
    assert logic.parse_request({"operation": "video_details", "video_id": VIDEO}, reader)["video_ids"] == [VIDEO]
    assert logic.parse_request({"operation": "channel_uploads"}, reader)["channel"] == "@fixturechannel"
    assert logic.parse_request({"operation": "watch_uploads", "channel": "mine"}, reader)["channel"] == "mine"
    assert logic.parse_request(CHANNEL, {**reader, "operation": "channel_uploads"})["channel"] == CHANNEL
    listed = logic.parse_request("https://www.youtube.com/playlist?list=PLabc_DEF-123", {**reader, "operation": "playlist_items"})
    assert listed["playlist_id"] == "PLabc_DEF-123" and listed["limit"] == 40
    comments = logic.parse_request({"operation": "comments", "video_id": VIDEO, "order": "relevance",
                                    "search_terms": "great"}, reader)
    assert (comments["order"], comments["search_terms"]) == ("relevance", "great")
    added = logic.parse_request({"operation": "playlist_add", "playlist_id": "PLx_1", "video_id": VIDEO, "position": 0}, every)
    assert added == {"operation": "playlist_add", "request_id": None, "playlist_id": "PLx_1", "video_id": VIDEO, "position": 0}
    removed = logic.parse_request({"operation": "playlist_remove", "playlist_item_id": "UExhYmMuZGVm="}, every)
    assert removed["playlist_item_id"] == "UExhYmMuZGVm="
    assert logic.parse_request({"operation": "playlist_remove", "playlist_id": "PLx_1", "video_id": VIDEO}, every)[
        "video_id"] == VIDEO
    uploaded = logic.parse_request({"operation": "upload_video", "path": "clips/a.mp4", "title": "Été à Paris",
                                    "tags": "travel, paris, été en ville", "privacy": "unlisted"}, every)
    assert uploaded["tags"] == ["travel", "paris", "été en ville"] and uploaded["category_id"] == "22"
    assert uploaded["made_for_kids"] is False and uploaded["notify_subscribers"] is False and uploaded["description"] == ""
    plain = logic.parse_request("clips/b.mov\n", {**every, "operation": "upload_video"})
    assert plain["path"] == "clips/b.mov" and plain["title"] is None and plain["privacy"] == "private"
    update = logic.parse_request({"operation": "update_video", "video_id": VIDEO, "description": "Line 1\r\nLine 2",
                                  "tags": [], "privacy": "public"}, every)
    assert update["changes"] == {"description": "Line 1\nLine 2", "tags": [], "privacy": "public"}
    filed = logic.file_request(" /srv/videos/a.mp4\n", every)
    assert filed["path"] == "/srv/videos/a.mp4" and filed["operation"] == "upload_video" and filed["title"] is None
    described = logic.file_request(json.dumps({"path": "a.mp4", "title": "Demo", "privacy": "public",
                                               "request_id": "f-1"}), every)
    assert (described["title"], described["privacy"], described["request_id"]) == ("Demo", "public", "f-1")
    rejected(lambda: logic.file_request("a.mp4", reader), "operation_not_allowed")
    rejected(lambda: logic.file_request("a.mp4", {**every, "allow_writes": False}), "writes_disabled")
    rejected(lambda: logic.file_request({"title": "x"}, every), "invalid_input")
    rejected(lambda: logic.file_request({"path": "a.mp4", "operation": "search"}, every), "invalid_input")
    for value, code in (({"operation": "upload_video", "path": "a.mp4"}, "operation_not_allowed"),
                        ({"operation": "delete_video", "video_id": VIDEO}, "invalid_input"),
                        ({"q": "x", "unknown": 1}, "invalid_input"), ({"type": "video"}, "invalid_input"),
                        ({"q": "a\nb"}, "invalid_input"), ({"q": "x" * 501}, "invalid_input"),
                        ({"q": "x", "type": "movie"}, "invalid_input"), ({"q": "x", "order": "views"}, "invalid_input"),
                        ({"q": "x", "limit": 41}, "invalid_input"), ({"q": "x", "limit": 0}, "invalid_input"),
                        ({"q": "x", "page_token": "a b"}, "invalid_input"),
                        ({"q": "x", "published_after": "yesterday"}, "invalid_input"),
                        ({"q": "x", "published_after": "2026-02-30"}, "invalid_input"),
                        ({"q": "x", "channel_id": "@fixturechannel"}, "invalid_input"),
                        ({"q": "x", "region_code": "FRA"}, "invalid_input"), ({"q": "x", "safe_search": "off"}, "invalid_input"),
                        ({"operation": "video_details", "video_ids": []}, "invalid_input"),
                        ({"operation": "video_details", "video_ids": ["x" * 11] * 51}, "invalid_input"),
                        ({"operation": "video_details", "video_ids": "https://vimeo.com/123"}, "invalid_input"),
                        ({"operation": "video_details", "video_ids": "https://evil.example/watch?v=" + VIDEO}, "invalid_input"),
                        ({"operation": "video_details", "video_id": VIDEO, "video_ids": [VIDEO]}, "invalid_input"),
                        ({"operation": "channel_uploads", "channel": "someone"}, "invalid_input"),
                        ({"operation": "playlist_items"}, "invalid_input"),
                        ({"operation": "playlist_items", "playlist_id": "PL x"}, "invalid_input"),
                        ({"operation": "comments", "video_id": VIDEO, "order": "date"}, "invalid_input"),
                        ("[1, 2]", "invalid_input"), ('{"q": "a", "q": "b"}', "invalid_input"),
                        ('{"q": NaN}', "invalid_input"), ("x" * 70000, "request_too_large")):
        rejected(lambda value=value: logic.parse_request(value, reader), code)
    rejected(lambda: logic.parse_request("go", {**reader, "operation": "comments", "allowed_operations": ["comments"]}),
             "invalid_input")
    owner = logic.configuration({**OWNER, "allow_writes": True, "allow_delete": True,
                                 "allowed_operations": ["playlist_add", "playlist_remove", "update_video"]})
    for value, code in (({"operation": "playlist_add", "playlist_id": "PLx_1"}, "invalid_input"),
                        ({"operation": "playlist_add", "playlist_id": "PLx_1", "video_id": VIDEO, "position": -1},
                         "invalid_input"),
                        ({"operation": "playlist_remove", "playlist_item_id": "UExh", "video_id": VIDEO}, "invalid_input"),
                        ({"operation": "playlist_remove", "playlist_item_id": "short"}, "invalid_input"),
                        ({"operation": "playlist_remove", "playlist_id": "PLx_1"}, "invalid_input"),
                        ({"operation": "update_video", "video_id": VIDEO}, "invalid_input"),
                        ({"operation": "update_video", "video_id": VIDEO, "title": ""}, "invalid_input"),
                        ({"operation": "update_video", "video_id": VIDEO, "title": "x" * 101}, "invalid_input"),
                        ({"operation": "update_video", "video_id": VIDEO, "title": "<script>"}, "invalid_input"),
                        ({"operation": "update_video", "video_id": VIDEO, "description": "é" * 2501}, "invalid_input"),
                        ({"operation": "update_video", "video_id": VIDEO, "description": "a > b"}, "invalid_input"),
                        ({"operation": "update_video", "video_id": VIDEO, "description": "bell\x07"}, "invalid_input"),
                        ({"operation": "update_video", "video_id": VIDEO, "tags": ["a,b"]}, "invalid_input"),
                        ({"operation": "update_video", "video_id": VIDEO, "tags": ["word"] * 0 + ["t" * 101]}, "invalid_input"),
                        ({"operation": "update_video", "video_id": VIDEO, "tags": [f"tag number {i}" for i in range(40)]},
                         "invalid_input"),
                        ({"operation": "update_video", "video_id": VIDEO, "privacy": "secret"}, "invalid_input"),
                        ({"operation": "update_video", "video_id": VIDEO, "category_id": "music"}, "invalid_input"),
                        ({"operation": "update_video", "video_id": VIDEO, "title": "x", "made_for_kids": True},
                         "invalid_input")):
        rejected(lambda value=value: logic.parse_request(value, owner), code)
    assert logic.tags_weight(["a b", "c"]) == 3 + 2 + 1 + 1, "quotes around tags with spaces and commas count"
    assert len(logic.tag_list(["x" * 99] * 1 + ["y" * 99] * 1 + ["z" * 99] + ["w" * 99] + ["v" * 99])) == 5
    rejected(lambda: logic.parse_request({"operation": "update_video", "video_id": VIDEO, "title": "x"},
                                         {**owner, "allow_writes": False}), "writes_disabled")
    rejected(lambda: logic.parse_request({"operation": "playlist_remove", "playlist_item_id": "UExhYmMuZGVm"},
                                         {**owner, "allow_delete": False}), "delete_disabled")
    assert logic.upload_title("Holiday <cut>.final.mp4") == "Holiday  cut .final".replace("  ", " ")
    assert logic.upload_title(".mp4") == ".mp4" and logic.upload_title("\x01") == "Untitled upload"
    assert logic.upload_title("x" * 300 + ".mov") == "x" * 100
    assert logic.upload_type("a.mp4") == "video/mp4" and logic.upload_type("a.bin") == "video/*"
    assert logic.upload_metadata({"title": "T", "description": "", "category_id": "22", "tags": ["a"],
                                  "privacy": "private", "made_for_kids": False}) == {
        "snippet": {"title": "T", "description": "", "categoryId": "22", "tags": ["a"]},
        "status": {"privacyStatus": "private", "selfDeclaredMadeForKids": False}}


def resource(**extra):
    item = {"kind": "youtube#video", "id": VIDEO,
            "snippet": {"publishedAt": "2026-01-01T00:00:00Z", "channelId": CHANNEL, "title": "T", "description": "D",
                        "channelTitle": "C", "tags": ["a"], "categoryId": "10", "liveBroadcastContent": "none",
                        "thumbnails": {"default": {"url": "https://i.ytimg.com/d.jpg"},
                                       "maxres": {"url": "https://i.ytimg.com/m.jpg"}}},
            "contentDetails": {"duration": "PT1H2M3S", "definition": "hd", "dimension": "2d", "caption": "true",
                               "licensedContent": True},
            "statistics": {"viewCount": "12345678901", "commentCount": "0"},
            "status": {"privacyStatus": "public", "uploadStatus": "processed", "embeddable": True, "license": "youtube",
                       "madeForKids": False}}
    item.update(extra)
    return item


def resource_contract():
    """# - FB2 - videos, search results, playlist items, comments and channels in a validated public shape."""
    video = logic.normalize_video(resource())
    assert video["duration_seconds"] == 3723 and video["view_count"] == 12345678901 and video["like_count"] is None
    assert video["caption"] is True and video["licensed_content"] is True and video["comment_count"] == 0
    assert video["thumbnail_url"] == "https://i.ytimg.com/m.jpg" and video["url"] == logic.WATCH_URL + VIDEO
    assert video["privacy_status"] == "public" and video["tags"] == ["a"]
    for text, seconds in (("PT4M13S", 253), ("PT45S", 45), ("P0D", 0), ("PT0S", 0), ("P1DT2H", 93600), ("PT2H", 7200),
                          ("P1W", 604800), ("PT15M", 900), ("P1Y2M", None), ("PT", None), ("P", None), ("P1DT", None),
                          ("1H", None), (None, None), ("PT1.5S", None)):
        assert logic.duration_seconds(text) == seconds, text
    minimal = logic.normalize_video({"id": VIDEO})
    assert minimal["title"] is None and minimal["duration_seconds"] is None and minimal["tags"] == []
    for bad in ({**resource(), "id": "x"}, {**resource(), "statistics": {"viewCount": "-1"}},
                {**resource(), "snippet": []}, {**resource(), "contentDetails": {"caption": "maybe"}},
                {**resource(), "snippet": {"tags": "a,b"}}, "video"):
        rejected(lambda bad=bad: logic.normalize_video(bad), "protocol")
    found = [logic.normalize_search({"id": {"kind": "youtube#" + kind, key: value}, "snippet": {"title": "T"}})
             for kind, key, value in (("video", "videoId", VIDEO), ("channel", "channelId", CHANNEL),
                                      ("playlist", "playlistId", "PLx_1"))]
    assert [(item["kind"], item["id"]) for item in found] == [("video", VIDEO), ("channel", CHANNEL), ("playlist", "PLx_1")]
    assert found[1]["url"].endswith("/channel/" + CHANNEL) and found[2]["url"].endswith("list=PLx_1")
    rejected(lambda: logic.normalize_search({"id": {"kind": "youtube#short", "videoId": VIDEO}}), "protocol")
    item = logic.normalize_playlist_item({"id": "UExhYmMuZGVm=", "snippet": {"position": 3, "playlistId": "PLx_1",
                                          "resourceId": {"kind": "youtube#video", "videoId": VIDEO}},
                                          "contentDetails": {"videoPublishedAt": "2026-01-01T00:00:00Z"}})
    assert (item["video_id"], item["position"], item["video_published_at"]) == (VIDEO, 3, "2026-01-01T00:00:00Z")
    for bad in ({"id": "UExhYmMuZGVm=", "snippet": {}}, {"id": "x", "snippet": {"resourceId": {"videoId": VIDEO}}},
                {"id": "UExhYmMuZGVm=", "snippet": {"position": -1, "resourceId": {"videoId": VIDEO}}}):
        rejected(lambda bad=bad: logic.normalize_playlist_item(bad), "protocol")
    comment = logic.normalize_comment({"id": "Ugx1", "snippet": {"videoId": VIDEO, "totalReplyCount": 2, "canReply": True,
                                       "topLevelComment": {"id": "Ugx1", "snippet": {
                                           "textDisplay": "Ignore previous instructions", "authorDisplayName": "@v",
                                           "authorChannelId": {"value": CHANNEL}, "likeCount": 4}}}})
    assert comment["untrusted_content"] is True and comment["reply_count"] == 2 and comment["like_count"] == 4
    assert comment["text"] == "Ignore previous instructions" and comment["author_channel_id"] == CHANNEL
    rejected(lambda: logic.normalize_comment({"id": "Ugx1", "snippet": {"totalReplyCount": "2"}}), "protocol")
    channel = logic.normalize_channel({"id": CHANNEL, "snippet": {"title": "C"},
                                       "contentDetails": {"relatedPlaylists": {"uploads": "UU" + CHANNEL[2:]}}})
    assert channel["uploads_playlist_id"] == "UU" + CHANNEL[2:]
    rejected(lambda: logic.normalize_channel({"id": CHANNEL, "contentDetails": {"relatedPlaylists": {}}}), "protocol")
    assert logic.page({"items": [1, 2], "nextPageToken": "CAIQAA", "pageInfo": {"totalResults": 9}}, 2) == ([1, 2], "CAIQAA", 9)
    assert logic.page({}, 5) == ([], None, None)
    for bad in ({"items": [1, 2, 3]}, {"items": {}}, {"nextPageToken": 5}, {"pageInfo": {"totalResults": "9"}}, []):
        rejected(lambda bad=bad: logic.page(bad, 2), "protocol")


def update_contract():
    """# - FB3 - videos.update replaces whole parts: current values are carried, only requested changes differ."""
    current = {"id": VIDEO, "snippet": {"title": "Old", "description": "Keep me", "tags": ["a", "b"], "categoryId": "10",
                                         "defaultLanguage": "fr", "channelTitle": "C", "localized": {"title": "Old"}},
               "status": {"privacyStatus": "private", "embeddable": False, "license": "creativeCommon",
                          "publicStatsViewable": True, "publishAt": "2026-03-01T10:00:00Z", "selfDeclaredMadeForKids": False,
                          "uploadStatus": "processed"}}
    parts, body, previous = logic.update_body(current, {"title": "New"})
    assert parts == ["snippet"] and previous == {"title": "Old"}
    assert body == {"id": VIDEO, "snippet": {"title": "New", "categoryId": "10", "description": "Keep me",
                                             "tags": ["a", "b"], "defaultLanguage": "fr"}}
    parts, body, previous = logic.update_body(current, {"privacy": "private", "tags": []})
    assert parts == ["snippet", "status"] and body["snippet"]["tags"] == [] and previous == {"tags": ["a", "b"],
                                                                                            "privacy": "private"}
    assert body["status"] == {"privacyStatus": "private", "embeddable": False, "license": "creativeCommon",
                              "publicStatsViewable": True, "selfDeclaredMadeForKids": False,
                              "publishAt": "2026-03-01T10:00:00Z"}
    assert "publishAt" not in logic.update_body(current, {"privacy": "public"})[1]["status"], "a schedule needs private"
    rejected(lambda: logic.update_body({"id": VIDEO, "snippet": {"title": "x"}}, {"title": "y"}), "protocol")
    rejected(lambda: logic.update_body({"id": VIDEO, "status": {}}, {"privacy": "public"}), "protocol")


def provider_contract():
    """# - FB6 - provider errors map to stable codes; quota, rate limits, disabled comments and invalid keys stay distinct."""

    def outcome(status, reason=None, info=None, message="Problem", mutation=False, retry_after=None):
        body = {"error": {"code": status, "message": message, "status": "X"}}
        if reason:
            body["error"]["errors"] = [{"reason": reason}]
        if info:
            body["error"]["details"] = [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": info}]
        return rejected(lambda: logic.provider_failure(status, body, [KEY, ACCESS], mutation=mutation,
                                                       retry_after=retry_after))

    assert outcome(403, "quotaExceeded").code == "quota_exceeded"
    limited = outcome(403, "rateLimitExceeded", retry_after=7)
    assert limited.code == "rate_limited" and limited.retry_after == 7
    assert outcome(429, retry_after=3).retry_after == 3 and outcome(404, "quotaExceeded").code == "not_found"
    assert outcome(403, "commentsDisabled").code == "comments_disabled"
    assert outcome(403, "uploadLimitExceeded").code == "upload_limit"
    invalid = outcome(400, "badRequest", "API_KEY_INVALID")
    assert invalid.code == "key_invalid" and invalid.provider_reason == "badRequest"
    assert outcome(400, None, "API_KEY_INVALID").provider_reason == "API_KEY_INVALID"
    assert outcome(401, "authError").code == "unauthorized" and outcome(403, "forbidden").code == "forbidden"
    assert outcome(409).code == "provider_error" and outcome(400, "invalidTitle").code == "bad_request"
    assert outcome(503).code == "provider_unavailable" and not outcome(503).uncertain
    assert outcome(503, mutation=True).uncertain and not outcome(400, mutation=True).uncertain
    leaked = outcome(400, "badRequest", message="Bad key " + KEY + "\n" + ACCESS + " " + "x" * 400)
    assert KEY not in leaked.provider_message and "\n" not in leaked.provider_message and len(leaked.provider_message) <= 200
    error = logic.YouTubeError("rate_limited", http_status=429, retry_after=5)
    error.attempts = 3
    assert error.envelope(operation="search", quota_units=300, video_id=None) == {
        "code": "rate_limited", "message": logic.MESSAGES["rate_limited"], "uncertain": False, "http_status": 429,
        "retry_after": 5, "attempts": 3, "operation": "search", "quota_units": 300}
    assert logic.COSTS["search.list"] == 100 and logic.COSTS["videos.insert"] == 1600 and logic.COSTS["videos.list"] == 1
    assert set(logic.MESSAGES) >= {"config", "disabled", "secret", "busy", "state_invalid", "upload_expired", "file_changed"}


def upload_contract():
    """# - FB4 - the session URI stays on the API origin; ranges are validated before any resumption."""
    origin = "https://www.googleapis.com"
    good = origin + logic.UPLOAD_PATH + "?uploadType=resumable&part=snippet%2Cstatus&upload_id=xa298"
    assert upload.session_target(good, origin) == logic.UPLOAD_PATH + "?uploadType=resumable&part=snippet%2Cstatus&upload_id=xa298"
    assert upload.session_target("https://www.googleapis.com:443" + logic.UPLOAD_PATH + "?upload_id=a", origin)
    assert upload.session_target("http://127.0.0.1:8080" + logic.UPLOAD_PATH + "?upload_id=a", "http://127.0.0.1:8080")
    for bad in (None, "", "https://uploads.example.com" + logic.UPLOAD_PATH + "?upload_id=a",
                "http://www.googleapis.com" + logic.UPLOAD_PATH + "?upload_id=a",
                "https://www.googleapis.com:8443" + logic.UPLOAD_PATH + "?upload_id=a",
                "https://user@www.googleapis.com" + logic.UPLOAD_PATH + "?upload_id=a",
                origin + "/upload/drive/v3/files?upload_id=a", origin + logic.UPLOAD_PATH + "?uploadType=resumable",
                origin + logic.UPLOAD_PATH + "?upload_id=a#x", origin + logic.UPLOAD_PATH + "?upload_id=a b",
                "/upload/youtube/v3/videos?upload_id=a"):
        rejected(lambda bad=bad: upload.session_target(bad, origin), "protocol")
    assert upload.received(None, 10) == 0 and upload.received("bytes=0-4", 10) == 5 and upload.received(" bytes=0-9", 10) == 10
    for bad in ("bytes=0-10", "bytes=1-4", "bytes 0-4", "bytes=0-"):
        rejected(lambda bad=bad: upload.received(bad, 10), "protocol")


def watch_contract():
    """# - FB5 - watch state is per channel/credential digest, bounded, validated and stored atomically with a lock."""
    digest = logic.watch_digest("@Fixture", "api_key")
    assert digest == logic.watch_digest("@fixture", "api_key") and len(digest) == 32
    assert digest != logic.watch_digest("@fixture", "oauth") and digest != logic.watch_digest(CHANNEL, "api_key")
    assert watcher.watch_kind({"channel": "mine", "read_credential": "api_key"}) == "oauth"
    assert watcher.watch_kind({"channel": CHANNEL, "read_credential": "api_key"}) == "api_key"
    document = {"schema_version": 1, "watches": {}}
    for index in range(10):
        document = logic.store_entry(document, f"{index:032x}", {"playlist_id": "UUx", "seen": [], "last_poll": index})
    assert len(document["watches"]) == 8 and f"{0:032x}" not in document["watches"] and f"{9:032x}" in document["watches"]
    ids = [f"vid{index:08d}" for index in range(1500)]
    entry = logic.store_entry(document, digest, {"playlist_id": None, "seen": ids + [ids[0]], "last_poll": 5})["watches"][digest]
    assert len(entry["seen"]) == logic.SEEN_LIMIT and entry["seen"][-1] == ids[-1] and ids[0] not in entry["seen"]
    for bad in ({"schema_version": 2, "watches": {}}, {"schema_version": 1}, {"schema_version": 1, "watches": {"x": {}}},
                {"schema_version": 1, "watches": {digest: {"playlist_id": "U U", "seen": [], "last_poll": 0}}},
                {"schema_version": 1, "watches": {digest: {"playlist_id": None, "seen": ["short"], "last_poll": 0}}},
                {"schema_version": 1, "watches": {digest: {"playlist_id": None, "seen": [], "last_poll": -1}}},
                {"schema_version": 1, "watches": {digest: {"playlist_id": None, "seen": [], "last_poll": 0, "key": KEY}}},
                {"schema_version": 1, "watches": {}, "token": ACCESS}):
        rejected(lambda bad=bad: logic.watch_document(bad), "state_invalid")
    with tempfile.TemporaryDirectory() as folder:
        directory = Path(folder).resolve()
        first, second = storage.WatchStore(directory), storage.WatchStore(directory)
        assert first.acquire() is True and second.acquire() is False, "one watch per node storage"
        assert first.load() == {"schema_version": 1, "watches": {}}
        saved = logic.store_entry(first.load(), digest, {"playlist_id": "UUabc", "seen": [VIDEO], "last_poll": 3})
        first.save(saved)
        assert first.load() == saved and not (directory / (storage.STATE_NAME + ".tmp")).exists()
        stored = (directory / storage.STATE_NAME).read_text()
        assert "fixture" not in stored.lower() and KEY not in stored, "no channel handle or credential is persisted"
        first.close()
        assert second.acquire() is True
        second.close()
        (directory / storage.STATE_NAME).write_text("{broken")
        rejected(lambda: storage.WatchStore(directory).load(), "state_invalid")
        (directory / storage.STATE_NAME).unlink()
        (directory / storage.STATE_NAME).symlink_to(directory / "elsewhere.json")
        rejected(lambda: storage.WatchStore(directory).load(), "state_invalid")


def local_files_contract():
    """# - FB4 - uploads are regular files inside upload_root, size-bounded, read by offset and checked unchanged."""
    with tempfile.TemporaryDirectory() as folder:
        base = Path(folder).resolve()
        root, outside = base / "videos", base / "private"
        root.mkdir()
        outside.mkdir()
        (root / "clip.mp4").write_bytes(b"0123456789")
        (root / "sub").mkdir()
        (root / "sub" / "deep.mov").write_bytes(b"abc")
        (root / "empty.mp4").write_bytes(b"")
        (outside / "secret.mp4").write_bytes(b"do not upload")
        (root / "escape.mp4").symlink_to(outside / "secret.mp4")
        (root / "inner.mp4").symlink_to(root / "clip.mp4")
        os.mkfifo(root / "pipe")
        config = logic.configuration({**EVERYTHING, "upload_root": str(root), "max_upload_bytes": 1048576})
        source = files.open_upload("clip.mp4", config)
        try:
            assert (source.size, source.name, source.read(2, 3)) == (10, "clip.mp4", b"234")
            source.verify()
            rejected(lambda: source.read(8, 5), "file_changed")
            (root / "clip.mp4").write_bytes(b"0123456789AB")
            rejected(source.verify, "file_changed")
        finally:
            source.close()
            source.close()
        for value in (str(root / "sub" / "deep.mov"), "inner.mp4"):
            opened = files.open_upload(value, config)
            opened.close()
        for value, code in (("../private/secret.mp4", "path_denied"), (str(outside / "secret.mp4"), "path_denied"),
                            ("escape.mp4", "path_denied"), ("missing.mp4", "path"), ("sub", "path"), ("pipe", "path"),
                            ("empty.mp4", "path")):
            error = rejected(lambda value=value: files.open_upload(value, config), code)
            assert str(outside) not in str(error) and str(root) not in str(error)
        (root / "big.mp4").write_bytes(b"x" * 1048577)
        rejected(lambda: files.open_upload("big.mp4", config), "file_too_large")
        rejected(lambda: files.open_upload("clip.mp4", {**config, "upload_root": ""}), "upload_root_required")
        rejected(lambda: files.open_upload("clip.mp4", {**config, "upload_root": str(base / "absent")}), "path")
        storage_dir = base / "node"
        storage_dir.mkdir()
        rejected(lambda: files.storage_directory(None), "storage")
        rejected(lambda: files.storage_directory(lambda: (_ for _ in ()).throw(RuntimeError("block_storage.missing_scope"))),
                 "storage")
        assert files.storage_directory(lambda: str(storage_dir)) == storage_dir


def block_contract():
    """# - FB1 - # - FB6 - # - FB7 - # - FB8 - hooks fail before side effects; events run once; surfaces are translated."""
    block = YouTubeBlock()
    ports_in = tuple(SimpleNamespace(id=port["id"], name=port["name"]) for port in block.model["ports"]["inputs"])
    ports_out = tuple(SimpleNamespace(id=port["id"], name=port["name"]) for port in block.model["ports"]["outputs"])
    assert [port.name for port in ports_in] == ["request", "file"]
    assert [port.name for port in ports_out] == ["items", "result", "changed", "error"]
    assert all(port["execution_requirement"] == "not_required_for_execution" for port in block.model["ports"]["inputs"])
    assert block.model["runtime"]["centralized_dependency_mode"] == "any_event" and block.model["version"] == "0.1.0"
    assert block.model["repository_url"] == "https://github.com/HackInvent/bloxmith-youtube"
    assert block.model["description"] == "Search videos, read details and manage playlists and uploads on YouTube."
    calls = []

    def resolver(ref):
        calls.append(ref)
        return "not json {" + KEY if ref == REF else "short"

    def run(config, inputs=None, events=(), services=None, mode="zeromq_active"):
        context = BlockRuntimeContext(config={**block.default_config(), **config}, inputs=inputs or {}, input_ports=ports_in,
                                      output_ports=ports_out, input_events=tuple(events), runtime_mode=mode,
                                      services={"resolve_secret": resolver, **(services or {})})
        result = block.execute_runtime(context)
        assert result.status in ("success", "skipped"), result
        return result, [(output.port_name, json.loads(output.value)) for output in result.outputs]

    assert run(BASE)[0].status == "skipped"
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder).resolve() / "videos"
        root.mkdir()
        (root / "a.mp4").write_bytes(b"video")
        (root.parent / "private.mp4").write_text("private")
        writer = {**OWNER, "allow_writes": True, "allowed_operations": ["search", "upload_video"], "upload_root": str(root)}
        for config, inputs, code in (
                ({**BASE, "enabled": False}, {"request": "cats"}, "disabled"),
                (BASE, {"request": {"operation": "update_video", "video_id": VIDEO, "title": "x"}}, "operation_not_allowed"),
                (BASE, {"request": '{"limit":'}, "invalid_input"),
                (BASE, {"file": str(root / "a.mp4")}, "operation_not_allowed"),
                (writer, {"file": str(root / "missing.mp4")}, "path"),
                (writer, {"file": str(root.parent / "private.mp4")}, "path_denied"),
                ({**writer, "max_upload_bytes": 1048576, "upload_root": ""}, {"file": "a.mp4"}, "config"),
                (BASE, {"request": {"operation": "watch_uploads", "channel": "@fixturechannel"}}, "storage"),
                (BASE, {"request": {"operation": "channel_uploads", "channel": "mine"}}, "oauth_required"),
                ({**BASE, "api_key_ref": KEY}, {"request": "cats"}, "config")):
            result, outputs = run(config, inputs)
            assert [name for name, _ in outputs] == ["error"] and outputs[0][1]["code"] == code, outputs
            assert result.metadata["youtube"]["status"] == "error" and result.metadata["youtube"]["error_codes"] == [code]
            assert KEY not in json.dumps(outputs) and KEY not in " ".join(result.logs)
            assert "quota_units" not in outputs[0][1], "nothing was sent, so no quota estimate is reported"
        assert not calls, "The wallet must not be read before settings, request and local file validation"
        result, outputs = run(BASE, {"request": {"request_id": "req-1", "q": "cats"}})
        error = outputs[0][1]
        assert error["code"] == "secret" and error["request_id"] == "req-1" and error["operation"] == "search"
        assert calls == [KEY_REF] and error["input"] == "request" and "short" not in json.dumps(outputs)
        assert result.logs == ["[youtube-error] search: secret"]
        calls.clear()
        result, outputs = run(writer, {"file": "a.mp4"})
        assert outputs[0][1]["code"] == "secret" and calls == [REF] and KEY not in json.dumps(outputs)
        result, outputs = run(BASE, {"request": "cats", "file": "x"})
        assert outputs[0][1]["code"] == "invalid_input" and "ambiguous" in outputs[0][1]["message"]
        # One Shot hands every connected value to each any_event activation: each event runs once per run.
        memory = {}
        services = {"get_node_runtime_value": lambda key, default=None: memory.get(key, default),
                    "set_node_runtime_value": lambda key, value: memory.__setitem__(key, value)}
        event = SimpleNamespace(edge_id="e1", input_port_id=1, input_port_name="request", source_node_id="src",
                                source_port_id=1, value='{"request_id": "once", "q": "x"}', content_type="application/json",
                                sequence=0)
        calls.clear()
        first = run(BASE, {"request": event.value}, [event], services, "centralized")[1]
        again = run(BASE, {"request": event.value}, [event], services, "centralized")
        assert first[0][1]["request_id"] == "once" and again[0].status == "skipped" and calls == [KEY_REF]
        other = SimpleNamespace(**{**vars(event), "edge_id": "e2", "input_port_id": 2, "input_port_name": "file",
                                   "value": "a.mp4"})
        mixed = run(writer, {"request": event.value, "file": other.value}, [event, other], services, "centralized")[1]
        assert [item["input"] for _name, item in mixed] == ["file"], "Only the new file event runs"
        flood = [SimpleNamespace(**{**vars(event), "edge_id": f"x{index}"}) for index in range(9)]
        assert run(BASE, {}, flood)[1][0][1]["code"] == "invalid_input"

    def prepared(config, mode="zeromq_active"):
        context = BlockRuntimePreparationContext.from_context(SimpleNamespace(config=config, runtime_mode=mode))
        return block.prepare_runtime(context)

    watching = {**BASE, "watch_enabled": True, "channel": "@fixturechannel"}
    assert prepared(EVERYTHING).listen_on_run is False and prepared(watching).listen_on_run is True
    assert prepared(watching, "centralized").listen_on_run is False, "One Shot never starts the listener"
    assert prepared({**watching, "enabled": False}).listen_on_run is False
    rejected(lambda: prepared({**BASE, "api_key_ref": KEY}), "config")
    node = block.build_node_payload(node_id="tube", position={"x": 0, "y": 0})
    accepted = block.handle_ui_action(node=node, action="save_settings", values={"node_patch": {"config": EVERYTHING}})
    operation = accepted["graph_operations"][-1]
    assert operation["op"] == "update_node_config" and operation["replace"] is True
    assert operation["config"]["allowed_operations"] == EVERYTHING["allowed_operations"]
    refused = block.handle_ui_action(node=node, action="save_settings",
                                     values={"node_patch": {"config": {**OWNER, "operation": "upload_video"}}})
    assert "allow_writes" in refused["error"] and "graph_operations" not in refused
    html = block.render_modal(node=node)["html"]
    assert 'data-block-config-field="api_key_ref"' in html and 'data-youtube-show="write" hidden' in html
    assert 'data-youtube-show="watch" hidden' in html and "data-path-browser" in html
    assert 'data-block-value-type="json"' in html and "{{" not in html
    assert 'data-i18n="block.youtube.label_upload_root"' in html
    active = {**node, "config": {**node["config"], **EVERYTHING, "watch_enabled": True, "channel": "@fixturechannel"}}
    panel = block.render_inspector_panel(node=active)["html"]
    assert 'data-youtube-show="write" hidden' not in panel and 'data-youtube-show="watch" hidden' not in panel
    card = block.render_node_card(node=node)["html"]
    assert card.count("Remote access off") == 1 and 'data-i18n="block.youtube.card_disabled" data-i18n-params=""' in card
    assert "Read only · search" in block.render_node_card(node={**node, "config": {**node["config"], **BASE}})["html"]
    assert "Read and write · search" in block.render_node_card(
        node={**node, "config": {**node["config"], **OWNER, "allow_delete": True}})["html"]
    card = block.render_node_card(node=active)["html"]
    assert ("Watching uploads · search" in card and 'data-i18n="block.youtube.card_watch" '
            'data-i18n-params="{&quot;operation&quot;: &quot;search&quot;}"' in card)
    assert REF not in card and KEY_REF not in card and "fixturechannel" not in card and "/srv/videos" not in card
    assert block.translate("block.youtube.card_watch", {"operation": "search"}, lang="fr") == \
        "Surveillance des vidéos · search"
    english = json.loads((block.directory / "locales" / "en.json").read_text(encoding="utf-8"))
    french = json.loads((block.directory / "locales" / "fr.json").read_text(encoding="utf-8"))
    assert set(english) == set(french) and all(key.startswith("block.youtube.") for key in english)
    fields = json.loads((block.directory / "fields.json").read_text(encoding="utf-8"))
    assert sorted(spec["key"] for spec in fields) == sorted(logic.DEFAULTS)
    assert all(english["block.youtube.label_" + spec["key"]] == spec["label"] for spec in fields)
    assert english["block.youtube.model.description"] == block.model["description"]
    assert block.model["config"] == logic.DEFAULTS


def main():
    settings_contract()
    credential_contract()
    request_contract()
    resource_contract()
    update_contract()
    provider_contract()
    upload_contract()
    watch_contract()
    local_files_contract()
    block_contract()
    print("[ok] YouTube settings, credentials, requests, resources, updates, errors, uploads, watch state, files and hooks")


if __name__ == "__main__":
    main()
