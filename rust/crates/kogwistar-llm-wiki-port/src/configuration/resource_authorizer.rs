//! Explicit application resource-ACL adapter configuration.

use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ResourceAuthorizerSpec {
    pub module_name: String,
    pub attribute: String,
}

#[derive(Debug)]
pub struct ResourceAuthorizerError;

impl std::fmt::Display for ResourceAuthorizerError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str("LLM_WIKI_RESOURCE_AUTHORIZER must be module:callable")
    }
}

impl std::error::Error for ResourceAuthorizerError {}

pub fn parse_resource_authorizer(
    specification: &str,
) -> Result<Option<ResourceAuthorizerSpec>, ResourceAuthorizerError> {
    let configured = specification.trim();
    if configured.is_empty() {
        return Ok(None);
    }

    let Some((module_name, attribute)) = configured.split_once(':') else {
        return Err(ResourceAuthorizerError);
    };
    if module_name.trim().is_empty() || attribute.trim().is_empty() || attribute.contains(':') {
        return Err(ResourceAuthorizerError);
    }

    Ok(Some(ResourceAuthorizerSpec {
        module_name: module_name.trim().to_string(),
        attribute: attribute.trim().to_string(),
    }))
}

pub fn resource_authorizer_from_env_vars<I, K, V>(
    env: I,
) -> Result<Option<ResourceAuthorizerSpec>, ResourceAuthorizerError>
where
    I: IntoIterator<Item = (K, V)>,
    K: AsRef<str>,
    V: AsRef<str>,
{
    let configured = env
        .into_iter()
        .find(|(key, _)| key.as_ref() == "LLM_WIKI_RESOURCE_AUTHORIZER")
        .map(|(_, value)| value.as_ref().to_string());
    parse_resource_authorizer(configured.as_deref().unwrap_or(""))
}

pub fn resource_authorizer_from_env(
) -> Result<Option<ResourceAuthorizerSpec>, ResourceAuthorizerError> {
    resource_authorizer_from_env_vars(std::env::vars())
}
