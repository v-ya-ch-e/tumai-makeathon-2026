# CLAUDE.md

## Project context

This repository is our **TUM.ai Makeathon 2026** submission for Reply's [*The Campus Co-Pilot Suite*](context/CHALLENGE_BRIEF.md) challenge: build an autonomous AI agent (or multi-agent system) that takes concrete actions across TUM's fragmented digital ecosystem (TUMonline, Moodle, ZHS, Mensa, Matrix, Confluence, …) using AWS Bedrock. The code is a FastAPI backend under [`backend/`](backend/) ([`backend/app/main.py`](backend/app/main.py)) deployed per the recipes in [`DEPLOYMENT.md`](DEPLOYMENT.md) and [`CI-CONFIGURATION.md`](CI-CONFIGURATION.md).

### Context files (`context/`)

Read these in order when picking up the project:

- [`context/CHALLENGE_OVERVIEW.md`](context/CHALLENGE_OVERVIEW.md) — one-page orientation: sponsor, Reply room 1100, Discord, deadlines.
- [`context/CHALLENGE_BRIEF.md`](context/CHALLENGE_BRIEF.md) — **primary** — verbatim Reply challenge description from [`DataReply/makeathon`](https://github.com/DataReply/makeathon).
- [`context/TUM_SYSTEMS.md`](context/TUM_SYSTEMS.md) — APIs and scraping approaches for every TUM system the agent may touch.
- [`context/AWS_RESOURCES.md`](context/AWS_RESOURCES.md) — Bedrock models, `eu.` inference profiles, S3 / S3 Vectors, credentials, troubleshooting.
- [`context/CODE_EXAMPLES.md`](context/CODE_EXAMPLES.md) — copy-paste-ready Python + TypeScript snippets plus a FastAPI port of the reference RAG server.
- [`context/EVENT_LOGISTICS.md`](context/EVENT_LOGISTICS.md) — timetable, rooms, food, overnight rules.

## Behavioral guidelines

Guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.
