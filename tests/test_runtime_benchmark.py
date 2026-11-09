import asyncio

import pytest

from evaluation.runtime_benchmark import percentile


def test_nearest_rank_percentiles_are_stable_for_small_samples():
    assert percentile([10, 20, 30, 40], 0.50) == 20
    assert percentile([10, 20, 30, 40], 0.95) == 40
    assert percentile([], 0.95) is None


def test_benchmark_rejects_workload_exceeding_shared_rate_limit():
    from evaluation.runtime_benchmark import benchmark

    with pytest.raises(ValueError, match='rate-limit budget'):
        asyncio.run(benchmark('http://127.0.0.1:1', request_count=26, concurrency=1, timeout=0.1))
