"""User-scoped host bridge for Codex-backed structured maintenance calls."""

from __future__ import annotations

import base64
import binascii
import hmac
import json
import logging
import os
import tempfile
import threading
from collections.abc import Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Literal, cast

from kogwistar.json_types import JsonValue

from .codex_workbench_agent import CodexCliSettings, CodexProcessRunner

logger = logging.getLogger(__name__)

_MAX_BODY_BYTES = 20 * 1024 * 1024
_MAX_MESSAGES = 64
_MAX_MESSAGE_CHARS = 120_000
_MAX_SCHEMA_CHARS = 64_000
_MAX_IMAGES = 4
_MAX_IMAGE_BYTES = 12 * 1024 * 1024

JsonObject = dict[str, JsonValue]


def _codex_transport(value: object) -> Literal["exec", "app_server"]:
    if value == "exec":
        return "exec"
    if value == "app_server":
        return "app_server"
    return "exec"


class CodexBridgeState:
    __slots__ = ("lock", "runner", "settings", "token")

    def __init__(self, *, token: str, settings: CodexCliSettings) -> None:
        if not token:
            raise ValueError("Codex bridge token must not be empty")
        self.token = token
        self.settings = settings
        self.runner = CodexProcessRunner()
        self.lock = threading.Lock()

    def complete(self, payload: Mapping[str, JsonValue]) -> JsonObject:
        messages = payload.get("messages")
        schema = payload.get("response_schema")
        if not isinstance(messages, list) or not messages or len(messages) > _MAX_MESSAGES:
            raise ValueError("messages must contain between 1 and 64 items")
        if not isinstance(schema, dict):
            raise TypeError("response_schema must be an object")
        encoded_schema = json.dumps(schema, ensure_ascii=False, sort_keys=True)
        if len(encoded_schema) > _MAX_SCHEMA_CHARS:
            raise ValueError("response_schema exceeds the bridge limit")
        normalized: list[dict[str, JsonValue]] = []
        image_payloads: list[tuple[bytes, str]] = []
        total_chars = 0
        for message in messages:
            if not isinstance(message, dict):
                raise TypeError("each message must be an object")
            role = str(message.get("role") or "user")
            content = message.get("content")
            if isinstance(content, str):
                normalized_content: JsonValue = content
                total_chars += len(content)
            elif isinstance(content, list):
                normalized_content, extracted = _normalize_multimodal_content(content)
                image_payloads.extend(extracted)
                total_chars += len(json.dumps(normalized_content, ensure_ascii=False))
            else:
                raise TypeError("message content must be a string or content-part list")
            normalized.append({"role": role, "content": normalized_content})
        if total_chars > _MAX_MESSAGE_CHARS:
            raise ValueError("maintenance context exceeds the bridge limit")
        model = str(payload.get("model") or self.settings.model or "")
        prompt = _build_prompt(normalized, schema)
        with tempfile.TemporaryDirectory(prefix="kogwistar-codex-images-") as directory:
            image_paths = []
            for index, (payload_bytes, suffix) in enumerate(image_payloads):
                path = Path(directory) / f"image-{index}{suffix}"
                path.write_bytes(payload_bytes)
                image_paths.append(path)
            with self.lock:
                raw = self.runner.run(
                    settings=CodexCliSettings(
                        executable=self.settings.executable,
                        model=model or None,
                        profile=self.settings.profile,
                        timeout_seconds=self.settings.timeout_seconds,
                        transport=self.settings.transport,
                    ),
                    prompt=prompt,
                    progress=lambda: None,
                    output_schema=schema,
                    images=image_paths,
                )
        try:
            output = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("Codex returned invalid JSON for the requested schema") from exc
        if not isinstance(output, dict):
            raise TypeError("Codex returned a non-object structured result")
        return {"output": output, "provider": "codex", "model": model or "default"}


def _normalize_multimodal_content(
    content: list[JsonValue],
) -> tuple[list[JsonValue], list[tuple[bytes, str]]]:
    if len(content) > _MAX_IMAGES + 16:
        raise ValueError("message content contains too many parts")
    normalized: list[JsonValue] = []
    images: list[tuple[bytes, str]] = []
    for part in content:
        if not isinstance(part, dict):
            raise TypeError("multimodal content parts must be objects")
        part_type = part.get("type")
        if part_type == "text":
            text = part.get("text")
            if not isinstance(text, str):
                raise TypeError("text content parts require string text")
            normalized.append({"type": "text", "text": text})
            continue
        if part_type != "image_url":
            raise ValueError("unsupported multimodal content part")
        image_url = part.get("image_url")
        if not isinstance(image_url, dict) or not isinstance(image_url.get("url"), str):
            raise TypeError("image_url content parts require a URL")
        url = image_url["url"]
        if not url.startswith("data:image/") or ";base64," not in url:
            raise ValueError("Codex bridge accepts only base64 data-image URLs")
        encoded = url.split(";base64,", 1)[1]
        try:
            decoded = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("invalid base64 image content") from exc
        if not decoded or len(decoded) > _MAX_IMAGE_BYTES:
            raise ValueError("image content exceeds the bridge limit")
        if len(images) >= _MAX_IMAGES:
            raise ValueError("too many images for one Codex bridge request")
        media_type = url.removeprefix("data:image/").split(";", 1)[0].lower()
        suffix = {
            "jpeg": ".jpg",
            "jpg": ".jpg",
            "png": ".png",
            "webp": ".webp",
            "gif": ".gif",
        }.get(media_type)
        if suffix is None:
            raise ValueError(f"unsupported Codex image format: {media_type!r}")
        images.append((decoded, suffix))
        normalized.append({"type": "image", "text": "[image attached]"})
    return normalized, images


def _build_prompt(messages: list[dict[str, JsonValue]], schema: JsonObject) -> str:
    return (
        "You are a bounded maintenance reasoning provider. Return only a JSON object "
        "matching the supplied response schema. Use only the supplied messages. "
        "Do not call tools, access files, access networks, use MCP, create jobs, "
        "or perform mutations. Do not include secrets or credentials.\n\n"
        "MESSAGES:\n"
        + json.dumps(messages, ensure_ascii=False, sort_keys=True)
        + "\n\nRESPONSE_SCHEMA:\n"
        + json.dumps(schema, ensure_ascii=False, sort_keys=True)
    )


class _BridgeHandler(BaseHTTPRequestHandler):
    def _bridge_server(self) -> _BridgeServer:
        return cast(_BridgeServer, self.server)

    def do_GET(self) -> None:
        if self.path != "/healthz":
            self._send(404, {"error": "not_found"})
            return
        self._send(200, {"ok": True, "provider": "codex"})

    def do_POST(self) -> None:
        if self.path != "/v1/structured":
            self._send(404, {"error": "not_found"})
            return
        expected = f"Bearer {self._bridge_server().state.token}"
        supplied = self.headers.get("Authorization", "")
        if not hmac.compare_digest(supplied, expected):
            self._send(401, {"error": "unauthorized"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > _MAX_BODY_BYTES:
                raise ValueError("request body exceeds the bridge limit")
            payload = cast(JsonValue, json.loads(self.rfile.read(length).decode("utf-8")))
            if not isinstance(payload, dict):
                raise TypeError("request body must be an object")
            self._send(200, self._bridge_server().state.complete(payload))
        except TimeoutError as exc:
            self._send(504, {"error": "timeout", "message": str(exc)})
        except (TypeError, ValueError) as exc:
            self._send(400, {"error": "invalid_request", "message": str(exc)})
        except Exception as exc:
            logger.exception("Codex bridge completion failed")
            self._send(502, {"error": "bridge_failure", "message": str(exc)})

    def log_message(self, format: str, *args: object) -> None:
        logger.info("codex_bridge " + format, *args)

    def _send(self, status: int, payload: JsonObject) -> None:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


class _BridgeServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], state: CodexBridgeState) -> None:
        super().__init__(address, _BridgeHandler)
        self.state = state


def serve_codex_bridge(*, host: str, port: int, token: str, settings: CodexCliSettings) -> None:
    server = _BridgeServer((host, port), CodexBridgeState(token=token, settings=settings))
    logger.info("Codex bridge listening on %s:%s", host, port)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()


def bridge_settings_from_environment() -> CodexCliSettings:
    return CodexCliSettings(
        executable=os.environ.get("KOGWISTAR_CODEX_EXECUTABLE"),
        model=os.environ.get("KOGWISTAR_MAINTENANCE_CODEX_MODEL") or None,
        profile=os.environ.get("KOGWISTAR_CODEX_PROFILE") or None,
        timeout_seconds=max(1, int(os.environ.get("KOGWISTAR_MAINTENANCE_CODEX_TIMEOUT_SECONDS", "300"))),
        transport=_codex_transport(
            os.environ.get("KOGWISTAR_CODEX_TRANSPORT", "exec")
        ),
    )


__all__ = ["bridge_settings_from_environment", "serve_codex_bridge"]
