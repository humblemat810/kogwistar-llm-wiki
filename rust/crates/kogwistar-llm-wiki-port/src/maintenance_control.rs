use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

use crate::maintenance_profiles::{normalize_budget, normalize_profile_ladder};

pub const DEFAULT_REQUEST_FOLLOW_UPS: i64 = 1;
pub const DEFAULT_REQUEST_MAX_ROUNDS: i64 = DEFAULT_REQUEST_FOLLOW_UPS + 1;
pub const REQUEST_MAX_ROUNDS_ENV: &str = "LLM_WIKI_MAINTENANCE_DEFAULT_REQUEST_MAX_ROUNDS";
pub const REQUEST_ENABLED_ENV: &str = "LLM_WIKI_MAINTENANCE_REQUEST_ENABLED";
pub const BACKGROUND_ENABLED_ENV: &str = "LLM_WIKI_MAINTENANCE_BACKGROUND_ENABLED";
pub const MULTI_WORKER_ENV: &str = "KOGWISTAR_MAINTENANCE_MULTI_WORKER";

pub type Budget = BTreeMap<String, BTreeMap<String, f64>>;

fn env_value(name: &str) -> String {
    std::env::var(name).unwrap_or_default().trim().to_owned()
}

fn configured_bool(name: &str, default: bool) -> bool {
    match env_value(name).to_ascii_lowercase().as_str() {
        "1" | "true" | "yes" | "on" => true,
        "0" | "false" | "no" | "off" => false,
        _ => default,
    }
}

fn bool_value(value: Option<&Value>, default: bool) -> Result<bool, String> {
    match value {
        None | Some(Value::Null) => Ok(default),
        Some(Value::Bool(value)) => Ok(*value),
        Some(Value::String(value)) => match value.trim().to_ascii_lowercase().as_str() {
            "1" | "true" | "yes" | "on" => Ok(true),
            "0" | "false" | "no" | "off" => Ok(false),
            _ => Err("maintenance control boolean is invalid".to_owned()),
        },
        _ => Err("maintenance control boolean is invalid".to_owned()),
    }
}

fn int_value(value: Option<&Value>, default: i64) -> i64 {
    match value {
        Some(Value::Number(number)) => number.as_i64().unwrap_or(default),
        Some(Value::String(value)) => value.parse().unwrap_or(default),
        Some(Value::Bool(true)) => 1,
        Some(Value::Bool(false)) => 0,
        _ => default,
    }
}

fn mapping_value<'a>(source: Option<&'a Value>, key: &str) -> Option<&'a Value> {
    source
        .and_then(Value::as_object)
        .and_then(|values| values.get(key))
}

pub fn configured_default_request_max_rounds() -> Result<i64, String> {
    let raw = env_value(REQUEST_MAX_ROUNDS_ENV);
    if raw.is_empty() {
        return Ok(DEFAULT_REQUEST_MAX_ROUNDS);
    }
    let parsed = raw
        .parse::<i64>()
        .map_err(|_| format!("{REQUEST_MAX_ROUNDS_ENV} must be a positive integer"))?;
    if parsed <= 0 {
        return Err(format!("{REQUEST_MAX_ROUNDS_ENV} must be positive"));
    }
    Ok(parsed)
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct MaintenanceControlState {
    pub request_enabled: bool,
    pub background_enabled: bool,
    pub enabled: bool,
    pub profile: String,
    pub profile_ladder: Vec<Value>,
    pub profile_ladder_configured: bool,
    pub budget: Budget,
    pub spend: Budget,
    pub deferred_cycle: bool,
    pub status_reason: String,
    pub updated_at_ms: i64,
    pub updated_by: String,
}

impl MaintenanceControlState {
    #[must_use]
    pub fn default_state() -> Self {
        Self {
            request_enabled: true,
            background_enabled: false,
            enabled: true,
            profile: "balanced".to_owned(),
            profile_ladder: Vec::new(),
            profile_ladder_configured: false,
            budget: Budget::new(),
            spend: Budget::new(),
            deferred_cycle: false,
            status_reason: "configured".to_owned(),
            updated_at_ms: 0,
            updated_by: "default".to_owned(),
        }
    }

    pub fn from_mapping(raw: Option<&Value>) -> Result<Self, String> {
        let object = raw.and_then(Value::as_object);
        let profile = object
            .and_then(|values| values.get("profile"))
            .and_then(Value::as_str)
            .map(str::trim)
            .map(str::to_owned)
            .filter(|value| !value.is_empty())
            .unwrap_or_else(|| {
                let raw = env_value("LLM_WIKI_MAINTENANCE_PROFILE");
                if raw.is_empty() {
                    let model_class = env_value("LLM_WIKI_MAINTENANCE_MODEL_CLASS");
                    let model_class = if model_class.is_empty() {
                        "unknown".to_owned()
                    } else {
                        model_class
                    };
                    if model_class == "free" {
                        "high".to_owned()
                    } else {
                        "lite".to_owned()
                    }
                } else {
                    raw
                }
            })
            .to_ascii_lowercase();
        if !["high", "balanced", "budgeted", "lite"].contains(&profile.as_str()) {
            return Err(format!("invalid maintenance profile: {profile}"));
        }
        let ladder_levels = if object.is_some_and(|values| values.contains_key("profile_ladder")) {
            normalize_profile_ladder(object.and_then(|values| values.get("profile_ladder")))?
        } else {
            let raw = env_value("LLM_WIKI_MAINTENANCE_PROFILE_LADDER");
            if raw.is_empty() {
                Vec::new()
            } else {
                let parsed = serde_json::from_str::<Value>(&raw).map_err(|_| {
                    "LLM_WIKI_MAINTENANCE_PROFILE_LADDER must be valid JSON".to_owned()
                })?;
                normalize_profile_ladder(Some(&parsed))?
            }
        };
        let request_enabled = bool_value(
            object.and_then(|values| values.get("request_enabled")),
            configured_bool(REQUEST_ENABLED_ENV, true),
        )?;
        let background_enabled = bool_value(
            object.and_then(|values| values.get("background_enabled")),
            configured_bool(BACKGROUND_ENABLED_ENV, false),
        )?;
        let enabled = bool_value(
            object.and_then(|values| values.get("enabled")),
            configured_bool("LLM_WIKI_MAINTENANCE_ENABLED", false),
        )?;
        let budget = normalize_budget(mapping_value(raw, "budget"))?;
        let spend = normalize_budget(mapping_value(raw, "spend"))?;
        Ok(Self {
            request_enabled,
            background_enabled,
            enabled,
            profile,
            profile_ladder: ladder_levels
                .iter()
                .map(serde_json::to_value)
                .collect::<Result<Vec<_>, _>>()
                .map_err(|error| error.to_string())?,
            profile_ladder_configured: object
                .map(|values| {
                    values
                        .get("profile_ladder_configured")
                        .and_then(Value::as_bool)
                        .unwrap_or_else(|| values.contains_key("profile_ladder"))
                })
                .unwrap_or_default(),
            budget,
            spend,
            deferred_cycle: bool_value(
                object.and_then(|values| values.get("deferred_cycle")),
                false,
            )?,
            status_reason: object
                .and_then(|values| values.get("status_reason"))
                .and_then(Value::as_str)
                .map(str::to_owned)
                .filter(|value| !value.is_empty())
                .unwrap_or_else(|| "configured".to_owned()),
            updated_at_ms: int_value(object.and_then(|values| values.get("updated_at_ms")), 0),
            updated_by: object
                .and_then(|values| values.get("updated_by"))
                .and_then(Value::as_str)
                .map(str::to_owned)
                .filter(|value| !value.is_empty())
                .unwrap_or_else(|| "default".to_owned()),
        })
    }

    #[must_use]
    pub fn changed_with_clock(mut self, updates: Value, updated_at_ms: i64) -> Self {
        if let Some(value) = updates.as_object() {
            for (key, value) in value {
                match key.as_str() {
                    "request_enabled" => {
                        self.request_enabled = value.as_bool().unwrap_or(self.request_enabled)
                    }
                    "background_enabled" => {
                        self.background_enabled = value.as_bool().unwrap_or(self.background_enabled)
                    }
                    "enabled" => self.enabled = value.as_bool().unwrap_or(self.enabled),
                    "profile" => self.profile = value.as_str().unwrap_or(&self.profile).to_owned(),
                    "profile_ladder" => {
                        self.profile_ladder = value.as_array().cloned().unwrap_or_default()
                    }
                    "profile_ladder_configured" => {
                        self.profile_ladder_configured =
                            value.as_bool().unwrap_or(self.profile_ladder_configured)
                    }
                    "budget" => {
                        self.budget =
                            serde_json::from_value(value.clone()).unwrap_or(self.budget.clone())
                    }
                    "spend" => {
                        self.spend =
                            serde_json::from_value(value.clone()).unwrap_or(self.spend.clone())
                    }
                    "deferred_cycle" => {
                        self.deferred_cycle = value.as_bool().unwrap_or(self.deferred_cycle)
                    }
                    "status_reason" => {
                        self.status_reason =
                            value.as_str().unwrap_or(&self.status_reason).to_owned()
                    }
                    "updated_by" => {
                        self.updated_by = value.as_str().unwrap_or(&self.updated_by).to_owned()
                    }
                    _ => {}
                }
            }
        }
        self.updated_at_ms = updated_at_ms;
        self
    }
}

pub fn corrupt_control_state() -> Result<MaintenanceControlState, String> {
    MaintenanceControlState::from_mapping(Some(&json!({"enabled": false})))
}
