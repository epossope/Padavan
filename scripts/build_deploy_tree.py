"""Build the exact, allowlisted source tree used by the Amvera deploy branch."""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_FILES = (
    "amvera.yml", "requirements.txt",
    "artifact_service.py", "bot.py", "diagnostics.py", "ingestion.py",
    "knowledge_store.py", "media_cache.py", "miniapp_api.py", "model_router.py",
    "retrieval.py", "streaming_runtime.py", "telegram_renderer.py", "url_enricher.py",
    # Imported by bot.py now (entity resolver) or lazily by the unchanged
    # default-off semantic/shadow paths.  They must be present even while the
    # production semantic runtime is disabled.
    "entity_resolver.py", "semantic_core.py", "semantic_planner.py",
    "plan_runtime.py", "grounded_response.py", "semantic_orchestrator.py",
    "semantic_runtime.py",
    "miniapp/index.html", "miniapp/tokens.js", "miniapp/ui.js", "miniapp/orb.js",
    "miniapp/app.js", "miniapp/screens.js", "miniapp/mobile.js", "miniapp/voice-conversation.js",
    "miniapp/style.css", "miniapp/mobile.css", "miniapp/refinement.css", "miniapp/design-match.css",
    "miniapp/EAGENT_DESIGN_TOKENS.json",
    "miniapp/fonts/sans-light.ttf", "miniapp/fonts/sans-regular.ttf",
    "miniapp/fonts/mono-light.ttf", "miniapp/fonts/mono-regular.ttf",
    "miniapp/models/vosk-model-small-ru-0.22.tar.gz",
)
FORBIDDEN_PARTS = {".env", ".venv", "storage", "data", "runtime_logs", "__pycache__", ".git"}


def build(output: Path) -> tuple[int, int]:
    output = output.resolve()
    if output == ROOT:
        raise ValueError("output_must_not_be_the_repository_root")
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)
    total = 0
    for relative in RUNTIME_FILES:
        source = ROOT / relative
        if not source.is_file():
            raise FileNotFoundError(f"required_runtime_file_missing:{relative}")
        if any(part in FORBIDDEN_PARTS for part in Path(relative).parts):
            raise ValueError(f"forbidden_runtime_path:{relative}")
        target = output / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        total += target.stat().st_size
    return len(RUNTIME_FILES), total


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    count, total = build(args.output)
    print(f"DEPLOY_FILES={count}")
    print(f"DEPLOY_SIZE_BYTES={total}")


if __name__ == "__main__":
    main()
