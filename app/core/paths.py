"""Repository-owned resources, independent of module nesting or working directory."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend"
MIGRATIONS = ROOT / "migrations"
