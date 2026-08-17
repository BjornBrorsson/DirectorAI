# Feature: Safety & Budget Controls

> Status: Draft | Last verified: 2026-08-17

## What it does

Monitors token thresholds, estimates API cost, enforces structured output schemas on every Gemini call, and ensures dev mode does not send live requests unexpectedly.

## Where the code will live

*To be added as modules are implemented.*

## Keys & config

* `GEMINI_API_KEY` — required for live calls.
* `DIRECTORCUT_DEV_MODE` — when truthy, disables outbound API calls.

## Gotchas

* Always leave a safety margin below the 1M token context ceiling.
