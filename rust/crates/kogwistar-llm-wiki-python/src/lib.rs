use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyModule;
use serde_json::Value;

use kogwistar_llm_wiki_port::ids::stable_id;
use kogwistar_llm_wiki_port::parse_quality::basic_sense_eval_value;
use kogwistar_llm_wiki_port::provenance::{evidence_pack_digest_hash, python_ascii_json};

fn parse_json(payload_json: &str) -> PyResult<Value> {
    serde_json::from_str(payload_json).map_err(PyValueError::new_err)
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
    let Some(Value::Object(value)) = Some(value) else {
        return Err(PyValueError::new_err("evidence pack payload must be an object"));
    };
    let digest = value
        .iter()
        .map(|(key, value)| (key.clone(), value.clone()))
        .collect();
    Ok(evidence_pack_digest_hash(&digest))
}

#[pyfunction]
fn basic_sense_eval(payload_json: &str) -> PyResult<String> {
    let value = parse_json(payload_json)?;
    let graph_payload = value.get("graph_payload").cloned().unwrap_or(Value::Null);
    let diagnostics = value.get("diagnostics").cloned().unwrap_or(Value::Null);
    serde_json::to_string(&basic_sense_eval_value(&graph_payload, &diagnostics))
        .map_err(PyValueError::new_err)
}

#[pymodule]
fn _kogwistar_llm_wiki_rust(py: Python<'_>, module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add("__version__", env!("CARGO_PKG_VERSION"))?;
    module.add_function(wrap_pyfunction!(stable_id_json, module)?)?;
    module.add_function(wrap_pyfunction!(canonical_json, module)?)?;
    module.add_function(wrap_pyfunction!(evidence_pack_digest_hash, module)?)?;
    module.add_function(wrap_pyfunction!(basic_sense_eval, module)?)?;
    Ok(())
}
