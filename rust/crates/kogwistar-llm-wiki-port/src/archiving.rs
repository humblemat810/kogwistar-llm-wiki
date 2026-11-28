use std::collections::BTreeMap;
use std::path::{Component, Path, PathBuf};

use serde::Serialize;
use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};

use crate::models::JsonObject;

pub const ARCHIVE_FORMAT_VERSION: i64 = 2;
pub const READABLE_ARCHIVE_FORMATS: [i64; 2] = [1, ARCHIVE_FORMAT_VERSION];
pub const ARTIFACT_DIRS: [&str; 5] = [
    "raw_documents",
    "source_artifacts",
    "parser_runs",
    "workflow_runs",
    "maintenance",
];
pub const SECRET_NAMES: [&str; 5] = [
    ".env",
    ".env.local",
    "auth.sqlite",
    "credentials.json",
    "secrets.json",
];

#[must_use]
pub fn artifact_dirs() -> [&'static str; 5] {
    ARTIFACT_DIRS
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum ArchiveError {
    UnsafePath { name: String, reason: &'static str },
    OutsidePrefix { name: String, prefix: String },
}

impl std::fmt::Display for ArchiveError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::UnsafePath { name, reason } => {
                write!(
                    formatter,
                    "archive contains an unsafe path: {name:?}: {reason}"
                )
            }
            Self::OutsidePrefix { name, prefix } => {
                write!(formatter, "archive member is outside {prefix:?}: {name:?}")
            }
        }
    }
}

impl std::error::Error for ArchiveError {}

#[must_use]
pub fn readable_archive_format(version: i64) -> bool {
    READABLE_ARCHIVE_FORMATS.contains(&version)
}

#[must_use]
pub fn secret_name(name: &str) -> bool {
    SECRET_NAMES.contains(&name)
}

pub fn safe_relative_member(name: &str, prefix: &str) -> Result<PathBuf, ArchiveError> {
    let normalized = name.replace('\\', "/");
    let value = Path::new(&normalized);
    if value.is_absolute() || value.has_root() {
        return Err(ArchiveError::UnsafePath {
            name: name.to_owned(),
            reason: "absolute",
        });
    }
    if value
        .components()
        .any(|component| matches!(component, Component::ParentDir))
    {
        return Err(ArchiveError::UnsafePath {
            name: name.to_owned(),
            reason: "parent traversal",
        });
    }
    value
        .strip_prefix(prefix)
        .map(Path::to_path_buf)
        .map_err(|_| ArchiveError::OutsidePrefix {
            name: name.to_owned(),
            prefix: prefix.to_owned(),
        })
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct RestoreReport {
    pub archive_id: String,
    pub workspace_id: String,
    pub target_workspace_id: String,
    pub dry_run: bool,
    pub imported_events: u64,
    pub replayed_namespaces: u64,
    pub rebuilt_vectors: bool,
}

#[must_use]
pub fn restore_report_wire(report: &RestoreReport) -> Value {
    json!({
        "archive_id": report.archive_id,
        "workspace_id": report.workspace_id,
        "target_workspace_id": report.target_workspace_id,
        "dry_run": report.dry_run,
        "imported_events": report.imported_events,
        "replayed_namespaces": report.replayed_namespaces,
        "rebuilt_vectors": report.rebuilt_vectors
    })
}

fn canonical_json_value(value: &Value) -> String {
    let Value::Object(values) = value else {
        return serde_json::to_string(value).expect("canonical JSON value");
    };
    let sorted = values
        .iter()
        .map(|(key, value)| {
            let child = canonical_json_value(value);
            let parsed = serde_json::from_str::<Value>(&child).unwrap_or_else(|_| value.clone());
            (key.clone(), parsed)
        })
        .collect::<BTreeMap<_, _>>();
    let mut map = Map::new();
    for (key, value) in sorted {
        map.insert(key, value);
    }
    serde_json::to_string(&Value::Object(map)).expect("canonical JSON value")
}

#[must_use]
pub fn embedding_fingerprint(profiles: &JsonObject) -> String {
    let profile_value = Value::Object(
        profiles
            .iter()
            .map(|(key, value)| (key.clone(), value.clone()))
            .collect(),
    );
    let canonical = canonical_json_value(&profile_value);
    let digest = Sha256::digest(canonical.as_bytes());
    digest.iter().map(|byte| format!("{byte:02x}")).collect()
}
