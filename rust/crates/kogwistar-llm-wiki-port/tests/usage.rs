use kogwistar_llm_wiki_port::models::JsonObject;
use kogwistar_llm_wiki_port::providers::ProviderEndpointConfig;
use kogwistar_llm_wiki_port::usage::{
    budget_event_from_dict, budget_event_to_dict, provider_call_count, summarize_budget_events,
    summarize_provider_budget_events, BudgetAttribution, BudgetEvent, WorkflowProviderSettings,
};
use serde_json::{json, Value};

fn event(
    kind: &str,
    amount: f64,
    unit: &str,
    provider_run_id: Option<&str>,
    extra_meta: Option<serde_json::Value>,
) -> BudgetEvent {
    let mut meta = JsonObject::new();
    if let Some(provider_run_id) = provider_run_id {
        meta.insert("provider_run_id".to_owned(), Value::from(provider_run_id));
    }
    if let Some(Value::Object(extra)) = extra_meta {
        for (key, value) in extra {
            meta.insert(key, value);
        }
    }
    BudgetEvent {
        event_id: None,
        run_id: "run:1".to_owned(),
        source: "langchain-provider".to_owned(),
        kind: kind.to_owned(),
        amount,
        unit: unit.to_owned(),
        scope: "run".to_owned(),
        ts_ms: Some(123),
        meta,
        attribution: Some(BudgetAttribution {
            source_document_id: Some("doc:1".to_owned()),
            ..BudgetAttribution::default()
        }),
    }
}

#[test]
fn budget_event_round_trips_python_wire_contract() {
    let event = event("token", 12.0, "input_tokens", Some("call:1"), None);
    let wire = budget_event_to_dict(&event);
    assert_eq!(wire["run_id"], json!("run:1"));
    assert_eq!(wire["source"], json!("langchain-provider"));
    assert_eq!(wire["kind"], json!("token"));
    assert_eq!(wire["amount"], json!(12.0));
    assert_eq!(wire["scope"], json!("run"));
    assert_eq!(wire["ts_ms"], json!(123));
    assert_eq!(wire["meta"]["provider_run_id"], json!("call:1"));
    assert_eq!(wire["attribution"]["source_document_id"], json!("doc:1"));
    assert_eq!(budget_event_from_dict(&wire).unwrap(), event);
    assert!(budget_event_from_dict(&json!({"amount": -1})).is_err());
    assert!(budget_event_from_dict(&json!({"amount": "NaN"})).is_err());
}

#[test]
fn provider_call_count_matches_python() {
    let events = vec![
        event("token", 4.0, "input_tokens", Some("call:1"), None),
        event("time", 12.0, "ms", Some("call:1"), None),
        event("token", 8.0, "output_tokens", Some("call:2"), None),
        event("token", 1.0, "input_tokens", None, None),
    ];
    assert_eq!(provider_call_count(&events), 2);
}

#[test]
fn usage_summary_tracks_python_contract() {
    let events = vec![
        event("token", 120.0, "input_tokens", Some("call:1"), None),
        event("token", 400.0, "cached_input_tokens", Some("call:1"), None),
        event("token", 35.0, "output_tokens", Some("call:1"), None),
        event("cost", 0.0042, "total_cost", Some("call:1"), None),
        event("time", 12.0, "ms", Some("call:1"), None),
    ];
    let summary = summarize_budget_events(&events);
    assert_eq!(summary.input_tokens, 120);
    assert_eq!(summary.cached_input_tokens, 400);
    assert_eq!(summary.output_tokens, 35);
    assert_eq!(summary.total_tokens, 155);
    assert_eq!(summary.total_cost, 0.0042);
    assert_eq!(summary.time_ms, 12);
    assert_eq!(summary.event_count, 5);
    assert_eq!(summary.by_unit.get("input_tokens"), Some(&1));
}

#[test]
fn provider_summary_and_cost_status_match_python() {
    let settings = WorkflowProviderSettings {
        proposal_mode: Some("children".to_owned()),
        parser: ProviderEndpointConfig {
            provider: "azure".to_owned(),
            model: "gpt-5-mini".to_owned(),
            temperature: 0.1,
            ..ProviderEndpointConfig::default()
        },
    };
    let no_cost = vec![event("token", 100.0, "input_tokens", Some("call:1"), None)];
    let summary = summarize_provider_budget_events(&no_cost, &settings);
    assert_eq!(summary["total_cost"], Value::Null);
    assert_eq!(summary["cost_status"], json!("unavailable_missing_tokens"));
    assert_eq!(summary["cost_source"], Value::Null);
    assert_eq!(summary["provider"], json!("azure"));
    assert_eq!(summary["model"], json!("gpt-5-mini"));
    assert_eq!(summary["input_tokens"], json!(100));

    let reported = vec![event(
        "cost",
        0.01,
        "total_cost",
        Some("call:1"),
        Some(json!({"cost_status": "estimated", "cost_source": "rate-card"})),
    )];
    let summary = summarize_provider_budget_events(&reported, &settings);
    assert_eq!(summary["cost_status"], json!("estimated"));
    assert_eq!(summary["cost_source"], json!(["rate-card"]));

    let provider = vec![event("cost", 0.02, "total_cost", Some("call:1"), None)];
    let summary = summarize_provider_budget_events(&provider, &settings);
    assert_eq!(summary["cost_status"], json!("provider_reported"));
    assert_eq!(summary["cost_source"], json!("provider"));
}
