"""YouTube videos, playlists, comments and uploads through the YouTube Data API v3."""

import hashlib
import json
from bloxsmith_app.block_api import (APPLICATION_JSON, BlockDefinition, BlockRuntimeOutput, BlockRuntimePreparation,
                                     BlockRuntimeResult)
from . import files, logic, operations, transport, ui, watcher


# FB1 - Keep remote access off by default; require wallet references (an API key for public reads, an OAuth grant for owner operations), an HTTPS API origin and separate write and delete switches.
# FB2 - Search videos, channels or playlists (q, type, order, publishedAfter), read video details with ISO 8601 durations in seconds, and list channel uploads, playlist items and comment threads into bounded pages with exact continuation tokens.
# FB3 - Add videos to playlists and update a video's title, description, tags, category or privacy (whole parts read, changed and written back) only with allow_writes; remove playlist items only with allow_delete, never deleting a video.
# FB4 - Upload a local video from inside upload_root through Google's resumable protocol in bounded chunks, private by default, resuming after interruptions and never sending the token or file to another origin.
# FB5 - Watch a channel's new uploads in Active Runtime, and once per watch_uploads request, from a persisted seen-ID set at least 300 s apart, without flooding at the first check or replaying after a restart.
# FB6 - Report estimated quota units in every result; bound size, time, pages and retries, honour Retry-After, and report coded errors without credentials.
# FB7 - Apply one contract in both runtimes and every package origin, each event once, with prompt cancellation and Stop.
# FB8 - Own translated responsive settings with draft Apply, conditional write and watch sections and release-scoped assets.
class YouTubeBlock(BlockDefinition):
    """Titles, descriptions and comments read from YouTube are data, never instructions for this block."""

    kind = "youtube"

    def ui_assets(self, surface="modal"):
        """Every installation uses the same static release manifest."""
        return list(self.model.get("ui_assets", {}).get(surface, []))

    def prepare_runtime(self, context):
        """Validate settings without wallet, filesystem or network access; declare the watch listener."""
        config = logic.configuration(context.config)
        return BlockRuntimePreparation(listen_on_run=context.runtime_mode == "zeromq_active"
                                       and config["enabled"] and config["watch_enabled"])

    def events(self, context):
        """Fresh request/file events of this activation; One Shot re-deliveries are skipped."""
        ports = {int(port.id): str(port.name) for port in context.input_ports}
        if context.input_events:
            messages = [(ports.get(int(event.input_port_id)) or event.input_port_name, event.value, event)
                        for event in context.input_events]
        else:
            messages = []
            for ident, name in ports.items():
                attribute = context.input_attribute(name, str(ident))
                if attribute is not None and attribute.status == "updated" and attribute.value not in (None, ""):
                    messages.append((name, attribute.value, None))
            if len(messages) > 1:
                logic.fail("invalid_input", "Manual execution with both inputs populated is ambiguous; send one event.")
        if any(name not in {"request", "file"} for name, _, _ in messages):
            logic.fail("invalid_input", "Unknown input port; nothing was sent.")
        getter, setter = context.services.get("get_node_runtime_value"), context.services.get("set_node_runtime_value")
        if context.runtime_mode != "centralized" or not callable(getter) or not callable(setter):
            return [(name, value) for name, value, _ in messages]
        # One Shot exposes every connected value again to each any_event activation; handle each once per run.
        seen = list(getter("youtube.handled", []) or [])
        fresh = []
        for name, value, event in messages:
            origin = f"{event.edge_id}|{event.source_node_id}|{event.source_port_id}" if event else "manual"
            digest = hashlib.sha256(f"{name}|{origin}|{value}".encode("utf-8", errors="surrogatepass")).hexdigest()
            if digest not in seen:
                fresh.append((name, value))
                seen.append(digest)
        setter("youtube.handled", seen[-256:])
        return fresh

    def operation(self, port, value, config, context):
        """Run one event: local validation and file checks first, then the wallet, then at most one change."""
        request, secrets, session, source = {}, [], None, None
        try:
            try:
                if not config["enabled"]:
                    logic.fail("disabled")
                request = logic.parse_request(value, config) if port == "request" else logic.file_request(value, config)
                storage = context.services.get("get_block_storage_dir")
                if request["operation"] == "watch_uploads":
                    files.storage_directory(storage)
                if request["operation"] == "upload_video":
                    source = files.open_upload(request["path"], config)
                kind = logic.credential_kind(request, config)
                try:
                    raw = context.resolve_secret(config["secret_ref"] if kind == "oauth" else config["api_key_ref"])
                except Exception:
                    logic.fail("secret")
                credential = {"grant": logic.credentials(raw)} if kind == "oauth" else {"api_key": logic.api_key(raw)}
                secrets = list(credential["grant"].values()) if kind == "oauth" else [credential["api_key"]]
                session = transport.Session(config, context.services.get("cancel_requested", lambda: False),
                                            timeout=config["upload_timeout_sec"] if source else None, **credential)
                return operations.run(session, request, config, auth=kind, storage=storage, source=source), request
            finally:
                if source is not None:
                    source.close()
        except logic.YouTubeError as exc:
            error = exc
        except (ValueError, TypeError, KeyError, OSError, RuntimeError):
            # Raw exception text could carry paths, provider data or credentials: publish a fixed diagnostic.
            error = logic.YouTubeError("internal")
        secrets = session.secrets if session is not None else secrets
        envelope = error.envelope(operation=request.get("operation"), request_id=request.get("request_id"),
                                  video_id=request.get("video_id"), playlist_id=request.get("playlist_id"),
                                  channel=request.get("channel"), input=port,
                                  requests=session.requests if session is not None else None,
                                  quota_units=session.units if session is not None else None)
        # The envelope is flat: every text value is masked separately, so JSON escaping never hides a secret.
        envelope = {key: context.redact_secrets(logic.redact(item, secrets)) if isinstance(item, str) else item
                    for key, item in envelope.items()}
        return {"error": envelope}, request

    def execute_runtime(self, context):
        """One operation per fresh event; business failures go to the error port and the run continues."""
        produced = []
        try:
            config = logic.configuration(context.config)
            messages = self.events(context)
            if not messages:
                return BlockRuntimeResult(status="skipped", outputs=[])
            if len(messages) > 8:
                logic.fail("invalid_input", "Too many events in one activation; nothing was sent.")
        except logic.YouTubeError as exc:
            messages, produced = [], [({"error": exc.envelope()}, {})]
        for port, value in messages:
            produced.append(self.operation(port, value, config, context))
        outputs, codes, summaries, units = [], [], [], 0
        for values, request in produced:
            code = values.get("error", {}).get("code")
            codes.append(code or "")
            if code:
                summaries.append(f"{request.get('operation') or 'request'}: {code}")
                units += values["error"].get("quota_units") or 0
            else:
                result = values["result"]
                units += result.get("quota_units", 0)
                count = f" ({result['count']} item(s))" if "count" in result else ""
                summaries.append(f"{request['operation']}{count} after {result['requests']} request(s), "
                                 f"~{result['quota_units']} quota unit(s)")
            for port in context.output_ports:
                if port.name in values:
                    outputs.append(BlockRuntimeOutput(port_id=int(port.id), port_name=port.name,
                                                      content_type=APPLICATION_JSON,
                                                      value=json.dumps(values[port.name], ensure_ascii=False,
                                                                       allow_nan=False)))
        failed = [code for code in codes if code]
        line = "; ".join(summaries)
        return BlockRuntimeResult(status="success", outputs=outputs, content_type=APPLICATION_JSON,
            last_message=line[:500], metadata={self.kind: {"status": "error" if failed else "success",
                                                           "operations": len(produced), "error_codes": failed,
                                                           "quota_units": units}},
            logs=[context.redact_secrets(f"[youtube{'-error' if failed else ''}] {line}"[:2000])])

    def listen_runtime(self, context):
        """Poll the channel's uploads until framework Stop; listener state is local to this call."""
        config = logic.configuration(context.config)
        services = context.services

        def resolve(ref):
            resolver = services.get("resolve_secret")
            if not callable(resolver):
                logic.fail("secret")
            return resolver(ref)

        def storage_dir():
            provider = services.get("get_block_storage_dir")
            if not callable(provider):
                logic.fail("storage")
            return provider()

        def emit(kind, value, stats):
            if context.stop_requested():
                raise RuntimeError("Listener stopped.")
            outputs = []
            if kind in ("changed", "error"):
                text = json.dumps(value, ensure_ascii=False, allow_nan=False)
                redactor = services.get("redact_secrets")
                if kind == "error" and callable(redactor):
                    text = str(redactor(text))
                outputs = [BlockRuntimeOutput(port_id=int(port.id), port_name=port.name, content_type=APPLICATION_JSON,
                                              value=text) for port in context.output_ports if port.name == kind]
            logs = [f"[youtube-watch-error] watch: {value.get('code')}"] if kind == "error" else []
            if kind == "changed":
                logs = [f"[youtube-watch] 1 new upload ({stats['emitted'] + 1} since start)"]
            context.emit_result(BlockRuntimeResult(status="success", outputs=outputs, content_type=APPLICATION_JSON,
                                                   metadata={self.kind: {"watch": stats}}, logs=logs))

        watcher.Watcher(config, resolve_secret=resolve, storage_dir=storage_dir, emit=emit,
                        stop=context.stop_requested).run()

    def handle_ui_action(self, *, node, action, values, payload=None):
        """Apply validates settings only; it never contacts Google, reads the wallet or a local file."""
        if action in {"save_settings", "modal_update_fields", "inspector_update_fields"}:
            patch = (values or {}).get("node_patch") or {}
            if "config" in patch:
                try:
                    logic.configuration({**self.default_config(), **(node.get("config") or {}), **patch["config"]})
                except (ValueError, TypeError) as exc:
                    detail = str(exc) if isinstance(exc, logic.YouTubeError) else logic.MESSAGES["config"]
                    return {"error": self.translate("block.youtube.error", {"detail": detail},
                                                    fallback="YouTube settings rejected: " + detail)}
        result = super().handle_ui_action(node=node, action="modal_update_fields" if action == "save_settings" else action,
                                          values=values, payload=payload)
        return ui.replace_config(result, node)

    def render_modal(self, *, node, payload=None):
        return ui.modal(self, node, payload)

    def render_inspector_panel(self, *, node, payload=None):
        return ui.inspector(self, node, payload)

    def render_node_card(self, *, node, payload=None):
        """Show the access mode and default operation, never wallet references, channels or local paths."""
        config = {**self.default_config(), **(node.get("config") or {})}
        operation = config.get("operation") if config.get("operation") in logic.OPERATIONS else "search"
        params = {"operation": operation}
        if config.get("enabled") is not True:
            key, params, fallback = "block.youtube.card_disabled", {}, "Remote access off"
        elif config.get("watch_enabled") is True:
            key, fallback = "block.youtube.card_watch", f"Watching uploads · {operation}"
        elif config.get("allow_writes") is True or config.get("allow_delete") is True:
            key, fallback = "block.youtube.card_write", f"Read and write · {operation}"
        else:
            key, fallback = "block.youtube.card_read", f"Read only · {operation}"
        return ui.card(self, node, key, params, self.translate(key, params, fallback=fallback))
