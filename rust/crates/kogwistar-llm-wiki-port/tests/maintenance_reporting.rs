use kogwistar_llm_wiki_port::maintenance_reporting::summarize_maintenance_costs;
use serde_json::{json, Value};

#[test]
fn summarize_costs_groups_by_kind_and_model() {
    let events: Vec<Value> = vec![
        json!({
            "run_id": "run-1",
            "source": "runtime",
            "kind": "token",
            "amount": 10,
            "unit": "input_tokens",
            "meta": {
                "maintenance_kind": "document_propose_crosslinks",
                "model": "gpt-5-nano"
            }
        }),
        json!({
            "run_id": "run-1",
            "source": "runtime",
            "kind": "token",
            "amount": 5,
            "unit": "output_tokens",
            "meta": {
                "maintenance_kind": "document_propose_crosslinks",
                "model": "gpt-5-nano"
            }
        }),
        json!({
            "run_id": "run-1",
            "source": "runtime",
            "kind": "cost",
            "amount": 0.12,
            "unit": "total_cost",
            "meta": {
                "maintenance_kind": "document_propose_crosslinks",
                "model": "gpt-5-nano"
            }
        }),
        json!({
            "run_id": "run-2",
            "source": "runtime",
            "kind": "token",
            "amount": 7,
            "unit": "total_tokens",
            "meta": {
                "maintenance_kind": "graph_patch_apply",
                "model": "local"
            }
        }),
    ];
    let summary = summarize_maintenance_costs(&events, "unknown", "unknown");
    assert_eq!(
        summary["document_propose_crosslinks|gpt-5-nano"]["input_tokens"],
        json!(10)
    );
    assert_eq!(
        summary["document_propose_crosslinks|gpt-5-nano"]["output_tokens"],
        json!(5)
    );
    assert_eq!(
        summary["document_propose_crosslinks|gpt-5-nano"]["total_tokens"],
        json!(15)
    );
    assert_eq!(
        summary["document_propose_crosslinks|gpt-5-nano"]["total_cost"],
        json!(0.12)
    );
    assert_eq!(summary["graph_patch_apply|local"]["total_tokens"], json!(7));
}
