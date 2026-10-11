"""The YouTube operations, built from Session calls and pure logic.

Every operation validates what it can before its first request and sends at
most one change. Listings follow page tokens bounded by page_size, max_pages
(search_max_pages for search), the request limit and the output size limit; a
page is never cut, so next_page_token always resumes exactly where the result
stopped. Every result reports the requests sent and the estimated quota units.
"""

import json

from . import logic, upload, watcher

API = logic.API_ROOT
COSTS = logic.COSTS


def identity(request):
    return {"request_id": request["request_id"], "operation": request["operation"]}


def finish(result, session):
    result["requests"] = session.requests
    result["quota_units"] = session.units
    return result


def listing(session, request, config, resource, params, normalize, *, auth, cost, max_pages):
    """Pages of normalized items, never more than the request limit; returns (items, summary)."""
    limit, token = request["limit"], request["page_token"]
    found, pages, weight, total_results = [], 0, 2, None
    while True:
        if pages >= max_pages:
            stop, next_token = "max_pages", token
            break
        size = min(config["page_size"], limit - len(found))
        query = {**params, "maxResults": size}
        if token:
            query["pageToken"] = token
        body = session.call("GET", API + "/" + resource, query, auth=auth, cost=cost)
        pages += 1
        raw, following, total = logic.page(body, size)
        if total is not None:
            total_results = total
        items = [normalize(item) for item in raw]
        added = sum(len(json.dumps(item, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) + 1 for item in items)
        if weight + added > logic.OUTPUT_LIMIT:
            if not found:
                logic.fail("protocol", "One YouTube page exceeds the 16 MiB output limit; lower page_size.")
            stop, next_token = "output_limit", token  # The page that does not fit is dropped whole; its token resumes it.
            break
        found.extend(items)
        weight += added
        token = following
        if not token:
            stop, next_token = "end", None
            break
        if len(found) >= limit:
            stop, next_token = "limit", token
            break
    summary = {**identity(request), "count": len(found), "truncated": next_token is not None, "stop_reason": stop,
               "next_page_token": next_token, "pages": pages, "total_results": total_results}
    return found, summary


def search(session, request, config, auth):
    """search.list (100 quota units per page)."""
    params = {"part": "snippet", "type": request["type"], "order": request["order"]}
    for key, name in (("q", "q"), ("published_after", "publishedAfter"), ("published_before", "publishedBefore"),
                      ("channel_id", "channelId"), ("region_code", "regionCode"), ("safe_search", "safeSearch")):
        if request.get(key) is not None:
            params[name] = request[key]
    items, summary = listing(session, request, config, "search", params, logic.normalize_search, auth=auth,
                             cost=COSTS["search.list"], max_pages=config["search_max_pages"])
    summary.update(q=request.get("q"), type=request["type"], order=request["order"])
    return {"items": items, "result": finish(summary, session)}


def fetch_videos(session, ids, auth, part="snippet,contentDetails,statistics,status"):
    """videos.list for up to 50 IDs; returns normalized-ready raw items keyed by ID."""
    body = session.call("GET", API + "/videos", {"part": part, "id": ",".join(ids)}, auth=auth,
                        cost=COSTS["videos.list"])
    raw, _token, _total = logic.page(body, len(ids))
    return raw


def video_details(session, request, config, auth):
    """videos.list with snippet, contentDetails (duration in seconds), statistics and status."""
    videos = {video["id"]: video for video in (logic.normalize_video(item)
                                                for item in fetch_videos(session, request["video_ids"], auth))}
    items = [videos[ident] for ident in request["video_ids"] if ident in videos]
    missing = [ident for ident in request["video_ids"] if ident not in videos]
    result = {**identity(request), "count": len(items), "missing": missing}
    return {"items": items, "result": finish(result, session)}


def channel_uploads(session, request, config, auth):
    """The channel's uploads playlist through playlistItems.list, newest first."""
    channel = watcher.resolve_channel(session, request["channel"], auth)
    params = {"part": "snippet,contentDetails,status", "playlistId": channel["uploads_playlist_id"]}
    items, summary = listing(session, request, config, "playlistItems", params, logic.normalize_playlist_item,
                             auth=auth, cost=COSTS["playlistItems.list"], max_pages=config["max_pages"])
    summary.update(channel=request["channel"], channel_id=channel["id"], channel_title=channel["title"],
                   uploads_playlist_id=channel["uploads_playlist_id"])
    return {"items": items, "result": finish(summary, session)}


def playlist_items(session, request, config, auth):
    params = {"part": "snippet,contentDetails,status", "playlistId": request["playlist_id"]}
    items, summary = listing(session, request, config, "playlistItems", params, logic.normalize_playlist_item,
                             auth=auth, cost=COSTS["playlistItems.list"], max_pages=config["max_pages"])
    summary["playlist_id"] = request["playlist_id"]
    return {"items": items, "result": finish(summary, session)}


def comments(session, request, config, auth):
    """Top-level comment threads of one video (commentThreads.list, plain text)."""
    params = {"part": "snippet", "videoId": request["video_id"], "order": request["order"], "textFormat": "plainText"}
    if request.get("search_terms"):
        params["searchTerms"] = request["search_terms"]
    items, summary = listing(session, request, config, "commentThreads", params, logic.normalize_comment, auth=auth,
                             cost=COSTS["commentThreads.list"], max_pages=config["max_pages"])
    summary.update(video_id=request["video_id"], order=request["order"])
    return {"items": items, "result": finish(summary, session)}


def playlist_add(session, request, config):
    """playlistItems.insert: one new item; never replayed when the answer is lost."""
    snippet = {"playlistId": request["playlist_id"], "resourceId": {"kind": "youtube#video", "videoId": request["video_id"]}}
    if request["position"] is not None:
        snippet["position"] = request["position"]
    created = session.call("POST", API + "/playlistItems", {"part": "snippet"}, {"snippet": snippet}, auth="oauth",
                           cost=COSTS["playlistItems.insert"], mutation=True)
    item = logic.normalize_playlist_item(created)
    if item["video_id"] != request["video_id"]:
        logic.fail("protocol", "YouTube answered the insertion with another video; check playlist item " + item["id"] + ".",
                   uncertain=True)
    result = {**identity(request), "playlist_id": request["playlist_id"], "video_id": request["video_id"], "item": item}
    return {"result": finish(result, session)}


def find_item(session, request, config):
    """The first playlist item of a video, looked up in at most max_pages pages."""
    token, pages, matches, first = None, 0, 0, None
    while pages < config["max_pages"]:
        query = {"part": "snippet", "playlistId": request["playlist_id"], "maxResults": 50}
        if token:
            query["pageToken"] = token
        raw, token, _total = logic.page(session.call("GET", API + "/playlistItems", query, auth="oauth",
                                                     cost=COSTS["playlistItems.list"]), 50)
        pages += 1
        for item in (logic.normalize_playlist_item(entry) for entry in raw):
            if item["video_id"] == request["video_id"]:
                matches += 1
                first = first or item["id"]
        if not token:
            break
    if first is None:
        logic.fail("not_found", f"The video is not in the first {pages} page(s) of the playlist; nothing was removed.")
    return first, matches


def playlist_remove(session, request, config):
    """playlistItems.delete: removes the item from the playlist; the video itself is never deleted."""
    matches = None
    item = request.get("playlist_item_id")
    if item is None:
        item, matches = find_item(session, request, config)
    session.call("DELETE", API + "/playlistItems", {"id": item}, auth="oauth", cost=COSTS["playlistItems.delete"],
                 mutation=True)
    result = {**identity(request), "playlist_item_id": item, "playlist_id": request.get("playlist_id"),
              "video_id": request.get("video_id"), "removed": True, "matches": matches}
    return {"result": finish(result, session)}


def update_video(session, request, config):
    """Read the current snippet and status, then videos.update with only the requested changes applied."""
    raw = fetch_videos(session, [request["video_id"]], "oauth", "snippet,status")
    if not raw:
        logic.fail("not_found", "The video was not found, or this OAuth grant does not own it; nothing was changed.")
    current = raw[0]
    if not isinstance(current, dict) or current.get("id") != request["video_id"]:
        logic.fail("protocol")
    parts, body, previous = logic.update_body(current, request["changes"])
    updated = session.call("PUT", API + "/videos", {"part": ",".join(parts)}, body, auth="oauth",
                           cost=COSTS["videos.update"], mutation=True, replayable=True)
    video = logic.normalize_video(updated)
    result = {**identity(request), "video_id": request["video_id"], "changed": sorted(request["changes"]),
              "previous": previous, "video": video}
    return {"result": finish(result, session)}


def run(session, request, config, *, auth, storage=None, source=None, clock=None):
    operation = request["operation"]
    if operation == "search":
        return search(session, request, config, auth)
    if operation == "video_details":
        return video_details(session, request, config, auth)
    if operation == "channel_uploads":
        return channel_uploads(session, request, config, auth)
    if operation == "playlist_items":
        return playlist_items(session, request, config, auth)
    if operation == "comments":
        return comments(session, request, config, auth)
    if operation == "watch_uploads":
        values = watcher.single_poll(session, request, config, storage, auth, **({"clock": clock} if clock else {}))
        finish(values["result"], session)
        return values
    if operation == "playlist_add":
        return playlist_add(session, request, config)
    if operation == "playlist_remove":
        return playlist_remove(session, request, config)
    if operation == "upload_video":
        result = upload.upload_video(session, request, config, source)
        return {"result": finish({**identity(request), **result}, session)}
    return update_video(session, request, config)
