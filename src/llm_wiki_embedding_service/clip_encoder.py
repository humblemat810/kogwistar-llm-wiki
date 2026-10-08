"""CPU-friendly CLIP encoder using the checkpoint's learned modality projections."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import Protocol, cast

from llm_wiki_embedding_contract import EmbeddingProfile, validate_dense_vectors

from .config import (
    CLIP_DIMENSION,
    CLIP_MODEL,
    CLIP_MODEL_SHA256,
    CLIP_REVISION,
    EmbeddingServiceConfig,
)
from .encoder import EmbeddingInferenceError, _validate_torch


class _ClipModelLike(Protocol):
    config: object

    def eval(self) -> "_ClipModelLike": ...
    def to(self, device: str) -> "_ClipModelLike": ...
    def get_text_features(self, **kwargs: object) -> object: ...
    def get_image_features(self, **kwargs: object) -> object: ...


class _ClipProcessorLike(Protocol):
    def __call__(self, **kwargs: object) -> Mapping[str, object]: ...


class CLIPDualProjectionEncoder:
    """Encode text and images through CLIP's learned 512-D shared space.

    For an item containing both modalities, the normalized text and vision
    projections are averaged and normalized again to preserve the one-vector
    per-item wire contract.
    """

    def __init__(
        self,
        model: _ClipModelLike,
        processor: _ClipProcessorLike,
        *,
        profile: EmbeddingProfile,
        device: str,
        batch_size: int = 1,
    ) -> None:
        self._model = model
        self._processor = processor
        self.profile = profile
        self.device = device
        self.batch_size = max(1, int(batch_size))

    @classmethod
    def from_pretrained(cls, config: EmbeddingServiceConfig) -> CLIPDualProjectionEncoder:
        if config.encoder != "clip-vit-b32" or config.dimension != CLIP_DIMENSION:
            raise ValueError("CLIP encoder requires its declared 512-D profile")
        try:
            import torch
            from transformers import CLIPModel, CLIPProcessor
        except ImportError as exc:
            raise RuntimeError("CLIP embedding requires Torch, Transformers, and Pillow") from exc
        _validate_torch(torch, config)
        if (
            config.model_path
            and config.model == CLIP_MODEL
            and config.revision == CLIP_REVISION
        ):
            _verify_safetensors_sha256(
                Path(config.model_path) / "0_CLIPModel" / "model.safetensors",
                expected=CLIP_MODEL_SHA256,
            )
        common: dict[str, object] = {
            "revision": config.revision,
            "subfolder": "0_CLIPModel",
            "cache_dir": config.model_cache_dir,
            "token": config.token,
        }
        model = cast(_ClipModelLike, CLIPModel.from_pretrained(
            config.model_path or config.model,
            use_safetensors=True,
            **common,
        ).eval())
        projection_dim = int(getattr(model.config, "projection_dim", 0))
        if projection_dim != config.dimension:
            raise RuntimeError(
                f"CLIP checkpoint projection dimension {projection_dim} does not match configured dimension {config.dimension}"
            )
        processor = cast(
            _ClipProcessorLike,
            CLIPProcessor.from_pretrained(config.model_path or config.model, **common),
        )
        if config.device == "cuda":
            model = model.to(config.device)
        return cls(
            model,
            processor,
            profile=config.profile,
            device=config.device,
            batch_size=config.batch_size,
        )

    def _features(
        self, inputs: object, *, modality: str
    ) -> list[tuple[float, ...]]:
        import torch

        if not isinstance(inputs, Mapping):
            raise EmbeddingInferenceError("CLIP processor returned invalid inputs")
        typed_inputs = cast(Mapping[str, object], inputs)
        moved = {
            key: (
                cast(Callable[[str], object], getattr(value, "to"))(self.device)
                if callable(getattr(value, "to", None))
                else value
            )
            for key, value in typed_inputs.items()
        }
        method_name = "get_text_features" if modality == "text" else "get_image_features"
        method = getattr(self._model, method_name, None)
        if not callable(method):
            raise EmbeddingInferenceError(f"CLIP model lacks {method_name}")
        with torch.inference_mode():
            values = method(**moved)
        projected_values = getattr(values, "pooler_output", None)
        if projected_values is not None:
            values = projected_values
        if getattr(values, "ndim", None) != 2 or int(values.shape[-1]) != self.profile.dimension:
            raise EmbeddingInferenceError(f"CLIP {modality} projection returned an invalid shape")
        values = torch.nn.functional.normalize(values.float(), p=2, dim=-1).detach().cpu().tolist()
        return [tuple(float(value) for value in row) for row in values]

    def encode(self, items: Sequence[Mapping[str, object]]) -> list[tuple[tuple[float, ...], ...]]:
        from PIL import Image

        owned: list[object] = []
        texts: list[tuple[int, str]] = []
        images: list[tuple[int, object]] = []
        try:
            for index, item in enumerate(items):
                if item.get("text") is not None:
                    texts.append((index, str(item["text"])))
                asset = item.get("asset")
                if isinstance(asset, Mapping):
                    raw = asset.get("bytes")
                    if not isinstance(raw, bytes) or not raw:
                        raise EmbeddingInferenceError("CLIP image asset must contain bytes")
                    with Image.open(BytesIO(raw)) as decoded:
                        image = decoded.convert("RGB")
                    owned.append(image)
                    images.append((index, image))
            vectors: list[list[tuple[float, ...]]] = [[] for _ in items]
            for start in range(0, len(texts), self.batch_size):
                batch = texts[start : start + self.batch_size]
                inputs = self._processor(
                    text=[text for _, text in batch],
                    padding=True,
                    truncation=True,
                    max_length=77,
                    return_tensors="pt",
                )
                encoded = self._features(inputs, modality="text")
                if len(encoded) != len(batch):
                    raise EmbeddingInferenceError("CLIP returned the wrong number of text vectors")
                for (index, _), vector in zip(batch, encoded):
                    vectors[index].append(vector)
            for start in range(0, len(images), self.batch_size):
                batch = images[start : start + self.batch_size]
                inputs = self._processor(
                    images=[image for _, image in batch],
                    return_tensors="pt",
                )
                encoded = self._features(inputs, modality="image")
                if len(encoded) != len(batch):
                    raise EmbeddingInferenceError("CLIP returned the wrong number of image vectors")
                for (index, _), vector in zip(batch, encoded):
                    vectors[index].append(vector)
            result: list[tuple[tuple[float, ...], ...]] = []
            for item_vectors in vectors:
                if not item_vectors:
                    raise EmbeddingInferenceError("CLIP item has no encodable modality")
                combined = [sum(vector[column] for vector in item_vectors) for column in range(self.profile.dimension)]
                norm = sum(value * value for value in combined) ** 0.5
                if norm == 0.0:
                    raise EmbeddingInferenceError("CLIP projection returned a zero vector")
                checked = validate_dense_vectors(
                    [[value / norm for value in combined]],
                    dimension=self.profile.dimension,
                )
                result.append(checked)
            return result
        finally:
            for image in owned:
                image.close()


def _verify_safetensors_sha256(path: Path, *, expected: str) -> None:
    digest = sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise RuntimeError(f"cannot read pinned CLIP checkpoint {path}") from exc
    if digest.hexdigest() != expected:
        raise RuntimeError("pinned CLIP checkpoint SHA-256 mismatch")


__all__ = ["CLIPDualProjectionEncoder"]
