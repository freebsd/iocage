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

"""Run independent jail operations within priority completion barriers."""
import concurrent.futures


class IOCParallel:
    """Schedule jail operations between priority completion barriers.

    jails maps canonical names to records with priority (int), running
    (bool), and depends (canonical names). worker(name) performs one
    operation and raises on failure. Only workers run on executor threads;
    validation, dependency readiness, and results stay in the calling thread.
    """

    def __init__(self, jails, action, worker, jobs=None, ignore=False):
        if action not in ('start', 'stop'):
            raise ValueError('Action must be start or stop')

        if jobs is not None and (
            isinstance(jobs, bool) or not isinstance(jobs, int) or jobs < 1
        ):
            raise ValueError('jobs must be a positive integer')

        self.jails = jails
        self.action = action
        self.worker = worker
        self.jobs = jobs
        self.ignore = ignore
        self.failures = {}
        self.completed = set()
        self.validate()

    def validate(self):
        """Reject invalid dependency plans before submitting any work."""
        if self.action == 'stop':
            return

        visited = set()
        visiting = set()  # The active dependency path, for cycle detection.

        def visit(name):
            jail = self.jails[name]

            # A running prerequisite is already satisfied; its own startup
            # dependencies and priority no longer constrain this batch.
            if jail['running'] or name in visited:
                return

            if name in visiting:
                raise ValueError(f'Dependency cycle involving {name}')

            visiting.add(name)

            for dependency in jail['depends']:
                if dependency not in self.jails:
                    raise ValueError(
                        f'{name}: missing prerequisite {dependency}'
                    )

                prerequisite = self.jails[dependency]

                if (not prerequisite['running'] and
                        prerequisite['priority'] > jail['priority']):
                    raise ValueError(
                        f'{name}: prerequisite {dependency} has a later '
                        'start priority'
                    )

                visit(dependency)

            visiting.remove(name)
            visited.add(name)

        for name in self.jails:
            visit(name)

    def run(self):
        """Return jail-specific failures after settling submitted workers."""
        groups = {}
        desired_running = self.action == 'start'

        for name, jail in self.jails.items():
            if jail['running'] == desired_running:
                self.completed.add(name)
            else:
                groups.setdefault(jail['priority'], []).append(name)

        if not groups:
            return self.failures

        priorities = sorted(groups, reverse=self.action == 'stop')
        largest_group = max(len(names) for names in groups.values())
        # Set the size explicitly: the executor's CPU-based default would
        # otherwise cap a whole group's concurrency when jobs is omitted.
        capacity = min(self.jobs or largest_group, largest_group)
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=capacity)

        try:
            # Reuse threads across barriers; run_group settles every future
            # before returning, so separate priorities still never overlap.
            for index, priority in enumerate(priorities):
                self.run_group(groups[priority], executor, capacity)

                if desired_running and self.failures and not self.ignore:
                    for later in priorities[index + 1:]:
                        for name in groups[later]:
                            self.failures[name] = (
                                'Not started after an earlier priority failed'
                            )

                    break
        finally:
            # Cancel queued work and settle active workers on interruption.
            executor.shutdown(wait=True, cancel_futures=True)

        return self.failures

    def run_group(self, names, executor, capacity):
        """Admit ready jails in the coordinator, never inside a worker."""
        # Sort once and retain insertion order as completed names are removed.
        pending = dict.fromkeys(sorted(names))
        futures = {}

        while pending or futures:
            for name in tuple(pending):
                # Keep work in pending until a slot is free. Dependents never
                # occupy worker slots while waiting for their prerequisites.
                if len(futures) == capacity:
                    break

                dependencies = (
                    self.jails[name]['depends']
                    if self.action == 'start' else ()
                )

                if any(dep in self.failures for dep in dependencies):
                    self.failures[name] = 'Prerequisite failed to start'
                    del pending[name]
                elif all(dep in self.completed for dep in dependencies):
                    future = executor.submit(self.worker, name)
                    futures[future] = name
                    del pending[name]

            if not futures:
                # Only chains behind failed prerequisites can remain:
                # preflight validated the graph and earlier barriers.
                for name in pending:
                    self.failures[name] = 'Prerequisite failed to start'

                break

            done, _ = concurrent.futures.wait(
                futures, return_when=concurrent.futures.FIRST_COMPLETED
            )

            for future in done:
                name = futures.pop(future)

                try:
                    future.result()
                except (Exception, SystemExit) as error:
                    # Existing lifecycle paths can exit through SystemExit.
                    # KeyboardInterrupt must escape to settle the executor.
                    self.failures[name] = str(error) or type(error).__name__
                else:
                    self.completed.add(name)
