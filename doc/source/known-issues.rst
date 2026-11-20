.. index:: Known Issues
.. _Known Issues:

Known Issues
============

This section provides a short list of known issues.

.. index:: Known Issues, IPv6 Host Bind Failure
.. _IPv6 Host Bind Failures:

IPv6 host bind failures
-----------------------

In some cases, a jail with an *ip6* address may take too long adding the
address to the interface. Services defined to bind specifically to the
address may then fail. If this happens, add this to :file:`sysctl.conf`
to disable DAD (duplicate address detection) probe packets:

.. code-block:: none

 # disable duplicate address detection probe packets for jails
 net.inet6.ip6.dad_count=0

Adding these lines permanently disables DAD. To set this for ONLY the
current system boot, type :command:`sysctl net.inet6.ip6.dad_count=0` in
a command line interface (CLI). More information about this issue is
available from a
`2013 mailing list post <https://lists.freebsd.org/pipermail/freebsd-jail/2013-July/002347.html>`_.
