use std::collections::HashMap;
use std::path::Path;

use serde::Serialize;
use serde_json::{json, Value};

use crate::cli::CliInvocation;
use crate::daemon_loop::{MaintenanceDaemon, ProjectionDaemon};
use crate::ids::stable_id;
use crate::ingest::artifacts::{artifact_node, ArtifactNode};
use crate::ingest::run::{ingest_run_plan, parsed_artifacts};
use crate::models::{IngestPipelineArtifacts, IngestPipelineRequest};
use crate::source_unit::SourceUnit;

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum CliRuntimeError {
    MissingArgument(&'static str),
    InvalidChoice(&'static str, String),
    Io(String),
    IngestRun,
    UnsupportedCommand,
}

impl std::fmt::Display for CliRuntimeError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::MissingArgument(name) => write!(formatter, "missing required argument: {name}"),
            Self::InvalidChoice(name, value) => {
                write!(formatter, "invalid choice for {name}: {value}")
            }
            Self::Io(value) => write!(formatter, "{value}"),
            Self::IngestRun => write!(formatter, "invalid ingest request"),
            Self::UnsupportedCommand => write!(formatter, "unsupported CLI runtime command"),
        }
    }
}

impl std::error::Error for CliRuntimeError {}

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct IngestedDocument {
    pub source_unit: SourceUnit,
    pub artifacts: IngestPipelineArtifacts,
    pub node: ArtifactNode,
}

#[derive(Debug, Default)]
pub struct InMemoryCliRuntime {
    documents: HashMap<String, Vec<IngestedDocument>>,
}

impl InMemoryCliRuntime {
    pub fn ingest(
        &mut self,
        request: IngestPipelineRequest,
    ) -> Result<IngestedDocument, CliRuntimeError> {
        let source_document_id = stable_id(
            "kogwistar_llm_wiki.source_document",
            &[request.workspace_id.as_str(), request.source_uri.as_str()],
        )
        .to_string();
        let plan = ingest_run_plan(&request, &source_document_id)
            .map_err(|_| CliRuntimeError::IngestRun)?;
        let source_unit = SourceUnit::new(
            request.workspace_id.as_str(),
            source_document_id.as_str(),
            request.raw_text.as_str(),
        );
        let node_id = stable_id(
            "kogwistar_llm_wiki.graph_node",
            &[
                request.workspace_id.as_str(),
                source_unit.revision_id.as_str(),
            ],
        )
        .to_string();
        let node = artifact_node(
            &request,
            source_unit.source_document_id.as_str(),
            "base_kg",
            Some(node_id.as_str()),
            "document",
            request.parser_lane.as_str(),
            "shared",
            request.title.as_str(),
            request.raw_text.as_str(),
            None,
        );
        let maintenance_job_id = stable_id(
            "kogwistar_llm_wiki.maintenance_job",
            &[
                request.workspace_id.as_str(),
                source_unit.revision_id.as_str(),
            ],
        )
        .to_string();
        let candidate_link_id = stable_id(
            "kogwistar_llm_wiki.candidate_link",
            &[
                request.workspace_id.as_str(),
                source_unit.revision_id.as_str(),
            ],
        )
        .to_string();
        let promotion_candidate_id = stable_id(
            "kogwistar_llm_wiki.promotion_candidate",
            &[
                request.workspace_id.as_str(),
                source_unit.revision_id.as_str(),
            ],
        )
        .to_string();
        let document = IngestedDocument {
            source_unit,
            artifacts: parsed_artifacts(
                &plan,
                maintenance_job_id.as_str(),
                candidate_link_id.as_str(),
                promotion_candidate_id.as_str(),
                None,
            ),
            node,
        };
        self.documents
            .entry(request.workspace_id.clone())
            .or_default()
            .push(document.clone());
        Ok(document)
    }

    pub fn report(
        &self,
        workspace_id: &str,
        report_scope: &str,
        dump_mode: &str,
        dump_limit_per_space: usize,
    ) -> Result<Value, CliRuntimeError> {
        let documents = self
            .documents
            .get(workspace_id)
            .cloned()
            .unwrap_or_default();
        let nodes: Vec<Value> = documents.iter().map(|doc| json!(doc.node)).collect();
        let score = 100.0_f64.min(nodes.len() as f64 * 100.0);
        let quality = if score >= 70.0 {
            "good"
        } else if score >= 40.0 {
            "mixed"
        } else {
            "weak"
        };
        let sample_nodes: Vec<Value> = documents
            .iter()
            .take(dump_limit_per_space)
            .map(|document| {
                json!({
                    "graph_space": "base_kg",
                    "id": document.node.id,
                    "label": document.node.label,
                    "node_type": document.node.kind,
                    "artifact_kind": document.node.metadata.get("artifact_kind"),
                    "status": document.node.metadata.get("lifecycle_status"),
                })
            })
            .collect();
        Ok(json!({
            "workspace_id": workspace_id,
            "report_scope": report_scope,
            "dump_mode": dump_mode,
            "graph_quality": quality,
            "graph_health_score": score,
            "graph_spaces": [{
                "graph_space": "base_kg",
                "node_count": nodes.len(),
                "edge_count": 0,
            }],
            "review_artifact_counts": {},
            "maintenance_patch_report": {
                "patch_count": 0,
                "proposed_operations": 0,
                "applied_operations": 0,
                "rejected_operations": 0,
                "retracted_operations": 0,
                "skipped_operations": 0,
                "failed_operations": 0,
            },
            "sample_nodes": sample_nodes,
            "sample_edges": [],
            "notes": ["in-memory CLI runtime"],
        }))
    }
}

pub trait CliRuntime {
    fn ingest(
        &mut self,
        request: IngestPipelineRequest,
    ) -> Result<IngestedDocument, CliRuntimeError>;
    fn report(
        &self,
        workspace_id: &str,
        report_scope: &str,
        dump_mode: &str,
        dump_limit_per_space: usize,
    ) -> Result<Value, CliRuntimeError>;
}

impl CliRuntime for InMemoryCliRuntime {
    fn ingest(
        &mut self,
        request: IngestPipelineRequest,
    ) -> Result<IngestedDocument, CliRuntimeError> {
        InMemoryCliRuntime::ingest(self, request)
    }

    fn report(
        &self,
        workspace_id: &str,
        report_scope: &str,
        dump_mode: &str,
        dump_limit_per_space: usize,
    ) -> Result<Value, CliRuntimeError> {
        InMemoryCliRuntime::report(
            self,
            workspace_id,
            report_scope,
            dump_mode,
            dump_limit_per_space,
        )
    }
}

pub fn run_cli<R: CliRuntime>(
    invocation: &CliInvocation,
    runtime: &mut R,
) -> Result<Value, CliRuntimeError> {
    match invocation.command {
        crate::cli::CliCommand::Demo => run_content_command(invocation, runtime, true),
        crate::cli::CliCommand::Ingest => run_content_command(invocation, runtime, false),
        crate::cli::CliCommand::Report => run_report_command(invocation, runtime),
        crate::cli::CliCommand::Daemon => run_daemon_command(invocation),
        _ => Err(CliRuntimeError::UnsupportedCommand),
    }
}

fn run_content_command<R: CliRuntime>(
    invocation: &CliInvocation,
    runtime: &mut R,
    demo: bool,
) -> Result<Value, CliRuntimeError> {
    let workspace_id = required_string(invocation, "workspace")?;
    let source = required_string(invocation, "source")?;
    let path = Path::new(&source);
    if !path.exists() {
        return Err(CliRuntimeError::Io(format!(
            "source file not found: {source}"
        )));
    }
    let raw_text =
        std::fs::read_to_string(path).map_err(|error| CliRuntimeError::Io(error.to_string()))?;
    let request = request_from_source(invocation, &workspace_id, &source, raw_text)?;
    let document = runtime.ingest(request)?;
    let artifact_value = json!(document.artifacts);
    if demo {
        let vault = required_string(invocation, "vault")?;
        Ok(json!({
            "workspace_id": workspace_id,
            "source": source,
            "vault": vault,
            "mode": "demo-memory-single-process",
            "corpus_mode": "single-file",
            "corpus_count": 1,
            "artifacts": artifact_value,
            "artifacts_by_source": [{"source": source, "artifacts": artifact_value}],
            "vault_result": {"vault_root": vault, "notes": 1, "canvases": 0, "dangling_links": 0},
        }))
    } else {
        Ok(json!({"workspace_id": workspace_id, "source": source, "artifacts": artifact_value}))
    }
}

fn run_daemon_command(invocation: &CliInvocation) -> Result<Value, CliRuntimeError> {
    let Some(subcommand) = invocation.subcommand.as_deref() else {
        return Err(CliRuntimeError::MissingArgument("daemon_type"));
    };
    match subcommand {
        "projection" => {
            let workspace_id = required_string(invocation, "workspace")?;
            let vault = required_string(invocation, "vault")?;
            let interval = interval_value(invocation, "interval", 5.0)?;
            let daemon = ProjectionDaemon::new(interval);
            let iterations = daemon.inner.with_max_iterations(1).run_pass(|| true);
            Ok(json!({
                "workspace_id": workspace_id,
                "vault": vault,
                "interval": interval,
                "iterations": iterations,
                "status": "stopped",
            }))
        }
        "maintenance" => {
            let workspace_id = required_string(invocation, "workspace")?;
            let interval = interval_value(invocation, "interval", 10.0)?;
            let background_interval = interval_value(invocation, "background_interval", 600.0)?;
            let daemon = MaintenanceDaemon::new(interval);
            let iterations = daemon.inner.with_max_iterations(1).run_pass(|| true);
            Ok(json!({
                "workspace_id": workspace_id,
                "interval": interval,
                "background_interval": background_interval,
                "iterations": iterations,
                "status": "stopped",
            }))
        }
        "maintenance-control" => Ok(json!({
            "request_enabled": invocation.first_argument("request_enabled"),
            "background_enabled": invocation.first_argument("background_enabled"),
            "enabled": invocation.first_argument("enabled"),
            "profile": invocation.first_argument("profile"),
            "status": invocation.first_argument("status").unwrap_or("false"),
        })),
        _ => Err(CliRuntimeError::UnsupportedCommand),
    }
}

fn interval_value(
    invocation: &CliInvocation,
    name: &'static str,
    default: f64,
) -> Result<f64, CliRuntimeError> {
    let Some(value) = invocation.first_argument(name) else {
        return Ok(default);
    };
    value
        .parse::<f64>()
        .map_err(|_| CliRuntimeError::InvalidChoice(name, value.to_owned()))
}

fn run_report_command<R: CliRuntime>(
    invocation: &CliInvocation,
    runtime: &R,
) -> Result<Value, CliRuntimeError> {
    let workspace_id = required_string(invocation, "workspace")?;
    let data_dir = required_string(invocation, "data_dir")?;
    if data_dir.trim().is_empty() {
        return Err(CliRuntimeError::Io("data_dir must not be blank".to_owned()));
    }
    let report_scope = invocation.first_argument("report_scope").unwrap_or("all");
    let dump_mode = invocation.first_argument("dump_mode").unwrap_or("summary");
    let dump_limit_per_space = invocation
        .first_argument("dump_limit_per_space")
        .map(|value| value.parse::<usize>())
        .transpose()
        .map_err(|_| {
            CliRuntimeError::InvalidChoice(
                "--dump-limit-per-space",
                value_string(invocation, "dump_limit_per_space"),
            )
        })?;
    runtime.report(
        &workspace_id,
        report_scope,
        dump_mode,
        dump_limit_per_space.unwrap_or(20),
    )
}

fn request_from_source(
    invocation: &CliInvocation,
    workspace_id: &str,
    source: &str,
    raw_text: String,
) -> Result<IngestPipelineRequest, CliRuntimeError> {
    let title = invocation
        .first_argument("title")
        .unwrap_or_else(|| {
            Path::new(source)
                .file_stem()
                .and_then(std::ffi::OsStr::to_str)
                .unwrap_or(source)
        })
        .to_owned();
    let source_uri = format!(
        "file://{}",
        Path::new(source)
            .canonicalize()
            .unwrap_or_else(|_| Path::new(source).to_path_buf())
            .to_string_lossy()
            .replace('\\', "/")
    );
    Ok(IngestPipelineRequest {
        workspace_id: workspace_id.to_owned(),
        source_uri,
        title,
        raw_text,
        source_format: choice(invocation, "source_format", &["text", "markdown"], "text")?,
        operation_mode: choice(
            invocation,
            "operation_mode",
            &["parse_first", "maintenance_first", "hybrid"],
            "parse_first",
        )?,
        parser_mode: choice(
            invocation,
            "parser_mode",
            &["heuristic", "ollama", "gemini", "openai", "azure_openai"],
            "heuristic",
        )?,
        parser_lane: choice(
            invocation,
            "parser_lane",
            &["page_index", "workflow_layered"],
            "page_index",
        )?,
        promotion_mode: choice(invocation, "promotion_mode", &["pending", "sync"], "sync")?,
        auto_accept_threshold: 0.85,
        llm_provider: invocation.first_argument("llm_provider").map(str::to_owned),
        llm_model: invocation.first_argument("llm_model").map(str::to_owned),
        provenance_policy: crate::models::ProvenancePolicy::Optional,
        provenance: None,
        parse_limits: Default::default(),
    })
}

fn required_string(
    invocation: &CliInvocation,
    name: &'static str,
) -> Result<String, CliRuntimeError> {
    invocation
        .first_argument(name)
        .map(str::to_owned)
        .ok_or(CliRuntimeError::MissingArgument(name))
}

fn choice(
    invocation: &CliInvocation,
    name: &'static str,
    allowed: &[&str],
    default: &str,
) -> Result<String, CliRuntimeError> {
    let value = invocation.first_argument(name).unwrap_or(default);
    if allowed.contains(&value) {
        Ok(value.to_owned())
    } else {
        Err(CliRuntimeError::InvalidChoice(name, value.to_owned()))
    }
}

fn value_string(invocation: &CliInvocation, name: &str) -> String {
    invocation
        .first_argument(name)
        .unwrap_or_default()
        .to_owned()
}
