import pytest

from directorcut.config import Settings


@pytest.fixture
def settings(tmp_path):
    return Settings(
        gemini_api_key="",
        ingest_dir=tmp_path / "ingest",
        output_dir=tmp_path / "output",
        env="test",
        log_level="DEBUG",
    )
