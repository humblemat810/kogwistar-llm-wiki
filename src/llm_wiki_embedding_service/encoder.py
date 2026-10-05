"""Qwen3-VL inference with no dependency on the host application."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from io import BytesIO
from typing import Any

from llm_wiki_embedding_contract import (
    ContractValidationError,
    EmbeddingProfile,
    validate_dense_vectors,
)

from .config import (
    BGE_QUERY_INSTRUCTION,
    BGE_SMALL_MAX_SEQUENCE_LENGTH,
    MAX_DIMENSION,
    MIN_DIMENSION,
    EmbeddingServiceConfig,
)


class EmbeddingInferenceError(ValueError):
    """The configured model returned an unusable embedding."""


class Qwen3VLDenseEncoder:
    """One dense vector per text, image, or mixed request item."""

    def __init__(self, model: Any, processor: Any, *, profile: EmbeddingProfile, device: str, batch_size: int = 1, vision_processor: Any | None = None, instruction: str) -> None:
        self._model = model
        self._processor = processor
        self._vision_processor = vision_processor
        self.profile = profile
        self.device = device
        self.batch_size = max(1, int(batch_size))
        self.instruction = instruction

    @classmethod
    def from_pretrained(cls, config: EmbeddingServiceConfig) -> Qwen3VLDenseEncoder:
        if not MIN_DIMENSION <= config.dimension <= MAX_DIMENSION:
            raise ValueError("embedding dimension must be between 64 and 2048")
        try:
            import torch
            from qwen_vl_utils import process_vision_info  # pyright: ignore[reportMissingImports]
            from transformers import AutoModelForMultimodalLM, AutoProcessor
        except ImportError as exc:
            raise RuntimeError("embedding service requires Torch, Transformers, qwen-vl-utils, and Accelerate") from exc
        _validate_torch(torch, config)
        model_source = config.model_path or config.model
        kwargs: dict[str, object] = {"trust_remote_code": True, "revision": config.revision}
        if config.device == "cuda":
            kwargs.update({"torch_dtype": torch.float16, "device_map": "auto"})
        else:
            kwargs["torch_dtype"] = torch.float32
        model = AutoModelForMultimodalLM.from_pretrained(model_source, **kwargs).eval()
        processor = AutoProcessor.from_pretrained(model_source, trust_remote_code=True, revision=config.revision, padding_side="right")
        return cls(model, processor, profile=config.profile, device=config.device, batch_size=config.batch_size, vision_processor=process_vision_info, instruction=config.instruction)

    def _conversation(self, item: Mapping[str, object], *, value: object | None = None) -> list[dict[str, object]]:
        content: list[dict[str, object]] = []
        if item.get("asset") is not None:
            content.append({"type": "image", "image": value})
        if item.get("text") is not None:
            content.append({"type": "text", "text": str(item["text"])})
        if not content:
            raise ContractValidationError("embedding item requires text or asset")
        return [{"role": "system", "content": [{"type": "text", "text": self.instruction}]}, {"role": "user", "content": content}]

    def _prepare(self, conversations: Sequence[list[dict[str, object]]]) -> Any:
        template = getattr(self._processor, "apply_chat_template", None)
        texts = template(conversations, add_generation_prompt=True, tokenize=False) if callable(template) else [str(c) for c in conversations]
        kwargs: dict[str, object] = {"text": texts, "truncation": True, "max_length": self.profile.max_sequence_length, "padding": True, "return_tensors": "pt"}
        if self._vision_processor is not None:
            images, videos, *rest = self._vision_processor(conversations, image_patch_size=16, return_video_metadata=True, return_video_kwargs=True)
            kwargs["images"], kwargs["videos"] = images, videos
            if rest and isinstance(rest[-1], Mapping):
                kwargs.update(rest[-1])
        return self._processor(**kwargs)

    def _run(self, inputs: Any) -> list[tuple[tuple[float, ...], ...]]:
        import torch
        if isinstance(inputs, Mapping):
            inputs = {key: value.to(self.device) if hasattr(value, "to") else value for key, value in inputs.items()}
        elif hasattr(inputs, "to"):
            inputs = inputs.to(self.device)
        with torch.inference_mode():
            output = getattr(self._model, "model", self._model)(**inputs) if isinstance(inputs, Mapping) else getattr(self._model, "model", self._model)(inputs)
        values = getattr(output, "embeddings", getattr(output, "last_hidden_state", output))
        if getattr(values, "ndim", None) == 3:
            mask = inputs.get("attention_mask") if isinstance(inputs, Mapping) else None
            if mask is None:
                values = values[:, -1, :]
            else:
                positions = mask.long().sum(dim=1).clamp_min(1) - 1
                values = values[torch.arange(values.shape[0], device=values.device), positions]
        if getattr(values, "ndim", None) != 2:
            raise EmbeddingInferenceError("model did not return one dense vector per item")
        values = torch.nn.functional.normalize(values[..., : self.profile.dimension], p=2, dim=-1).detach().cpu().tolist()
        return [validate_dense_vectors([row], dimension=self.profile.dimension) for row in values]

    def encode(self, items: Sequence[Mapping[str, object]]) -> list[tuple[tuple[float, ...], ...]]:
        conversations: list[list[dict[str, object]]] = []
        owned: list[Any] = []
        try:
            for item in items:
                asset = item.get("asset")
                value: object | None = None
                if isinstance(asset, Mapping):
                    from PIL import Image
                    value = Image.open(BytesIO(asset["bytes"])) .convert("RGB")
                    owned.append(value)
                conversations.append(self._conversation(item, value=value))
            result: list[tuple[tuple[float, ...], ...]] = []
            for start in range(0, len(conversations), self.batch_size):
                result.extend(self._run(self._prepare(conversations[start : start + self.batch_size])))
            if len(result) != len(items):
                raise EmbeddingInferenceError("model returned a different number of vectors")
            return result
        finally:
            for value in owned:
                close = getattr(value, "close", None)
                if callable(close):
                    close()


class BgeSmallTextEncoder:
    """CPU-friendly, text-only BGE encoder with retrieval query prompting."""

    def __init__(
        self,
        model: Any,
        tokenizer: Any,
        *,
        profile: EmbeddingProfile,
        device: str,
    ) -> None:
        self._model = model
        self._tokenizer = tokenizer
        self.profile = profile
        self.device = device

    @classmethod
    def from_pretrained(cls, config: EmbeddingServiceConfig) -> BgeSmallTextEncoder:
        if config.encoder != "bge-small-en-v1.5":
            raise ValueError("BGE encoder requires the bge-small-en-v1.5 profile")
        try:
            import torch
            from transformers import AutoModel, AutoTokenizer
        except ImportError as exc:
            raise RuntimeError("BGE embedding service requires Torch and Transformers") from exc
        _validate_torch(torch, config)
        model_source = config.model_path or config.model
        tokenizer = AutoTokenizer.from_pretrained(
            model_source,
            revision=config.revision,
            cache_dir=config.model_cache_dir,
            token=config.token,
        )
        model = AutoModel.from_pretrained(
            model_source,
            revision=config.revision,
            cache_dir=config.model_cache_dir,
            token=config.token,
        ).to(config.device).eval()
        return cls(model, tokenizer, profile=config.profile, device=config.device)

    def encode(self, items: Sequence[Mapping[str, object]]) -> list[tuple[tuple[float, ...], ...]]:
        import torch

        texts: list[str] = []
        for item in items:
            if item.get("asset") is not None:
                raise ContractValidationError("BGE text encoder does not support assets")
            text = str(item.get("text") or "").strip()
            if not text:
                raise ContractValidationError("BGE text encoder requires non-empty text")
            operation = item.get("operation", "document")
            if operation == "query":
                text = BGE_QUERY_INSTRUCTION + text
            elif operation != "document":
                raise ContractValidationError("embedding operation must be query or document")
            texts.append(text)

        encoded: Any = self._tokenizer(
            texts,
            padding=True,
            truncation=True,
            max_length=BGE_SMALL_MAX_SEQUENCE_LENGTH,
            return_tensors="pt",
        )
        encoded = {
            key: value.to(self.device) if hasattr(value, "to") else value
            for key, value in encoded.items()
        }
        with torch.inference_mode():
            output = self._model(**encoded)
        hidden: Any = getattr(output, "last_hidden_state", None)
        if getattr(hidden, "ndim", None) != 3:
            raise EmbeddingInferenceError("BGE model did not return token hidden states")
        if hidden.shape[-1] != self.profile.dimension:
            raise EmbeddingInferenceError("BGE model output dimension does not match its profile")
        vectors = torch.nn.functional.normalize(hidden[:, 0, :], p=2, dim=-1).detach().cpu().tolist()
        if len(vectors) != len(items):
            raise EmbeddingInferenceError("model returned a different number of vectors")
        return [validate_dense_vectors([row], dimension=self.profile.dimension) for row in vectors]


def build_dense_encoder(config: EmbeddingServiceConfig) -> object:
    """Select a standalone encoder from the explicit service profile."""

    if config.encoder == "clip-vit-b32":
        from .clip_encoder import CLIPDualProjectionEncoder

        return CLIPDualProjectionEncoder.from_pretrained(config)
    if config.encoder == "bge-small-en-v1.5":
        return BgeSmallTextEncoder.from_pretrained(config)
    return Qwen3VLDenseEncoder.from_pretrained(config)


def _validate_torch(torch: Any, config: EmbeddingServiceConfig) -> None:
    version = str(torch.__version__).partition("+")[0]
    if version != "2.8.0":
        raise RuntimeError(f"embedding service requires torch 2.8.0, found {torch.__version__}")
    expected = {"cpu": None, "cu126": "12.6", "cu128": "12.8"}[config.torch_backend]
    actual = getattr(torch.version, "cuda", None)
    if actual != expected:
        found = actual or "CPU"
        expected_text = expected or "CPU"
        raise RuntimeError(f"Torch profile requires {expected_text}, found {found}")
    if config.device == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA was requested but no usable GPU is visible")
    if config.device == "cuda":
        try:
            import accelerate  # noqa: F401
        except ImportError as exc:
            raise RuntimeError("CUDA embedding requires accelerate") from exc


__all__ = ["BgeSmallTextEncoder", "EmbeddingInferenceError", "Qwen3VLDenseEncoder", "build_dense_encoder"]
