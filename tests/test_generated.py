from __future__ import annotations

import inspect
import json
import re
from pathlib import Path
from typing import Any, Dict

from ampost import OPERATIONS, Ampost, AsyncAmpost, __version__

ROOT = Path(__file__).resolve().parent.parent
SPEC: Dict[str, Any] = json.loads((ROOT / "openapi.json").read_text(encoding="utf-8"))
HTTP_METHODS = {"get", "post", "put", "patch", "delete"}


def spec_operations() -> Dict[str, Any]:
    return {
        op["operationId"]: op
        for path in SPEC["paths"].values()
        for method, op in path.items()
        if method in HTTP_METHODS
    }


def test_every_operation_has_a_sync_and_an_async_method() -> None:
    assert sorted(o["operation_id"] for o in OPERATIONS) == sorted(spec_operations())
    sync, async_ = Ampost(api_key="k"), AsyncAmpost(api_key="k")
    for op in OPERATIONS:
        assert callable(getattr(getattr(sync, op["resource"]), op["method"])), op
        assert inspect.iscoroutinefunction(getattr(getattr(async_, op["resource"]), op["method"])) or op["paginated"], op


def test_marks_exactly_the_operations_that_take_an_idempotency_key() -> None:
    expected = {
        op_id
        for op_id, op in spec_operations().items()
        if any(p.get("name") == "Idempotency-Key" for p in op.get("parameters", []))
    }
    assert {o["operation_id"] for o in OPERATIONS if o["idempotent"]} == expected


def test_version_matches_pyproject() -> None:
    match = re.search(r'^version = "([^"]+)"', (ROOT / "pyproject.toml").read_text(encoding="utf-8"), re.M)
    assert match and match.group(1) == __version__
