"""API の OpenAPI（JSON）を書き出す。web/ の型（src/api/schema.ts）はここから作る（npm run gen:api）

python -m audio2chordpro.server.openapi web/src/api/openapi.json
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from .app import create_app


def schema() -> dict:
    with tempfile.TemporaryDirectory() as root:
        app = create_app(root)
        try:
            return app.openapi()
        finally:
            app.state.runner.close()


if __name__ == "__main__":
    text = json.dumps(schema(), ensure_ascii=False, indent=1) + "\n"
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
