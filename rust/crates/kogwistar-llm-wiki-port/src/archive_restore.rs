// T5.6 archive restore / event-chain contracts - mirroring Python archiving/archive_contracts.py
use serde::{Deserialize, Serialize};

use crate::models::JsonObject;

pub const ARCHIVE_FORMAT_VERSION: u32 = 2;
pub const READABLE_ARCHIVE_FORMATS: [u32; 2] = [1, 2];
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

/// Mirrors Python `ArchiveError`.
#[derive(Clone, Debug, Eq, PartialEq)]
pub enum ArchiveError {
    UnsafeRestore,
    UnsupportedFormat { version: u32 },
    SecretDetected { path: String },
    IoError(String),
}

impl std::fmt::Display for ArchiveError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::UnsafeRestore => write!(f, "archive restore is not safe"),
            Self::UnsupportedFormat { version } => {
                write!(f, "unsupported archive format version {version}")
            }
            Self::SecretDetected { path } => write!(f, "secret detected in archive path {path}"),
            Self::IoError(msg) => write!(f, "archive io error: {msg}"),
        }
    }
}

impl std::error::Error for ArchiveError {}

/// Mirrors Python `ArchiveNamespace`.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ArchiveNamespace {
    pub label: String,
    pub engine: String,
    pub namespace: String,
}

/// Mirrors Python `RestoreReport`.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct RestoreReport {
    pub archive_id: String,
    pub workspace_id: String,
    pub target_workspace_id: String,
    pub dry_run: bool,
    pub imported_events: u64,
    pub replayed_namespaces: u64,
    pub rebuilt_vectors: bool,
}

impl RestoreReport {
    pub fn as_dict(&self) -> JsonObject {
        let mut map = JsonObject::new();
        map.insert(
            "archive_id".to_owned(),
            serde_json::Value::String(self.archive_id.clone()),
        );
        map.insert(
            "workspace_id".to_owned(),
            serde_json::Value::String(self.workspace_id.clone()),
        );
        map.insert(
            "target_workspace_id".to_owned(),
            serde_json::Value::String(self.target_workspace_id.clone()),
        );
        map.insert("dry_run".to_owned(), serde_json::Value::Bool(self.dry_run));
        map.insert(
            "imported_events".to_owned(),
            serde_json::Value::Number(self.imported_events.into()),
        );
        map.insert(
            "replayed_namespaces".to_owned(),
            serde_json::Value::Number(self.replayed_namespaces.into()),
        );
        map.insert(
            "rebuilt_vectors".to_owned(),
            serde_json::Value::Bool(self.rebuilt_vectors),
        );
        map
    }
}

/// 歸檔格式版本是否可讀取。
pub fn is_readable_format(version: u32) -> bool {
    READABLE_ARCHIVE_FORMATS.contains(&version)
}

/// 路徑片段是否為需遮蔽的秘密名稱。
pub fn is_secret_name(name: &str) -> bool {
    SECRET_NAMES.contains(&name)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn archive_format_version_matches_python() {
        assert_eq!(ARCHIVE_FORMAT_VERSION, 2);
        assert!(is_readable_format(1));
        assert!(is_readable_format(2));
        assert!(!is_readable_format(3));
    }

    #[test]
    fn secret_names_match_python() {
        assert!(is_secret_name(".env"));
        assert!(is_secret_name("credentials.json"));
        assert!(!is_secret_name("notes.md"));
    }

    #[test]
    fn restore_report_serialization_matches_python() {
        let report = RestoreReport {
            archive_id: "arc-1".to_owned(),
            workspace_id: "ws-1".to_owned(),
            target_workspace_id: "ws-2".to_owned(),
            dry_run: true,
            imported_events: 10,
            replayed_namespaces: 3,
            rebuilt_vectors: false,
        };
        let dict = report.as_dict();
        assert_eq!(dict.get("dry_run"), Some(&serde_json::Value::Bool(true)));
        assert_eq!(
            dict.get("imported_events"),
            Some(&serde_json::Value::Number(10.into()))
        );
        assert_eq!(
            serde_json::to_value(&report).unwrap()["archive_id"],
            serde_json::Value::String("arc-1".to_owned())
        );
    }
}
