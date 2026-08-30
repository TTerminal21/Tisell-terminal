"""Analytics layer.

Pure computation over data the caller supplies - nothing here talks to the
network or to DuckDB. That keeps these functions importable by the Streamlit
dashboard and the TUI alike, and testable without a running data layer.
"""
