"""Secrets hygiene: log redaction + repo scan guard.

Fake keys are assembled at runtime (never as literals) so this test file
itself passes the repo scan it enforces - and so no test ever embeds a real
.env value.
"""

import logging
from pathlib import Path

from directorcut.log import _mask, redact, register_secret, setup_logging
from directorcut.safety.secrets import scan_file, scan_repo

REPO_ROOT = Path(__file__).resolve().parents[1]

# Google API key shape: "AIzaSy" + 33 chars; sk- style: "sk-" + 20+ chars.
FAKE_GOOGLE_KEY = "AIzaSy" + "X9_" * 11
FAKE_SK_KEY = "sk-" + "a1" * 15


def test_repo_has_no_committed_secrets():
    findings = scan_repo(REPO_ROOT)
    # Findings already carry masked matches - safe to print on failure.
    assert findings == []


def test_redact_masks_google_style_key():
    line = f"GET https://x.test/?key={FAKE_GOOGLE_KEY} failed"
    out = redact(line)
    assert FAKE_GOOGLE_KEY not in out
    assert "***" in out


def test_redact_masks_sk_style_key():
    line = f"Authorization: Bearer {FAKE_SK_KEY}"
    out = redact(line)
    assert FAKE_SK_KEY not in out


def test_redact_masks_registered_secret():
    secret = "el_test_" + "z" * 24
    register_secret(secret)
    try:
        out = redact(f"using key {secret} now")
        assert secret not in out
        assert "***" in out
    finally:
        from directorcut.log import _registered_secrets

        _registered_secrets.discard(secret)


def test_redact_leaves_normal_text_alone():
    assert redact("nothing secret here") == "nothing secret here"


def test_log_output_is_redacted(tmp_path, capsys):
    secret = FAKE_GOOGLE_KEY  # shaped string - masked even unregistered
    setup_logging("INFO", secrets=[FAKE_SK_KEY])
    logging.getLogger("directorcut.test").info("key=%s and %s", secret, FAKE_SK_KEY)
    out = capsys.readouterr().out
    assert FAKE_GOOGLE_KEY not in out
    assert FAKE_SK_KEY not in out
    assert "***" in out


def test_mask_short_and_long():
    assert _mask("tiny") == "***"
    masked = _mask(FAKE_GOOGLE_KEY)
    assert masked.startswith(FAKE_GOOGLE_KEY[:4])
    assert masked.endswith(FAKE_GOOGLE_KEY[-4:])
    assert "***" in masked


def test_scan_flags_key_in_source_file(tmp_path):
    f = tmp_path / "leak.py"
    f.write_text(f'KEY = "{FAKE_GOOGLE_KEY}"\n', encoding="utf-8")
    findings = scan_file(f)
    assert len(findings) == 1
    assert findings[0].pattern == "google_api_key"
    assert FAKE_GOOGLE_KEY not in findings[0].match  # masked


def test_scan_skips_env_and_tasks(tmp_path):
    env = tmp_path / ".env"
    env.write_text(f"GEMINI_API_KEY={FAKE_GOOGLE_KEY}\n", encoding="utf-8")
    tasks = tmp_path / "tasks" / "scratch"
    tasks.mkdir(parents=True)
    (tasks / "debug.py").write_text(f'k = "{FAKE_SK_KEY}"\n', encoding="utf-8")
    example = tmp_path / ".env.example"
    example.write_text(f"GEMINI_API_KEY={FAKE_GOOGLE_KEY}\n", encoding="utf-8")

    assert scan_repo(tmp_path) == []


def test_scan_skips_binary_files(tmp_path):
    f = tmp_path / "blob.bin"
    f.write_bytes(b"\xff\xd8\xff" + FAKE_GOOGLE_KEY.encode())
    assert scan_repo(tmp_path) == []
