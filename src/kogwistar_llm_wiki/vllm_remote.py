"""Direct, experimental adapter for vLLM's Qwen3-VL chat embeddings API.

The adapter keeps LLM-Wiki responsible for source resolution, asset hashing,
profile identity, and projection persistence. vLLM only performs inference.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from hashlib import sha256
import base64
import json
import math
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .multimodal_projection import (
    AssetResolver,
    EmbeddingSet,
    MultimodalEmbeddingProfile,
    MultimodalEncoder,
    MultimodalImageQueryEncoder,
    MultimodalSourceUnit,
    ProjectionIntegrityError,
    _normalise_embedding_set,
)
from .multimodal_remote import (
    EmbeddingProtocolError,
    EmbeddingServiceUnavailable,
    _asset_bytes,
)


_DIGEST_RE = re.compile(r"@sha256:[0-9a-fA-F]{64}$")
DEFAULT_VLLM_MODEL = "Qwen/Qwen3-VL-Embedding-2B"
DEFAULT_VLLM_DIMENSION = 1024
DEFAULT_VLLM_MAX_MODEL_LEN = 8192
DEFAULT_VLLM_CROP_TOKEN_BUDGET = 7680


@dataclass(frozen=True, slots=True)
class VllmEmbeddingSettings:
    """Transport and immutable serving identity for one vLLM instance."""

    url: str
    token: str
    image_digest: str
    model: str = DEFAULT_VLLM_MODEL
    model_revision: str | None = None
    dimension: int = DEFAULT_VLLM_DIMENSION
    instruction: str = "Represent the user's input."
    timeout_seconds: float = 30.0
    max_request_bytes: int = 5_000_000
    max_image_bytes: int = 4_000_000
    allowed_hosts: tuple[str, ...] = ()
    max_model_len: int = DEFAULT_VLLM_MAX_MODEL_LEN
    crop_token_budget: int = DEFAULT_VLLM_CROP_TOKEN_BUDGET
    enforce_eager: bool = True
    max_num_seqs: int = 1
    gpu_memory_utilization: float = 0.86

    def __post_init__(self) -> None:
        parsed = urlparse(self.url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("vLLM URL must use HTTP(S) with a hostname")
        if not self.token:
            raise ValueError("vLLM token is required")
        if self.model != DEFAULT_VLLM_MODEL:
            raise ValueError(f"experimental vLLM backend supports only {DEFAULT_VLLM_MODEL}")
        if not self.model_revision or not self.model_revision.strip():
            raise ValueError("vLLM model revision is required")
        if not _DIGEST_RE.search(self.image_digest.strip()):
            raise ValueError("vLLM image must be pinned by an @sha256 digest")
        if self.dimension != DEFAULT_VLLM_DIMENSION:
            raise ValueError("the experimental vLLM backend currently supports 1024 dimensions only")
        if self.timeout_seconds <= 0 or self.max_request_bytes <= 0 or self.max_image_bytes <= 0:
            raise ValueError("vLLM limits must be positive")
        if self.max_model_len <= 0 or self.crop_token_budget <= 0:
            raise ValueError("vLLM context limits must be positive")
        if self.crop_token_budget > self.max_model_len:
            raise ValueError("vLLM crop token budget cannot exceed max model length")
        if self.max_model_len > DEFAULT_VLLM_MAX_MODEL_LEN:
            raise ValueError("vLLM max model length cannot exceed 8192")
        if self.max_num_seqs <= 0:
            raise ValueError("vLLM max_num_seqs must be positive")
        if not 0 < self.gpu_memory_utilization <= 1:
            raise ValueError("vLLM GPU memory utilization must be between 0 and 1")
        if self.allowed_hosts and parsed.hostname.lower() not in {
            host.lower() for host in self.allowed_hosts
        }:
            raise ValueError(f"vLLM host {parsed.hostname!r} is not allowlisted")

    @property
    def profile(self) -> MultimodalEmbeddingProfile:
        instruction_fingerprint = sha256(self.instruction.encode("utf-8")).hexdigest()[:16]
        return MultimodalEmbeddingProfile(
            provider="vllm",
            model=self.model,
            model_revision=self.model_revision,
            embedding="dense",
            dimension=self.dimension,
            metric="dot",
            preprocessing_fingerprint=(
                f"qwen3-vl:vllm:{self.image_digest}:pooling:embed:"
                f"{instruction_fingerprint}:1024:context={self.max_model_len}:"
                f"crop={self.crop_token_budget}:eager={int(self.enforce_eager)}:"
                f"seqs={self.max_num_seqs}"
            ),
            max_sequence_length=self.max_model_len,
            max_image_patches=768,
            crop_token_budget=self.crop_token_budget,
            tokenizer_fingerprint="vllm-tokenize-v1",
            crop_policy="server_token_count_character_prefix",
        )


def _content_parts(
    unit: MultimodalSourceUnit,
    resolver: AssetResolver | None,
    *,
    max_image_bytes: int,
) -> list[dict[str, object]]:
    parts: list[dict[str, object]] = []
    if unit.content_ref is not None:
        if resolver is None:
            raise ProjectionIntegrityError(
                f"asset resolver is required for vLLM unit {unit.view_id!r}"
            )
        raw = _asset_bytes(resolver.resolve(unit))
        if len(raw) > max_image_bytes:
            raise ProjectionIntegrityError(
                f"asset for {unit.view_id!r} exceeds the vLLM image byte limit"
            )
        digest = sha256(raw).hexdigest()
        if unit.asset_sha256 and unit.asset_sha256.lower() != digest:
            raise ProjectionIntegrityError(
                f"asset hash mismatch for {unit.view_id!r}: expected {unit.asset_sha256}, got {digest}"
            )
        content_type = str(
            unit.metadata.get(
                "content_type",
                "image/png" if unit.modality in {"image", "chart", "table", "pdf_page"} else "video/mp4",
            )
        )
        if not (content_type.startswith("image/") or content_type.startswith("video/")):
            raise ProjectionIntegrityError(
                f"vLLM requires image/video bytes for unit {unit.view_id!r}"
            )
        parts.append(
            {
                "type": "image_url" if content_type.startswith("image/") else "video_url",
                "image_url" if content_type.startswith("image/") else "video_url": {
                    "url": f"data:{content_type};base64,{base64.b64encode(raw).decode('ascii')}"
                },
            }
        )
    if unit.text is not None:
        parts.append({"type": "text", "text": unit.text})
    elif parts:
        parts.append({"type": "text", "text": ""})
    if not parts:
        raise ProjectionIntegrityError(f"vLLM unit {unit.view_id!r} has no content")
    return parts


class VllmMultimodalEncoder(MultimodalEncoder, MultimodalImageQueryEncoder):
    """Call vLLM's Qwen3-VL Chat Embeddings API without local inference."""

    def __init__(self, settings: VllmEmbeddingSettings, *, opener: Any = urlopen) -> None:
        self.settings = settings
        self._profile = settings.profile
        self._opener = opener
        self._capabilities_checked = False

    @property
    def profile(self) -> MultimodalEmbeddingProfile:
        return self._profile

    def readiness(self) -> dict[str, object]:
        """Probe health and authenticated model identity before projection work."""
        request = Request(self.settings.url.rstrip("/") + "/health", method="GET")
        try:
            with self._opener(request, timeout=self.settings.timeout_seconds) as response:
                status = int(getattr(response, "status", 200))
        except (HTTPError, TimeoutError, URLError, OSError) as exc:
            return {"ready": False, "reason": str(exc)}
        if status >= 400:
            return {"ready": False, "status": status, "reason": "vLLM health check failed"}
        models_request = Request(
            self.settings.url.rstrip("/") + "/v1/models",
            headers={"Authorization": f"Bearer {self.settings.token}"},
            method="GET",
        )
        try:
            with self._opener(models_request, timeout=self.settings.timeout_seconds) as response:
                model_status = int(getattr(response, "status", 200))
                raw = response.read()
        except (HTTPError, TimeoutError, URLError, OSError) as exc:
            return {"ready": False, "reason": str(exc)}
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {"ready": False, "reason": "invalid vLLM model response"}
        models = payload.get("data") if isinstance(payload, Mapping) else None
        model_ids = {
            str(item.get("id"))
            for item in models
            if isinstance(item, Mapping) and item.get("id")
        } if isinstance(models, Sequence) and not isinstance(models, (str, bytes)) else set()
        if model_status >= 400 or self.settings.model not in model_ids:
            return {"ready": False, "reason": "vLLM model identity mismatch"}
        return {
            "ready": True,
            "status": status,
            "model": self.settings.model,
            "profile_fingerprint": self.profile.fingerprint,
        }

    def _post_json(self, path: str, payload: dict[str, object]) -> Mapping[str, object]:
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        if len(body) > self.settings.max_request_bytes:
            raise ProjectionIntegrityError("vLLM request exceeds configured byte limit")
        request = Request(
            self.settings.url.rstrip("/") + path,
            data=body,
            headers={
                "Authorization": f"Bearer {self.settings.token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with self._opener(request, timeout=self.settings.timeout_seconds) as response:
                status = int(getattr(response, "status", 200))
                raw = response.read()
        except HTTPError as exc:
            if exc.code in {408, 425, 429} or exc.code >= 500:
                raise EmbeddingServiceUnavailable(f"vLLM HTTP {exc.code}") from exc
            raise EmbeddingProtocolError(f"vLLM HTTP {exc.code}") from exc
        except (TimeoutError, URLError, OSError) as exc:
            raise EmbeddingServiceUnavailable("vLLM is unavailable") from exc
        if status >= 500:
            raise EmbeddingServiceUnavailable(f"vLLM HTTP {status}")
        try:
            result = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise EmbeddingProtocolError("vLLM returned invalid JSON") from exc
        if not isinstance(result, Mapping):
            raise EmbeddingProtocolError("vLLM response must be an object")
        return result

    def _token_count(self, messages: list[dict[str, object]]) -> int:
        result = self._post_json(
            "/tokenize",
            {
                "model": self.settings.model,
                "messages": messages,
                "continue_final_message": True,
                "add_generation_prompt": False,
                "add_special_tokens": True,
            },
        )
        count = result.get("count")
        if isinstance(count, int) and count >= 0:
            return count
        tokens = result.get("tokens")
        if isinstance(tokens, Sequence) and not isinstance(tokens, (str, bytes)):
            return len(tokens)
        raise EmbeddingProtocolError("vLLM tokenize response omitted token count")

    @staticmethod
    def _with_text_prefix(
        messages: list[dict[str, object]], text_index: int, prefix_length: int
    ) -> list[dict[str, object]]:
        result = deepcopy(messages)
        content = result[1].get("content")
        if not isinstance(content, list):
            return result
        text_part = content[text_index]
        if isinstance(text_part, Mapping):
            text_part["text"] = str(text_part.get("text", ""))[:prefix_length]
        return result

    def _bounded_messages(self, messages: list[dict[str, object]]) -> list[dict[str, object]]:
        count = self._token_count(messages)
        if count <= self.settings.crop_token_budget:
            return messages
        content = messages[1].get("content")
        text_indexes = [
            index
            for index, part in enumerate(content if isinstance(content, list) else [])
            if isinstance(part, Mapping) and part.get("type") == "text"
        ]
        if not text_indexes:
            raise ProjectionIntegrityError(
                "vLLM image content exceeds the configured token budget; "
                "reduce image size or increase the context limit"
            )
        text_index = text_indexes[-1]
        text_part = content[text_index]
        original = str(text_part.get("text", "")) if isinstance(text_part, Mapping) else ""
        low, high = 0, len(original)
        best: list[dict[str, object]] | None = None
        while low <= high:
            middle = (low + high) // 2
            candidate = self._with_text_prefix(messages, text_index, middle)
            if self._token_count(candidate) <= self.settings.crop_token_budget:
                best = candidate
                low = middle + 1
            else:
                high = middle - 1
        if best is None:
            raise ProjectionIntegrityError(
                "vLLM non-text prompt content exceeds the configured token budget"
            )
        return best

    def _request_unchecked(self, messages: list[dict[str, object]]) -> EmbeddingSet:
        payload = {
            "model": self.settings.model,
            "messages": messages,
            "encoding_format": "float",
            "dimensions": self.profile.dimension,
            "continue_final_message": True,
            "add_generation_prompt": False,
            "add_special_tokens": True,
        }
        result = self._post_json("/v1/embeddings", payload)
        if result.get("model") != self.settings.model:
            raise EmbeddingProtocolError("vLLM returned an unexpected model identity")
        data = result.get("data")
        if not isinstance(data, Sequence) or isinstance(data, (str, bytes)) or len(data) != 1:
            raise EmbeddingProtocolError("vLLM must return exactly one embedding result")
        item = data[0]
        if not isinstance(item, Mapping) or item.get("index") != 0:
            raise EmbeddingProtocolError("vLLM returned an invalid result index")
        try:
            embedding = _normalise_embedding_set(
                [item["embedding"]], dimension=self.profile.dimension
            )
        except (KeyError, TypeError, ValueError, ProjectionIntegrityError) as exc:
            raise EmbeddingProtocolError("vLLM returned an invalid embedding vector") from exc
        if any(not math.isfinite(value) for vector in embedding for value in vector):
            raise EmbeddingProtocolError("vLLM returned a non-finite embedding vector")
        return embedding

    def _request(self, messages: list[dict[str, object]]) -> EmbeddingSet:
        bounded = self._bounded_messages(messages)
        if not self._capabilities_checked:
            self._request_unchecked(
                self._messages([{"type": "text", "text": "capability probe"}])
            )
            self._capabilities_checked = True
        return self._request_unchecked(bounded)

    def _messages(self, parts: list[dict[str, object]]) -> list[dict[str, object]]:
        return [
            {"role": "system", "content": [{"type": "text", "text": self.settings.instruction}]},
            {"role": "user", "content": parts},
            {"role": "assistant", "content": [{"type": "text", "text": ""}]},
        ]

    def _request_many(
        self,
        messages: Sequence[list[dict[str, object]]],
        *,
        batch_size: int | None,
    ) -> list[EmbeddingSet]:
        """Issue bounded concurrent requests while preserving input order.

        vLLM's multimodal Chat Embeddings extension accepts one message set per
        request. A bounded window lets its scheduler coalesce GPU work without
        making the adapter unbounded or reordering source units.
        """
        if not messages:
            return []
        window = max(1, int(batch_size or 1))
        results: list[EmbeddingSet] = []
        for start in range(0, len(messages), window):
            chunk = messages[start : start + window]
            with ThreadPoolExecutor(max_workers=len(chunk)) as executor:
                results.extend(executor.map(self._request, chunk))
        return results

    def encode_queries(self, queries: Sequence[str], *, batch_size: int | None = None) -> Sequence[EmbeddingSet]:
        return self._request_many(
            [self._messages([{"type": "text", "text": str(query)}]) for query in queries],
            batch_size=batch_size,
        )

    def encode_documents(
        self,
        units: Sequence[MultimodalSourceUnit],
        *,
        batch_size: int | None = None,
        resolver: AssetResolver | None = None,
    ) -> Sequence[EmbeddingSet]:
        return self._request_many(
            [
                self._messages(
                    _content_parts(unit, resolver, max_image_bytes=self.settings.max_image_bytes)
                )
                for unit in units
            ],
            batch_size=batch_size,
        )

    def encode_image_queries(self, images: Sequence[object], *, batch_size: int | None = None) -> Sequence[EmbeddingSet]:
        messages: list[list[dict[str, object]]] = []
        for image in images:
            raw = _asset_bytes(image)
            if len(raw) > self.settings.max_image_bytes:
                raise ProjectionIntegrityError("image query exceeds the vLLM image byte limit")
            parts = [{
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/png;base64,{base64.b64encode(raw).decode('ascii')}"
                },
            }, {"type": "text", "text": ""}]
            messages.append(self._messages(parts))
        return self._request_many(messages, batch_size=batch_size)


__all__ = [
    "DEFAULT_VLLM_MODEL",
    "DEFAULT_VLLM_DIMENSION",
    "VllmEmbeddingSettings",
    "VllmMultimodalEncoder",
]
