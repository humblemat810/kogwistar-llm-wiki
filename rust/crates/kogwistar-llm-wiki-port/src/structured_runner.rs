// T5.12 structured runner - mirroring Python codex/codex_workbench_agent.py CodexRunner protocol
use serde::{Deserialize, Serialize};

/// Runner transport: exec (CLI subprocess) or app_server (JSON-RPC stdio).
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RunnerTransport {
    #[default]
    Exec,
    AppServer,
}

/// Runner settings mirroring Python `CodexCliSettings`.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct RunnerSettings {
    pub executable: Option<String>,
    pub model: Option<String>,
    pub profile: Option<String>,
    pub timeout_seconds: u64,
    pub transport: RunnerTransport,
}

impl Default for RunnerSettings {
    fn default() -> Self {
        RunnerSettings {
            executable: None,
            model: None,
            profile: None,
            timeout_seconds: 300,
            transport: RunnerTransport::default(),
        }
    }
}

/// Structured runner trait: one bounded turn returning a string result.
/// Mirrors Python `CodexRunner.run`.
pub trait StructuredRunner {
    fn run(
        &self,
        settings: &RunnerSettings,
        prompt: &str,
        output_schema: Option<&serde_json::Value>,
    ) -> Result<String, RunnerError>;
}

/// Runner error envelope.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum RunnerError {
    Timeout,
    InvalidSchema(String),
    ExecutionFailed(String),
}

impl std::fmt::Display for RunnerError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Timeout => write!(f, "runner timeout"),
            Self::InvalidSchema(msg) => write!(f, "invalid output schema: {msg}"),
            Self::ExecutionFailed(msg) => write!(f, "runner execution failed: {msg}"),
        }
    }
}

impl std::error::Error for RunnerError {}

#[cfg(test)]
mod tests {
    use super::*;

    struct FakeRunner;

    impl StructuredRunner for FakeRunner {
        fn run(
            &self,
            _settings: &RunnerSettings,
            prompt: &str,
            _output_schema: Option<&serde_json::Value>,
        ) -> Result<String, RunnerError> {
            if prompt.is_empty() {
                Err(RunnerError::ExecutionFailed("empty prompt".to_owned()))
            } else {
                Ok(format!("echo:{}", prompt))
            }
        }
    }

    #[test]
    fn runner_settings_defaults() {
        let s = RunnerSettings::default();
        assert_eq!(s.timeout_seconds, 300);
        assert_eq!(s.transport, RunnerTransport::Exec);
        assert!(s.executable.is_none());
    }

    #[test]
    fn runner_returns_echo_for_non_empty_prompt() {
        let runner = FakeRunner;
        let settings = RunnerSettings::default();
        let result = runner.run(&settings, "hello", None).unwrap();
        assert_eq!(result, "echo:hello");
    }

    #[test]
    fn runner_rejects_empty_prompt() {
        let runner = FakeRunner;
        let settings = RunnerSettings::default();
        let err = runner.run(&settings, "", None).unwrap_err();
        assert!(matches!(err, RunnerError::ExecutionFailed(_)));
    }

    #[test]
    fn runner_transport_serializes_snake_case() {
        let json = serde_json::to_string(&RunnerTransport::AppServer).unwrap();
        assert_eq!(json, "\"app_server\"");
    }
}
