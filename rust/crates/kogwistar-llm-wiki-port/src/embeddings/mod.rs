//! Embeddings remote I/O: authenticated HTTP adapter for the external
//! multimodal embedding service.
//!
//! Mirrors Python contract `src/kogwistar_llm_wiki/embeddings/multimodal_remote.py`.

pub mod remote;

pub use remote::{
    EmbeddingServiceError, EmbeddingServiceSettings, HttpResponseLike, RemoteMultimodalEncoder,
    UrlOpener,
};
