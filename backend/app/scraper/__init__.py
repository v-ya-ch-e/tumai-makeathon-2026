"""Standalone wg-gesucht scraper agent.

Runs in its own container, shares MySQL with the FastAPI backend, and is the
sole writer of `ListingRow` / `PhotoRow`. See ADR-018 in docs/DECISIONS.md.
"""
