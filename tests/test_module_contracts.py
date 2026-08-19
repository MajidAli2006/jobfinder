"""Cross-module references must resolve.

`profile.DEFAULT_CANDIDATE_BRIEF` did not exist, and nothing caught it: ruff
does not resolve attributes across modules, and the line only ran when the
left-hand side of an `or` was falsy — which needed a machine with no
candidate.local.json. This walks every `module.ATTR` reference in the package
and checks it against the imported module.
"""

from __future__ import annotations

import ast
import importlib
import pkgutil
import unittest
from pathlib import Path

import job_agent

PACKAGE = "job_agent"
SOURCE_ROOT = Path(job_agent.__file__).parent


def _local_modules() -> dict[str, object]:
    loaded = {PACKAGE: job_agent}
    for info in pkgutil.walk_packages(job_agent.__path__, PACKAGE + "."):
        loaded[info.name.split(".")[-1]] = importlib.import_module(info.name)
    return loaded


def _imported_aliases(tree: ast.Module, known: dict) -> dict[str, str]:
    """Local module names this file can reach, mapped to the module they name."""
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level or (node.module or "").startswith(PACKAGE):
                for alias in node.names:
                    if alias.name in known:
                        aliases[alias.asname or alias.name] = alias.name
        elif isinstance(node, ast.Import):
            for alias in node.names:
                short = alias.name.split(".")[-1]
                if alias.name.startswith(PACKAGE) and short in known:
                    aliases[alias.asname or short] = short
    return aliases


class CrossModuleAttributeTests(unittest.TestCase):

    def test_every_referenced_attribute_exists(self):
        known = _local_modules()
        missing = []

        for path in sorted(SOURCE_ROOT.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            aliases = _imported_aliases(tree, known)

            for node in ast.walk(tree):
                if not isinstance(node, ast.Attribute):
                    continue
                if not isinstance(node.value, ast.Name):
                    continue
                target = aliases.get(node.value.id)
                if target and not hasattr(known[target], node.attr):
                    missing.append(
                        f"{path.name}:{node.lineno}: {node.value.id}.{node.attr} "
                        f"is not defined in {target}.py"
                    )

        self.assertEqual(missing, [], "\n" + "\n".join(missing))


if __name__ == "__main__":
    unittest.main()
