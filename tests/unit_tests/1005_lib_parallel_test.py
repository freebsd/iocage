# Copyright (c) 2014-2026, iocage
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted providing that the following conditions
# are met:
# 1. Redistributions of source code must retain the above copyright
#    notice, this list of conditions and the following disclaimer.
# 2. Redistributions in binary form must reproduce the above copyright
#    notice, this list of conditions and the following disclaimer in the
#    documentation and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE AUTHOR ``AS IS'' AND ANY EXPRESS OR
# IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED.  IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY
# DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS
# OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION)
# HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT,
# STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING
# IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.

"""Concurrency tests use synchronization rather than elapsed-time guesses."""
import concurrent.futures
import threading

import pytest

from iocage_lib.ioc_parallel import IOCParallel


def jail(priority=10, running=False, depends=()):
    return {
        'priority': priority,
        'running': running,
        'depends': depends
    }


@pytest.mark.parametrize('action', ['start', 'stop'])
def test_equal_priorities_overlap_and_groups_wait(action):
    events = []
    lock = threading.Lock()
    barriers = {10: threading.Barrier(2), 20: threading.Barrier(2)}
    records = {
        name: jail(priority, running=action == 'stop')
        for name, priority in [('a', 10), ('b', 10), ('c', 20), ('d', 20)]
    }

    def worker(name):
        with lock:
            events.append(('begin', name))

        barriers[records[name]['priority']].wait(timeout=5)

        with lock:
            events.append(('end', name))

    assert IOCParallel(records, action, worker).run() == {}
    first, second = ('ab', 'cd') if action == 'start' else ('cd', 'ab')
    assert max(events.index(('end', n)) for n in first) < min(
        events.index(('begin', n)) for n in second
    )


def test_default_has_no_executor_cpu_limit():
    count = 40
    barrier = threading.Barrier(count)
    records = {str(n): jail() for n in range(count)}
    scheduler = IOCParallel(
        records, 'start', lambda name: barrier.wait(timeout=10)
    )
    assert scheduler.run() == {}


@pytest.mark.parametrize('jobs', [1, 2, 4])
def test_worker_limit(jobs):
    active = 0
    maximum = 0
    admitted = threading.Event()
    release = threading.Event()
    lock = threading.Lock()

    def worker(name):
        nonlocal active, maximum

        with lock:
            active += 1
            maximum = max(maximum, active)

            if active == jobs:
                admitted.set()

        assert release.wait(timeout=5)

        with lock:
            active -= 1

    records = {str(n): jail() for n in range(8)}
    scheduler = IOCParallel(records, 'start', worker, jobs=jobs)

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as caller:
        result = caller.submit(scheduler.run)

        try:
            assert admitted.wait(timeout=5)
            assert maximum == jobs
        finally:
            release.set()

        assert result.result(timeout=5) == {}

    assert maximum == jobs


def test_dependencies_do_not_occupy_worker_slots():
    records = {
        'a': jail(depends=('b',)),
        'b': jail(depends=('c',)),
        'c': jail(),
        'd': jail(depends=('b',))
    }
    calls = []
    assert IOCParallel(records, 'start', calls.append, jobs=1).run() == {}
    assert calls == ['c', 'b', 'a', 'd']


@pytest.mark.parametrize('records,match', [
    ({'a': jail(depends=('missing',))}, 'missing prerequisite'),
    ({'a': jail(depends=('a',))}, 'cycle'),
    ({'a': jail(depends=('b',)), 'b': jail(depends=('a',))}, 'cycle'),
    ({'a': jail(10, depends=('b',)), 'b': jail(20)}, 'later start priority')
])
def test_bad_dependencies_fail_before_execution(records, match):
    calls = []

    with pytest.raises(ValueError, match=match):
        IOCParallel(records, 'start', calls.append).run()

    assert calls == []


def test_running_prerequisite_satisfies_even_later_priority():
    records = {
        'a': jail(10, depends=('b',)),
        'b': jail(20, running=True, depends=('missing',))
    }
    calls = []
    assert IOCParallel(records, 'start', calls.append).run() == {}
    assert calls == ['a']


@pytest.mark.parametrize('action', ['start', 'stop'])
def test_correct_states_are_skipped(action):
    calls = []
    records = {'a': jail(running=action == 'start')}
    assert IOCParallel(records, action, calls.append).run() == {}
    assert calls == []


@pytest.mark.parametrize('ignore', [False, True])
@pytest.mark.parametrize('exception', [RuntimeError, SystemExit])
def test_start_failure_finishes_group_and_blocks_dependents(ignore, exception):
    records = {
        'a': jail(),
        'b': jail(),
        'c': jail(depends=('a',)),
        'd': jail(depends=('c',)),
        'e': jail(20)
    }
    calls = []

    def worker(name):
        calls.append(name)

        if name == 'a':
            raise exception('broken hook')

    failures = IOCParallel(records, 'start', worker, ignore=ignore).run()
    assert set(calls) == ({'a', 'b', 'e'} if ignore else {'a', 'b'})
    assert failures['a'] == 'broken hook'
    assert 'Prerequisite' in failures['c']
    assert 'Prerequisite' in failures['d']
    assert ('e' in failures) is not ignore


def test_stop_failure_does_not_block_lower_priority_or_dependencies():
    records = {
        'a': jail(20, True),
        'b': jail(20, True, depends=('a',)),
        'c': jail(10, True)
    }
    calls = []
    barrier = threading.Barrier(2)

    def worker(name):
        calls.append(name)

        if name in ('a', 'b'):
            barrier.wait(timeout=5)

        if name == 'a':
            raise SystemExit('failed stop')

    assert IOCParallel(records, 'stop', worker).run() == {'a': 'failed stop'}
    assert set(calls) == set(records)
    assert calls[-1] == 'c'


def test_failed_chain_settles_with_one_worker_and_reverse_name_order():
    calls = []
    records = {
        'a': jail(depends=('b',)),
        'b': jail(depends=('c',)),
        'c': jail()
    }

    def worker(name):
        calls.append(name)
        raise RuntimeError('failed prerequisite')

    failures = IOCParallel(records, 'start', worker, jobs=1).run()
    assert calls == ['c']
    assert set(failures) == set(records)


@pytest.mark.parametrize('jobs', [0, -1, True, 1.5, '2'])
def test_invalid_worker_limits(jobs):
    with pytest.raises(ValueError, match='positive integer'):
        IOCParallel({}, 'start', lambda name: None, jobs=jobs)


@pytest.mark.parametrize('target', ['a', 'c'])
def test_interruption_waits_for_active_workers_and_admits_no_more(
    monkeypatch, target
):
    active = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    calls = []
    wait_count = 0
    real_wait = concurrent.futures.wait

    def worker(name):
        calls.append(name)

        if name == target:
            active.set()
            assert release.wait(timeout=5)
            finished.set()

    def interrupt(*args, **kwargs):
        nonlocal wait_count
        wait_count += 1

        if wait_count != (1 if target == 'a' else 3):
            return real_wait(*args, **kwargs)

        assert active.wait(timeout=5)
        release.set()
        raise KeyboardInterrupt

    monkeypatch.setattr(concurrent.futures, 'wait', interrupt)

    with pytest.raises(KeyboardInterrupt):
        IOCParallel(
            {'a': jail(), 'b': jail(), 'c': jail(20),
             'd': jail(20), 'e': jail(30)},
            'start', worker, jobs=1
        ).run()

    assert calls == (['a'] if target == 'a' else ['a', 'b', 'c'])
    assert finished.is_set()
