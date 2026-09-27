# PyInstaller spec for the `directorcut` CLI (one-file console binary).
#
# Build from the repo root, in an env where the package + pyinstaller are
# installed:
#
#   pip install .
#   pip install pyinstaller
#   pyinstaller directorcut.spec
#
# Deliberately NOT bundled:
#   * ffmpeg / ffprobe - they stay external on PATH (or FFMPEG_PATH /
#     FFPROBE_PATH); directorcut.ffmpeg._find_binary resolves them at
#     runtime. Bundling them would triple the binary size and licensing is
#     murkier - the `setup` wizard + docs tell the user to install them.
#   * faster-whisper and its native stack (ctranslate2 / onnxruntime / av /
#     tokenizers / huggingface_hub) - the local STT backend is a
#     source-install feature (`pip install directorcut-ai[local-stt]`) and
#     would add ~1GB to the binary. To ship a whisper-capable binary, remove
#     the excludes below after `pip install faster-whisper` and rebuild.
#
# The `directorcut setup` wizard + user config work identically frozen:
# keys still land in %APPDATA%/DirectorCut/config.env, never next to the exe.

from PyInstaller.utils.hooks import collect_submodules

# directorcut.* is collected wholesale: cli commands import their stage
# modules lazily and uvicorn loads "directorcut.api.main:app" by string, so
# modulegraph alone misses them. uvicorn.* is likewise dynamic (loop /
# protocol / lifespan implementations).
hiddenimports = collect_submodules("directorcut") + collect_submodules("uvicorn")

a = Analysis(
    ["src/directorcut/cli.py"],
    pathex=["src"],
    binaries=[],
    # Ship the docs inside the bundle (sys._MEIPASS/docs) - the packaging
    # ticket requires docs/ to travel with the binary.
    datas=[("docs", "docs")],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "faster_whisper",
        "ctranslate2",
        "onnxruntime",
        "tokenizers",
        "huggingface_hub",
        "av",
        "torch",
        "torchaudio",
        "transformers",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="directorcut",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX binaries trip some AV heuristics; size is already sane.
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
