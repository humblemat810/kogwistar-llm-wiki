/// Service health and startup-recovery contracts for product daemons.
///
/// Mirrors Python contract `src/kogwistar_llm_wiki/daemons/runtime_support.py`.
/// Provides self-contained service-health declaration, heartbeat, stop, and
/// startup-recovery surface construction without depending on external engine
/// crates; a daemon adapter uses these to coordinate lifecycle with the host.
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, HashSet};

/// Status of a service instance.
#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ServiceStatus {
    Starting,
    Healthy,
    Degraded,
    Unhealthy,
    Stopped,
}

impl ServiceStatus {
    #[must_use]
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Starting => "starting",
            Self::Healthy => "healthy",
            Self::Degraded => "degraded",
            Self::Unhealthy => "unhealthy",
            Self::Stopped => "stopped",
        }
    }
}

/// A declared service in the health registry.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ServiceDeclaration {
    pub service_id: String,
    pub service_kind: String,
    pub owner_app: String,
    pub deterministic: bool,
    pub llm_assisted: bool,
    pub workspace_id: String,
    pub namespace: String,
    pub version: String,
    pub config_metadata: BTreeMap<String, serde_json::Value>,
    pub operator_tags: Vec<String>,
}

/// A running service instance.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ServiceInstance {
    pub service_id: String,
    pub workspace_id: String,
    pub namespace: String,
    pub instance_id: String,
    pub status: ServiceStatus,
    pub host: Option<String>,
    pub pid: Option<u32>,
    pub last_error: Option<String>,
}

/// Recovery surface for a named application surface.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct RecoverySurface {
    pub surface_id: String,
    pub surface_kind: String,
    pub status: String,
    pub details: BTreeMap<String, serde_json::Value>,
}

/// Output reconciliation state for a projection manifest surface.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct OutputReconciliationState {
    pub surface_id: String,
    pub surface_kind: String,
    pub status: String,
    pub observed_version: Option<String>,
    pub drift_detected: bool,
    pub namespace: String,
    pub details: BTreeMap<String, serde_json::Value>,
}

/// Result of a startup recovery pass.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct RecoveryReport {
    pub workspace_id: String,
    pub repaired_count: usize,
    pub scanned_count: usize,
    pub queues: Vec<String>,
    pub lane_rows: Vec<String>,
    pub checkpoints: Vec<String>,
    pub run_history: Vec<String>,
    pub dead_letters: Vec<String>,
    pub findings: Vec<String>,
    pub repaired_lane_projections: Vec<LaneProjectionRepair>,
}

/// A repaired lane projection entry.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct LaneProjectionRepair {
    pub namespace: String,
    pub repaired_count: usize,
    pub scanned_count: usize,
}

/// Service health registry: declare, heartbeat, stop service instances.
pub trait ServiceRegistry: Send + Sync {
    fn declare_service(&mut self, declaration: ServiceDeclaration);
    fn start_instance(
        &mut self,
        workspace_id: &str,
        namespace: &str,
        instance_id: &str,
        status: ServiceStatus,
        host: Option<String>,
        pid: Option<u32>,
    );
    #[allow(clippy::too_many_arguments)]
    fn heartbeat(
        &mut self,
        _service_id: &str,
        workspace_id: &str,
        namespace: &str,
        instance_id: &str,
        status: ServiceStatus,
        last_error: Option<&str>,
        host: Option<String>,
        pid: Option<u32>,
    ) -> Result<(), ServiceRegistryError>;
    fn stop_service(
        &mut self,
        _service_id: &str,
        workspace_id: &str,
        namespace: &str,
        instance_id: &str,
        status: ServiceStatus,
    ) -> Result<(), ServiceRegistryError>;
}

/// Error from service registry operations.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ServiceRegistryError(pub String);

impl std::fmt::Display for ServiceRegistryError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "service registry error: {}", self.0)
    }
}

impl std::error::Error for ServiceRegistryError {}

/// In-memory service registry implementation.
#[derive(Clone, Debug, Default)]
pub struct InMemoryServiceRegistry {
    pub declarations: BTreeMap<String, ServiceDeclaration>,
    pub instances: BTreeMap<String, ServiceInstance>,
}

impl InMemoryServiceRegistry {
    #[must_use]
    pub fn new() -> Self {
        Self::default()
    }
}

impl ServiceRegistry for InMemoryServiceRegistry {
    fn declare_service(&mut self, declaration: ServiceDeclaration) {
        self.declarations
            .insert(declaration.service_id.clone(), declaration);
    }

    fn start_instance(
        &mut self,
        workspace_id: &str,
        namespace: &str,
        instance_id: &str,
        status: ServiceStatus,
        host: Option<String>,
        pid: Option<u32>,
    ) {
        let service_id = format!("{}:{}:{}", workspace_id, instance_id, status.as_str());
        self.instances.insert(
            service_id.clone(),
            ServiceInstance {
                service_id,
                workspace_id: workspace_id.to_owned(),
                namespace: namespace.to_owned(),
                instance_id: instance_id.to_owned(),
                status,
                host,
                pid,
                last_error: None,
            },
        );
    }

    #[allow(clippy::too_many_arguments)]
    fn heartbeat(
        &mut self,
        _service_id: &str,
        _workspace_id: &str,
        _namespace: &str,
        instance_id: &str,
        status: ServiceStatus,
        last_error: Option<&str>,
        host: Option<String>,
        pid: Option<u32>,
    ) -> Result<(), ServiceRegistryError> {
        let key = self
            .instances
            .iter()
            .find(|(_, inst)| inst.instance_id == instance_id)
            .map(|(k, _)| k.clone());
        let Some(key) = key else {
            return Err(ServiceRegistryError(format!(
                "instance {instance_id} not found for heartbeat"
            )));
        };
        let inst = self.instances.get_mut(&key).unwrap();
        inst.status = status;
        inst.last_error = last_error.map(|s| s.to_owned());
        inst.host = host;
        inst.pid = pid;
        Ok(())
    }

    fn stop_service(
        &mut self,
        _service_id: &str,
        _workspace_id: &str,
        _namespace: &str,
        instance_id: &str,
        status: ServiceStatus,
    ) -> Result<(), ServiceRegistryError> {
        let key = self
            .instances
            .iter()
            .find(|(_, inst)| inst.instance_id == instance_id)
            .map(|(k, _)| k.clone());
        let Some(key) = key else {
            return Err(ServiceRegistryError(format!(
                "instance {instance_id} not found for stop"
            )));
        };
        let inst = self.instances.get_mut(&key).unwrap();
        inst.status = status;
        Ok(())
    }
}

/// Build a service ID from workspace and service kind.
#[must_use]
pub fn service_id(workspace_id: &str, service_kind: &str) -> String {
    format!("kogwistar-llm-wiki:{workspace_id}:{service_kind}")
}

/// Declare service health on a registry.
#[allow(clippy::too_many_arguments)]
pub fn declare_service_health(
    registry: &mut dyn ServiceRegistry,
    workspace_id: &str,
    service_kind: &str,
    instance_id: &str,
    deterministic: bool,
    llm_assisted: bool,
    operator_tags: Vec<String>,
    config_metadata: Option<BTreeMap<String, serde_json::Value>>,
    status: ServiceStatus,
) {
    let service_id = service_id(workspace_id, service_kind);
    let mut metadata = config_metadata.unwrap_or_default();
    metadata.insert(
        "workspace_id".to_owned(),
        serde_json::Value::String(workspace_id.to_owned()),
    );
    registry.declare_service(ServiceDeclaration {
        service_id,
        service_kind: service_kind.to_owned(),
        owner_app: "kogwistar-llm-wiki".to_owned(),
        deterministic,
        llm_assisted,
        workspace_id: workspace_id.to_owned(),
        namespace: workspace_id.to_owned(),
        version: "1".to_owned(),
        config_metadata: metadata,
        operator_tags,
    });
    registry.start_instance(
        workspace_id,
        workspace_id,
        instance_id,
        status,
        host_name(),
        std::process::id().into(),
    );
}

/// Heartbeat a service instance.
pub fn heartbeat_service_health(
    registry: &mut dyn ServiceRegistry,
    workspace_id: &str,
    service_kind: &str,
    instance_id: &str,
    status: ServiceStatus,
    last_error: Option<&str>,
) -> bool {
    let service_id = service_id(workspace_id, service_kind);
    let namespace = workspace_id.to_owned();
    match registry.heartbeat(
        &service_id,
        workspace_id,
        &namespace,
        instance_id,
        status,
        last_error,
        host_name(),
        std::process::id().into(),
    ) {
        Ok(()) => true,
        Err(_) => false,
    }
}

/// Stop a service instance.
pub fn stop_service_health(
    registry: &mut dyn ServiceRegistry,
    workspace_id: &str,
    service_kind: &str,
    instance_id: &str,
) -> bool {
    let service_id = service_id(workspace_id, service_kind);
    let namespace = workspace_id.to_owned();
    match registry.stop_service(
        &service_id,
        workspace_id,
        &namespace,
        instance_id,
        ServiceStatus::Stopped,
    ) {
        Ok(()) => true,
        Err(_) => false,
    }
}

/// Get the current hostname, or None if unavailable.
#[must_use]
pub fn host_name() -> Option<String> {
    std::env::var("HOSTNAME")
        .ok()
        .or_else(|| std::env::var("COMPUTERNAME").ok())
}

/// Resolve startup namespaces for a workspace.
///
/// Mirrors Python `_startup_namespaces`: conversation + conv_bg + conv_fg,
/// optionally maintenance_jobs and projection_jobs.
#[must_use]
pub fn startup_namespaces(
    conversation_namespace: Option<&str>,
    workspace_id: &str,
    include_maintenance: bool,
    include_projection: bool,
) -> Vec<String> {
    let ns = WorkspaceNamespaces::new(workspace_id);
    let mut candidates: Vec<String> = Vec::new();
    candidates.push(conversation_namespace.unwrap_or("conversation").to_owned());
    candidates.push(ns.conv_bg.clone());
    candidates.push(ns.conv_fg.clone());
    if include_maintenance {
        candidates.push(ns.maintenance_jobs.clone());
    }
    if include_projection {
        candidates.push(ns.projection_jobs.clone());
    }
    let mut out: Vec<String> = Vec::new();
    let mut seen: HashSet<String> = HashSet::new();
    for namespace in candidates {
        if seen.insert(namespace.clone()) {
            out.push(namespace);
        }
    }
    out
}

/// Workspace namespace derivation, mirroring Python `WorkspaceNamespaces`.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct WorkspaceNamespaces {
    pub workspace_id: String,
    pub conv_bg: String,
    pub conv_fg: String,
    pub maintenance_jobs: String,
    pub projection_jobs: String,
    pub projection_manifest: String,
}

impl WorkspaceNamespaces {
    #[must_use]
    pub fn new(workspace_id: &str) -> Self {
        Self {
            workspace_id: workspace_id.to_owned(),
            conv_bg: format!("{workspace_id}-conv-bg"),
            conv_fg: format!("{workspace_id}-conv-fg"),
            maintenance_jobs: format!("{workspace_id}-maintenance-jobs"),
            projection_jobs: format!("{workspace_id}-projection-jobs"),
            projection_manifest: format!("{workspace_id}-projection-manifest"),
        }
    }
}

/// Build a projection manifest output-reconciliation surface.
#[must_use]
pub fn projection_manifest_surface(
    workspace_id: &str,
    status: &str,
    observed_version: Option<&str>,
    ready_count: usize,
    desired_count: usize,
    failed_count: usize,
) -> OutputReconciliationState {
    let ns = WorkspaceNamespaces::new(workspace_id);
    let drift = matches!(status, "missing" | "failed" | "error");
    OutputReconciliationState {
        surface_id: format!("{workspace_id}:projection_manifest"),
        surface_kind: "projection_manifest".to_owned(),
        status: status.to_owned(),
        observed_version: observed_version.map(|s| s.to_owned()),
        drift_detected: drift,
        namespace: ns.projection_manifest.clone(),
        details: BTreeMap::from([
            (
                "workspace_id".to_owned(),
                serde_json::Value::String(workspace_id.to_owned()),
            ),
            (
                "ready_count".to_owned(),
                serde_json::Value::Number(ready_count.into()),
            ),
            (
                "projected_count".to_owned(),
                serde_json::Value::Number(ready_count.into()),
            ),
            (
                "desired_count".to_owned(),
                serde_json::Value::Number(desired_count.into()),
            ),
            (
                "failed_count".to_owned(),
                serde_json::Value::Number(failed_count.into()),
            ),
        ]),
    }
}

/// Build a vault recovery surface.
#[must_use]
pub fn vault_surface(workspace_id: &str, vault_root: Option<&str>) -> RecoverySurface {
    let present = vault_root
        .map(|root| std::path::Path::new(root).exists())
        .unwrap_or(false);
    let status = if !present {
        if vault_root.is_some() {
            "missing"
        } else {
            "not_configured"
        }
    } else {
        "present"
    };
    RecoverySurface {
        surface_id: format!("{workspace_id}:vault"),
        surface_kind: "vault_materialization".to_owned(),
        status: status.to_owned(),
        details: BTreeMap::from([(
            "workspace_id".to_owned(),
            serde_json::Value::String(workspace_id.to_owned()),
        )]),
    }
}

/// Build a daemon health recovery surface.
#[must_use]
pub fn daemon_surface(daemon_id: &str) -> RecoverySurface {
    RecoverySurface {
        surface_id: daemon_id.to_owned(),
        surface_kind: "daemon_health".to_owned(),
        status: "starting".to_owned(),
        details: BTreeMap::from([
            (
                "desired_state".to_owned(),
                serde_json::Value::String("running".to_owned()),
            ),
            (
                "observed_state".to_owned(),
                serde_json::Value::String("starting".to_owned()),
            ),
            ("last_heartbeat_at".to_owned(), serde_json::Value::Null),
            ("restart_count".to_owned(), serde_json::Value::Null),
        ]),
    }
}

/// Core startup recovery: assemble app surfaces and run recovery.
#[must_use]
pub fn core_startup_recovery(
    workspace_id: &str,
    daemon_id: &str,
    include_maintenance: bool,
    include_projection: bool,
    conversation_namespace: Option<&str>,
    _vault_root: Option<&str>,
    has_service_health: bool,
) -> RecoveryReport {
    let mut app_surfaces: Vec<String> = Vec::new();
    if !has_service_health {
        app_surfaces.push(daemon_id.to_owned());
    }
    if include_projection {
        app_surfaces.push(format!("{workspace_id}:projection_manifest"));
        app_surfaces.push(format!("{workspace_id}:vault"));
    }
    let namespaces = startup_namespaces(
        conversation_namespace,
        workspace_id,
        include_maintenance,
        include_projection,
    );
    RecoveryReport {
        workspace_id: workspace_id.to_owned(),
        repaired_count: 0,
        scanned_count: namespaces.len(),
        queues: namespaces.clone(),
        lane_rows: namespaces.clone(),
        checkpoints: namespaces.clone(),
        run_history: namespaces.clone(),
        dead_letters: Vec::new(),
        findings: app_surfaces,
        repaired_lane_projections: Vec::new(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn service_id_is_stable() {
        assert_eq!(
            service_id("ws-1", "maintenance"),
            "kogwistar-llm-wiki:ws-1:maintenance"
        );
    }

    #[test]
    fn service_status_as_str() {
        assert_eq!(ServiceStatus::Healthy.as_str(), "healthy");
        assert_eq!(ServiceStatus::Stopped.as_str(), "stopped");
    }

    #[test]
    fn declare_and_heartbeat_service() {
        let mut registry = InMemoryServiceRegistry::new();
        declare_service_health(
            &mut registry,
            "ws-1",
            "maintenance",
            "inst-1",
            true,
            false,
            vec!["tag-a".to_owned()],
            None,
            ServiceStatus::Starting,
        );
        assert!(registry
            .declarations
            .contains_key("kogwistar-llm-wiki:ws-1:maintenance"));
        assert!(registry
            .instances
            .values()
            .any(|inst| inst.instance_id == "inst-1"));

        let ok = heartbeat_service_health(
            &mut registry,
            "ws-1",
            "maintenance",
            "inst-1",
            ServiceStatus::Healthy,
            None,
        );
        assert!(ok);
        let inst = registry
            .instances
            .values()
            .find(|inst| inst.instance_id == "inst-1")
            .unwrap();
        assert_eq!(inst.status, ServiceStatus::Healthy);
    }

    #[test]
    fn heartbeat_unknown_instance_returns_false() {
        let mut registry = InMemoryServiceRegistry::new();
        let ok = heartbeat_service_health(
            &mut registry,
            "ws-1",
            "maintenance",
            "no-such-inst",
            ServiceStatus::Healthy,
            None,
        );
        assert!(!ok);
    }

    #[test]
    fn stop_service_health_marks_stopped() {
        let mut registry = InMemoryServiceRegistry::new();
        declare_service_health(
            &mut registry,
            "ws-1",
            "maintenance",
            "inst-1",
            true,
            false,
            vec![],
            None,
            ServiceStatus::Starting,
        );
        let ok = stop_service_health(&mut registry, "ws-1", "maintenance", "inst-1");
        assert!(ok);
        let inst = registry
            .instances
            .values()
            .find(|inst| inst.instance_id == "inst-1")
            .unwrap();
        assert_eq!(inst.status, ServiceStatus::Stopped);
    }

    #[test]
    fn startup_namespaces_dedups_and_orders() {
        let namespaces = startup_namespaces(Some("conv"), "ws-1", false, false);
        assert_eq!(
            namespaces,
            vec![
                "conv".to_owned(),
                "ws-1-conv-bg".to_owned(),
                "ws-1-conv-fg".to_owned()
            ]
        );
    }

    #[test]
    fn startup_namespaces_includes_optional() {
        let namespaces = startup_namespaces(None, "ws-1", true, true);
        assert!(namespaces.contains(&"ws-1-maintenance-jobs".to_owned()));
        assert!(namespaces.contains(&"ws-1-projection-jobs".to_owned()));
    }

    #[test]
    fn workspace_namespaces_new() {
        let ns = WorkspaceNamespaces::new("ws-1");
        assert_eq!(ns.workspace_id, "ws-1");
        assert_eq!(ns.conv_bg, "ws-1-conv-bg");
        assert_eq!(ns.projection_manifest, "ws-1-projection-manifest");
    }

    #[test]
    fn projection_manifest_surface_drift() {
        let surface = projection_manifest_surface("ws-1", "missing", None, 0, 5, 0);
        assert!(surface.drift_detected);
        assert_eq!(surface.status, "missing");
        assert_eq!(
            surface.details.get("ready_count"),
            Some(&serde_json::json!(0))
        );
    }

    #[test]
    fn projection_manifest_surface_no_drift() {
        let surface = projection_manifest_surface("ws-1", "ready", Some("v2"), 3, 3, 0);
        assert!(!surface.drift_detected);
        assert_eq!(surface.observed_version, Some("v2".to_owned()));
    }

    #[test]
    fn vault_surface_not_configured() {
        let surface = vault_surface("ws-1", None);
        assert_eq!(surface.status, "not_configured");
    }

    #[test]
    fn vault_surface_missing() {
        let surface = vault_surface("ws-1", Some("/nonexistent/vault"));
        assert_eq!(surface.status, "missing");
    }

    #[test]
    fn daemon_surface_has_expected_fields() {
        let surface = daemon_surface("daemon-1");
        assert_eq!(surface.surface_id, "daemon-1");
        assert_eq!(surface.surface_kind, "daemon_health");
        assert_eq!(surface.status, "starting");
        assert_eq!(
            surface.details.get("desired_state"),
            Some(&serde_json::json!("running"))
        );
    }

    #[test]
    fn core_startup_recovery_assembles_surfaces() {
        let report =
            core_startup_recovery("ws-1", "daemon-1", true, true, Some("conv"), None, false);
        assert_eq!(report.workspace_id, "ws-1");
        assert!(report.findings.contains(&"daemon-1".to_owned()));
        assert!(report
            .findings
            .contains(&"ws-1:projection_manifest".to_owned()));
        assert!(report.findings.contains(&"ws-1:vault".to_owned()));
    }

    #[test]
    fn core_startup_recovery_skips_daemon_when_service_health_present() {
        let report = core_startup_recovery("ws-1", "daemon-1", false, false, None, None, true);
        assert!(!report.findings.contains(&"daemon-1".to_owned()));
    }
}
