.. index:: Advanced Usage
.. _Advanced Usage:

Advanced Usage
==============

.. index:: Clones
.. _Clones:

Clones
------

When a jail is cloned, iocage creates a ZFS clone filesystem.
Essentially, clones are cheap, lightweight, and writable snapshots.

A clone depends on its source snapshot and filesystem. To destroy the
source jail and preserve its clones, the clone must be promoted first.

.. index:: Create clones
.. _Create a Clone:

Create a Clone
++++++++++++++

To clone **www01** to **www02**, run:

:samp:`# iocage clone www01 --name www02`

Clone a jail from an existing snapshot with:

:samp:`# iocage clone www01@snapshotname --name www03`

.. index:: Promote a Clone
.. _Promoting a Clone:

Promoting a Clone
+++++++++++++++++

.. warning:: This functionality isn't fully added to iocage, and may not
   function as expected.

**To promote a cloned jail, run:**

:command:`iocage promote [UUID | NAME]`

This reverses the *clone* and *source* jail relationship. The clone
becomes the source and the source jail is demoted to a clone.

**The demoted jail can now be removed:**

:command:`iocage destroy [UUID | NAME]`

.. index:: Updating Jails
.. _Updating Jails:

Updating Jails
--------------

Updates are handled with the freebsd-update(8) utility. Jails can be
updated while they are stopped or running.

.. note:: The command :command:`iocage update [UUID | NAME]`
   automatically creates a backup snapshot of the jail given.

To create a backup snapshot manually, run:

:command:`iocage snapshot -n [snapshotname] [UUID | NAME]`

To update a jail to latest patch level, run:

:command:`iocage update [UUID | NAME]`

When updates are finished and the jail appears to function properly,
remove the snapshot:

:command:`iocage snapremove [UUID|NAME]@[snapshotname]`

To test updating without affecting a jail, create a clone and update the
clone the same way as outlined above.

To clone a jail, run:

:command:`iocage clone [UUID|NAME] --name [testupdate]`

.. note:: The **[-n | --name]** flag is optional. :command:`iocage`
   assigns a UUID to the jail if **[-n | --name]** is not used.

.. index:: Upgrade Jails
.. _Upgrading Jails:

Upgrading Jails
---------------

Upgrades are handled with the freebsd-update(8) utility. By default, the
user must supply the new RELEASE for the jail's upgrade. For example:

:samp:`# iocage upgrade examplejail -r 15.1-RELEASE`

Tells jail *examplejail* to upgrade its RELEASE to *15.1-RELEASE*.

.. note:: It is recommended to keep the iocage host and jails RELEASE
   synchronized.

To upgrade a jail to the host's RELEASE, run:

:command:`iocage upgrade -r [15.1-RELEASE] [UUID | NAME]`

This upgrades the jail to the same RELEASE as the host. This method also
applies to basejails.

.. index:: Auto-Boot
.. _AutoBoot:

Auto-boot
---------

Make sure :command:`iocage_enable="YES"` is set in :file:`/etc/rc.conf`.

To enable a jail to auto-boot during a system boot, simply run:

:samp:`# iocage set boot=on UUID|NAME`

.. note:: Setting :command:`boot=on` during jail creation starts the
   jail after the jail is created.

.. index:: Boot Priority
.. _Boot Priority:

Boot Priority
+++++++++++++

Boot order can be specified by setting the priority value:

:command:`iocage set priority=[20] [UUID|NAME]`

*Lower* values are higher in the boot priority.

.. index:: Depends Property
.. _Depends Property:

Depends Property
++++++++++++++++

Use the :literal:`depends` property to require other jails to start
before this one. It is space delimited. Jails listed as dependents
also wait to start if those jails have listed :literal:`depends`.

Example: :command:`iocage set depends=“foo bar” baz`

.. index:: Snapshot Management
.. _Snapshot Management:

Snapshot Management
-------------------

iocage supports transparent ZFS snapshot management out of the box.
Snapshots are point-in-time copies of data, a safety point to which a
jail can be reverted at any time. Initially, snapshots take up almost no
space, as only changing data is recorded.

You may use **ALL** as a target jail name for these commands if you want to target all jails at once.

List snapshots for a jail:

:command:`iocage snaplist [UUID|NAME]`

Create a new snapshot:

:command:`iocage snapshot [UUID|NAME]`

This creates a snapshot based on the current time.

:command:`iocage snapshot [UUID|NAME] -n [SNAPSHOT NAME]`

This creates a snapshot with the given name.

Delete a snapshot:

:command:`iocage snapremove [UUID|NAME] -n [SNAPSHOT NAME]`

Delete all snapshots from a jail (requires `-f / --force`):

:command:`iocage snapremove [UUID|NAME] -n ALL -f`

.. index:: Resource Limits
.. _Resource Limits:

Resource Limits
---------------

iocage supports CPU affinity through ``cpuset`` and resource limits through
FreeBSD's ``rctl(8)``. CPU affinity does not require resource accounting.

Supported FreeBSD GENERIC kernels include ``options RACCT`` and
``options RCTL``; custom kernels must retain both to use resource limits.
To enable resource accounting, add this line to ``/boot/loader.conf`` and
reboot the host, unless accounting is already enabled:

.. code-block:: none

   kern.racct.enable="1"

Verify that resource accounting is enabled; this must print ``1``:

:samp:`# sysctl -n kern.racct.enable`

Display the host's available logical CPU IDs:

:samp:`# cpuset -g -s 0`

Restrict a jail to logical CPU 1, if that CPU is available:

:samp:`# iocage set cpuset=1 examplejail`

CPU IDs start at 0. CPU affinity changes take effect when the jail starts
or is fully restarted with :command:`iocage restart examplejail`; they do
not reserve CPUs exclusively for the jail.

Limit the jail's aggregate resident memory to 4 GiB:

:samp:`# iocage set memoryuse=deny=4G examplejail`

.. warning:: FreeBSD's ``rctl(8)`` manual warns that limiting ``memoryuse``
   can cause thrashing severe enough to make the host unresponsive. This
   limits resident memory; ``vmemoryuse`` limits virtual address space and
   ``swapuse`` limits swap reservations and usage.

Limit the jail's aggregate CPU use to 20% of one CPU's capacity:

:samp:`# iocage set pcpu=deny=20 examplejail`

For ``pcpu``, 100 represents one CPU's capacity, and 200 represents two CPUs'
capacity. FreeBSD enforces ``pcpu`` limits with the ``deny`` action;
``throttle`` is supported only for the I/O resources ``readbps``, ``writebps``,
``readiops``, and ``writeiops``.

Resource limit properties use ``action=amount`` values. iocage creates rules
accounted for across the jail. Setting a supported limit on a running jail
attempts to apply it immediately; check the command's result and active rules
to confirm success. Configured limits also apply at startup. To remove a
limit, set its property to ``off``:

:samp:`# iocage set memoryuse=off examplejail`

For a running jail, display its active rules and current resource usage from
the host:

:samp:`# rctl jail:ioc-examplejail`

:samp:`# rctl -hu jail:ioc-examplejail`

Use the actual jail name reported by ``jls -n name`` in the ``jail:`` filter.
See the `FreeBSD rctl(8) manual
<https://man.freebsd.org/cgi/man.cgi?query=rctl&sektion=8>`_
for supported resources, actions, and units.

.. index:: Automatic Package Installation
.. _Automatic Package Installation:

Automatic Package Installation
------------------------------

Packages can be installed automatically at creation time!

Use the [-p | --pkglist] option at creation time, which needs to point
to a JSON file containing one package name per line.

.. note:: An Internet connection is required for automatic package
   installations, as :command:`pkg install` obtains packages from online
   repositories.

Create a :file:`pkgs.json` file and add package names to it.

:file:`pkgs.json`:

.. code-block:: json

   {
       "pkgs": [
       "nginx",
       "tmux"
       ]
   }

Now, create a jail and supply :file:`pkgs.json`:

:command:`iocage create -r [RELEASE] -p [path-to/pkgs.json] -n [NAME]`

.. note:: The **[-n | --name]** flag is optional. :command:`iocage`
   assigns a UUID to the jail if **[-n | --name]** is not used.

This installs **nginx** and **tmux** in the newly created jail.
