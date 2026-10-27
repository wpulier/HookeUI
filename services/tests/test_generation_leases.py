"""A runtime cannot be removed beneath admitted work, including nested owners."""
import threading

import pytest


def test_generation_removal_waits_for_all_owners(tmp_path):
    from orchestrator import generation_leases as leases

    root = tmp_path / 'package'
    generation = root / 'abc'
    generation.mkdir(parents=True)
    with leases.use(generation):
        with leases.use(generation):
            with pytest.raises(leases.Busy, match='running work'):
                with leases.withdraw(root):
                    pytest.fail('removal was admitted')
        with pytest.raises(leases.Busy):
            with leases.withdraw(generation):
                pytest.fail('outer owner was lost')
    with leases.withdraw(root):
        generation.rmdir()


def test_retirement_runs_once_after_the_last_lease(tmp_path):
    from orchestrator import generation_leases as leases

    generation = tmp_path / 'runtime'
    generation.mkdir()
    retired = []
    with leases.use(generation):
        with leases.use(generation):
            leases.when_idle(generation, lambda: retired.append('stopped'))
        assert retired == []
    assert retired == ['stopped']


def test_withdrawal_refuses_stale_admission_without_blocking_other_generations(tmp_path):
    from orchestrator import generation_leases as leases

    generation, other = tmp_path / 'runtime', tmp_path / 'other'
    generation.mkdir(); other.mkdir()
    finished = threading.Event()
    def enter():
        with leases.use(other):
            finished.set()
    with leases.withdraw(generation):
        with pytest.raises(leases.Busy, match='being removed'):
            with leases.use(generation):
                pytest.fail('stale admission during withdrawal')
        thread = threading.Thread(target=enter)
        thread.start()
        try:
            assert finished.wait(2), 'unrelated admission waited on recursive removal'
        finally:
            thread.join(2)
        generation.rmdir()
    with pytest.raises(leases.Missing, match='no longer available'):
        with leases.use(generation):
            pytest.fail('stale admission after withdrawal')


def test_slow_retirement_does_not_block_unrelated_admission(tmp_path):
    from orchestrator import generation_leases as leases

    first, second = tmp_path / 'first', tmp_path / 'second'
    first.mkdir(); second.mkdir()
    entered, release, admitted = threading.Event(), threading.Event(), threading.Event()
    def retirement():
        entered.set()
        assert release.wait(5)
    def own_first():
        with leases.use(first):
            leases.when_idle(first, retirement)
    def own_second():
        with leases.use(second):
            admitted.set()
    a = threading.Thread(target=own_first)
    a.start()
    try:
        assert entered.wait(2)
        b = threading.Thread(target=own_second)
        b.start()
        assert admitted.wait(2), 'unrelated admission blocked by retirement'
    finally:
        release.set()
        a.join(2)
        b.join(2)


def test_preparation_reserves_absent_directory_and_releases_after_failure(tmp_path):
    from orchestrator import generation_leases as leases

    package = tmp_path / 'not-created'
    future = package / 'generation'
    with pytest.raises(RuntimeError, match='cancelled preparation'):
        with leases.prepare(future):
            assert not package.exists()
            with pytest.raises(leases.Busy, match='running work'):
                with leases.withdraw(package):
                    pytest.fail('removal admitted before preparation created its directory')
            raise RuntimeError('cancelled preparation')
    with leases.withdraw(package):
        with pytest.raises(leases.Busy, match='being removed'):
            with leases.prepare(future):
                pytest.fail('preparation admitted during removal')
    assert not package.exists()
    with leases.prepare(future):
        future.mkdir(parents=True)
    with leases.use(future):
        assert future.is_dir()
