"""Install a built wheel and exercise bundled knowledge without a checkout.

Usage: python tests/wheel_knowledge_smoke.py path/to/fusion_cad_mcp.whl
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path


def main(wheel: str) -> None:
    wheel_path = Path(wheel).resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix="fusion-wheel-smoke-") as temp:
        target = Path(temp) / "installed"
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--no-index", "--no-deps", "--target", str(target), str(wheel_path)],
            check=True,
        )
        probe = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from fusion_cad_mcp.tools import knowledge as kn
assert Path(kn.__file__).resolve().is_relative_to(Path(sys.argv[1]).resolve())
kn.REPO_ROOT_HINTS = []
kn._resolve_corpus_dir = lambda: None
for name, query in [('tool', 'extrude'), ('pattern', 'constrained rectangle'), ('gotcha', 'lock badge')]:
    path = kn._resolve_skill_doc({'tool':'tools.md', 'pattern':'patterns.md', 'gotcha':'gotchas.md'}[name])
    assert path is not None and path.is_relative_to(Path(sys.argv[1]).resolve()), (name, path)
    result = getattr(kn, 'find_' + name)(query)
    assert result.ok and result.result['hits'], (name, result)
"""
        subprocess.run([sys.executable, "-I", "-c", probe, str(target)], cwd=temp, check=True)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: wheel_knowledge_smoke.py WHEEL")
    main(sys.argv[1])
