use std::fmt;

use serde::{Deserialize, Serialize};
use uuid::Uuid;

use crate::ids::stable_id;

#[derive(Debug)]
pub enum ParseContractError {
    InvalidRegion(&'static str),
    InvalidTarget(&'static str),
}

impl fmt::Display for ParseContractError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::InvalidRegion(message) => write!(formatter, "{message}"),
            Self::InvalidTarget(message) => write!(formatter, "{message}"),
        }
    }
}

impl std::error::Error for ParseContractError {}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct SourceRegion {
    pub source_document_id: String,
    pub start_char: u64,
    pub end_char: u64,
}

impl SourceRegion {
    pub fn new(
        source_document_id: impl Into<String>,
        start_char: u64,
        end_char: u64,
    ) -> Result<Self, ParseContractError> {
        let value = Self {
            source_document_id: source_document_id.into(),
            start_char,
            end_char,
        };
        value.validate()?;
        Ok(value)
    }

    fn validate(&self) -> Result<(), ParseContractError> {
        if self.start_char == self.end_char {
            return Err(ParseContractError::InvalidRegion(
                "source region end_char must be greater than start_char",
            ));
        }
        Ok(())
    }
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct ParseTarget {
    pub source_document_id: String,
    pub source_revision_id: String,
    pub revision_document_id: String,
    pub region: SourceRegion,
    pub reason: String,
    pub parser_profile: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub generation_member_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub llm_provider: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub llm_model: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub model_version: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub prompt_version: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub parser_version: Option<String>,
}

impl ParseTarget {
    #[allow(clippy::too_many_arguments)]
    #[allow(clippy::fn_params_excessive_bools)]
    pub fn new(
        source_document_id: impl Into<String>,
        source_revision_id: impl Into<String>,
        revision_document_id: impl Into<String>,
        region: SourceRegion,
        reason: impl Into<String>,
        parser_profile: impl Into<String>,
        generation_member_id: Option<String>,
        llm_provider: Option<String>,
        llm_model: Option<String>,
        model_version: Option<String>,
        prompt_version: Option<String>,
        parser_version: Option<String>,
    ) -> Result<Self, ParseContractError> {
        let revision_document_id = revision_document_id.into();
        let value = Self {
            source_document_id: source_document_id.into(),
            source_revision_id: source_revision_id.into(),
            revision_document_id: revision_document_id.clone(),
            region,
            reason: reason.into(),
            parser_profile: parser_profile.into(),
            generation_member_id,
            llm_provider,
            llm_model,
            model_version,
            prompt_version,
            parser_version,
        };
        value.validate()?;
        Ok(value)
    }

    fn validate(&self) -> Result<(), ParseContractError> {
        self.region.validate()?;
        if self.region.source_document_id != self.revision_document_id {
            return Err(ParseContractError::InvalidTarget(
                "parse target region must point to its revision document",
            ));
        }
        Ok(())
    }
}

#[must_use]
pub fn generation_id(
    workspace_id: &str,
    source_document_id: &str,
    source_revision_id: &str,
    parser_profile: &str,
    derivation_id: &str,
) -> Uuid {
    stable_id(
        "kogwistar_llm_wiki.parse_generation",
        &[
            workspace_id,
            source_document_id,
            source_revision_id,
            parser_profile,
            derivation_id,
        ],
    )
}

#[must_use]
pub fn parse_session_id(
    workspace_id: &str,
    source_document_id: &str,
    source_revision_id: &str,
    parser_profile: &str,
) -> Uuid {
    stable_id(
        "kogwistar_llm_wiki.parse_session",
        &[
            workspace_id,
            source_document_id,
            source_revision_id,
            parser_profile,
        ],
    )
}

#[allow(clippy::too_many_arguments)]
#[must_use]
pub fn reparse_session_id(
    workspace_id: &str,
    source_document_id: &str,
    source_revision_id: &str,
    parser_profile: &str,
    region: &SourceRegion,
    llm_provider: Option<&str>,
    llm_model: Option<&str>,
    model_version: Option<&str>,
    prompt_version: Option<&str>,
    parser_version: Option<&str>,
) -> Uuid {
    stable_id(
        "kogwistar_llm_wiki.reparse_session",
        &[
            workspace_id,
            source_document_id,
            source_revision_id,
            parser_profile,
            region.source_document_id.as_str(),
            region.start_char.to_string().as_str(),
            region.end_char.to_string().as_str(),
            llm_provider.unwrap_or_default(),
            llm_model.unwrap_or_default(),
            model_version.unwrap_or_default(),
            prompt_version.unwrap_or_default(),
            parser_version.unwrap_or_default(),
        ],
    )
}

#[must_use]
pub fn generation_member_id(generation_id: &str, commit_id: &str, ordinal: u64) -> Uuid {
    stable_id(
        "kogwistar_llm_wiki.parse_generation_member",
        &[generation_id, commit_id, ordinal.to_string().as_str()],
    )
}

#[must_use]
pub fn frontier_id(session_id: &str, region: &SourceRegion, ordinal: u64) -> Uuid {
    stable_id(
        "kogwistar_llm_wiki.parse_frontier",
        &[
            session_id,
            region.source_document_id.as_str(),
            region.start_char.to_string().as_str(),
            region.end_char.to_string().as_str(),
            ordinal.to_string().as_str(),
        ],
    )
}

#[must_use]
pub fn legacy_generation_id(workspace_id: &str, source_document_id: &str) -> Uuid {
    stable_id(
        "kogwistar_llm_wiki.parse_generation.legacy_g0",
        &[workspace_id, source_document_id],
    )
}
