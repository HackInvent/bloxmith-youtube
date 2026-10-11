"""Watch new uploads of one channel: a single poll (watch_uploads) or the Active Runtime listener.

Each check reads the channel's uploads playlist, newest first, one page of 50
items, and the next page only while a whole page is new (at most 4 pages). A
video is new when its ID is not in the persisted seen set (the last 1000 IDs).
The first check of a channel only records the current uploads, unless
emit_existing asks for the newest watch_max_items of them. New videos are
handed over oldest first, at most watch_max_items per check; the rest wait for
the next check.

Delivery is at-most-once at the Stop boundary: a video ID is added to the seen
set and the document is stored before the video is handed to the runtime. If the
hand-off is refused, the previous document is restored and the video is emitted
by the next Run. A check never runs within watch_interval_sec (at least 300 s)
of the previous one. All listener state lives in this per-hook object.
"""

import time

from . import files, logic, storage, transport

API = logic.API_ROOT


class Halt(Exception):
    """Stop processing after a refused hand-off or a storage failure."""


def resolve_channel(session, channel, auth):
    """channels.list by ID, by @handle or mine; returns the channel with its uploads playlist."""
    params = {"part": "snippet,contentDetails"}
    if channel == "mine":
        params["mine"] = "true"
    elif channel.startswith("@"):
        params["forHandle"] = channel
    else:
        params["id"] = channel
    raw, _token, _total = logic.page(session.call("GET", API + "/channels", params, auth=auth,
                                                  cost=logic.COSTS["channels.list"]), 50)
    if not raw:
        logic.fail("not_found", "The channel was not found, or this credential cannot see it.")
    return logic.normalize_channel(raw[0])


def scan(session, playlist, seen, auth, *, first):
    """Uploads, newest first; a further page only while the whole previous page was new."""
    found, token, pages = [], None, 0
    while pages < logic.WATCH_PAGES:
        query = {"part": "snippet,contentDetails,status", "playlistId": playlist, "maxResults": logic.WATCH_PAGE_SIZE}
        if token:
            query["pageToken"] = token
        raw, token, _total = logic.page(session.call("GET", API + "/playlistItems", query, auth=auth,
                                                     cost=logic.COSTS["playlistItems.list"]), logic.WATCH_PAGE_SIZE)
        pages += 1
        items = [logic.normalize_playlist_item(item) for item in raw]
        found.extend(items)
        if first or not token or any(item["video_id"] in seen for item in items):
            break
    unique, known = [], set()
    for item in found:
        if item["video_id"] not in known:
            known.add(item["video_id"])
            unique.append(item)
    return unique, pages


def poll(session, entry, channel, config, auth, now):
    """One bounded check; returns (stored entry before hand-off, new items oldest first, details)."""
    first = entry is None
    playlist, info = (None if first else entry["playlist_id"]), None
    if playlist is None:
        info = resolve_channel(session, channel, auth)
        playlist = info["uploads_playlist_id"]
    seen = set() if first else set(entry["seen"])
    try:
        items, pages = scan(session, playlist, seen, auth, first=first)
    except logic.YouTubeError as exc:
        if exc.code != "not_found" or info is not None:
            raise
        # The remembered uploads playlist is gone: resolve the channel again, once.
        info = resolve_channel(session, channel, auth)
        playlist = info["uploads_playlist_id"]
        items, pages = scan(session, playlist, seen, auth, first=first)
    cap = config["watch_max_items"]
    if first:
        fresh = list(reversed(items[:cap])) if config["emit_existing"] else []
        chosen = {item["video_id"] for item in fresh}
        base = [item["video_id"] for item in reversed(items) if item["video_id"] not in chosen]
    else:
        fresh = [item for item in reversed(items) if item["video_id"] not in seen][:cap]
        base = list(entry["seen"])
    channel_id = info["id"] if info else next((item["channel_id"] for item in items if item["channel_id"]), None)
    details = {"channel_id": channel_id, "playlist_id": playlist, "pages": pages,
               "baseline": first and not config["emit_existing"]}
    return {"playlist_id": playlist, "seen": base, "last_poll": int(now)}, fresh, details


def envelope(request_id, channel, details, items):
    """The changed output: new uploads of one channel, oldest first."""
    return {"operation": "watch_uploads", "request_id": request_id, "channel": channel,
            "channel_id": details["channel_id"], "playlist_id": details["playlist_id"], "count": len(items),
            "items": items}


def single_poll(session, request, config, storage_dir, auth, *, clock=time.time):
    """watch_uploads: one check sharing the listener's stored state; at most once per watch_interval_sec."""
    directory = files.storage_directory(storage_dir)
    store = storage.WatchStore(directory)
    if not store.acquire():
        logic.fail("busy")
    try:
        document = store.load()
        digest = logic.watch_digest(request["channel"], auth)
        entry = document["watches"].get(digest)
        now = int(clock())
        result = {"request_id": request["request_id"], "operation": "watch_uploads", "channel": request["channel"],
                  "count": 0, "baseline": False, "throttled": False}
        if entry is not None and 0 <= now - entry["last_poll"] < config["watch_interval_sec"]:
            result.update(throttled=True, playlist_id=entry["playlist_id"], channel_id=None,
                          next_check_in_sec=config["watch_interval_sec"] - (now - entry["last_poll"]))
            return {"result": result}
        following, fresh, details = poll(session, entry, request["channel"], config, auth, now)
        following["seen"] = following["seen"] + [item["video_id"] for item in fresh]
        store.save(logic.store_entry(document, digest, following))
        result.update(count=len(fresh), baseline=details["baseline"], channel_id=details["channel_id"],
                      playlist_id=details["playlist_id"], pages=details["pages"],
                      next_check_in_sec=config["watch_interval_sec"])
        values = {"result": result}
        if fresh:
            values["changed"] = envelope(request["request_id"], request["channel"], details, fresh)
        return values
    finally:
        store.close()


def watch_kind(config):
    """The credential of the watch: OAuth for mine or OAuth reads, otherwise the API key."""
    return "oauth" if config["channel"] == "mine" or config["read_credential"] == "oauth" else "api_key"


class Watcher:
    def __init__(self, config, *, resolve_secret, storage_dir, emit, stop, session=transport.Session,
                 monotonic=time.monotonic, wall=time.time):
        self.config, self.resolve_secret, self.storage_dir = config, resolve_secret, storage_dir
        self.emit_callback, self.stop, self.session = emit, stop, session
        self.monotonic, self.wall = monotonic, wall
        self.kind = watch_kind(config)
        self.digest = logic.watch_digest(config["channel"], self.kind)
        self.stats = {"state": "starting", "cycles": 0, "emitted": 0, "errors": 0, "quota_units": 0, "last_error": None}
        self.last_error = None
        self.store, self.document = None, None

    def emit(self, kind, value=None):
        self.emit_callback(kind, value, dict(self.stats))

    def wait(self, seconds):
        end = self.monotonic() + max(0.0, seconds)
        while not self.stop() and self.monotonic() < end:
            time.sleep(min(0.05, max(0.0, end - self.monotonic())))

    def report(self, error):
        """Emit each distinct failure once; recovery re-arms the report."""
        self.stats.update(state="backoff", last_error=error.code, errors=self.stats["errors"] + 1)
        if error.code != self.last_error and not self.stop():
            self.last_error = error.code
            try:
                self.emit("error", error.envelope(operation="watch_uploads", source="watch", channel=self.config["channel"]))
            except Exception:
                raise Halt() from None

    def idle(self, error):
        """Fail closed: report once, then poll nothing until framework Stop."""
        try:
            self.report(error)
        except Halt:
            pass
        self.stats["state"] = "blocked"
        while not self.stop():
            time.sleep(0.1)

    def run(self):
        try:
            self.store = storage.WatchStore(self.storage_dir())
        except Exception:
            self.idle(logic.YouTubeError("storage"))
            return
        try:
            while True:
                try:
                    acquired = self.store.acquire()
                except logic.YouTubeError as exc:
                    self.idle(exc)
                    return
                if acquired:
                    break
                try:
                    self.report(logic.YouTubeError("busy"))
                except Halt:
                    pass
                self.wait(2)
                if self.stop():
                    return
            try:
                self.document = self.store.load()
            except logic.YouTubeError as exc:
                self.idle(exc)
                return
            self.loop()
        except Halt:
            while not self.stop():
                time.sleep(0.1)
        finally:
            self.store.close()

    def loop(self):
        """Check when due: at start unless the stored last check is more recent than the interval."""
        entry = self.document["watches"].get(self.digest)
        elapsed = int(self.wall()) - entry["last_poll"] if entry else None
        due = self.monotonic() + (self.config["watch_interval_sec"] - elapsed
                                  if elapsed is not None and 0 <= elapsed < self.config["watch_interval_sec"] else 0)
        while not self.stop():
            now = self.monotonic()
            if now < due:
                self.wait(min(0.1, due - now))
                continue
            delay = self.cycle()
            due = self.monotonic() + max(self.config["watch_interval_sec"], delay or 0)

    def recovered(self):
        self.last_error = None
        self.stats.update(state="listening", last_error=None)

    def save(self, entry):
        following = logic.store_entry(self.document, self.digest, entry)
        self.store.save(following)
        self.document = following

    def credentials(self):
        if self.kind == "oauth":
            return {"grant": logic.credentials(self.resolve_secret(self.config["secret_ref"]))}
        return {"api_key": logic.api_key(self.resolve_secret(self.config["api_key_ref"]))}

    def cycle(self):
        """One check: find new uploads, store the position, then hand the videos over one by one."""
        try:
            credential = self.credentials()
        except logic.YouTubeError as exc:
            self.report(exc)
            return None
        except Exception:
            self.report(logic.YouTubeError("secret"))
            return None
        session = self.session(self.config, self.stop, **credential)
        try:
            try:
                entry = self.document["watches"].get(self.digest)
                following, fresh, details = poll(session, entry, self.config["channel"], self.config, self.kind,
                                                 int(self.wall()))
                self.save(following)
                self.deliver(following, fresh, details)
            except logic.YouTubeError as exc:
                if self.stop():
                    return None
                self.report(exc)
                return exc.retry_after
        finally:
            self.stats["quota_units"] += session.units
        self.recovered()
        self.stats["cycles"] += 1
        if not self.stop():
            try:
                self.emit("status")
            except Exception:
                raise Halt() from None
        return None

    def deliver(self, entry, fresh, details):
        """Hand new videos over oldest first; each is recorded as seen just before its hand-off."""
        for item in fresh:
            if self.stop():
                break
            previous = self.document
            entry = {**entry, "seen": entry["seen"] + [item["video_id"]]}
            self.save(entry)
            try:
                self.emit("changed", envelope(None, self.config["channel"], details, [item]))
            except Exception:
                # Not handed off: restore the stored document so the next Run delivers it again.
                try:
                    self.store.save(previous)
                    self.document = previous
                except logic.YouTubeError:
                    pass
                raise Halt() from None
            self.stats["emitted"] += 1
