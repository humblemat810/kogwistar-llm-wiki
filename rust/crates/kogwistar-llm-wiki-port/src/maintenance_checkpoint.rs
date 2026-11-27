//! Checkpoint fallback contract: bounded, append-only checkpoint history with
//! restore-from-last-valid and explicit invalid-checkpoint rejection.
//! Mirrors the durable, recoverable maintenance-run intent from Python without
//! coupling to a particular storage backend.

use serde_json::Value;

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum CheckpointStatus {
    Valid,
    Invalid,
    Missing,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Checkpoint {
    pub id: String,
    pub workspace_id: String,
    pub seq: u64,
    pub payload_hash: String,
    pub status: CheckpointStatus,
}

pub const MAX_CHECKPOINT_HISTORY: usize = 5;

#[derive(Clone, Debug, Default)]
pub struct CheckpointStore {
    checkpoints: Vec<Checkpoint>,
}

impl CheckpointStore {
    #[must_use]
    pub fn new() -> Self {
        Self::default()
    }

    /// Append a checkpoint, evicting the oldest once the bounded history is full.
    pub fn save(&mut self, checkpoint: Checkpoint) {
        if self.checkpoints.len() >= MAX_CHECKPOINT_HISTORY {
            self.checkpoints.remove(0);
        }
        self.checkpoints.push(checkpoint);
    }

    /// Restore the most recent valid checkpoint, if any.
    #[must_use]
    pub fn restore_last_valid(&self) -> Option<&Checkpoint> {
        self.checkpoints
            .iter()
            .rev()
            .find(|c| c.status == CheckpointStatus::Valid)
    }

    /// Reject an explicitly invalid checkpoint id.
    #[must_use]
    pub fn reject_invalid(&self, id: &str) -> bool {
        self.checkpoints
            .iter()
            .any(|c| c.id == id && c.status == CheckpointStatus::Invalid)
    }

    #[must_use]
    pub fn len(&self) -> usize {
        self.checkpoints.len()
    }

    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.checkpoints.is_empty()
    }
}

fn hash_payload(payload: &Value) -> String {
    use std::collections::hash_map::DefaultHasher;
    use std::hash::{Hash, Hasher};
    let mut hasher = DefaultHasher::new();
    payload.hash(&mut hasher);
    format!("{:x}", hasher.finish())
}

#[must_use]
pub fn make_checkpoint(
    id: &str,
    workspace_id: &str,
    seq: u64,
    payload: &Value,
    status: CheckpointStatus,
) -> Checkpoint {
    Checkpoint {
        id: id.to_owned(),
        workspace_id: workspace_id.to_owned(),
        seq,
        payload_hash: hash_payload(payload),
        status,
    }
}
