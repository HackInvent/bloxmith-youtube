"""Bounded, cancellable HTTPS exchanges with the YouTube Data API and the Google OAuth token endpoint.

Standard library only. Each exchange runs in a helper thread so cancellation,
Stop and the deadline can shut the socket down at once. Reads and video updates
(idempotent) are retried after HTTP 429, a 403 rate-limit reason, 5xx or a lost
connection; playlist insertions and removals only after a rate-limit answer,
which Google did not process. An ambiguous change is reported as uncertain,
never replayed. The API key travels in the x-goog-api-key header, never in a URL.
"""

import http.client
import json
import socket
import ssl
import threading
import time
from urllib.parse import urlencode, urlsplit

from . import logic

CHUNK = 65536
USER_AGENT = "BloxSmith-YouTube/0.1.0"
RETRYABLE = ("provider_unavailable",)
KEPT_HEADERS = ("location", "range")


class Failure(Exception):
    """A content-free transport outcome: code and the phase it happened in."""

    def __init__(self, code, phase):
        super().__init__(code)
        self.code, self.phase = code, phase


def reject_constant(_value):
    raise ValueError("Non-finite JSON number.")


def remaining(deadline, phase):
    left = deadline - time.monotonic()
    if left <= 0:
        raise Failure("timeout", phase)
    return max(0.001, left)


def read_json(response, conn, deadline, limit, found):
    """Read a bounded JSON body and report why it cannot be used, never raising on content."""
    chunks, total = [], 0
    while total <= limit:
        if conn.sock is not None:
            conn.sock.settimeout(remaining(deadline, "receive"))
        piece = response.read1(min(CHUNK, limit + 1 - total))
        if not piece:
            break
        total += len(piece)
        chunks.append(piece)
    media = found.get("content-type", "").split(";", 1)[0].strip().lower()
    if total > limit:
        return None, "too_large"
    if found.get("content-encoding", "identity").strip().lower() not in ("identity", ""):
        return None, "encoded"
    if total == 0:
        return {}, None
    if media != "application/json":
        return None, "not_json"
    try:
        return json.loads(b"".join(chunks).decode("utf-8", errors="strict"), parse_constant=reject_constant), None
    except (ValueError, UnicodeError, RecursionError):
        return None, "invalid_json"


def perform(state, origin, ca_file, method, path, headers, body, deadline, limit):
    """One request/response without redirects, cookies, proxies or decompression."""
    parsed = urlsplit(origin)
    host, port = parsed.hostname, parsed.port
    try:
        if parsed.scheme == "https":
            context = ssl.create_default_context(cafile=ca_file or None)
            conn = http.client.HTTPSConnection(host, port or 443, timeout=remaining(deadline, "connect"), context=context)
        else:
            conn = http.client.HTTPConnection(host, port or 80, timeout=remaining(deadline, "connect"))
        state["conn"] = conn
        conn.connect()
        if state.get("aborted"):
            raise Failure("network", "connect")
        state["phase"] = "send"
        conn.putrequest(method, path, skip_host=False, skip_accept_encoding=True)
        for key, value in headers.items():
            conn.putheader(key, value)
        conn.putheader("Content-Length", str(len(body or b"")))
        conn.sock.settimeout(remaining(deadline, "send"))
        conn.endheaders(message_body=body or None)
        state["phase"] = "receive"
        conn.sock.settimeout(remaining(deadline, "receive"))
        response = conn.getresponse()
        found = {key.lower(): value for key, value in response.getheaders()}
        retry = found.get("retry-after", "").strip()
        result = {"status": response.status, "retry_after": int(retry) if retry.isdigit() and len(retry) <= 6 else None,
                  "headers": {key: found[key][:4096] for key in KEPT_HEADERS if key in found}}
        result["body"], result["body_error"] = read_json(response, conn, deadline, limit, found)
        return result
    except Failure:
        raise
    except ssl.SSLCertVerificationError:
        raise Failure("tls", state["phase"]) from None
    except ssl.SSLError:
        raise Failure("tls" if state["phase"] == "connect" else "network", state["phase"]) from None
    except (socket.timeout, TimeoutError):
        raise Failure("timeout", state["phase"]) from None
    except (OSError, http.client.HTTPException, ValueError, AttributeError):
        raise Failure("network", state["phase"]) from None
    finally:
        conn = state.get("conn")
        if conn is not None:
            conn.close()


def abort(state):
    """Shut the socket down from the supervising thread; the helper thread then fails fast."""
    state["aborted"] = True
    conn = state.get("conn")
    sock = getattr(conn, "sock", None) if conn is not None else None
    if sock is not None:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass


def exchange(origin, ca_file, method, path, headers, body, *, deadline, cancel, limit):
    """Run one request in a helper thread and supervise cancellation and the deadline."""
    state, box = {"phase": "connect", "conn": None}, {}

    def target():
        try:
            box["result"] = perform(state, origin, ca_file, method, path, headers, body, deadline, limit)
        except Failure as failure:
            box["failure"] = failure
        except Exception:  # Defensive: an unexpected library error is a transport failure.
            box["failure"] = Failure("network", state["phase"])

    worker = threading.Thread(target=target, name="youtube-http", daemon=True)
    worker.start()
    while worker.is_alive():
        worker.join(0.05)
        if not worker.is_alive():
            break
        if cancel():
            phase = state["phase"]
            abort(state)
            worker.join(0.5)
            raise Failure("cancelled", phase)
        if time.monotonic() >= deadline + 0.5:
            phase = state["phase"]
            abort(state)
            worker.join(0.5)
            raise Failure("timeout", phase)
    if "failure" in box:
        raise box["failure"]
    return box["result"]


class Session:
    """One activation (or one watch check): one deadline, one credential, bounded requests and a quota estimate."""

    def __init__(self, config, cancel=lambda: False, *, api_key=None, grant=None, timeout=None):
        self.config = config
        self.api_key, self.grant = api_key, dict(grant) if grant else None
        self.cancel = cancel if callable(cancel) else (lambda: False)
        self.deadline = time.monotonic() + (timeout or config["timeout_sec"])
        self.secrets = [value for value in (self.grant or {}).values() if isinstance(value, str)]
        if api_key:
            self.secrets.append(api_key)
        self.token = None
        self.requests = 0
        self.units = 0

    def pause(self, seconds):
        """Cancellation-aware wait between a retryable response and its retry."""
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if self.cancel():
                logic.fail("cancelled", logic.MESSAGES["cancelled_before_send"])
            time.sleep(min(0.05, max(0.0, end - time.monotonic())))

    def send(self, origin, method, path, headers, body, *, mutation, replayable, cost=0, limit=None):
        """One exchange mapped to a structured outcome; never raises a raw library error."""
        if self.cancel():
            logic.fail("cancelled", logic.MESSAGES["cancelled_before_send"])
        if self.deadline - time.monotonic() <= 0.2:
            logic.fail("timeout")
        self.requests += 1
        try:
            outcome = exchange(origin, self.config["ca_file"], method, path, headers, body, deadline=self.deadline,
                               cancel=self.cancel, limit=limit or self.config["max_response_bytes"])
            self.units += cost  # Google charges every request it receives, refused ones included.
            return outcome
        except Failure as failure:
            if failure.phase != "connect":
                self.units += cost
            uncertain = mutation and failure.phase == "receive"
            if failure.code == "tls":
                logic.fail("tls")
            if failure.code == "timeout":
                logic.fail("timeout", logic.MESSAGES["timeout_uncertain" if uncertain else "timeout"], uncertain=uncertain)
            if failure.code == "cancelled":
                logic.fail("cancelled", logic.MESSAGES["cancelled" if uncertain else "cancelled_before_send"],
                           uncertain=uncertain)
            logic.fail("network", logic.MESSAGES["network_uncertain" if uncertain else "network"], uncertain=uncertain,
                       retryable=replayable)

    def retrying(self, attempt, *, replayable):
        """Run attempt() with bounded retries: rate limits always; 5xx and lost connections when replayable."""
        attempts = 0
        while True:
            attempts += 1
            try:
                return attempt()
            except logic.YouTubeError as exc:
                exc.attempts = attempts
                retryable = exc.code == "rate_limited" or (replayable and (
                    exc.code in RETRYABLE or getattr(exc, "retryable", False)))
                wait = exc.retry_after if exc.retry_after is not None else min(2 ** (attempts - 1), 8)
                if (retryable and attempts <= self.config["max_retries"] and wait <= self.config["max_retry_wait_sec"]
                        and time.monotonic() + wait + 1 < self.deadline):
                    self.pause(wait)
                    continue
                raise

    def authorize(self):
        """Use the wallet access token, or exchange the refresh-token grant once per session."""
        if self.token is not None:
            return self.token
        if self.grant is None:
            logic.fail("oauth_required")
        if "access_token" in self.grant:
            self.token = self.grant["access_token"]
            return self.token
        form = urlencode({"client_id": self.grant["client_id"], "client_secret": self.grant["client_secret"],
                          "refresh_token": self.grant["refresh_token"], "grant_type": "refresh_token"}).encode("ascii")
        headers = {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json",
                   "Accept-Encoding": "identity", "User-Agent": USER_AGENT, "Connection": "close"}

        def attempt():
            outcome = self.send(self.config["oauth_base_url"], "POST", "/token", headers, form, mutation=False,
                                replayable=True)
            status, body = outcome["status"], outcome["body"]
            if status == 429:
                logic.fail("rate_limited", http_status=429, retry_after=outcome["retry_after"])
            if status >= 500:
                logic.fail("provider_unavailable", "Google's token endpoint is temporarily unavailable (HTTP 5xx).",
                           http_status=status)
            if status != 200 or outcome["body_error"] or not isinstance(body, dict):
                error = body.get("error") if isinstance(body, dict) else None
                logic.fail("auth_failed", http_status=status,
                           provider_status=logic.clean(error, 64) if isinstance(error, str) else None)
            token = body.get("access_token")
            if (str(body.get("token_type", "")).lower() != "bearer" or not isinstance(token, str)
                    or not logic.TOKEN_TEXT.fullmatch(token)):
                logic.fail("auth_failed")
            return token

        self.token = self.retrying(attempt, replayable=True)
        self.secrets.append(self.token)
        return self.token

    def headers(self, auth, accept="application/json"):
        """Request headers carrying the chosen credential: x-goog-api-key, or an OAuth bearer token."""
        result = {"Accept": accept, "Accept-Encoding": "identity", "User-Agent": USER_AGENT, "Connection": "close"}
        if auth == "oauth":
            result["Authorization"] = "Bearer " + self.authorize()
        else:
            if not self.api_key:
                logic.fail("api_key_required")
            result["X-Goog-Api-Key"] = self.api_key
        return result

    def call(self, method, path, query=None, payload=None, *, auth, cost, mutation=False, replayable=None,
             extra_headers=None, full=False):
        """Call an API path and return its JSON body (an empty object for an empty 2xx body).

        With full=True the whole outcome (status, headers, body) of the 2xx answer is returned.
        """
        replayable = not mutation if replayable is None else replayable
        headers = self.headers(auth)
        headers.update(extra_headers or {})
        target = path + ("?" + urlencode(query, doseq=True) if query else "")
        body = None
        if payload is not None:
            body = logic.encoded(payload, max(self.config["max_request_bytes"], 65536), "request_too_large")
            headers["Content-Type"] = "application/json; charset=UTF-8"

        def attempt():
            outcome = self.send(self.config["api_base_url"], method, target, headers, body, mutation=mutation,
                                replayable=replayable, cost=cost)
            status = outcome["status"]
            if 200 <= status < 300:
                if outcome["body_error"] or not isinstance(outcome["body"], dict):
                    logic.fail("protocol", uncertain=mutation)
                return outcome if full else outcome["body"]
            if status >= 500 and outcome["body_error"]:
                logic.fail("provider_unavailable", logic.MESSAGES["provider_unavailable_uncertain" if mutation else
                                                                  "provider_unavailable"], uncertain=mutation,
                           http_status=status)
            logic.provider_failure(status, outcome["body"], self.secrets, mutation=mutation,
                                   retry_after=outcome["retry_after"])

        return self.retrying(attempt, replayable=replayable)
