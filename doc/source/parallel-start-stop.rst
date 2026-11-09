.. _Parallel Start and Stop:

Parallel start and stop
=======================

Parallel execution is optional. Existing commands remain sequential unless
``--parallel`` is supplied::

    iocage start --parallel ALL
    iocage stop --parallel --rc
    iocage start --parallel --jobs 4 database cache web
    iocage stop --parallel --jobs 4 database cache web

Start processes lower numeric priorities first; stop processes higher priorities
first. Each group finishes before the next begins. By default, every independent
jail in a group can run concurrently. ``--jobs N`` limits the number of active
operations and requires a positive integer and ``--parallel``.

Named jails are resolved and deduplicated before dispatch. Their priorities,
rather than argument order, determine parallel execution order. Do not combine
``ALL`` with names or ``--rc`` with names. Already-running starts and
already-stopped stops are skipped. An empty boot selection succeeds.
Templates are excluded from ``ALL`` and ``--rc`` selections. Starting an
explicitly named template or requiring one through ``depends`` remains a
preflight error.

Dependencies and failures
-------------------------

For start, stopped ``depends`` prerequisites are included even if they were not
selected or have ``boot=off``. Shared prerequisites start once. A stopped
prerequisite must have an earlier or equal priority; an already-running
prerequisite is satisfied regardless of its priority. Same-priority dependents
wait for their prerequisites to finish successfully. Missing prerequisites,
cycles, unsupported startup types, and conflicting priorities fail preflight.

If a start fails, the command completes other eligible operations in that group,
skips failed dependents, and does not begin later groups. Stop attempts every
group even when one jail fails. Failures are reported by jail name and produce a
nonzero exit status. ``--ignore`` continues eligible work and suppresses the
aggregate exception; preflight errors remain fatal.

The ``depends`` property controls startup only. Same-priority stops run
together. Use different priorities when shutdown order matters. ``--force``
retains its usual meaning and bypasses pre-stop actions for every selected jail.

.. note::
   Completion means that iocage's lifecycle operation, including its hooks, has
   finished. It does not establish application readiness. Use suitable startup
   hooks if a dependent application must wait for a service health check.

Address reservations are shared within one invocation. Dynamic devfs allocation
and NAT rule updates are protected by resource locks. Do not run competing
lifecycle commands during a trial. Callback delivery is serialized, but
callbacks may execute on worker threads. Output from different jails can
interleave; the final failure summary is sorted by jail name.
An interruption stops admission and waits for submitted workers to settle; it
does not undo successful jail operations.

Boot and shutdown
-----------------

The rc script supports these settings in ``/etc/rc.conf``::

    iocage_parallel="YES"
    iocage_parallel_jobs="4"
    iocage_program="/usr/local/bin/iocage"

``iocage_parallel`` defaults to ``NO``. Leave ``iocage_parallel_jobs`` unset or
empty for whole-group concurrency. ``iocage_program`` defaults to the packaged
executable and can select a separate installation. Both boot and shutdown use
the same settings and select ``boot=on`` jails.

Testing the branch
------------------

Unit tests use simulated lifecycle operations and synchronization barriers to
check overlap, ordering, dependency failures, allocations, and worker limits.
Install the test requirements in a development virtual environment and run::

    python -m pip install -r requirements.txt -r requirements-test.txt
    python -m pytest tests/unit_tests

The new functional tests create four disposable jails with priorities 10, 10,
20, and 20. Host hooks record monotonic timestamps and wait for their peer to
prove overlap. Tests cover whole-group execution, one/two workers, dependencies,
force stop, boot selection, empty devfs clones, and injected failure
with/without ``--ignore``.
The optional NAT test checks distinct addresses/rulesets, connectivity, and
interface/ruleset cleanup.

.. warning::
   Run functional tests on a disposable FreeBSD host and ZFS pool. The full
   functional suite includes destructive cleanup of the selected pool. The
   optional NAT test changes the host's firewall configuration.

Install the branch into the test environment and put its executable first in
``PATH``. As root, with an appropriate host-compatible release::

    python -m pip install .
    python -m pytest tests/functional_tests \
        --zpool=IOCAGE_TEST --release=<matching-RELEASE>

Once the test host has a fetched release, run just the parallel scenarios::

    python -m pytest tests/functional_tests/0005_parallel_test.py \
        --zpool=IOCAGE_TEST --release=<fetched-RELEASE>

Add ``--nat`` to include the VNET/NAT test; the host must have working NAT
and an external route. ``--ping_ip`` selects the connectivity test destination.

Production branch trial
-----------------------

For an existing pkg/ports installation, use a separate virtual environment with
the same Python interpreter and system packages. This keeps pkg-managed Python
files intact. The branch must first be committed and published as
``feature/parallel-jail-start-stop``. Run as root in ``/bin/sh``::

    freebsd-version -ku
    /usr/local/bin/iocage --version
    head -n 1 /usr/local/bin/iocage

    IOCAGE_BRANCH_PYTHON=$(
        sed -n '1s/^#!//p' /usr/local/bin/iocage
    )
    test -x "$IOCAGE_BRANCH_PYTHON" || exit 1

    git clone --single-branch \
        --branch feature/parallel-jail-start-stop \
        https://github.com/totalAldo/iocage.git \
        /root/iocage-parallel-src

    "$IOCAGE_BRANCH_PYTHON" -m venv --system-site-packages \
        /root/iocage-parallel-venv

    cd /root/iocage-parallel-src
    /root/iocage-parallel-venv/bin/python -m pip install \
        -r requirements.txt
    /root/iocage-parallel-venv/bin/python -m pip install \
        --ignore-installed --no-deps .
    git rev-parse HEAD

    cd /
    /root/iocage-parallel-venv/bin/python -c \
        'import iocage_lib; print(iocage_lib.__file__)'
    /root/iocage-parallel-venv/bin/python -m pip check
    /root/iocage-parallel-venv/bin/iocage start --help

Inspect the shebang before using the interpreter extraction: it must contain a
plain absolute executable path, without flags. Otherwise select the actual
pkg-installed interpreter explicitly. Confirm that the imported library comes
from the virtual environment. Record the commit ID because the branch keeps the
existing project version. If the branch is not published, transfer the local
checkout and start at the virtual-environment creation step instead.

A built wheel can also be transferred to the host without publishing the
branch. After creating the virtual environment and installing any missing
requirements, install the transferred wheel::

    /root/iocage-parallel-venv/bin/python -m pip install \
        --ignore-installed --no-deps /root/iocage-1.13-py3-none-any.whl

Record the wheel's SHA256 checksum when testing an uncommitted checkout; the
base commit ID alone does not identify its changes.

During a maintenance window, start with disposable named canaries::

    /root/iocage-parallel-venv/bin/iocage start \
        --parallel --jobs 2 canary_a canary_b
    /root/iocage-parallel-venv/bin/iocage stop \
        --parallel --jobs 2 canary_a canary_b

Then test multiple priority groups and whole-group concurrency. Check
application health, jail state, and ``/var/log/iocage.log``. Keep the venv
at its original path: its entrypoint contains an absolute interpreter path.

To trial boot/shutdown, back up the installed rc script and record the current
values (or absence) of the three new rc settings. Install the branch script and
select its executable::

    cp -p /usr/local/etc/rc.d/iocage /root/iocage-rc.before-parallel
    install -m 755 /root/iocage-parallel-src/rc.d/iocage \
        /usr/local/etc/rc.d/iocage
    sysrc iocage_program="/root/iocage-parallel-venv/bin/iocage"
    sysrc iocage_parallel="YES"
    sysrc iocage_parallel_jobs="2"

Test a controlled service stop/start before a reboot. Setting
``iocage_parallel=NO`` restores sequential scheduling in the branch. To restore
the packaged implementation, restore the saved script and previous rc values,
removing settings that were previously absent. Invoke ``/usr/local/bin/iocage``
again. No jail configuration migration is introduced by this feature.
