# Feature: Ingestion & Proxy Engine

> Status: Draft | Last verified: 2026-08-17

## What it does

Validates the `/ingest` directory contents, extracts a compact audio proxy, and generates a 360p 1-FPS visual proxy suitable for Gemini's context window.

## Where the code will live

*To be added as modules are implemented.*

## Keys & config

* `INGEST_DIR` — source assets directory.
* `OUTPUT_DIR/proxies/` — generated proxy files.

## Gotchas

* Token budget must be estimated before cloud upload.
* Visual proxy timecode must map exactly back to the original source.
