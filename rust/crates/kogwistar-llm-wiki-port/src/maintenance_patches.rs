use serde::Serialize;
use serde_json::Value;

use crate::maintenance_status::MaintenanceIntent;

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "UPPERCASE")]
pub enum MaintenanceOperationKind {
    AddNode,
    AddEdge,
    TombstoneNode,
    TombstoneEdge,
    RequestReview,
    Noop,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum MaintenanceScopeKind {
    Workspace,
    Conversation,
    Thread,
}

impl MaintenanceScopeKind {
    #[must_use]
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Workspace => "workspace",
            Self::Conversation => "conversation",
            Self::Thread => "thread",
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct MaintenanceScope {
    pub workspace_id: String,
    pub scope_kind: String,
    pub conversation_id: Option<String>,
    pub thread_id: Option<String>,
}

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct MaintenancePatch {
    pub patch_id: String,
    pub intent: MaintenanceIntent,
    pub scope: MaintenanceScope,
    pub operations: Vec<Value>,
    pub status: String,
    pub rationale: Option<String>,
    pub requires_atomic_group: bool,
}
