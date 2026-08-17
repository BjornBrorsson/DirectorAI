# Feature: CLI / Local Client

> Status: Draft | Last verified: 2026-08-17

## What it does

Provides a command-line interface to run the entire DirectorCut-AI pipeline end-to-end, with per-stage subcommands and dry-run support.

## Where the code will live

*To be added as modules are implemented.*

## Keys & config

* All settings from `.env` or env vars.

## Gotchas

* Keep CLI thin; business logic belongs in stage modules.
