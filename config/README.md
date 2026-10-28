# Configuration

Model defaults are documented in docs/model-rules.md and embedded in the tested rules; there is no unused duplicate parameter file. Algorithm-changing requests use explicit variant names. Frozen Pyxis regime data ships as package data and must not be overwritten with guessed future states.

Portfolio policy is explicit in each request: model budgets, maximum asset weight and optional complete sector map/cap. Example uses 30% per operating model with a 10% unallocated buffer. It is a demonstration, not an optimal capital allocation result.

Optional prototype credentials use environment variables only; see .env.example. Core, examples and tests need none.
