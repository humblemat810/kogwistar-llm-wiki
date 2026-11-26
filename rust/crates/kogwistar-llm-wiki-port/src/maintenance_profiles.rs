use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

pub const PROFILE_NAMES: [&str; 4] = ["high", "balanced", "budgeted", "lite"];
pub const WINDOW_NAMES: [&str; 3] = ["daily", "weekly", "monthly"];
pub const METRIC_NAMES: [&str; 4] = ["tokens", "input_tokens", "output_tokens", "money"];
pub const WINDOW_SECONDS: [(&str, u64); 3] = [
    ("daily", 86_400),
    ("weekly", 604_800),
    ("monthly", 2_592_000),
];
pub const MODEL_CLASSES: [&str; 3] = ["free", "metered", "unknown"];

pub type Budget = BTreeMap<String, BTreeMap<String, f64>>;

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct MaintenanceProfileDecision {
    pub requested: String,
    pub effective: String,
    pub enabled: bool,
    pub reason: String,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct MaintenanceProfileLevel {
    pub name: String,
    pub provider: String,
    pub model: Option<String>,
    pub profile: String,
    pub budget: Budget,
}

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct MaintenanceProfileLadderDecision {
    pub level: Option<MaintenanceProfileLevel>,
    pub profile: MaintenanceProfileDecision,
    pub level_index: Option<usize>,
    pub reason: String,
}

fn as_float(value: Option<&Value>, default: f64) -> f64 {
    match value {
        Some(Value::Number(number)) => number.as_f64().unwrap_or(default),
        Some(Value::String(value)) => value.parse().unwrap_or(default),
        Some(Value::Bool(_)) | None => default,
        _ => default,
    }
}

fn optional_number(value: Option<&Value>, name: &str) -> Result<Option<f64>, String> {
    let Some(value) = value else {
        return Ok(None);
    };
    match value {
        Value::Null => Ok(None),
        Value::String(value) if value.trim().is_empty() => Ok(None),
        Value::Number(number) => {
            let result = number.as_f64().unwrap_or(f64::NAN);
            if !result.is_finite() || result < 0.0 {
                Err(format!("{name} must be a finite non-negative number"))
            } else {
                Ok(Some(result))
            }
        }
        _ => Err(format!("{name} must be a non-negative number or empty")),
    }
}

pub fn normalize_budget(value: Option<&Value>) -> Result<Budget, String> {
    let mut result = Budget::new();
    let Some(Value::Object(raw)) = value else {
        return Ok(result);
    };
    for (window, raw_metrics) in raw {
        if !WINDOW_NAMES.contains(&window.as_str()) {
            return Err(format!("unknown maintenance budget window: {window}"));
        }
        let Some(raw_metrics) = raw_metrics.as_object() else {
            return Err(format!("maintenance budget {window} must be an object"));
        };
        let mut metrics = BTreeMap::new();
        for (metric, raw_cap) in raw_metrics {
            if !METRIC_NAMES.contains(&metric.as_str()) {
                return Err(format!("unknown maintenance budget metric: {metric}"));
            }
            let cap = optional_number(Some(raw_cap), &format!("{window}.{metric}"))?;
            if let Some(cap) = cap {
                metrics.insert(metric.clone(), cap);
            }
        }
        if !metrics.is_empty() {
            result.insert(window.clone(), metrics);
        }
    }
    Ok(result)
}

pub fn resolve_profile(
    enabled: bool,
    requested: &str,
    budget: Option<&Value>,
) -> Result<MaintenanceProfileDecision, String> {
    let requested = requested.trim().to_ascii_lowercase();
    if !PROFILE_NAMES.contains(&requested.as_str()) {
        return Err(format!("unknown maintenance profile: {requested}"));
    }
    if !enabled {
        return Ok(MaintenanceProfileDecision {
            requested,
            effective: "off".to_owned(),
            enabled: false,
            reason: "master_disabled".to_owned(),
        });
    }
    if requested == "budgeted" && normalize_budget(budget)?.is_empty() {
        return Ok(MaintenanceProfileDecision {
            requested,
            effective: "lite".to_owned(),
            enabled: true,
            reason: "budgeted_requires_configured_budget".to_owned(),
        });
    }
    Ok(MaintenanceProfileDecision {
        requested: requested.clone(),
        effective: requested,
        enabled: true,
        reason: "configured".to_owned(),
    })
}

pub fn budget_fits(
    budget: &Value,
    spend: &Value,
    estimate: &Value,
) -> Result<(bool, String), String> {
    let normalized = normalize_budget(Some(budget))?;
    for (window, metrics) in normalized {
        let current = spend
            .as_object()
            .and_then(|values| values.get(&window))
            .cloned()
            .unwrap_or(Value::Null);
        for (metric, cap) in metrics {
            let used = as_float(
                current.as_object().and_then(|values| values.get(&metric)),
                0.0,
            );
            let amount = as_float(
                estimate.as_object().and_then(|values| values.get(&metric)),
                0.0,
            );
            if used + amount > cap {
                return Ok((false, format!("{window}_{metric}_cap")));
            }
        }
    }
    Ok((true, "within_budget".to_owned()))
}

pub fn normalize_profile_ladder(
    value: Option<&Value>,
) -> Result<Vec<MaintenanceProfileLevel>, String> {
    let Some(value) = value else {
        return Ok(Vec::new());
    };
    let Value::Array(raw) = value else {
        return Err("maintenance profile ladder must be a JSON array".to_owned());
    };
    if raw.is_empty() || raw.len() > 8 {
        return Err("maintenance profile ladder must contain 1 to 8 levels".to_owned());
    }
    let mut levels = Vec::new();
    let mut names = BTreeMap::new();
    for (index, raw) in raw.iter().enumerate() {
        let Some(raw) = raw.as_object() else {
            return Err(format!(
                "maintenance profile ladder level {index} must be an object"
            ));
        };
        let name = raw
            .get("name")
            .and_then(Value::as_str)
            .map(str::trim)
            .filter(|value| !value.is_empty())
            .map(str::to_owned)
            .unwrap_or_else(|| format!("level-{}", index + 1));
        let provider = raw
            .get("provider")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .trim()
            .to_ascii_lowercase();
        let profile = raw
            .get("profile")
            .and_then(Value::as_str)
            .unwrap_or("balanced")
            .trim()
            .to_ascii_lowercase();
        let model = raw
            .get("model")
            .and_then(Value::as_str)
            .map(str::trim)
            .filter(|value| !value.is_empty())
            .map(str::to_owned);
        if name.is_empty() || names.contains_key(&name) {
            return Err(
                "maintenance profile ladder level names must be non-empty and unique".to_owned(),
            );
        }
        if provider.is_empty() {
            return Err(format!(
                "maintenance profile ladder level {name} needs a provider"
            ));
        }
        if !PROFILE_NAMES.contains(&profile.as_str()) {
            return Err(format!(
                "maintenance profile ladder level {name} has an invalid profile"
            ));
        }
        names.insert(name.clone(), true);
        levels.push(MaintenanceProfileLevel {
            name,
            provider,
            model,
            profile,
            budget: normalize_budget(raw.get("budget").filter(|value: &&Value| value.is_object()))?,
        });
    }
    Ok(levels)
}

#[allow(clippy::too_many_arguments)]
pub fn resolve_profile_ladder(
    enabled: bool,
    requested: &str,
    budget: Option<&Value>,
    ladder: Option<&Value>,
    ladder_spend: Option<&Value>,
    estimate: &Value,
) -> Result<MaintenanceProfileLadderDecision, String> {
    let levels = normalize_profile_ladder(ladder)?;
    if levels.is_empty() {
        let profile = resolve_profile(enabled, requested, budget)?;
        return Ok(MaintenanceProfileLadderDecision {
            level: None,
            profile,
            level_index: None,
            reason: "configured".to_owned(),
        });
    }
    if !enabled {
        let profile = MaintenanceProfileDecision {
            requested: requested.to_owned(),
            effective: "off".to_owned(),
            enabled: false,
            reason: "master_disabled".to_owned(),
        };
        return Ok(MaintenanceProfileLadderDecision {
            level: None,
            profile,
            level_index: None,
            reason: "master_disabled".to_owned(),
        });
    }
    for (index, level) in levels.iter().enumerate() {
        let level_budget = if level.budget.is_empty() {
            normalize_budget(budget)?
        } else {
            level.budget.clone()
        };
        let spend = ladder_spend
            .and_then(Value::as_object)
            .and_then(|values| values.get(&level.name))
            .cloned()
            .unwrap_or(Value::Null);
        let (fits, reason) = budget_fits(&json!(level_budget), &spend, estimate)?;
        if fits {
            let profile = MaintenanceProfileDecision {
                requested: level.profile.clone(),
                effective: level.profile.clone(),
                enabled: true,
                reason: "ladder_selected".to_owned(),
            };
            return Ok(MaintenanceProfileLadderDecision {
                level: Some(level.clone()),
                profile,
                level_index: Some(index),
                reason,
            });
        }
    }
    let profile = MaintenanceProfileDecision {
        requested: requested.to_owned(),
        effective: "off".to_owned(),
        enabled: false,
        reason: "all_ladder_levels_exhausted".to_owned(),
    };
    Ok(MaintenanceProfileLadderDecision {
        level: None,
        profile,
        level_index: None,
        reason: "all_ladder_levels_exhausted".to_owned(),
    })
}
