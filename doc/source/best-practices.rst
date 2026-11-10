.. index:: Best Practices
.. _Best Practices:

Best Practices
--------------

This section provides some generic guidelines and tips for working with
:command:`iocage` managed jails.

**Load PF on the host**

  When using PF with a GENERIC kernel, load the PF module on the host before
  starting VNET jails that use it. See the :ref:`FAQ` for the required devfs
  configuration inside the jail.

**Always name jails and templates!**

  Use the -n option with :command:`iocage create` to set a name for the
  jail. This helps avoid mistakes and easily identify jails.

  Example: :samp:`iocage create -r 15.1-RELEASE -n testjail`

**Set the notes property**

  Set the **notes** property to something meaningful, especially for
  templates and jails used infrequently.

  Example:

  .. code-block:: none

   [root@tester ~]# iocage set notes="This is a test jail." testjail
   Property: notes has been updated to This is a test jail.

   [root@tester ~]# iocage get notes testjail
   This is a test jail.

**VNET**

  *VNET* provides more fine control and isolation for jails. VNET also
  allows jails to run their own firewalls. See :ref:`Networking` for
  configuration instructions.

**Discover templates!**

  Templates simplify using jail creation and customization, give it a
  try! See :ref:`Using Templates` to get started.

**Choose the appropriate restart**

  Use :command:`iocage restart examplejail` to stop and start the jail.
  This applies properties that require a jail restart, such as ``cpuset``.
  Use :command:`iocage restart -s examplejail` to restart the jail's processes
  while preserving the jail and its network stack. A soft restart does not
  apply properties that require the jail to be recreated.

**Check the firewall rules**

  When using *IPFW* inside a *VNET* jail, put **firewall_enable="YES"**
  and **firewall_type="open"** into :file:`/etc/rc.conf`. This excludes
  the firewall from accidentally blocking the user right from the
  beginning! Re-lock it once finished testing. It is also recommended to
  check the *PF* firewall rules on the host if jail and host rules are
  mixed.

**Delete old snapshots**

  Remove unnecessary snapshots, especially from jails where data is
  constantly changing!
