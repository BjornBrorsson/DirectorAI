# Feature: Pass 2 — Visual Structural Logger

> Status: Draft | Last verified: 2026-08-17

## What it does

Analyzes the 1-FPS visual proxy with Gemini to detect gameplay state events: `STATE_UI`, `STATE_ACTION`, `STATE_DEATH`, `STATE_TUTORIAL`.

## Where the code will live

*To be added as modules are implemented.*

## Keys & config

* `GEMINI_API_KEY`, `GEMINI_MODEL`.

## Gotchas

* Long videos must be chunked; events at chunk boundaries may need overlap handling.
