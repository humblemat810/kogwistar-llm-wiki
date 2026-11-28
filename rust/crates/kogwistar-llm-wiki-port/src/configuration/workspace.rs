use std::collections::BTreeMap;
use std::fmt;
use std::str::FromStr;

use serde::{Deserialize, Serialize};

#[derive(Clone, Copy, Debug, Eq, Hash, Ord, PartialEq, PartialOrd, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum GraphSpace {
    Source,
    BaseKg,
    CuratedKg,
    Conversation,
    Workflow,
    Review,
    Wisdom,
    Policy,
    Projection,
}

impl GraphSpace {
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Source => "source",
            Self::BaseKg => "base_kg",
            Self::CuratedKg => "curated_kg",
            Self::Conversation => "conversation",
            Self::Workflow => "workflow",
            Self::Review => "review",
            Self::Wisdom => "wisdom",
            Self::Policy => "policy",
            Self::Projection => "projection",
        }
    }
}

impl AsRef<str> for GraphSpace {
    fn as_ref(&self) -> &str {
        self.as_str()
    }
}

impl fmt::Display for GraphSpace {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.as_str())
    }
}

impl FromStr for GraphSpace {
    type Err = GraphSpaceParseError;

    fn from_str(value: &str) -> Result<Self, Self::Err> {
        match value {
            "source" => Ok(Self::Source),
            "base_kg" => Ok(Self::BaseKg),
            "curated_kg" => Ok(Self::CuratedKg),
            "conversation" => Ok(Self::Conversation),
            "workflow" => Ok(Self::Workflow),
            "review" => Ok(Self::Review),
            "wisdom" => Ok(Self::Wisdom),
            "policy" => Ok(Self::Policy),
            "projection" => Ok(Self::Projection),
            _ => Err(GraphSpaceParseError(value.to_owned())),
        }
    }
}

#[derive(Debug)]
pub struct GraphSpaceParseError(String);

impl fmt::Display for GraphSpaceParseError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(formatter, "unknown graph space: {}", self.0)
    }
}

impl std::error::Error for GraphSpaceParseError {}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct GraphSpaceNamespace {
    pub workspace_id: String,
    pub graph_space: GraphSpace,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub lane: Option<String>,
}

impl GraphSpaceNamespace {
    #[must_use]
    pub fn as_string(&self) -> String {
        let mut namespace = format!("ws:{}:g:{}", self.workspace_id, self.graph_space.as_str());
        if let Some(lane) = &self.lane {
            namespace.push_str(":lane:");
            namespace.push_str(lane);
        }
        namespace
    }

    #[must_use]
    pub fn metadata(&self, legacy_namespace: Option<&str>) -> BTreeMap<String, String> {
        let mut data = BTreeMap::from([
            ("workspace_id".to_owned(), self.workspace_id.clone()),
            (
                "graph_space".to_owned(),
                self.graph_space.as_str().to_owned(),
            ),
        ]);
        if let Some(lane) = &self.lane {
            data.insert("graph_lane".to_owned(), lane.clone());
        }
        if let Some(legacy_namespace) = legacy_namespace {
            data.insert("legacy_namespace".to_owned(), legacy_namespace.to_owned());
        }
        data
    }
}

impl fmt::Display for GraphSpaceNamespace {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.as_string())
    }
}

#[derive(Clone, Debug, Eq, Hash, PartialEq, Serialize, Deserialize)]
pub struct WorkspaceNamespaces {
    pub workspace_id: String,
}

impl WorkspaceNamespaces {
    #[must_use]
    pub fn space(&self, graph_space: GraphSpace, lane: Option<&str>) -> String {
        self.space_descriptor(graph_space, lane).as_string()
    }

    #[must_use]
    pub fn space_descriptor(
        &self,
        graph_space: GraphSpace,
        lane: Option<&str>,
    ) -> GraphSpaceNamespace {
        GraphSpaceNamespace {
            workspace_id: self.workspace_id.clone(),
            graph_space,
            lane: lane.map(str::to_owned),
        }
    }

    #[must_use]
    pub fn conv_fg(&self) -> String {
        format!("ws:{}:conv:fg", self.workspace_id)
    }

    #[must_use]
    pub fn conv_bg(&self) -> String {
        format!("ws:{}:conv:bg", self.workspace_id)
    }

    #[must_use]
    pub fn conversation_fg_space(&self) -> String {
        self.space(GraphSpace::Conversation, Some("foreground"))
    }

    #[must_use]
    pub fn conversation_bg_space(&self) -> String {
        self.space(GraphSpace::Conversation, Some("background"))
    }

    #[must_use]
    pub fn workflow_maintenance(&self) -> String {
        format!("ws:{}:wf:maintenance", self.workspace_id)
    }

    #[must_use]
    pub fn workflow_space(&self) -> String {
        self.space(GraphSpace::Workflow, None)
    }

    #[must_use]
    pub fn review(&self) -> String {
        format!("ws:{}:review", self.workspace_id)
    }

    #[must_use]
    pub fn review_space(&self) -> String {
        self.space(GraphSpace::Review, None)
    }

    #[must_use]
    pub fn curated_kg_space(&self) -> String {
        self.space(GraphSpace::CuratedKg, None)
    }

    #[must_use]
    pub fn source_space(&self) -> String {
        self.space(GraphSpace::Source, None)
    }

    #[must_use]
    pub fn base_kg_space(&self) -> String {
        self.space(GraphSpace::BaseKg, None)
    }

    #[must_use]
    pub fn derived_knowledge(&self) -> String {
        format!("ws:{}:derived_knowledge", self.workspace_id)
    }

    #[must_use]
    pub fn wisdom(&self) -> String {
        format!("ws:{}:wisdom", self.workspace_id)
    }

    #[must_use]
    pub fn wisdom_space(&self) -> String {
        self.space(GraphSpace::Wisdom, None)
    }

    #[must_use]
    pub fn policy_space(&self) -> String {
        self.space(GraphSpace::Policy, None)
    }

    #[must_use]
    pub fn projection_space(&self) -> String {
        self.space(GraphSpace::Projection, None)
    }

    #[must_use]
    pub fn projection_jobs(&self) -> String {
        format!("ws:{}:projection_jobs", self.workspace_id)
    }

    #[must_use]
    pub fn maintenance_jobs(&self) -> String {
        format!("ws:{}:maintenance_jobs", self.workspace_id)
    }

    #[must_use]
    pub fn workbench_jobs(&self) -> String {
        format!("ws:{}:workbench_jobs", self.workspace_id)
    }

    #[must_use]
    pub fn notification_jobs(&self) -> String {
        format!("ws:{}:notification_jobs", self.workspace_id)
    }

    #[must_use]
    pub fn urgent_notification_jobs(&self) -> String {
        format!("ws:{}:urgent_notification_jobs", self.workspace_id)
    }

    #[must_use]
    pub fn projection_state(&self) -> String {
        format!("ws:{}:projection_state", self.workspace_id)
    }

    #[must_use]
    pub fn projection_manifest(&self) -> String {
        format!("ws:{}:projection_manifest", self.workspace_id)
    }

    #[must_use]
    pub fn usage_events(&self) -> String {
        format!("ws:{}:usage_events", self.workspace_id)
    }

    #[must_use]
    pub fn usage_projection(&self) -> String {
        format!("ws:{}:usage_projection", self.workspace_id)
    }
}

#[must_use]
pub fn namespace_matches_graph_space_metadata<S>(
    namespace: &str,
    metadata: &BTreeMap<String, S>,
) -> bool
where
    S: AsRef<str>,
{
    let parts: Vec<&str> = namespace.split(':').collect();
    if parts.len() < 4 || parts[0] != "ws" || parts[2] != "g" {
        return false;
    }

    let workspace_id = parts[1];
    let graph_space = parts[3];
    let lane = if parts.len() >= 6 && parts[4] == "lane" {
        Some(parts[5])
    } else {
        None
    };

    if metadata
        .get("workspace_id")
        .map(AsRef::as_ref)
        .unwrap_or_default()
        != workspace_id
    {
        return false;
    }
    if metadata
        .get("graph_space")
        .map(AsRef::as_ref)
        .unwrap_or_default()
        != graph_space
    {
        return false;
    }
    lane.is_none_or(|lane| {
        metadata
            .get("graph_lane")
            .map(AsRef::as_ref)
            .unwrap_or_default()
            == lane
    })
}
