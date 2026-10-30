##### iocage must be installed for the tests to function

# Code testing
All the tests are written using the `pytest` unit testing framework. Code coverage is provided by `pytest-cov`

These examples use Python 3.14. On FreeBSD, install the SQLite module required
by coverage as root:

```
# pkg install py314-sqlite3
```

Then install the Python test dependencies:
```
$ python3.14 -m pip install -r requirements.txt -r requirements-dev.txt
```

## Unit tests

Located in the ``tests/unit_tests`` directory, they can be started as a normal user with the following command:

```
$ python3.14 -m pytest tests/unit_tests
```

## Functional tests

Located in ``tests/functional_tests``, they need root access, a disposable ZFS
pool, and the native libzfs Python bindings. Build bindings matching the test
interpreter from the current FreeBSD port ``filesystems/py-libzfs``. For
Python 3.14, with the FreeBSD source tree installed:

```
$ sudo make -C /usr/ports/filesystems/py-libzfs \
    BUILD_ALL_PYTHON_FLAVORS=yes FLAVOR=py314 install clean
```

Runtime iocage does not require these bindings. Building them requires the
FreeBSD source tree and the dependencies documented by that port.

**/!\ The contents of the specified ZFS pool will be destroyed**

To start the functional tests, run pytest with root privileges and the name of a zpool:
```
$ sudo python3.14 -m pytest tests/functional_tests --zpool=mypool --release=15.1-RELEASE
```

Other parameters are available, to see them run:
```
$ python3.14 -m pytest --fixtures
```
Extract:
```
zpool
    Specify a zpool to use.
release
    Specify a RELEASE to use.
server
    FTP server to login to.
user
    The user to use for fetching.
password
    The password to use for fetching.
root_dir
    Root directory containing all the RELEASEs for fetching.
http
    Have --server define a HTTP server instead.
hardened
    Have fetch expect the default HardenedBSD layout instead.
_file
    Use a local file directory for root-dir instead of FTP or HTTP.
auth
    Authentication method for HTTP fetching. Valid values: basic, digest
noupdate
    Decide whether or not to update the fetch to the latest patch level.
image
    Run the export and import operations.
```


# Example
- Follow [GitHub Installation in README.md](https://github.com/freebsd/iocage/blob/master/README.md)
- cd iocage
- sudo python3.14 -m pytest tests/functional_tests --zpool="TEST" --release=15.1-RELEASE --server="custom_server"

Use Python 3.11.4 or newer for tests. CI tests Python 3.11 through 3.15;
the examples and release/documentation builds continue to use Python 3.14.
Pass an explicit supported release with
``--release`` that is no newer than the host. With ``--nat --upgrade``, upgrade
tests cover 14.4-RELEASE to 14.5-RELEASE and 14.5-RELEASE to 15.1-RELEASE;
other target releases skip these tests.
