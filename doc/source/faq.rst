.. index:: FAQ
.. _FAQ:

FAQ
===

**What is iocage?**
    :command:`iocage` is a jail management program designed to simplify
    jail administration tasks.

**What is a jail?**
    A *Jail* is a FreeBSD **OS virtualization** technology allowing
    users to run multiple copies of the operating system. Some operating
    systems use the term **Zones** or **Containers** for OS
    virtualization.

**What is VNET?**
    VNET is an independent, per jail virtual networking stack.

**How do I configure network interfaces in a VNET or shared IP jail?**
    Both are configured in the same way:
    :command:`iocage set ip4_addr="[interface]|[IP]/[netmask]" [UUID | NAME]`.
    For more info, please refer to the :ref:`Networking` section of this
    documentation.

**Do I need to set my default gateway?**
    VNET jails have their own routing tables. ``defaultrouter`` and
    ``defaultrouter6`` default to ``auto``, which uses the host's default
    gateways. Set a different gateway when the jail's network requires it.
    Shared IP jails use the host's routing table.

**Can I run a firewall inside a jail?**
    Yes, VNET jails support **IPFW** and **PF**. Load the corresponding
    firewall module on the host. For PF, use a devfs ruleset that exposes
    ``/dev/pf``, such as ``devfs_ruleset=5``. Keep ``securelevel`` at **2**
    or lower if firewall rules must be changed inside the jail. See the
    `FreeBSD Handbook's VNET jail instructions
    <https://docs.freebsd.org/en/books/handbook/jails/#jails-vnet>`_
    for details.

**Can I enable both IPFW and PF at the same time?**
    Yes, make sure you allow traffic on both in/out for your jails.

**Can I create custom jail templates?**
    Yes, and thin provisioning is supported too!

**What is a jail clone?**
    **Clones** are ZFS clones. These are fully writable copies of the
    source jail.

**Can I limit the CPU and Memory use?**
    Yes. ``cpuset`` controls CPU affinity without resource accounting.
    Resource limit properties such as ``memoryuse`` and ``pcpu`` require
    FreeBSD resource accounting to be enabled.
    Refer to the :ref:`Resource Limits` section for examples.

**Is there a way to display resource consumption?**
    :command:`iocage df` shows ZFS storage usage. With resource accounting
    enabled, :command:`rctl -hu jail:ioc-examplejail` shows a running jail's
    CPU, memory, and other resource usage from the host. Use the actual jail
    name reported by :command:`jls -n name`.

**Is NAT supported for jails?**
    Yes. NAT is built into FreeBSD. Treat your server as a core
    router/firewall. Check the FreeBSD
    `Firewalls chapter <https://docs.freebsd.org/en/books/handbook/firewalls/>`_
    for more details.

**Will iocage work on a generic system with no ZFS pools?**
    No. ZFS is a must. If you run a FreeBSD server, you should be using
    ZFS!

**Is ZFS jailing supported?**
    Yes, please refer to the :file:`iocage.8` manual page.
