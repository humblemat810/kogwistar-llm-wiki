use std::collections::BTreeMap;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum CliCommand {
    Demo,
    Ingest,
    Report,
    Workbench,
    Mcp,
    Serve,
    EmbeddingService,
    SeedBundle,
    Archive,
    Embeddings,
    CodexMemory,
    CodexBridge,
    CodexCompose,
    Compose,
    Daemon,
}

impl CliCommand {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Demo => "demo",
            Self::Ingest => "ingest",
            Self::Report => "report",
            Self::Workbench => "workbench",
            Self::Mcp => "mcp",
            Self::Serve => "serve",
            Self::EmbeddingService => "embedding-service",
            Self::SeedBundle => "seed-bundle",
            Self::Archive => "archive",
            Self::Embeddings => "embeddings",
            Self::CodexMemory => "codex-memory",
            Self::CodexBridge => "codex-bridge",
            Self::CodexCompose => "codex-compose",
            Self::Compose => "compose",
            Self::Daemon => "daemon",
        }
    }

    pub fn handler_name(self) -> &'static str {
        match self {
            Self::Demo => "demo",
            Self::Ingest => "ingest",
            Self::Report => "report",
            Self::Workbench => "workbench",
            Self::Mcp => "mcp",
            Self::Serve => "serve",
            Self::EmbeddingService => "embedding_service",
            Self::SeedBundle => "seed_bundle",
            Self::Archive => "archive",
            Self::Embeddings => "embeddings",
            Self::CodexMemory => "codex_memory",
            Self::CodexBridge => "codex_bridge",
            Self::CodexCompose => "codex_compose",
            Self::Compose => "compose",
            Self::Daemon => "daemon",
        }
    }

    pub fn has_required_subcommand(self) -> bool {
        matches!(
            self,
            Self::Archive | Self::Embeddings | Self::Compose | Self::Daemon
        )
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum VectorBackend {
    Chroma,
    Postgres,
    Pinecone,
    Qdrant,
}

impl VectorBackend {
    pub fn parse(value: &str) -> Result<Self, CliError> {
        match value {
            "chroma" => Ok(Self::Chroma),
            "postgres" => Ok(Self::Postgres),
            "pinecone" => Ok(Self::Pinecone),
            "qdrant" => Ok(Self::Qdrant),
            _ => Err(CliError::InvalidChoice("--backend".into(), value.into())),
        }
    }

    pub fn as_str(self) -> &'static str {
        match self {
            Self::Chroma => "chroma",
            Self::Postgres => "postgres",
            Self::Pinecone => "pinecone",
            Self::Qdrant => "qdrant",
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ConversationPersistenceMode {
    SingleStage,
    TwoStage,
}

impl ConversationPersistenceMode {
    pub fn parse(value: &str) -> Result<Self, CliError> {
        match value {
            "single_stage" => Ok(Self::SingleStage),
            "two_stage" => Ok(Self::TwoStage),
            _ => Err(CliError::InvalidChoice(
                "--conversation-persistence-mode".into(),
                value.into(),
            )),
        }
    }

    pub fn as_str(self) -> &'static str {
        match self {
            Self::SingleStage => "single_stage",
            Self::TwoStage => "two_stage",
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CliGlobalSettings {
    pub data_dir: Option<String>,
    pub backend: VectorBackend,
    pub dsn: Option<String>,
    pub split_derived_knowledge: bool,
    pub conversation_persistence_mode: ConversationPersistenceMode,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CliInvocation {
    pub settings: CliGlobalSettings,
    pub command: CliCommand,
    pub subcommand: Option<String>,
    pub arguments: BTreeMap<String, Vec<String>>,
}

impl CliInvocation {
    pub fn resolved_handler_name(&self) -> &'static str {
        match (self.command, self.subcommand.as_deref()) {
            (CliCommand::Archive, Some("create")) => "archive_create",
            (CliCommand::Archive, Some("inspect")) => "archive_inspect",
            (CliCommand::Archive, Some("restore")) => "archive_restore",
            (CliCommand::Archive, Some("catalog")) => "archive_catalog",
            (CliCommand::Archive, Some("verify")) => "archive_verify",
            (CliCommand::Embeddings, Some("inspect")) => "embeddings_inspect",
            (CliCommand::Embeddings, Some("adopt-legacy-profile")) => "embeddings_adopt_legacy",
            (CliCommand::CodexMemory, _) => "codex_memory",
            (CliCommand::CodexBridge, _) => "codex_bridge",
            (CliCommand::CodexCompose, _) => "codex_compose",
            (CliCommand::Compose, Some("generate")) => "compose_generate",
            (CliCommand::Compose, Some("check")) => "compose_check",
            (CliCommand::Daemon, Some("projection")) => "daemon_projection",
            (CliCommand::Daemon, Some("maintenance")) => "daemon_maintenance",
            (CliCommand::Daemon, Some("maintenance-control")) => "maintenance_control",
            (command, _) => command.handler_name(),
        }
    }

    pub fn first_argument(&self, name: &str) -> Option<&str> {
        self.arguments
            .get(name)
            .and_then(|values| values.first().map(String::as_str))
    }

    pub fn argument_values(&self, name: &str) -> Option<&[String]> {
        self.arguments.get(name).map(Vec::as_slice)
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum CliError {
    MissingCommand,
    UnknownCommand(String),
    InvalidChoice(String, String),
    MissingArgument(String),
    MissingSubcommand(String),
}

pub struct CliArgumentSpec {
    pub name: &'static str,
    pub required: bool,
    pub boolean: bool,
}

pub fn parse_cli_with_env(
    arguments: &[String],
    environment: &BTreeMap<String, String>,
) -> Result<CliInvocation, CliError> {
    let env = |name: &str| environment.get(name).cloned();
    let backend = env("KOGWISTAR_VECTOR_BACKEND").unwrap_or_else(|| "chroma".into());
    let mut settings = CliGlobalSettings {
        data_dir: None,
        backend: VectorBackend::parse(&backend)?,
        dsn: env("KOGWISTAR_POSTGRES_DSN"),
        split_derived_knowledge: false,
        conversation_persistence_mode: ConversationPersistenceMode::SingleStage,
    };
    let mut index = 0;
    while index < arguments.len() {
        let value = arguments[index].as_str();
        match value {
            "--data-dir" => {
                index += 1;
                settings.data_dir = Some(cli_value(arguments, index, value)?);
            }
            "--backend" => {
                index += 1;
                settings.backend = VectorBackend::parse(&cli_value(arguments, index, value)?)?;
            }
            "--dsn" => {
                index += 1;
                settings.dsn = Some(cli_value(arguments, index, value)?);
            }
            "--split-derived-knowledge" => settings.split_derived_knowledge = true,
            "--conversation-persistence-mode" => {
                index += 1;
                settings.conversation_persistence_mode =
                    ConversationPersistenceMode::parse(&cli_value(arguments, index, value)?)?;
            }
            "--" => {
                index += 1;
                break;
            }
            _ if value.starts_with("--") => return Err(CliError::UnknownCommand(value.into())),
            _ => break,
        }
        index += 1;
    }
    if index >= arguments.len() {
        return Err(CliError::MissingCommand);
    }
    let command_name = arguments[index].as_str();
    let command = match command_name {
        "demo" => CliCommand::Demo,
        "ingest" => CliCommand::Ingest,
        "report" => CliCommand::Report,
        "workbench" => CliCommand::Workbench,
        "mcp" => CliCommand::Mcp,
        "serve" => CliCommand::Serve,
        "embedding-service" => CliCommand::EmbeddingService,
        "seed-bundle" => CliCommand::SeedBundle,
        "archive" => CliCommand::Archive,
        "embeddings" => CliCommand::Embeddings,
        "codex-memory" => CliCommand::CodexMemory,
        "codex-bridge" => CliCommand::CodexBridge,
        "codex-compose" => CliCommand::CodexCompose,
        "compose" => CliCommand::Compose,
        "daemon" => CliCommand::Daemon,
        _ => return Err(CliError::UnknownCommand(command_name.into())),
    };
    let mut cursor = index + 1;
    let mut subcommand = None;
    if command.has_required_subcommand() {
        let Some(value) = arguments.get(cursor).cloned() else {
            return Err(CliError::MissingSubcommand(command.as_str().to_string()));
        };
        subcommand = Some(value);
        cursor += 1;
    }
    let specs = argument_specs(command, subcommand.as_deref());
    let mut parsed = BTreeMap::new();
    let mut values = arguments[cursor..].iter();
    while let Some(name) = values.next() {
        if !name.starts_with("--") {
            return Err(CliError::UnknownCommand(name.into()));
        }
        let argument_name = name.trim_start_matches('-').replace('-', "_");
        let Some(spec) = specs.iter().find(|spec| spec.name == argument_name) else {
            return Err(CliError::UnknownCommand(name.into()));
        };
        if spec.boolean {
            parsed.insert(argument_name, vec!["true".to_string()]);
        } else {
            match values.next() {
                Some(value) => parsed.entry(argument_name).or_default().push(value.clone()),
                None => return Err(CliError::UnknownCommand(name.into())),
            };
        }
    }
    let missing = specs
        .iter()
        .filter(|spec| spec.required)
        .find(|spec| !parsed.contains_key(spec.name))
        .map(|spec| spec.name);
    if let Some(missing) = missing {
        return Err(CliError::MissingArgument(missing.to_string()));
    }
    Ok(CliInvocation {
        settings,
        command,
        subcommand,
        arguments: parsed,
    })
}

fn argument_specs(command: CliCommand, subcommand: Option<&str>) -> &'static [CliArgumentSpec] {
    match command {
        CliCommand::Demo => &[
            CliArgumentSpec {
                name: "workspace",
                required: true,
                boolean: false,
            },
            CliArgumentSpec {
                name: "source",
                required: true,
                boolean: false,
            },
            CliArgumentSpec {
                name: "vault",
                required: true,
                boolean: false,
            },
            CliArgumentSpec {
                name: "title",
                required: false,
                boolean: false,
            },
            CliArgumentSpec {
                name: "source_format",
                required: false,
                boolean: false,
            },
            CliArgumentSpec {
                name: "operation_mode",
                required: false,
                boolean: false,
            },
            CliArgumentSpec {
                name: "parser_mode",
                required: false,
                boolean: false,
            },
            CliArgumentSpec {
                name: "parser_lane",
                required: false,
                boolean: false,
            },
            CliArgumentSpec {
                name: "llm_provider",
                required: false,
                boolean: false,
            },
            CliArgumentSpec {
                name: "llm_model",
                required: false,
                boolean: false,
            },
            CliArgumentSpec {
                name: "promotion_mode",
                required: false,
                boolean: false,
            },
            CliArgumentSpec {
                name: "debug_run_dir",
                required: false,
                boolean: false,
            },
        ],
        CliCommand::Ingest => &[
            CliArgumentSpec {
                name: "workspace",
                required: true,
                boolean: false,
            },
            CliArgumentSpec {
                name: "source",
                required: true,
                boolean: false,
            },
            CliArgumentSpec {
                name: "title",
                required: false,
                boolean: false,
            },
            CliArgumentSpec {
                name: "source_format",
                required: false,
                boolean: false,
            },
            CliArgumentSpec {
                name: "operation_mode",
                required: false,
                boolean: false,
            },
            CliArgumentSpec {
                name: "parser_mode",
                required: false,
                boolean: false,
            },
            CliArgumentSpec {
                name: "parser_lane",
                required: false,
                boolean: false,
            },
            CliArgumentSpec {
                name: "llm_provider",
                required: false,
                boolean: false,
            },
            CliArgumentSpec {
                name: "llm_model",
                required: false,
                boolean: false,
            },
            CliArgumentSpec {
                name: "promotion_mode",
                required: false,
                boolean: false,
            },
            CliArgumentSpec {
                name: "debug_run_dir",
                required: false,
                boolean: false,
            },
        ],
        CliCommand::Report => &[
            CliArgumentSpec {
                name: "workspace",
                required: true,
                boolean: false,
            },
            CliArgumentSpec {
                name: "data_dir",
                required: true,
                boolean: false,
            },
            CliArgumentSpec {
                name: "backend",
                required: false,
                boolean: false,
            },
            CliArgumentSpec {
                name: "dsn",
                required: false,
                boolean: false,
            },
            CliArgumentSpec {
                name: "report_scope",
                required: false,
                boolean: false,
            },
            CliArgumentSpec {
                name: "dump_mode",
                required: false,
                boolean: false,
            },
            CliArgumentSpec {
                name: "dump_limit_per_space",
                required: false,
                boolean: false,
            },
            CliArgumentSpec {
                name: "split_derived_knowledge",
                required: false,
                boolean: true,
            },
        ],
        CliCommand::Workbench => &[
            CliArgumentSpec {
                name: "workspace",
                required: true,
                boolean: false,
            },
            CliArgumentSpec {
                name: "host",
                required: false,
                boolean: false,
            },
            CliArgumentSpec {
                name: "port",
                required: false,
                boolean: false,
            },
            CliArgumentSpec {
                name: "codex_workers",
                required: false,
                boolean: false,
            },
            CliArgumentSpec {
                name: "codex_executable",
                required: false,
                boolean: false,
            },
            CliArgumentSpec {
                name: "codex_model",
                required: false,
                boolean: false,
            },
            CliArgumentSpec {
                name: "codex_profile",
                required: false,
                boolean: false,
            },
            CliArgumentSpec {
                name: "codex_transport",
                required: false,
                boolean: false,
            },
            CliArgumentSpec {
                name: "codex_timeout",
                required: false,
                boolean: false,
            },
        ],
        CliCommand::Mcp => &[
            CliArgumentSpec {
                name: "workspace",
                required: true,
                boolean: false,
            },
            CliArgumentSpec {
                name: "host",
                required: false,
                boolean: false,
            },
            CliArgumentSpec {
                name: "port",
                required: false,
                boolean: false,
            },
            CliArgumentSpec {
                name: "path",
                required: false,
                boolean: false,
            },
            CliArgumentSpec {
                name: "transport",
                required: false,
                boolean: false,
            },
            CliArgumentSpec {
                name: "split_derived_knowledge",
                required: false,
                boolean: true,
            },
        ],
        CliCommand::Serve => &[
            CliArgumentSpec {
                name: "workspace",
                required: true,
                boolean: false,
            },
            CliArgumentSpec {
                name: "host",
                required: false,
                boolean: false,
            },
            CliArgumentSpec {
                name: "port",
                required: false,
                boolean: false,
            },
            CliArgumentSpec {
                name: "mcp_port",
                required: false,
                boolean: false,
            },
            CliArgumentSpec {
                name: "mcp_path",
                required: false,
                boolean: false,
            },
            CliArgumentSpec {
                name: "maintenance_interval",
                required: false,
                boolean: false,
            },
            CliArgumentSpec {
                name: "background_interval",
                required: false,
                boolean: false,
            },
        ],
        CliCommand::EmbeddingService => &[],
        CliCommand::SeedBundle => &[
            CliArgumentSpec {
                name: "workspace",
                required: true,
                boolean: false,
            },
            CliArgumentSpec {
                name: "bundle",
                required: true,
                boolean: false,
            },
            CliArgumentSpec {
                name: "output",
                required: true,
                boolean: false,
            },
            CliArgumentSpec {
                name: "cockpit_question",
                required: false,
                boolean: false,
            },
            CliArgumentSpec {
                name: "cockpit_session",
                required: false,
                boolean: false,
            },
            CliArgumentSpec {
                name: "cockpit_interaction",
                required: false,
                boolean: false,
            },
            CliArgumentSpec {
                name: "codex_executable",
                required: false,
                boolean: false,
            },
            CliArgumentSpec {
                name: "codex_model",
                required: false,
                boolean: false,
            },
            CliArgumentSpec {
                name: "codex_profile",
                required: false,
                boolean: false,
            },
            CliArgumentSpec {
                name: "codex_transport",
                required: false,
                boolean: false,
            },
            CliArgumentSpec {
                name: "codex_timeout",
                required: false,
                boolean: false,
            },
        ],
        CliCommand::Archive => match subcommand {
            Some("create") => &[
                CliArgumentSpec {
                    name: "workspace",
                    required: true,
                    boolean: false,
                },
                CliArgumentSpec {
                    name: "output",
                    required: true,
                    boolean: false,
                },
                CliArgumentSpec {
                    name: "parent",
                    required: false,
                    boolean: false,
                },
                CliArgumentSpec {
                    name: "include_backend_snapshot",
                    required: false,
                    boolean: true,
                },
            ],
            Some("inspect") | Some("verify") => &[CliArgumentSpec {
                name: "archive",
                required: true,
                boolean: false,
            }],
            Some("restore") => &[
                CliArgumentSpec {
                    name: "archive",
                    required: true,
                    boolean: false,
                },
                CliArgumentSpec {
                    name: "parent",
                    required: false,
                    boolean: false,
                },
                CliArgumentSpec {
                    name: "target_workspace",
                    required: false,
                    boolean: false,
                },
                CliArgumentSpec {
                    name: "apply",
                    required: false,
                    boolean: true,
                },
                CliArgumentSpec {
                    name: "use_backend_snapshot",
                    required: false,
                    boolean: true,
                },
                CliArgumentSpec {
                    name: "embedding_fingerprint",
                    required: false,
                    boolean: false,
                },
            ],
            Some("catalog") => &[
                CliArgumentSpec {
                    name: "directory",
                    required: true,
                    boolean: false,
                },
                CliArgumentSpec {
                    name: "before_ms",
                    required: false,
                    boolean: false,
                },
            ],
            _ => &[],
        },
        CliCommand::Embeddings => match subcommand {
            Some("inspect") => &[CliArgumentSpec {
                name: "workspace",
                required: true,
                boolean: false,
            }],
            Some("adopt-legacy-profile") => &[
                CliArgumentSpec {
                    name: "workspace",
                    required: true,
                    boolean: false,
                },
                CliArgumentSpec {
                    name: "acknowledge_legacy_vectors",
                    required: false,
                    boolean: true,
                },
            ],
            _ => &[],
        },
        CliCommand::CodexMemory => &[
            CliArgumentSpec {
                name: "workspace",
                required: true,
                boolean: false,
            },
            CliArgumentSpec {
                name: "project_root",
                required: false,
                boolean: false,
            },
            CliArgumentSpec {
                name: "mcp_url",
                required: false,
                boolean: false,
            },
            CliArgumentSpec {
                name: "binding_file",
                required: false,
                boolean: false,
            },
            CliArgumentSpec {
                name: "check_only",
                required: false,
                boolean: true,
            },
        ],
        CliCommand::CodexBridge => &[
            CliArgumentSpec {
                name: "host",
                required: false,
                boolean: false,
            },
            CliArgumentSpec {
                name: "port",
                required: false,
                boolean: false,
            },
            CliArgumentSpec {
                name: "token_env",
                required: false,
                boolean: false,
            },
            CliArgumentSpec {
                name: "env_file",
                required: false,
                boolean: false,
            },
            CliArgumentSpec {
                name: "no_env_file",
                required: false,
                boolean: true,
            },
            CliArgumentSpec {
                name: "codex_executable",
                required: false,
                boolean: false,
            },
            CliArgumentSpec {
                name: "codex_model",
                required: false,
                boolean: false,
            },
        ],
        CliCommand::CodexCompose => &[
            CliArgumentSpec {
                name: "mode",
                required: false,
                boolean: false,
            },
            CliArgumentSpec {
                name: "project",
                required: false,
                boolean: false,
            },
            CliArgumentSpec {
                name: "configure",
                required: false,
                boolean: true,
            },
            CliArgumentSpec {
                name: "stack",
                required: false,
                boolean: false,
            },
            CliArgumentSpec {
                name: "embedding",
                required: false,
                boolean: false,
            },
            CliArgumentSpec {
                name: "maintenance_enabled",
                required: false,
                boolean: false,
            },
            CliArgumentSpec {
                name: "request_enabled",
                required: false,
                boolean: false,
            },
            CliArgumentSpec {
                name: "background_enabled",
                required: false,
                boolean: false,
            },
            CliArgumentSpec {
                name: "maintenance_profile",
                required: false,
                boolean: false,
            },
            CliArgumentSpec {
                name: "profile_ladder_json",
                required: false,
                boolean: false,
            },
            CliArgumentSpec {
                name: "profile_ladder_file",
                required: false,
                boolean: false,
            },
            CliArgumentSpec {
                name: "combined_memory",
                required: false,
                boolean: false,
            },
            CliArgumentSpec {
                name: "combined_cpu",
                required: false,
                boolean: false,
            },
            CliArgumentSpec {
                name: "embedding_dimension",
                required: false,
                boolean: false,
            },
            CliArgumentSpec {
                name: "max_model_len",
                required: false,
                boolean: false,
            },
            CliArgumentSpec {
                name: "crop_token_budget",
                required: false,
                boolean: false,
            },
            CliArgumentSpec {
                name: "env_file",
                required: false,
                boolean: false,
            },
            CliArgumentSpec {
                name: "write_env",
                required: false,
                boolean: true,
            },
            CliArgumentSpec {
                name: "lxc",
                required: false,
                boolean: true,
            },
            CliArgumentSpec {
                name: "lxc_apply",
                required: false,
                boolean: true,
            },
            CliArgumentSpec {
                name: "build",
                required: false,
                boolean: true,
            },
            CliArgumentSpec {
                name: "login",
                required: false,
                boolean: true,
            },
            CliArgumentSpec {
                name: "dry_run",
                required: false,
                boolean: true,
            },
            CliArgumentSpec {
                name: "execute",
                required: false,
                boolean: true,
            },
        ],
        CliCommand::Compose => match subcommand {
            Some("generate") => &[
                CliArgumentSpec {
                    name: "output",
                    required: true,
                    boolean: false,
                },
                CliArgumentSpec {
                    name: "workspace",
                    required: false,
                    boolean: false,
                },
                CliArgumentSpec {
                    name: "backend",
                    required: false,
                    boolean: false,
                },
                CliArgumentSpec {
                    name: "project_name",
                    required: false,
                    boolean: false,
                },
                CliArgumentSpec {
                    name: "mode",
                    required: false,
                    boolean: false,
                },
                CliArgumentSpec {
                    name: "embedding_backend",
                    required: false,
                    boolean: false,
                },
                CliArgumentSpec {
                    name: "auth_mode",
                    required: false,
                    boolean: false,
                },
                CliArgumentSpec {
                    name: "with_otel",
                    required: false,
                    boolean: true,
                },
                CliArgumentSpec {
                    name: "with_oauth",
                    required: false,
                    boolean: true,
                },
                CliArgumentSpec {
                    name: "model_revision",
                    required: false,
                    boolean: false,
                },
                CliArgumentSpec {
                    name: "embedding_dimension",
                    required: false,
                    boolean: false,
                },
            ],
            Some("check") => &[CliArgumentSpec {
                name: "file",
                required: true,
                boolean: false,
            }],
            _ => &[],
        },
        CliCommand::Daemon => match subcommand {
            Some("projection") => &[
                CliArgumentSpec {
                    name: "workspace",
                    required: true,
                    boolean: false,
                },
                CliArgumentSpec {
                    name: "vault",
                    required: true,
                    boolean: false,
                },
                CliArgumentSpec {
                    name: "interval",
                    required: false,
                    boolean: false,
                },
            ],
            Some("maintenance") => &[
                CliArgumentSpec {
                    name: "workspace",
                    required: true,
                    boolean: false,
                },
                CliArgumentSpec {
                    name: "interval",
                    required: false,
                    boolean: false,
                },
                CliArgumentSpec {
                    name: "background_interval",
                    required: false,
                    boolean: false,
                },
            ],
            Some("maintenance-control") => &[
                CliArgumentSpec {
                    name: "request_enabled",
                    required: false,
                    boolean: false,
                },
                CliArgumentSpec {
                    name: "background_enabled",
                    required: false,
                    boolean: false,
                },
                CliArgumentSpec {
                    name: "enabled",
                    required: false,
                    boolean: false,
                },
                CliArgumentSpec {
                    name: "profile",
                    required: false,
                    boolean: false,
                },
                CliArgumentSpec {
                    name: "profile_ladder_json",
                    required: false,
                    boolean: false,
                },
                CliArgumentSpec {
                    name: "budget_json",
                    required: false,
                    boolean: false,
                },
                CliArgumentSpec {
                    name: "clear_budget",
                    required: false,
                    boolean: true,
                },
                CliArgumentSpec {
                    name: "status",
                    required: false,
                    boolean: true,
                },
                CliArgumentSpec {
                    name: "daily_tokens",
                    required: false,
                    boolean: false,
                },
                CliArgumentSpec {
                    name: "daily_input_tokens",
                    required: false,
                    boolean: false,
                },
                CliArgumentSpec {
                    name: "daily_output_tokens",
                    required: false,
                    boolean: false,
                },
                CliArgumentSpec {
                    name: "daily_money",
                    required: false,
                    boolean: false,
                },
                CliArgumentSpec {
                    name: "weekly_tokens",
                    required: false,
                    boolean: false,
                },
                CliArgumentSpec {
                    name: "weekly_input_tokens",
                    required: false,
                    boolean: false,
                },
                CliArgumentSpec {
                    name: "weekly_output_tokens",
                    required: false,
                    boolean: false,
                },
                CliArgumentSpec {
                    name: "weekly_money",
                    required: false,
                    boolean: false,
                },
                CliArgumentSpec {
                    name: "monthly_tokens",
                    required: false,
                    boolean: false,
                },
                CliArgumentSpec {
                    name: "monthly_input_tokens",
                    required: false,
                    boolean: false,
                },
                CliArgumentSpec {
                    name: "monthly_output_tokens",
                    required: false,
                    boolean: false,
                },
                CliArgumentSpec {
                    name: "monthly_money",
                    required: false,
                    boolean: false,
                },
                CliArgumentSpec {
                    name: "runtime_only",
                    required: false,
                    boolean: true,
                },
            ],
            _ => &[],
        },
    }
}

fn cli_value(arguments: &[String], index: usize, name: &str) -> Result<String, CliError> {
    arguments
        .get(index)
        .cloned()
        .ok_or_else(|| CliError::UnknownCommand(name.into()))
}
