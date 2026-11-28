use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyModule;
use serde_json::Value;
use std::collections::BTreeMap;

use kogwistar_llm_wiki_port::configuration::workspace::namespace_matches_graph_space_metadata;
use kogwistar_llm_wiki_port::ids::stable_id;
use kogwistar_llm_wiki_port::parse_quality::basic_sense_eval_value;
use kogwistar_llm_wiki_port::provenance::{
    evidence_pack_digest_hash as provenance_evidence_pack_digest_hash,
    python_ascii_json,
};

fn parse_json(payload_json: &str) -> PyResult<Value> {
    serde_json::from_str(payload_json)
        .map_err(|error| PyValueError::new_err(error.to_string()))
}

#[pyfunction]
fn stable_id_json(payload_json: &str) -> PyResult<String> {
    let values = parse_json(payload_json)?;
    let Some(values) = values.as_array() else {
        return Err(PyValueError::new_err("stable ID payload must be a JSON array"));
    };
    let Some(kind) = values.first().and_then(Value::as_str) else {
        return Err(PyValueError::new_err("stable ID kind must be a string"));
    };
    let parts = values
        .iter()
        .skip(1)
        .map(|value| match value {
            Value::String(value) => value.clone(),
            value => value.to_string(),
        })
        .collect::<Vec<_>>();
    Ok(stable_id(kind, &parts.iter().map(String::as_str).collect::<Vec<_>>()).to_string())
}

#[pyfunction]
fn canonical_json(payload_json: &str) -> PyResult<String> {
    let value = parse_json(payload_json)?;
    Ok(python_ascii_json(&value))
}

#[pyfunction]
fn evidence_pack_digest_hash(payload_json: &str) -> PyResult<String> {
    let value = parse_json(payload_json)?;
    let Some(Value::Object(object)) = Some(value) else {
        return Err(PyValueError::new_err("evidence pack payload must be an object"));
    };
    let digest: BTreeMap<String, Value> = object.into_iter().collect();
    Ok(provenance_evidence_pack_digest_hash(&digest))
}

#[pyfunction]
fn basic_sense_eval(payload_json: &str) -> PyResult<String> {
    let value = parse_json(payload_json)?;
    let graph_payload = value.get("graph_payload").cloned().unwrap_or(Value::Null);
    let diagnostics = value.get("diagnostics").cloned().unwrap_or(Value::Null);
    serde_json::to_string(&basic_sense_eval_value(&graph_payload, &diagnostics))
        .map_err(|error| PyValueError::new_err(error.to_string()))
}

#[pyfunction]
fn graph_space_match(payload_json: &str) -> PyResult<bool> {
    let value = parse_json(payload_json)?;
    let namespace = value
        .get("namespace")
        .and_then(Value::as_str)
        .ok_or_else(|| PyValueError::new_err("payload must include a namespace string"))?;
    let metadata = value
        .get("metadata")
        .and_then(Value::as_object)
        .ok_or_else(|| PyValueError::new_err("payload must include a metadata object"))?;
    let metadata: BTreeMap<String, String> = metadata
        .iter()
        .filter_map(|(key, value)| value.as_str().map(|s| (key.clone(), s.to_owned())))
        .collect();
    Ok(namespace_matches_graph_space_metadata(namespace, &metadata))
}

#[pymodule]
fn _kogwistar_llm_wiki_rust(_py: Python<'_>, module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add("__version__", env!("CARGO_PKG_VERSION"))?;
    module.add_function(wrap_pyfunction!(stable_id_json, module)?)?;
    module.add_function(wrap_pyfunction!(canonical_json, module)?)?;
    module.add_function(wrap_pyfunction!(evidence_pack_digest_hash, module)?)?;
    module.add_function(wrap_pyfunction!(basic_sense_eval, module)?)?;
    module.add_function(wrap_pyfunction!(graph_space_match, module)?)?;
    Ok(())
}
