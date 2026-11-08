# SPDX-FileCopyrightText: 2026 The OATS Authors
# SPDX-License-Identifier: Apache-2.0

import os
import time
from collections.abc import Callable
from typing import Any

import pytest

import oatschema
from oatschema import ValidationError, check_numbers, is_valid

pytestmark = pytest.mark.skipif(
    os.environ.get("OATSCHEMA_COST_TESTS") != "1", reason="cost tests run with OATSCHEMA_COST_TESTS=1"
)


def cost(call: Callable[[], Any]) -> float:
    best = float("inf")
    for _ in range(3):
        start = time.process_time()
        try:
            call()
        except ValidationError:
            pass
        best = min(best, time.process_time() - start)
    return best


def polygons(values: int) -> dict[str, Any]:
    vertices = [
        [{"x": (index % 997) + 0.5, "y": block + 0.25} for index in range(1000)] for block in range(values // 2000)
    ]
    return {"unit": "px", "blocks": [{"block_type": "polygon", "polygon": polygon} for polygon in vertices]}


RECORDS: dict[str, tuple[oatschema.SchemaName, Callable[[], Any]]] = {
    "polygons": ("document-extraction", lambda: polygons(400_000)),
    "labels": (
        "classification",
        lambda: {"labels": [{"label": f"l{i % 50}", "score": i / 10_000} for i in range(10_000)]},
    ),
    "embedding": ("embedding", lambda: {"vector": [(index % 1000) / 1000 for index in range(16_384)]}),
    "generic": ("generic", lambda: {"data": {f"key{index}": index * 0.5 for index in range(128)}}),
}


@pytest.mark.parametrize("shape", RECORDS)
def test_check_numbers_on_a_valid_record_costs_at_most_four_is_valid(shape: str) -> None:
    name, build = RECORDS[shape]
    record = build()
    assert is_valid(record, name)
    check_numbers(record, name)
    assert cost(lambda: check_numbers(record, name)) <= 4 * cost(lambda: is_valid(record, name))


def test_check_numbers_on_a_deep_record_grows_linearly() -> None:
    def record(depth: int) -> dict[str, Any]:
        nested: Any = 1
        for _ in range(depth):
            nested = [-0.0, nested]
        return {"labels": [], "attributes": {"k": nested}}

    shallow, deep = record(4_000), record(16_000)
    assert cost(lambda: check_numbers(deep, "classification")) <= 8 * cost(
        lambda: check_numbers(shallow, "classification")
    )
