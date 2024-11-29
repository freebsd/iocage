.. index:: Create Debian Jail
.. _Create a Debian Jail:

Create a Debian Buster Jail (native Linux)
===========================================

This section shows the process to set up a Debian Linux jail.
The examples in this section use a jail with the custom name
**debjail**. Remember to replace **debjail** with your jail's UUID or
NAME!

.. warning:: This is not recommended for production use. The intention
   is to show :command:`iocage` can do almost anything with jails.

**Create an empty jail:**

.. code-block:: none

 # iocage create -e -n debjail exec_start="/bin/true" exec_stop="/bin/true"
   allow_mount_linprocfs=1 allow_mount_devfs=1 allow_raw_sockets=1
   allow_socket_af=1 allow_mount_linsysfs=1 allow_mount=1

**Install devfs rules for vnet:**

In `/etc/devfs.rules`:

.. code-block:: none

 [devfsrules_jail_linux=6] add include $devfsrules_hide_all
 add include $devfsrules_unhide_basic
 add include $devfsrules_unhide_login
 add path 'tun*' unhide
 add path 'bpf*' unhide
 add path zfs unhide

**Install debootstrap on the host:**

:samp:`# pkg install debootstrap`

**Enable linux(4):**

:samp:`# sysrc linux_enable="YES"`
:samp:`# sysrc linux_mounts_enable="NO"`
:samp:`# service linux start`

**Grab the mountpoint for the empty jail, append /root/ to it, and run**
**debootstrap(8):**

:samp:`# iocage get mountpoint debjail`

:samp:`# debootstrap bookworm /iocage/jails/debjail/root/`

Apart from Debian releases, like *buster* or *testing*, you can
also use Ubuntu releases, eg *bionic*.

**Add lines to the jail** :file:`fstab` **file:**

Use :command:`iocage fstab -e [UUID | NAME]` to edit the :file:`fstab`
file of *debjail* directly. Add these lines to the file:

.. code-block:: none

 devfs    /iocage/jails/debjail/root/dev         devfs     rw          0 0
 tmpfs    /iocage/jails/debjail/root/dev/shm     tmpfs     rw,size=1g,mode=1777 0 0
 fdescfs  /iocage/jails/debjail/root/dev/fd      fdescfs   rw,linrdlnk 0 0
 linproc  /iocage/jails/debjail/root/proc        linprocfs rw          0 0
 linsys   /iocage/jails/debjail/root/sys         linsysfs  rw          0 0

**Start the jail and attach to it:**

:samp:`# iocage start debjail`

:samp:`# iocage console debjail`

The result is a 64-bit Debian Linux userland.
