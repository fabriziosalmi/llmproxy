use pyo3::prelude::*;
use regex::Regex;
use std::sync::OnceLock;

static PII_REGEX: OnceLock<Regex> = OnceLock::new();

#[pyfunction]
fn redact_pii(text: &str) -> PyResult<String> {
    let re = PII_REGEX.get_or_init(|| {
        Regex::new(r"(?x)
            [a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+ |
            \b\d{3}-\d{2}-\d{4}\b |
            \b(?:\d[\s-]*?){13,16}\b |
            \b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b
        ").unwrap()
    });
    
    let redacted = re.replace_all(text, "[REDACTED_PII]");
    Ok(redacted.to_string())
}

#[pymodule]
fn pii_redactor(_py: Python, m: &PyModule) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(redact_pii, m)?)?;
    Ok(())
}
