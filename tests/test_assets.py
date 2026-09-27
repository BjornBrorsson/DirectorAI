from directorcut.assets import _heuristic_role, interpret_assets
from directorcut.models import Asset, AssetRole, IngestManifest


def _manifest(tmp_path, *specs):
    return IngestManifest(
        project_name="t",
        assets=[
            Asset(path=tmp_path / name, role=role) for name, role in specs
        ],
    )


def test_interpret_assets_dev_heuristic(tmp_path, settings):
    """Dev mode resolves AUTO roles without any network calls."""
    manifest = _manifest(
        tmp_path,
        ("main.mp4", AssetRole.AUTO),
        ("extra.mp4", AssetRole.AUTO),
        ("vo.mp3", AssetRole.AUTO),
        ("still.png", AssetRole.AUTO),
    )

    out = interpret_assets(manifest, settings)
    roles = {a.path.name: a.role for a in out.assets}

    assert roles["main.mp4"] == AssetRole.A_ROLL
    assert roles["extra.mp4"] == AssetRole.B_ROLL
    assert roles["vo.mp3"] == AssetRole.VOICEOVER
    assert roles["still.png"] == AssetRole.IMAGE
    # Legacy fields track the resolved roles.
    assert out.video_path.name == "main.mp4"
    assert out.audio_path.name == "vo.mp3"


def test_interpret_assets_leaves_explicit_roles(tmp_path, settings):
    manifest = _manifest(
        tmp_path,
        ("a.mp4", AssetRole.A_ROLL),
        ("b.mp4", AssetRole.B_ROLL),
        ("bed.mp3", AssetRole.MUSIC),
    )

    out = interpret_assets(manifest, settings)
    roles = {a.path.name: a.role for a in out.assets}

    # No AUTO assets -> nothing sent to the model, roles unchanged.
    assert roles == {
        "a.mp4": AssetRole.A_ROLL,
        "b.mp4": AssetRole.B_ROLL,
        "bed.mp3": AssetRole.MUSIC,
    }


def test_heuristic_role_name_hints(tmp_path):
    music = Asset(path=tmp_path / "epic_music.mp3")
    sfx = Asset(path=tmp_path / "sfx_hit.mp3")
    assert _heuristic_role(music, has_a_roll=True) == AssetRole.MUSIC
    assert _heuristic_role(sfx, has_a_roll=True) == AssetRole.SFX
