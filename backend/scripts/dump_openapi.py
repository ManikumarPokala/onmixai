"""Dump the FastAPI OpenAPI schema to frontend/openapi.json (the snapshot the typed client
is generated from). Deterministic (sorted keys, stable indent) so CI can assert the committed
snapshot matches the live app — a backend schema change without a regenerated snapshot fails.

Usage: python -m scripts.dump_openapi   (from backend/)
"""

import json
from pathlib import Path

from src.main import create_app

_OUTPUT = Path(__file__).resolve().parents[2] / "frontend" / "openapi.json"


def main() -> None:
    schema = create_app().openapi()
    _OUTPUT.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n")
    print(f"wrote {_OUTPUT} ({len(schema['paths'])} paths)")


if __name__ == "__main__":
    main()
