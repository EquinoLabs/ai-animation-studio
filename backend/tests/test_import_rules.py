"""Automated test enforcing backend architectural boundaries via AST parsing.

Rules enforced (TechSpec Section 3):
1. Services/* may import domain, config, and llm; they must never import api or cli.
2. API and CLI call services/pipeline and nothing deeper.
3. Renderer and characters never import llm, scriptwriter, or planner.
4. No module may import or depend on ai-video-clipper.
"""

import ast
from pathlib import Path
from typing import List, NamedTuple, Set


class ImportViolation(NamedTuple):
    file_path: Path
    line_number: int
    imported_module: str
    rule_broken: str


def get_module_for_file(file_path: Path, app_root: Path) -> str:
    """Compute dotted module name relative to the parent of app_root."""
    rel = file_path.relative_to(app_root.parent)
    parts = list(rel.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def resolve_import(source_module: str, node: ast.AST) -> List[str]:
    """Extract full imported module paths from an ast.Import or ast.ImportFrom node."""
    imported_modules: List[str] = []
    if isinstance(node, ast.Import):
        for alias in node.names:
            imported_modules.append(alias.name)
    elif isinstance(node, ast.ImportFrom):
        base_mod = node.module or ""
        if node.level > 0:
            # Relative import resolution
            pkg_parts = source_module.split(".")
            # If the current module is not a package, strip the file module name
            if len(pkg_parts) >= node.level:
                prefix_parts = pkg_parts[: len(pkg_parts) - node.level]
                if base_mod:
                    prefix_parts.append(base_mod)
                resolved = ".".join(prefix_parts)
                imported_modules.append(resolved)
            else:
                imported_modules.append(base_mod)
        else:
            imported_modules.append(base_mod)
            for alias in node.names:
                imported_modules.append(f"{base_mod}.{alias.name}")
    return imported_modules


def check_ast_for_violations(
    tree: ast.AST, source_module: str, file_path: Path
) -> List[ImportViolation]:
    """Check an AST against architectural boundary rules."""
    violations: List[ImportViolation] = []

    for node in ast.walk(tree):
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue

        lineno = getattr(node, "lineno", 1)
        imported_targets = resolve_import(source_module, node)

        for target in imported_targets:
            # 1. Prohibition on clipper imports
            if any(target == clip or target.startswith(f"{clip}.") for clip in ["clipper", "ai_video_clipper"]):
                violations.append(
                    ImportViolation(
                        file_path=file_path,
                        line_number=lineno,
                        imported_module=target,
                        rule_broken="Forbidden import: dependency on clipper repository is prohibited.",
                    )
                )

            # 2. Services must never import API or CLI
            if ".services." in source_module or source_module.endswith(".services"):
                if any(
                    target.startswith(prefix)
                    for prefix in [
                        "backend.app.api",
                        "app.api",
                        "api",
                        "backend.app.cli",
                        "app.cli",
                        "cli",
                    ]
                ):
                    violations.append(
                        ImportViolation(
                            file_path=file_path,
                            line_number=lineno,
                            imported_module=target,
                            rule_broken="Services layer must never import from api or cli.",
                        )
                    )

            # 3. API and CLI must only access services/pipeline and nothing deeper
            if any(source_module.startswith(p) for p in ["backend.app.api", "app.api", "backend.app.cli", "app.cli"]):
                forbidden_services = [
                    "backend.app.services.scriptwriter",
                    "app.services.scriptwriter",
                    "services.scriptwriter",
                    "backend.app.services.planner",
                    "app.services.planner",
                    "services.planner",
                    "backend.app.services.assets",
                    "app.services.assets",
                    "services.assets",
                    "backend.app.services.tts",
                    "app.services.tts",
                    "services.tts",
                    "backend.app.services.characters",
                    "app.services.characters",
                    "services.characters",
                    "backend.app.services.renderer",
                    "app.services.renderer",
                    "services.renderer",
                ]
                if any(target.startswith(fs) for fs in forbidden_services):
                    violations.append(
                        ImportViolation(
                            file_path=file_path,
                            line_number=lineno,
                            imported_module=target,
                            rule_broken="API and CLI must call services/pipeline and nothing deeper.",
                        )
                    )

            # 4. Renderer and Characters must never import LLM, Scriptwriter, or Planner
            if any(
                part in source_module
                for part in [".services.renderer", ".services.characters"]
            ):
                forbidden_ai = [
                    "backend.app.llm",
                    "app.llm",
                    "llm",
                    "backend.app.services.scriptwriter",
                    "app.services.scriptwriter",
                    "services.scriptwriter",
                    "backend.app.services.planner",
                    "app.services.planner",
                    "services.planner",
                ]
                if any(target.startswith(fa) for fa in forbidden_ai):
                    violations.append(
                        ImportViolation(
                            file_path=file_path,
                            line_number=lineno,
                            imported_module=target,
                            rule_broken="Renderer and Characters must remain independent of AI/LLM modules.",
                        )
                    )

    return violations


def scan_backend_tree(app_root: Path) -> List[ImportViolation]:
    """Scan all Python files under app_root for boundary violations."""
    all_violations: List[ImportViolation] = []
    for py_file in app_root.rglob("*.py"):
        if ".venv" in py_file.parts or "__pycache__" in py_file.parts:
            continue
        source_module = get_module_for_file(py_file, app_root)
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
            violations = check_ast_for_violations(tree, source_module, py_file)
            all_violations.extend(violations)
        except SyntaxError as e:
            all_violations.append(
                ImportViolation(
                    file_path=py_file,
                    line_number=e.lineno or 1,
                    imported_module="<syntax-error>",
                    rule_broken=f"Syntax error parsing file: {e}",
                )
            )
    return all_violations


def test_no_architectural_import_violations():
    """Verify that the codebase complies strictly with TechSpec import rules."""
    repo_root = Path(__file__).resolve().parent.parent.parent
    app_root = repo_root / "backend" / "app"
    assert app_root.exists(), f"App root {app_root} not found"

    violations = scan_backend_tree(app_root)
    assert not violations, (
        f"Found {len(violations)} architectural boundary violation(s):\n"
        + "\n".join(
            f"  - {v.file_path}:{v.line_number} -> {v.imported_module} ({v.rule_broken})"
            for v in violations
        )
    )


def test_import_checker_detects_forbidden_imports():
    """Demonstrate that the checker reliably catches forbidden dependencies."""
    dummy_path = Path("fake_path.py")

    # Test 1: Service importing API
    code1 = "from backend.app.api import routes"
    tree1 = ast.parse(code1)
    violations1 = check_ast_for_violations(
        tree1, "backend.app.services.renderer.encoder", dummy_path
    )
    assert any("must never import from api or cli" in v.rule_broken for v in violations1)

    # Test 2: Renderer importing LLM
    code2 = "import backend.app.llm.base as llm_base"
    tree2 = ast.parse(code2)
    violations2 = check_ast_for_violations(
        tree2, "backend.app.services.renderer.encoder", dummy_path
    )
    assert any("independent of AI/LLM modules" in v.rule_broken for v in violations2)

    # Test 3: API importing deep service directly
    code3 = "from backend.app.services.scriptwriter import writer"
    tree3 = ast.parse(code3)
    violations3 = check_ast_for_violations(tree3, "backend.app.api.routes", dummy_path)
    assert any("must call services/pipeline and nothing deeper" in v.rule_broken for v in violations3)

    # Test 4: Forbidden clipper dependency
    code4 = "import ai_video_clipper.common"
    tree4 = ast.parse(code4)
    violations4 = check_ast_for_violations(tree4, "backend.app.domain.contracts", dummy_path)
    assert any("clipper repository is prohibited" in v.rule_broken for v in violations4)
