use std::collections::BTreeSet;

use serde::{Deserialize, Serialize};

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Outcome {
    Equivalent,
    Additive,
    CorrectiveReplacement,
    LowerConfidence,
    ReviewRequired,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct Decision {
    pub outcome: Outcome,
    pub view_update_allowed: bool,
    #[serde(default)]
    pub active_graph_patch_required: bool,
    #[serde(default)]
    pub requires_review: bool,
    #[serde(default)]
    pub overlapping_member_ids: Vec<String>,
    pub rationale: String,
}

#[must_use]
pub fn decide_parse_reconciliation(
    overlapping_member_ids: &[&str],
    same_fingerprint: Option<bool>,
    confidence: Option<(f64, f64)>,
    replacement_approved: bool,
) -> Decision {
    let overlapping_member_ids: BTreeSet<_> = overlapping_member_ids
        .iter()
        .map(|value| (*value).trim())
        .filter(|value| !value.is_empty())
        .map(str::to_owned)
        .collect();
    let overlapping_member_ids = overlapping_member_ids.into_iter().collect::<Vec<_>>();
    if overlapping_member_ids.is_empty() {
        return Decision {
            outcome: Outcome::Additive,
            view_update_allowed: true,
            active_graph_patch_required: false,
            requires_review: false,
            overlapping_member_ids,
            rationale: "new derivation covers a region without an active selection".to_owned(),
        };
    }
    if same_fingerprint == Some(true) {
        return Decision {
            outcome: Outcome::Equivalent,
            view_update_allowed: true,
            active_graph_patch_required: false,
            requires_review: false,
            overlapping_member_ids,
            rationale: "derivation fingerprint matches the active interpretation".to_owned(),
        };
    }
    if let Some((new_confidence, previous_confidence)) = confidence {
        if new_confidence < previous_confidence {
            return Decision {
                outcome: Outcome::LowerConfidence,
                view_update_allowed: false,
                active_graph_patch_required: false,
                requires_review: true,
                overlapping_member_ids,
                rationale:
                    "replacement derivation has lower confidence than the active interpretation"
                        .to_owned(),
            };
        }
    }
    if replacement_approved {
        return Decision {
            outcome: Outcome::CorrectiveReplacement,
            view_update_allowed: true,
            active_graph_patch_required: true,
            requires_review: false,
            overlapping_member_ids,
            rationale: "replacement was explicitly approved for reconciliation".to_owned(),
        };
    }
    Decision {
        outcome: Outcome::ReviewRequired,
        view_update_allowed: false,
        active_graph_patch_required: false,
        requires_review: true,
        overlapping_member_ids,
        rationale: "overlapping derivation lacks an equivalence proof or replacement approval"
            .to_owned(),
    }
}

#[cfg(test)]
mod tests {
    use super::{decide_parse_reconciliation, Outcome};

    #[test]
    fn invalid_members_are_normalized_away() {
        let decision = decide_parse_reconciliation(&["", " member-old ", ""], None, None, false);
        assert_eq!(decision.outcome, Outcome::ReviewRequired);
        assert_eq!(decision.overlapping_member_ids, ["member-old"]);
    }
}
