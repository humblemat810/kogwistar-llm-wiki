// T5.6 event chain - append-only idempotent event log
use serde::{Deserialize, Serialize};

use crate::models::JsonObject;

/// Mirrors a single append-only usage/archive event.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct Event {
    pub event_id: String,
    pub kind: String,
    pub payload: JsonObject,
    pub ts_ms: u64,
}

/// Mirrors Python idempotent event log (usage/events.py append semantics).
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize, Default)]
pub struct EventChain {
    pub events: Vec<Event>,
}

impl EventChain {
    pub fn new() -> Self {
        Self { events: Vec::new() }
    }

    pub fn append(&mut self, event: Event) {
        self.events.push(event);
    }

    pub fn len(&self) -> usize {
        self.events.len()
    }

    pub fn is_empty(&self) -> bool {
        self.events.is_empty()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn event_chain_append_and_len() {
        let mut chain = EventChain::new();
        assert!(chain.is_empty());
        chain.append(Event {
            event_id: "ev-1".to_owned(),
            kind: "usage_event".to_owned(),
            payload: JsonObject::new(),
            ts_ms: 1000,
        });
        assert_eq!(chain.len(), 1);
        assert!(!chain.is_empty());
    }
}
