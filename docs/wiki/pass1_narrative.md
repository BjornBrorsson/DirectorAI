# Feature: Pass 1 — Narrative Mapping Engine

> Status: Draft | Last verified: 2026-08-17

## What it does

Maps script/voiceover text to timestamps and tags each spoken segment with intent (`EXPOSITION`, `COMEDIC_SET`, `EMOTIONAL_SPIKE`, `CALLBACK_REQ`) and energy level.

## Where the code will live

*To be added as modules are implemented.*

## Keys & config

* `GEMINI_API_KEY`, `GEMINI_MODEL` — model access.

## Gotchas

* Transcription alignment needs confidence fallback when audio is noisy.
