use std::collections::BTreeMap;

use kogwistar_llm_wiki_port::models::JsonObject;
use kogwistar_llm_wiki_port::providers::ProviderEndpointConfig;
use kogwistar_llm_wiki_port::usage::{
    budget_event_from_dict, budget_event_to_dict, estimate_token_cost_usd, extract_provider_usage,
    generic_usage_adapter, provider_call_count, resolve_token_pricing, stable_event_id,
    summarize_budget_events, summarize_provider_budget_events, BudgetAttribution, BudgetError,
    BudgetEvent, ProviderUsageCallback, StateBackedBudgetLedger, TokenPricing,
    WorkflowProviderSettings,
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

fn map(value: serde_json::Value) -> JsonObject {
    value
        .as_object()
        .expect("JSON object")
        .iter()
        .map(|(key, value)| (key.clone(), value.clone()))
        .collect()
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

#[test]
fn token_pricing_resolver_matches_python_contract() {
    let env = BTreeMap::from([
        (
            "KOGWISTAR_LONGRUN_PARSER_INPUT_COST_PER_1K_TOKENS".to_owned(),
            "0.003".to_owned(),
        ),
        (
            "KOGWISTAR_LONGRUN_PARSER_OUTPUT_COST_PER_1K_TOKENS".to_owned(),
            "0.009".to_owned(),
        ),
    ]);
    let pricing = resolve_token_pricing("azure", "gpt-5-mini", &env).unwrap();
    assert_eq!(pricing.input_per_1k, Some(0.003));
    assert_eq!(pricing.output_per_1k, Some(0.009));
    assert_eq!(
        pricing.source,
        "KOGWISTAR_LONGRUN_PARSER_INPUT_COST_PER_1K_TOKENS"
    );

    let reference = resolve_token_pricing("OpenAI", "GPT-5-MINI", &BTreeMap::new()).unwrap();
    assert_eq!(reference.input_per_1k, Some(0.00025));
    assert_eq!(reference.cached_input_per_1k, Some(0.000025));
    assert_eq!(reference.output_per_1k, Some(0.002));
    assert!(reference
        .source
        .starts_with("reference:openai-public-standard"));

    let unavailable = resolve_token_pricing("other", "model", &BTreeMap::new()).unwrap();
    assert_eq!(unavailable.source, "unavailable:other/model");
    assert!(resolve_token_pricing(
        "other",
        "model",
        &BTreeMap::from([(
            "KOGWISTAR_PARSER_INPUT_COST_PER_1K_TOKENS".to_owned(),
            "-1".to_owned()
        )])
    )
    .is_err());
}

#[test]
fn provider_usage_extraction_matches_python_aliases() {
    let response = json!({
        "llm_output": {
            "token_usage": {
                "prompt_tokens": 120,
                "completion_tokens": 35,
                "total_tokens": 155
            }
        },
        "generations": []
    });
    let usage = extract_provider_usage(&response);
    assert_eq!(usage.get("input_tokens"), Some(&120.0));
    assert_eq!(usage.get("output_tokens"), Some(&35.0));
    assert_eq!(usage.get("total_tokens"), Some(&155.0));

    let cached = json!({
        "response_metadata": {
            "token_usage": {
                "prompt_tokens": 1000,
                "completion_tokens": 100,
                "prompt_tokens_details": {"cached_tokens": 400}
            }
        },
        "generations": []
    });
    let usage = extract_provider_usage(&cached);
    assert_eq!(usage.get("cached_input_tokens"), Some(&400.0));

    let fallback = extract_provider_usage(&json!({
        "response_metadata": {"token_usage": {"prompt_tokens": 2, "completion_tokens": 3}}
    }));
    assert_eq!(fallback.get("total_tokens"), Some(&5.0));
}

#[test]
fn token_cost_estimation_matches_python() {
    let pricing = TokenPricing::new(
        Some(0.001),
        Some(0.002),
        Some(0.0001),
        Some("test-rate-card"),
    )
    .unwrap();
    assert!(pricing.configured());
    assert_eq!(
        estimate_token_cost_usd(
            &json!({
                "input_tokens": 1000,
                "cached_input_tokens": 400,
                "output_tokens": 100
            }),
            &pricing
        ),
        Some((0.00084, "estimated"))
    );

    let pricing = TokenPricing::new(Some(0.001), None, None, Some("partial")).unwrap();
    assert_eq!(
        estimate_token_cost_usd(
            &json!({"input_tokens": 1000, "output_tokens": 100}),
            &pricing
        ),
        Some((0.001, "estimated_partial"))
    );
    assert_eq!(estimate_token_cost_usd(&json!({}), &pricing), None);
}

#[test]
fn budget_ledger_tracks_state_and_events() {
    let state = map(json!({
        "token_budget": 100,
        "budget_scope": "test",
        "budget_kind": "token",
        "now_ms": 100
    }));
    let mut ledger = StateBackedBudgetLedger::new(state);
    assert_eq!(ledger.total(), 100);
    assert_eq!(ledger.remaining(), 100);
    ledger
        .debit(
            60,
            "input",
            "provider",
            "run:1",
            Some("token"),
            None,
            None,
            None,
        )
        .unwrap();
    assert_eq!(ledger.used(), 60);
    assert_eq!(ledger.remaining(), 40);
    assert_eq!(ledger.events[0].kind, "debit");
    assert_eq!(ledger.events[0].unit, "token");
    assert_eq!(ledger.events[0].scope, "test");

    assert!(matches!(
        ledger.debit(
            41,
            "input",
            "provider",
            "run:2",
            Some("token"),
            None,
            None,
            None
        ),
        Err(BudgetError::Exhausted(_))
    ));
    assert_eq!(ledger.used(), 60);
    assert_eq!(ledger.events[1].kind, "exhausted");
}

#[test]
fn budget_ledger_supports_cost_time_and_suspension() {
    let state = map(json!({
        "token_budget": 10,
        "time_budget_ms": 20,
        "step_budget": 1,
        "call_budget": 1,
        "cost_budget": 0.1,
        "budget_scope": "test"
    }));
    let mut ledger = StateBackedBudgetLedger::new(state);
    ledger.debit_step(1, None, "run:1", None).unwrap();
    ledger.debit_call(1, None, "run:1", None).unwrap();
    ledger
        .debit_time(20, "step", "runtime", "run:1", None, None, None)
        .unwrap();
    ledger
        .ingest(event("cost", 0.1, "total_cost", Some("call:1"), None))
        .unwrap();

    assert_eq!(ledger.step_used(), 1);
    assert_eq!(ledger.call_used(), 1);
    assert_eq!(ledger.time_used_ms(), 20);
    assert_eq!(ledger.cost_used(), 0.1);
    assert!(ledger.should_suspend_for_budget());
}

#[test]
fn budget_ledger_uses_trusted_ceilings() {
    let state = map(json!({"token_budget": 100, "cost_budget": 10.0}));
    let mut ledger = StateBackedBudgetLedger::new(state);
    ledger.set_ceilings(Some(&BTreeMap::from([
        ("token_budget".to_owned(), 20.0),
        ("cost_budget".to_owned(), 1.0),
    ])));

    assert_eq!(ledger.total(), 20);
    assert_eq!(ledger.cost_budget(), 1.0);
}

#[test]
fn budget_ingest_uses_python_event_kinds() {
    let mut ledger = StateBackedBudgetLedger::new(map(json!({
        "token_budget": 100,
        "time_budget_ms": 100,
        "budget_scope": "test"
    })));

    ledger
        .ingest(event("token", 20.0, "input_tokens", Some("call:1"), None))
        .unwrap();
    ledger
        .ingest(event("time", 12.0, "ms", Some("call:1"), None))
        .unwrap();
    ledger
        .ingest(event("cost", 0.01, "total_cost", Some("call:1"), None))
        .unwrap();

    assert_eq!(ledger.used(), 20);
    assert_eq!(ledger.time_used_ms(), 12);
    assert_eq!(ledger.cost_used(), 0.01);
    assert_eq!(ledger.events[0].kind, "debit");
    assert_eq!(ledger.events[1].kind, "debit");
    assert_eq!(ledger.events[2].kind, "cost");
}

#[test]
fn generic_usage_adapter_matches_python() {
    let attribution = BudgetAttribution {
        source_document_id: Some("doc:1".to_owned()),
        ..BudgetAttribution::default()
    };
    let usage = BTreeMap::from([
        ("input_tokens".to_owned(), 120.0),
        ("cached_input_tokens".to_owned(), 10.0),
        ("output_tokens".to_owned(), 35.0),
        ("total_cost".to_owned(), 0.0042),
    ]);
    let events = generic_usage_adapter(&usage, "run:1", Some("run"), Some(&attribution));

    assert_eq!(
        events
            .iter()
            .map(|event| event.unit.as_str())
            .collect::<Vec<_>>(),
        vec![
            "input_tokens",
            "cached_input_tokens",
            "output_tokens",
            "total_cost"
        ]
    );
    assert_eq!(events[0].kind, "token");
    assert_eq!(events[3].kind, "cost");
    assert_eq!(events[0].source, "generic-usage");
    assert_eq!(events[0].meta["raw_key"], json!("input_tokens"));
    assert_eq!(events[0].attribution, Some(attribution));

    let total_only = BTreeMap::from([("total_tokens".to_owned(), 15.0)]);
    let events = generic_usage_adapter(&total_only, "run:1", None, None);
    assert_eq!(
        events
            .iter()
            .map(|event| event.unit.as_str())
            .collect::<Vec<_>>(),
        vec!["total_tokens"]
    );
}

#[test]
fn provider_usage_callback_estimates_and_ingests_usage() {
    let ledger = StateBackedBudgetLedger::new(map(json!({"token_budget": 10_000})));
    let pricing =
        TokenPricing::new(Some(0.001), Some(0.002), None, Some("test-rate-card")).unwrap();
    let mut callback = ProviderUsageCallback::new(
        ledger,
        "parser:doc",
        "doc-001",
        "azure",
        "gpt-5-mini",
        Some(pricing),
    );
    let response = json!({
        "response_metadata": {
            "token_usage": {
                "prompt_tokens": 1200,
                "completion_tokens": 300,
                "total_tokens": 1500
            }
        },
        "generations": []
    });

    let emitted = callback
        .on_llm_end(&response, "provider-call-1", 123, 1000, None)
        .unwrap();

    assert_eq!(callback.ledger.used(), 1500);
    let cost_events: Vec<&BudgetEvent> = callback
        .ledger
        .events
        .iter()
        .filter(|event| event.kind == "cost")
        .collect();
    assert_eq!(cost_events.len(), 1);
    assert_eq!(cost_events[0].amount, 0.0018);
    assert_eq!(
        cost_events[0].meta["cost_status"],
        json!("estimated_from_tokens")
    );
    assert_eq!(cost_events[0].meta["cost_source"], json!("test-rate-card"));
    assert_eq!(
        cost_events[0].event_id.as_deref(),
        Some("llm-usage-3b2858abafa010c7288a563c")
    );
    assert_eq!(emitted.len(), 3);
}

#[test]
fn provider_usage_callback_is_idempotent_and_reports_missing_cost() {
    let ledger = StateBackedBudgetLedger::new(map(json!({"token_budget": 10_000})));
    let mut callback = ProviderUsageCallback::new(
        ledger,
        "parser:doc",
        "doc-001",
        "azure",
        "unknown-model",
        None,
    );
    let response = json!({"response_metadata": {}, "generations": []});

    let emitted = callback
        .on_llm_end(&response, "provider-call-1", 123, 1000, None)
        .unwrap();
    let emitted_again = callback
        .on_llm_end(&response, "provider-call-1", 124, 1100, None)
        .unwrap();

    assert_eq!(emitted.len(), 1);
    assert!(emitted_again.is_empty());
    assert_eq!(callback.ledger.used(), 0);
    let cost_events: Vec<&BudgetEvent> = callback
        .ledger
        .events
        .iter()
        .filter(|event| event.kind == "cost")
        .collect();
    assert_eq!(cost_events.len(), 1);
    assert_eq!(cost_events[0].amount, 0.0);
    assert_eq!(
        cost_events[0].meta["cost_status"],
        json!("unavailable_missing_tokens")
    );
    assert_eq!(
        cost_events[0].event_id.as_deref(),
        Some("llm-usage-2d553a0920b73babc33bcb93")
    );
}

#[test]
fn provider_usage_callback_records_error_and_time() {
    let ledger = StateBackedBudgetLedger::new(map(json!({"token_budget": 10_000})));
    let mut callback =
        ProviderUsageCallback::new(ledger, "parser:doc", "doc-001", "azure", "gpt-5-mini", None);
    callback.on_llm_start("provider-call-1", 100);
    let emitted = callback
        .on_llm_error("RuntimeError", "provider-call-1", 123, 350)
        .unwrap();

    assert!(emitted.iter().any(|event| event.unit == "ms"));
    assert!(callback
        .ledger
        .events
        .iter()
        .any(|event| event.unit == "ms"));
    assert!(callback
        .ledger
        .events
        .iter()
        .any(|event| { event.meta.get("provider_error") == Some(&json!("RuntimeError")) }));
    assert!(stable_event_id("parser:doc", "provider-call-1", "ms").starts_with("llm-usage-"));
}
