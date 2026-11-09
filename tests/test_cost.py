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


def cost(call: Callable[[], Any], *, times: int = 1) -> float:
    best = float("inf")
    for _ in range(3):
        start = time.process_time()
        for _ in range(times):
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


def llm_output() -> dict[str, Any]:
    return {
        "model": "m",
        "prompt": "p",
        "response_data": "{}",
        "score": 0.95,
        "generation_params": {
            "temperature": 0.5,
            "max_tokens": 1000,
            "seed": "18446744073709551615",
            "response_format": {"type": "json_object"},
        },
        "generation_metadata": {
            "created": "2025-09-17T11:45:00Z",
            "usage": {"prompt_tokens": 820, "completion_tokens": 45, "total_tokens": 865},
        },
        "attributes": {f"a{index}": index + 0.5 for index in range(16)},
    }


RECORDS: dict[str, tuple[oatschema.SchemaName, Callable[[], Any], int, int]] = {
    "polygons": ("document-extraction", lambda: polygons(400_000), 4, 1),
    "labels": (
        "classification",
        lambda: {"labels": [{"label": f"l{i % 50}", "score": i / 10_000} for i in range(10_000)]},
        4,
        1,
    ),
    "embedding": ("embedding", lambda: {"vector": [(index % 1000) / 1000 for index in range(16_384)]}, 4, 1),
    "generic": ("generic", lambda: {"data": {f"key{index}": index * 0.5 for index in range(128)}}, 4, 100),
    "llm-output": ("llm-output", llm_output, 4, 1000),
    "regression": (
        "regression",
        lambda: {
            "target": "t",
            "points": [
                {
                    "value": i * 0.5,
                    "timestamp": "2025-10-12T12:00:00Z",
                    "statistic": "mean",
                    "interval_lower": i * 0.5 - 1,
                    "interval_upper": i * 0.5 + 1,
                    "score": 0.98,
                }
                for i in range(10_000)
            ],
        },
        4,
        1,
    ),
    "segments": (
        "audio-transcription",
        lambda: {
            "segments": [
                {
                    "text": f"w{i}",
                    "start_time": i * 0.5,
                    "end_time": i * 0.5 + 0.25,
                    "speaker": {"id": f"s{i % 3}", "name": "A"},
                    "events": ["music"],
                    "score": 0.98,
                }
                for i in range(10_000)
            ]
        },
        5,
        1,
    ),
    "results": (
        "ranking",
        lambda: {
            "target": "relevance",
            "results": [
                {"rank": i + 1, "id": f"d{i}", "score": 1 - i / 20_000, "attributes": {"distance": i % 64}}
                for i in range(10_000)
            ],
        },
        5,
        1,
    ),
    "scenes": (
        "temporal-segmentation",
        lambda: {
            "segments": [
                {
                    "label": "scene",
                    "start_time": i * 4.0 + 0.5,
                    "end_time": i * 4.0 + 3.75,
                    "attributes": {"start_frame": i * 90, "end_frame": i * 90 + 89},
                }
                for i in range(10_000)
            ]
        },
        5,
        1,
    ),
}


@pytest.mark.parametrize("shape", RECORDS)
def test_check_numbers_on_a_valid_record_costs_at_most_its_bound_times_is_valid(shape: str) -> None:
    name, build, bound, times = RECORDS[shape]
    record = build()
    assert is_valid(record, name)
    result = check_numbers(record, name)
    assert not result.converted and not result.dropped
    assert cost(lambda: check_numbers(record, name), times=times) <= bound * cost(
        lambda: is_valid(record, name), times=times
    )


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
