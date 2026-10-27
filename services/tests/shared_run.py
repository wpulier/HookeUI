"""One result file per test run, shared by every parallel test process of that run (for a session-scoped run of a
real worker or browser that each child's case then reads)."""
import os


def shared_result(tmp_path_factory, name):
    """One file per test run that every parallel test process of that run shares, never one from an earlier run."""
    run = os.environ.get('PYTEST_XDIST_TESTRUNUID')  # set by xdist, the same in every worker of one run
    return tmp_path_factory.getbasetemp().parent / f'{name}-{run}.json' if run else tmp_path_factory.getbasetemp() / f'{name}.json'
