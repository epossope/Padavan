"""Private, bounded derivatives for Mini App media previews.

Only authenticated handlers resolve files into this cache.  The cache contains
no user-controlled name in its path and is never served as a static directory.
"""
from __future__ import annotations

import hashlib
from pathlib import Path


MAX_EDGE = 480


def thumbnail_version(source: str | Path) -> str:
    """A non-sensitive derivative key that changes with the source bytes."""
    path = Path(source)
    stat = path.stat()
    return hashlib.sha256(f"{stat.st_size}:{stat.st_mtime_ns}".encode()).hexdigest()[:16]


def image_thumbnail(source: str | Path, cache_root: str | Path) -> tuple[Path | None, bool]:
    """Return ``(path, cache_hit)`` without letting image errors escape callers."""
    try:
        source_path = Path(source)
        version = thumbnail_version(source_path)
        target_dir = Path(cache_root)
        target = target_dir / f"{version}.webp"
        if target.is_file():
            return target, True
        # Pillow is deliberately imported lazily: a missing optional image
        # codec must not break state, chat, or ordinary file downloads.
        from PIL import Image, ImageOps
        target_dir.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".tmp")
        with Image.open(source_path) as image:
            image = ImageOps.exif_transpose(image)
            image.thumbnail((MAX_EDGE, MAX_EDGE), Image.Resampling.LANCZOS)
            if image.mode not in {"RGB", "RGBA"}:
                image = image.convert("RGBA" if "transparency" in image.info else "RGB")
            image.save(temporary, "WEBP", quality=78, method=4)
        temporary.replace(target)
        return target, False
    except Exception:
        return None, False


def remove_thumbnail(source: str | Path, cache_root: str | Path) -> None:
    """Best-effort invalidation for a deleted original."""
    try:
        (Path(cache_root) / f"{thumbnail_version(source)}.webp").unlink(missing_ok=True)
    except OSError:
        pass
