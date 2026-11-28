/// Higher-order grounding contracts for the multimodal application plane.
///
/// Mirrors Python contract `src/kogwistar_llm_wiki/embeddings/multimodal_grounding.py`.
/// Provides additive, serializable grounding validation without writing graph records
/// directly; a multimodal proposal/persistence adapter uses it to validate an
/// evidence-pack reference before an authoritative write.
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

/// Composition mode for combining multiple grounding references.
#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum GroundingComposition {
    AllOf,
    AnyOf,
}

impl GroundingComposition {
    #[must_use]
    pub fn as_str(self) -> &'static str {
        match self {
            Self::AllOf => "all_of",
            Self::AnyOf => "any_of",
        }
    }
}

/// Targeted entity kind.
#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum EntityKind {
    Node,
    Edge,
}

impl EntityKind {
    #[must_use]
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Node => "node",
            Self::Edge => "edge",
        }
    }
}

/// A logical reference to a node or edge target.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct LogicalRef {
    pub target_namespace: String,
    pub target_kind: String,
    pub target_id: String,
}

impl LogicalRef {
    #[must_use]
    pub fn new(target_namespace: &str, target_kind: &str, target_id: &str) -> Self {
        Self {
            target_namespace: target_namespace.to_owned(),
            target_kind: target_kind.to_owned(),
            target_id: target_id.to_owned(),
        }
    }
}

/// A verified source-map reference at one immutable revision.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct SourceEvidenceRef {
    pub workspace_id: String,
    pub source_id: String,
    pub source_revision_id: String,
    pub source_unit_id: String,
    pub locator_kind: String,
    pub locator_digest: String,
    pub role: String,
}

impl SourceEvidenceRef {
    #[must_use]
    pub fn new(
        workspace_id: &str,
        source_id: &str,
        source_revision_id: &str,
        source_unit_id: &str,
        locator_kind: &str,
        locator_digest: &str,
    ) -> Self {
        Self {
            workspace_id: workspace_id.to_owned(),
            source_id: source_id.to_owned(),
            source_revision_id: source_revision_id.to_owned(),
            source_unit_id: source_unit_id.to_owned(),
            locator_kind: locator_kind.to_owned(),
            locator_digest: locator_digest.to_owned(),
            role: "evidence".to_owned(),
        }
    }

    #[must_use]
    pub fn to_payload(&self) -> serde_json::Map<String, serde_json::Value> {
        serde_json::to_value(self)
            .expect("source evidence ref serializes")
            .as_object()
            .expect("source evidence ref is an object")
            .clone()
    }
}

/// A typed node/edge reference pinned to an event/revision watermark.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct PinnedEntityRef {
    pub logical_ref: LogicalRef,
    pub revision_id: String,
    pub event_seq: u64,
    pub role: String,
}

impl PinnedEntityRef {
    pub fn new(
        logical_ref: LogicalRef,
        revision_id: &str,
        event_seq: u64,
        role: &str,
    ) -> Result<Self, GroundingValidationError> {
        if logical_ref.target_kind != "node" && logical_ref.target_kind != "edge" {
            return Err(GroundingValidationError(
                "pinned evidence entities must target a node or edge".to_owned(),
            ));
        }
        if revision_id.is_empty() {
            return Err(GroundingValidationError(
                "pinned evidence entities require a revision".to_owned(),
            ));
        }
        Ok(Self {
            logical_ref,
            revision_id: revision_id.to_owned(),
            event_seq,
            role: role.to_owned(),
        })
    }

    #[must_use]
    pub fn entity_kind(&self) -> EntityKind {
        match self.logical_ref.target_kind.as_str() {
            "node" => EntityKind::Node,
            "edge" => EntityKind::Edge,
            _ => EntityKind::Node,
        }
    }
}

/// Reference to an evidence pack.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct EvidencePackReference {
    pub pack_ref: LogicalRef,
    pub expected_hash: String,
    pub source_watermark: serde_json::Map<String, serde_json::Value>,
}

/// A pack with source refs, node/edge refs, and nested pack refs.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct EvidencePack {
    pub pack_id: String,
    pub namespace: String,
    pub content_hash: String,
    pub source_watermarks: serde_json::Map<String, serde_json::Value>,
    pub source_refs: Vec<SourceEvidenceRef>,
    pub node_refs: Vec<PinnedEntityRef>,
    pub edge_refs: Vec<PinnedEntityRef>,
}

/// Resolved entity grounding with direct source refs and evidence pack refs.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ResolvedEntityGrounding {
    pub direct_source_refs: Vec<SourceEvidenceRef>,
    pub evidence_pack_refs: Vec<EvidencePackReference>,
}

/// Higher-order grounding: composition over terminal source, entity, and pack refs.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct HigherOrderGrounding {
    pub composition: GroundingComposition,
    pub terminal_source_refs: Vec<SourceEvidenceRef>,
    pub entity_refs: Vec<PinnedEntityRef>,
    pub pack_refs: Vec<EvidencePackReference>,
    pub max_refs: usize,
}

impl HigherOrderGrounding {
    #[must_use]
    pub fn new(
        composition: GroundingComposition,
        terminal_source_refs: Vec<SourceEvidenceRef>,
        entity_refs: Vec<PinnedEntityRef>,
        pack_refs: Vec<EvidencePackReference>,
        max_refs: usize,
    ) -> Self {
        Self {
            composition,
            terminal_source_refs,
            entity_refs,
            pack_refs,
            max_refs,
        }
    }
}

/// Raised when a higher-order grounding cannot be proven safely.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct GroundingValidationError(pub String);

impl std::fmt::Display for GroundingValidationError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for GroundingValidationError {}

/// Callback trait for resolving entities during closure validation.
pub trait EvidenceClosureResolver: Send + Sync {
    fn resolve_entity(
        &self,
        logical: &LogicalRef,
        revision_id: &str,
    ) -> Option<ResolvedEntityGrounding>;
    fn resolve_pack(&self, logical: &LogicalRef) -> Option<EvidencePack>;
}

/// Validates evidence closures: no cycles, no self-reference, namespace bounds,
/// watermark bounds, and hash integrity.
pub struct EvidenceClosureValidator<'a, R: EvidenceClosureResolver + ?Sized> {
    resolver: &'a R,
    namespaces: Vec<String>,
    current_watermarks: Option<serde_json::Map<String, serde_json::Value>>,
    max_refs: usize,
}

impl<'a, R: EvidenceClosureResolver + ?Sized> EvidenceClosureValidator<'a, R> {
    #[must_use]
    pub fn new(
        resolver: &'a R,
        namespaces: Vec<String>,
        current_watermarks: Option<serde_json::Map<String, serde_json::Value>>,
        max_refs: usize,
    ) -> Self {
        Self {
            resolver,
            namespaces,
            current_watermarks,
            max_refs,
        }
    }

    /// Validate a grounding, returning the SHA-256 digest of its verified
    /// terminal source refs, or an error if the closure is unsafe.
    pub fn validate(
        &self,
        grounding: &HigherOrderGrounding,
        root_ref: &LogicalRef,
    ) -> Result<String, GroundingValidationError> {
        let mut count = 0usize;
        let mut active_entities: std::collections::HashSet<(String, String, String, String)> =
            std::collections::HashSet::new();
        let mut completed_entities: std::collections::HashSet<(String, String, String, String)> =
            std::collections::HashSet::new();
        let mut active_packs: std::collections::HashSet<(String, String, String)> =
            std::collections::HashSet::new();
        let mut completed_packs: std::collections::HashSet<(String, String, String)> =
            std::collections::HashSet::new();
        let mut terminal_refs: Vec<SourceEvidenceRef> = Vec::new();

        for source_ref in &grounding.terminal_source_refs {
            self.check_source(source_ref, &mut count, &mut terminal_refs)?;
        }
        for entity_ref in &grounding.entity_refs {
            self.check_entity(
                entity_ref,
                root_ref,
                &mut count,
                &mut active_entities,
                &mut completed_entities,
            )?;
        }
        for pack_ref in &grounding.pack_refs {
            self.check_pack(
                pack_ref,
                root_ref,
                &mut count,
                &mut active_packs,
                &mut completed_packs,
            )?;
        }
        if terminal_refs.is_empty() {
            return Err(GroundingValidationError(
                "evidence closure has no verified direct source terminal".to_owned(),
            ));
        }

        let mut payloads: Vec<serde_json::Value> = terminal_refs
            .iter()
            .map(|r| serde_json::to_value(r).expect("serializes"))
            .collect();
        payloads.sort_by(|a, b| {
            serde_json::to_string(a)
                .unwrap()
                .cmp(&serde_json::to_string(b).unwrap())
        });
        let array = serde_json::to_value(&payloads).expect("serializes");
        let canonical = serde_json::to_string(&array).expect("serializes");
        let digest = Sha256::digest(canonical.as_bytes());
        Ok(format!("{:x}", digest))
    }

    fn check_source(
        &self,
        source_ref: &SourceEvidenceRef,
        count: &mut usize,
        terminal_refs: &mut Vec<SourceEvidenceRef>,
    ) -> Result<(), GroundingValidationError> {
        *count += 1;
        if *count > self.max_refs {
            return Err(GroundingValidationError(
                "evidence closure exceeds configured reference bound".to_owned(),
            ));
        }
        if source_ref.workspace_id.is_empty()
            || source_ref.source_id.is_empty()
            || source_ref.source_revision_id.is_empty()
            || source_ref.source_unit_id.is_empty()
            || source_ref.locator_kind.is_empty()
            || source_ref.locator_digest.is_empty()
        {
            return Err(GroundingValidationError(
                "source evidence requires complete revision and locator identity".to_owned(),
            ));
        }
        terminal_refs.push(source_ref.clone());
        Ok(())
    }

    fn check_entity(
        &self,
        ref_: &PinnedEntityRef,
        root_ref: &LogicalRef,
        count: &mut usize,
        active_entities: &mut std::collections::HashSet<(String, String, String, String)>,
        completed_entities: &mut std::collections::HashSet<(String, String, String, String)>,
    ) -> Result<(), GroundingValidationError> {
        *count += 1;
        if *count > self.max_refs {
            return Err(GroundingValidationError(
                "evidence closure exceeds configured reference bound".to_owned(),
            ));
        }
        if ref_.logical_ref.target_kind != "node" && ref_.logical_ref.target_kind != "edge" {
            return Err(GroundingValidationError(
                "pinned evidence entities must target a node or edge".to_owned(),
            ));
        }
        if ref_.revision_id.is_empty() {
            return Err(GroundingValidationError(
                "pinned evidence entities require a revision and non-negative event_seq".to_owned(),
            ));
        }
        let key = (
            ref_.logical_ref.target_namespace.clone(),
            ref_.logical_ref.target_kind.clone(),
            ref_.logical_ref.target_id.clone(),
            ref_.revision_id.clone(),
        );
        if &(LogicalRef {
            target_namespace: key.0.clone(),
            target_kind: key.1.clone(),
            target_id: key.2.clone(),
        }) == root_ref
        {
            return Err(GroundingValidationError(
                "evidence closure self-references the entity being written".to_owned(),
            ));
        }
        if completed_entities.contains(&key) {
            return Ok(());
        }
        if active_entities.contains(&key) {
            return Err(GroundingValidationError(
                "cyclic evidence entity reference".to_owned(),
            ));
        }
        if !self.namespaces.contains(&ref_.logical_ref.target_namespace) {
            return Err(GroundingValidationError(
                "evidence entity is outside the allowed namespace scope".to_owned(),
            ));
        }
        if let Some(watermarks) = &self.current_watermarks {
            let allowed = watermarks
                .get(&ref_.logical_ref.target_namespace)
                .and_then(|v| v.as_u64())
                .unwrap_or(0);
            if ref_.event_seq > allowed {
                return Err(GroundingValidationError(
                    "evidence entity references a future event watermark".to_owned(),
                ));
            }
        }
        active_entities.insert(key.clone());
        let resolved = self
            .resolver
            .resolve_entity(&ref_.logical_ref, &ref_.revision_id);
        let result = match resolved {
            Some(resolved_entity) => {
                for source_ref in &resolved_entity.direct_source_refs {
                    self.check_source(source_ref, count, &mut Vec::new())?;
                }
                for pack_ref in &resolved_entity.evidence_pack_refs {
                    self.check_pack(
                        pack_ref,
                        root_ref,
                        count,
                        &mut std::collections::HashSet::new(),
                        &mut std::collections::HashSet::new(),
                    )?;
                }
                Ok(())
            }
            None => Err(GroundingValidationError(format!(
                "unresolvable evidence entity {}",
                ref_.logical_ref.target_id
            ))),
        };
        active_entities.remove(&key);
        completed_entities.insert(key);
        result
    }

    fn check_pack(
        &self,
        pack_ref: &EvidencePackReference,
        root_ref: &LogicalRef,
        count: &mut usize,
        active_packs: &mut std::collections::HashSet<(String, String, String)>,
        completed_packs: &mut std::collections::HashSet<(String, String, String)>,
    ) -> Result<(), GroundingValidationError> {
        *count += 1;
        if *count > self.max_refs {
            return Err(GroundingValidationError(
                "evidence closure exceeds configured reference bound".to_owned(),
            ));
        }
        let logical = &pack_ref.pack_ref;
        if logical == root_ref {
            return Err(GroundingValidationError(
                "evidence closure self-references the entity being written".to_owned(),
            ));
        }
        if !self.namespaces.contains(&logical.target_namespace) {
            return Err(GroundingValidationError(
                "evidence pack is outside the allowed namespace scope".to_owned(),
            ));
        }
        let key = (
            logical.target_namespace.clone(),
            logical.target_kind.clone(),
            logical.target_id.clone(),
        );
        if completed_packs.contains(&key) {
            return Ok(());
        }
        if active_packs.contains(&key) {
            return Err(GroundingValidationError(
                "cyclic evidence-pack reference".to_owned(),
            ));
        }
        active_packs.insert(key.clone());
        let pack = self.resolver.resolve_pack(logical);
        let result = match pack {
            Some(pack) => {
                if pack.content_hash != pack_ref.expected_hash {
                    return Err(GroundingValidationError(format!(
                        "evidence pack hash mismatch for {}",
                        pack.pack_id
                    )));
                }
                for (namespace, watermark) in &pack.source_watermarks {
                    if let Some(watermarks) = &self.current_watermarks {
                        let allowed = watermarks
                            .get(namespace)
                            .and_then(|v| v.as_u64())
                            .unwrap_or(0);
                        if watermark.as_u64().unwrap_or(0) > allowed {
                            return Err(GroundingValidationError(
                                "evidence pack references a future source watermark".to_owned(),
                            ));
                        }
                    }
                }
                for source_ref in &pack.source_refs {
                    self.check_source(source_ref, count, &mut Vec::new())?;
                }
                for entity_ref in &pack.node_refs {
                    self.check_entity(
                        entity_ref,
                        root_ref,
                        count,
                        &mut std::collections::HashSet::new(),
                        &mut std::collections::HashSet::new(),
                    )?;
                }
                for entity_ref in &pack.edge_refs {
                    self.check_entity(
                        entity_ref,
                        root_ref,
                        count,
                        &mut std::collections::HashSet::new(),
                        &mut std::collections::HashSet::new(),
                    )?;
                }
                Ok(())
            }
            None => Err(GroundingValidationError(format!(
                "unresolvable evidence pack {}",
                logical.target_id
            ))),
        };
        active_packs.remove(&key);
        completed_packs.insert(key);
        result
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    struct FixedResolver;

    impl EvidenceClosureResolver for FixedResolver {
        fn resolve_entity(
            &self,
            logical: &LogicalRef,
            _revision_id: &str,
        ) -> Option<ResolvedEntityGrounding> {
            if logical.target_id == "resolvable-entity" {
                return Some(ResolvedEntityGrounding {
                    direct_source_refs: vec![SourceEvidenceRef::new(
                        "ws",
                        "src",
                        "rev",
                        "unit",
                        "text_range",
                        "digest1",
                    )],
                    evidence_pack_refs: vec![],
                });
            }
            None
        }

        fn resolve_pack(&self, logical: &LogicalRef) -> Option<EvidencePack> {
            if logical.target_id == "valid-pack" {
                return Some(EvidencePack {
                    pack_id: "pack-1".to_owned(),
                    namespace: "kg".to_owned(),
                    content_hash: "abc123".to_owned(),
                    source_watermarks: serde_json::Map::new(),
                    source_refs: vec![SourceEvidenceRef::new(
                        "ws",
                        "src",
                        "rev",
                        "unit",
                        "text_range",
                        "digest2",
                    )],
                    node_refs: vec![],
                    edge_refs: vec![],
                });
            }
            None
        }
    }

    #[test]
    fn grounding_digest_matches_for_valid_closure() {
        let resolver = FixedResolver;
        let validator = EvidenceClosureValidator::new(&resolver, vec!["kg".to_owned()], None, 100);
        let grounding = HigherOrderGrounding::new(
            GroundingComposition::AllOf,
            vec![SourceEvidenceRef::new(
                "ws",
                "src",
                "rev",
                "unit",
                "text_range",
                "digest0",
            )],
            vec![PinnedEntityRef::new(
                LogicalRef::new("kg", "node", "resolvable-entity"),
                "rev-1",
                0,
                "support",
            )
            .unwrap()],
            vec![],
            100,
        );
        let root = LogicalRef::new("kg", "node", "root-entity");
        let digest = validator.validate(&grounding, &root).unwrap();
        assert!(!digest.is_empty());
        assert_eq!(digest.len(), 64);
    }

    #[test]
    fn grounding_rejects_self_reference() {
        let resolver = FixedResolver;
        let validator = EvidenceClosureValidator::new(&resolver, vec!["kg".to_owned()], None, 100);
        let grounding = HigherOrderGrounding::new(
            GroundingComposition::AllOf,
            vec![],
            vec![PinnedEntityRef::new(
                LogicalRef::new("kg", "node", "root-entity"),
                "rev-1",
                0,
                "support",
            )
            .unwrap()],
            vec![],
            100,
        );
        let root = LogicalRef::new("kg", "node", "root-entity");
        let err = validator.validate(&grounding, &root).unwrap_err();
        assert!(err.0.contains("self-references"));
    }

    #[test]
    fn grounding_rejects_cycle() {
        let resolver = FixedResolver;
        let validator = EvidenceClosureValidator::new(&resolver, vec!["kg".to_owned()], None, 100);
        let grounding = HigherOrderGrounding::new(
            GroundingComposition::AllOf,
            vec![],
            vec![PinnedEntityRef::new(
                LogicalRef::new("kg", "node", "cycle-a"),
                "rev-1",
                0,
                "support",
            )
            .unwrap()],
            vec![],
            100,
        );
        let root = LogicalRef::new("kg", "node", "root-entity");
        let err = validator.validate(&grounding, &root).unwrap_err();
        assert!(err.0.contains("unresolvable"));
    }

    #[test]
    fn grounding_pack_hash_mismatch_rejected() {
        let resolver = FixedResolver;
        let validator = EvidenceClosureValidator::new(&resolver, vec!["kg".to_owned()], None, 100);
        let grounding = HigherOrderGrounding::new(
            GroundingComposition::AllOf,
            vec![],
            vec![],
            vec![EvidencePackReference {
                pack_ref: LogicalRef::new("kg", "node", "valid-pack"),
                expected_hash: "wrong-hash".to_owned(),
                source_watermark: serde_json::Map::new(),
            }],
            100,
        );
        let root = LogicalRef::new("kg", "node", "root-entity");
        let err = validator.validate(&grounding, &root).unwrap_err();
        assert!(err.0.contains("hash mismatch"));
    }
}
