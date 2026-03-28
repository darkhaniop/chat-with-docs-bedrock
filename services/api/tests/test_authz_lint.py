"""docs/07-security.md#authorization: "There is a lint test that fails if a route handler
reads a path parameter named `*Id` without a preceding `require_*` call." The business logic
for every route lives in `api.projects`/`api.documents` (docs/05-api-contracts.md), so this
walks those two modules' ASTs: any function taking a `project_id`/`document_id` parameter must
call `authz.require_project` or `authz.require_document` somewhere in its body.
"""

from __future__ import annotations

import ast
import inspect

from api import documents, projects

_ID_PARAMS = {"project_id", "document_id"}
_REQUIRE_CALLS = {"require_project", "require_document"}


def _calls_a_require_function(node: ast.FunctionDef) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            func = child.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if name in _REQUIRE_CALLS:
                return True
    return False


def _functions_missing_authz(module: object) -> list[str]:
    source = inspect.getsource(module)
    tree = ast.parse(source)
    offenders = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        param_names = {arg.arg for arg in node.args.args}
        if param_names & _ID_PARAMS and not _calls_a_require_function(node):
            offenders.append(node.name)
    return offenders


def test_every_project_or_document_scoped_function_calls_a_require_helper() -> None:
    offenders = _functions_missing_authz(projects) + _functions_missing_authz(documents)
    assert offenders == [], (
        f"functions reading project_id/document_id without a require_* call: {offenders}"
    )
