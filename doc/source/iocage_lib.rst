iocage\_lib package
===================

Console startup
---------------

Console validation uses ``IOCCheck(use_cache=True)`` to reuse dataset
properties cached in the current process. Mountpoint and ``exec`` checks
still run; missing datasets are checked again without caching inside the
creation lock before provisioning. Other callers retain the default
``use_cache=False`` behavior. ``reset_cache=True`` clears the cache before
validation and also enables cached dataset access.

``Dataset.get_dependents(use_cached_datasets=True)`` can reuse a complete
recursive filesystem snapshot when it covers the requested dataset.
Incomplete or out-of-scope snapshots fall back to the existing enumeration.
The default remains ``use_cached_datasets=False``; ``ds_cache=False`` always
queries ZFS. Existing dataset mutations invalidate the cached snapshot.
Use ``IOCage.reset_cache()`` to clear cached metadata after external dataset
changes in a long-lived process.

``IOCExec`` and ``InteractiveExec`` accept two optional keyword-only arguments
for reusing the context already resolved for a single execution:

* ``jail_config``: the effective configuration from
  ``IOCJson(path).json_get_value('all')``, including inherited defaults.
* ``jail_status``: the ``(running, jid)`` tuple from
  ``IOCList.list_get_jid(uuid)``.

Passing ``None`` retains each argument's existing lookup behavior. Supply
fresh values for the current execution; refresh both after starting a jail.
Console does this when ``--force`` starts a stopped jail, then reuses the
configuration for ``login_flags`` and ``exec_fib``. A failed launch after
validation is reported without automatically restarting the jail.

Submodules
----------

iocage\_lib.ioc\_check module
-----------------------------

.. automodule:: iocage_lib.ioc_check
    :members:
    :undoc-members:
    :show-inheritance:

iocage\_lib.ioc\_clean module
-----------------------------

.. automodule:: iocage_lib.ioc_clean
    :members:
    :undoc-members:
    :show-inheritance:

iocage\_lib.ioc\_common module
------------------------------

.. automodule:: iocage_lib.ioc_common
    :members:
    :undoc-members:
    :show-inheritance:

iocage\_lib.ioc\_create module
------------------------------

.. automodule:: iocage_lib.ioc_create
    :members:
    :undoc-members:
    :show-inheritance:

iocage\_lib.ioc\_debug module
-----------------------------

.. automodule:: iocage_lib.ioc_debug
    :members:
    :undoc-members:
    :show-inheritance:

iocage\_lib.ioc\_destroy module
-------------------------------

.. automodule:: iocage_lib.ioc_destroy
    :members:
    :undoc-members:
    :show-inheritance:

iocage\_lib.ioc\_exceptions module
----------------------------------

.. automodule:: iocage_lib.ioc_exceptions
    :members:
    :undoc-members:
    :show-inheritance:

iocage\_lib.ioc\_exec module
----------------------------

.. automodule:: iocage_lib.ioc_exec
    :members:
    :undoc-members:
    :show-inheritance:

iocage\_lib.ioc\_fetch module
-----------------------------

.. automodule:: iocage_lib.ioc_fetch
    :members:
    :undoc-members:
    :show-inheritance:

iocage\_lib.ioc\_fstab module
-----------------------------

.. automodule:: iocage_lib.ioc_fstab
    :members:
    :undoc-members:
    :show-inheritance:

iocage\_lib.ioc\_image module
-----------------------------

.. automodule:: iocage_lib.ioc_image
    :members:
    :undoc-members:
    :show-inheritance:

iocage\_lib.ioc\_json module
----------------------------

.. automodule:: iocage_lib.ioc_json
    :members:
    :undoc-members:
    :show-inheritance:

iocage\_lib.ioc\_list module
----------------------------

.. automodule:: iocage_lib.ioc_list
    :members:
    :undoc-members:
    :show-inheritance:

iocage\_lib.ioc\_plugin module
------------------------------

.. automodule:: iocage_lib.ioc_plugin
    :members:
    :undoc-members:
    :show-inheritance:

iocage\_lib.ioc\_start module
-----------------------------

.. automodule:: iocage_lib.ioc_start
    :members:
    :undoc-members:
    :show-inheritance:

iocage\_lib.ioc\_stop module
----------------------------

.. automodule:: iocage_lib.ioc_stop
    :members:
    :undoc-members:
    :show-inheritance:

iocage\_lib.ioc\_upgrade module
-------------------------------

.. automodule:: iocage_lib.ioc_upgrade
    :members:
    :undoc-members:
    :show-inheritance:

iocage\_lib.iocage module
-------------------------

.. automodule:: iocage_lib.iocage
    :members:
    :undoc-members:
    :show-inheritance:


Module contents
---------------

.. automodule:: iocage_lib
    :members:
    :undoc-members:
    :show-inheritance:
