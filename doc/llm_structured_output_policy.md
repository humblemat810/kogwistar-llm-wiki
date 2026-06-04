# LLM Structured Output Policy

For production parsing paths, use provider-controlled `json_schema` first.

If the provider or model rejects the schema, fall back to `function_calling`.

Keep the LLM-facing schema closed:
- avoid `Any` in response models
- prefer explicit nested models or JSON-safe typed aliases
- move host-only trace metadata out of the structured response when possible

Why this policy exists:
- parsing is a correctness-first contract
- `json_schema` gives the strongest shape guarantees when the schema is clean
- `function_calling` is a compatibility fallback for stricter or more brittle providers

Current repo convention:
- `json_schema` first for parsing seams
- `function_calling` fallback for provider compatibility
- deterministic local validation after parse

