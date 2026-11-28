//! Workspace-filtered run-history contract: list maintenance run records
//! filtered by workspace id, with bounded paging.
//! Mirrors the durable run-history query surface from Python without coupling
//! to a particular storage backend.

use serde_json::Value;

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct RunRecord {
    pub run_id: String,
    pub workspace_id: String,
    pub maintenance_kind: String,
    pub status: String,
    pub payload: Value,
}

#[derive(Clone, Debug, Default)]
pub struct RunHistory {
    records: Vec<RunRecord>,
}

impl RunHistory {
    #[must_use]
    pub fn new() -> Self {
        Self::default()
    }

    pub fn push(&mut self, record: RunRecord) {
        self.records.push(record);
    }

    /// List records for one workspace, most recent first, bounded by `limit`.
    #[must_use]
    pub fn list_for_workspace(&self, workspace_id: &str, limit: usize) -> Vec<RunRecord> {
        self.records
            .iter()
            .rev()
            .filter(|r| r.workspace_id == workspace_id)
            .take(limit)
            .cloned()
            .collect()
    }

    /// Count of records for one workspace.
    #[must_use]
    pub fn count_for_workspace(&self, workspace_id: &str) -> usize {
        self.records
            .iter()
            .filter(|r| r.workspace_id == workspace_id)
            .count()
    }

    #[must_use]
    pub fn len(&self) -> usize {
        self.records.len()
    }

    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.records.is_empty()
    }
}

#[must_use]
pub fn make_run_record(
    run_id: &str,
    workspace_id: &str,
    maintenance_kind: &str,
    status: &str,
    payload: Value,
) -> RunRecord {
    RunRecord {
        run_id: run_id.to_owned(),
        workspace_id: workspace_id.to_owned(),
        maintenance_kind: maintenance_kind.to_owned(),
        status: status.to_owned(),
        payload,
    }
}
