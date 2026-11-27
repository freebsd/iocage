# Copyright (c) 2014-2019, iocage
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
"""This stops jails."""
import os
import subprocess as su

from pathlib import Path

import iocage_lib.ioc_common
import iocage_lib.ioc_exceptions
import iocage_lib.ioc_exec
import iocage_lib.ioc_json
import iocage_lib.ioc_list
import iocage_lib.ioc_nat as ioc_nat


class IOCStop(object):
    """Stops a jail and unmounts the jails mountpoints."""

    def __init__(
        self, uuid, path, silent=False, callback=None,
        force=False, suppress_exception=False, expected_jid=None,
        cleanup_devfs=True
    ):
        self.pool = iocage_lib.ioc_json.IOCJson(" ").json_get_value("pool")
        self.iocroot = iocage_lib.ioc_json.IOCJson(
            self.pool).json_get_value("iocroot")
        self.uuid = uuid.replace(".", "_")
        self.path = path
        self.force = force
        self.expected_jid = expected_jid
        self.cleanup_devfs = cleanup_devfs
        self.callback = callback
        self.silent = silent

        try:
            self.conf = iocage_lib.ioc_json.IOCJson(
                path, suppress_log=True).json_get_value('all')
            self.status, self.jid = iocage_lib.ioc_list.IOCList().list_get_jid(
                uuid)
            if expected_jid is not None and self.jid != expected_jid:
                return
            self.nics = self.conf['interfaces']
            self.__stop_jail__()
        except (Exception, SystemExit) as e:
            if not suppress_exception:
                raise e

    def __stop_jail__(self):
        exec_fib = self.conf["exec_fib"]
        if not self.status:
            msg = f"{self.uuid} is not running!"
            iocage_lib.ioc_common.logit({
                "level": "ERROR",
                "message": msg
            },
                _callback=self.callback,
                silent=self.silent)

            return

        ioc_nat.prepare_stop(f'ioc-{self.uuid}', self.jid)
        msg = f"* Stopping {self.uuid}"
        iocage_lib.ioc_common.logit({
            "level": "INFO",
            "message": msg
        },
            _callback=self.callback,
            silent=self.silent)

        rctl_jail = iocage_lib.ioc_json.IOCRCTL(self.uuid)
        if rctl_jail.rctl_rules_exist():
            failed = rctl_jail.remove_rctl_rules()
            if failed:
                msg = 'Failed to remove'
            else:
                msg = 'Successfully removed'

            iocage_lib.ioc_common.logit(
                {
                    'level': 'ERROR' if failed else 'INFO',
                    'message': f'  + {msg} RCTL rules for {self.uuid}'
                },
                _callback=self.callback,
                silent=self.silent
            )

        failed_message = 'Please use --force flag to force stop jail'
        if not self.force:
            prestop_success, prestop_error = iocage_lib.ioc_common.runscript(
                self.conf['exec_prestop']
            )
            if prestop_error:
                msg = f'  + Executing prestop FAILED\n' \
                    f'ERROR:\n{prestop_error}\n\n{failed_message}'

                iocage_lib.ioc_common.logit({
                    'level': 'EXCEPTION',
                    'message': msg
                },
                    _callback=self.callback,
                    silent=self.silent
                )
            else:
                msg = '  + Executing prestop OK'
                iocage_lib.ioc_common.logit({
                    'level': 'INFO',
                    'message': msg
                },
                    _callback=self.callback,
                    silent=self.silent
                )

            exec_stop = self.conf['exec_stop'].split()
            with open(f'{self.iocroot}/log/{self.uuid}-console.log', 'a') as f:
                success, error = '', ''
                try:
                    output = iocage_lib.ioc_exec.SilentExec(
                        ['setfib', exec_fib, 'jexec', f'ioc-{self.uuid}']
                        + exec_stop, None, unjailed=True, decode=True
                    )
                except iocage_lib.ioc_exceptions.CommandFailed as e:
                    error = b' '.join(e.message).decode()
                    msg = '  + Stopping services FAILED\n' \
                        f'ERROR:\n{error}\n\n{failed_message}'

                    iocage_lib.ioc_common.logit({
                        'level': 'EXCEPTION',
                        'message': msg
                    },
                        _callback=self.callback,
                        silent=self.silent
                    )

                else:
                    success = output.stdout
                    msg = '  + Stopping services OK'
                    iocage_lib.ioc_common.logit({
                        'level': 'INFO',
                        'message': msg
                    },
                        _callback=self.callback,
                        silent=self.silent
                    )

                finally:
                    f.write(success or error)

            if self.conf['jail_zfs']:
                for jdataset in self.conf["jail_zfs_dataset"].split():
                    jdataset = jdataset.strip()

                    try:
                        children = iocage_lib.ioc_common.checkoutput(
                            ["zfs", "list", "-H", "-r", "-o", "name",
                             "-S", "name",
                             f"{self.pool}/{jdataset}"], stderr=su.STDOUT)

                        for child in children.split():
                            child = child.strip()

                            try:
                                iocage_lib.ioc_common.checkoutput(
                                    ["setfib", exec_fib, "jexec",
                                     f"ioc-{self.uuid}", "zfs", "umount",
                                     child], stderr=su.STDOUT)
                            except su.CalledProcessError as err:
                                mountpoint = iocage_lib.ioc_common.checkoutput(
                                    ["zfs", "get", "-H", "-o", "value",
                                     "mountpoint", f"{self.pool}/{jdataset}"]
                                ).strip()

                                if mountpoint == "none":
                                    pass
                                else:
                                    raise RuntimeError(
                                        "{}".format(
                                            err.output.decode("utf-8").rstrip()
                                        ))

                        try:
                            iocage_lib.ioc_common.checkoutput(
                                ["zfs", "unjail", f"ioc-{self.uuid}",
                                 f"{self.pool}/{jdataset}"], stderr=su.STDOUT)
                        except su.CalledProcessError as err:
                            raise RuntimeError(
                                "{}".format(
                                    err.output.decode("utf-8").rstrip()))

                    except su.CalledProcessError as err:
                        if "dataset does not exist" in \
                                err.output.decode("utf-8"):
                            # There's nothing to do if dataset doesn't exist
                            pass
                        else:
                            raise RuntimeError(
                                "{}".format(
                                    err.output.decode("utf-8").rstrip()))

        else:
            # We should remove all exec* keys from jail.conf and make sure
            # we force stop the jail process
            jail_conf = iocage_lib.ioc_json.JailRuntimeConfiguration(self.uuid)
            for r_key in [
                k for k in jail_conf.data if str(k).startswith('exec')
            ]:
                jail_conf.remove(r_key)

            jail_conf.sync_changes()

        removed = False
        try:
            with ioc_nat.removing(f'ioc-{self.uuid}', self.jid):
                devfs_error = self._remove_jail()
                removed = True
        finally:
            # Finish unmounting even if the final PF state sweep fails.
            if removed:
                self._poststop(devfs_error)

    def _poststop(self, devfs_error):
        poststop_success, poststop_error = iocage_lib.ioc_common.runscript(
            self.conf['exec_poststop']
        )

        if poststop_error:
            # This is the only exec case where we won't raise an exception
            # as jail has already stopped
            msg = f'  + Executing poststop FAILED\n{poststop_error}\n\n' \
                'Jail has been stopped but there may be leftovers ' \
                'from exec_poststop failure'
            iocage_lib.ioc_common.logit({
                'level': 'ERROR',
                'message': msg
            },
                _callback=self.callback,
                silent=self.silent
            )
        else:
            msg = '  + Executing poststop OK'
            iocage_lib.ioc_common.logit({
                'level': 'INFO',
                'message': msg
            },
                _callback=self.callback,
                silent=self.silent
            )

        for command in [
            ['umount', '-afF', f'{self.path}/fstab'],
            ['umount', '-f', f'{self.path}/root/dev/fd'],
            ['umount', '-f', f'{self.path}/root/dev'],
            ['umount', '-f', f'{self.path}/root/proc'],
            ['umount', '-f', f'{self.path}/root/compat/linux/proc']
        ]:
            su.Popen(
                command,
                stderr=su.PIPE
            ).communicate()

        if devfs_error:
            iocage_lib.ioc_common.logit({
                'level': 'EXCEPTION', 'message': devfs_error
            }, _callback=self.callback, silent=self.silent)

    def _remove_jail(self):
        """Tear down networking and remove the jail before its poststop hook."""
        ip4_addr = self.conf["ip4_addr"]
        ip6_addr = self.conf["ip6_addr"]
        vnet = self.conf["vnet"]
        dhcp = self.conf["dhcp"]
        debug_mode = True if os.environ.get(
            'IOCAGE_DEBUG', 'FALSE') == 'TRUE' else False
        nat = self.conf['nat']

        # We should still try to destroy the relevant networking
        # related resources if force is true, though we won't raise an
        # exception in that case
        # They haven't set an IP address, this interface won't exist
        destroy_nic = True if dhcp or ip4_addr != 'none' or \
            ip6_addr != 'none' or (nat and vnet) else False

        # disconnect vnet_interfaces from jail before stoppping
        if vnet and self.conf["vnet_interfaces"] != 'none':
            vnet_err = []

            for nic in self.conf["vnet_interfaces"].split(","):
                try:
                    iocage_lib.ioc_common.checkoutput(
                        ["ifconfig", f"{nic}", "-vnet", f"ioc-{self.uuid}"],
                        stderr=su.STDOUT)
                except su.CalledProcessError as err:
                    vnet_err.append(err.output.decode().rstrip())

        if vnet and destroy_nic:
            vnet_err = []

            for nic in self.nics.split(","):
                nic = nic.split(":")[0]
                try:
                    iocage_lib.ioc_common.checkoutput(
                        ["ifconfig", f"{nic}.{self.jid}", "destroy"],
                        stderr=su.STDOUT)
                except su.CalledProcessError as err:
                    vnet_err.append(err.output.decode().rstrip())

            if not vnet_err:
                iocage_lib.ioc_common.logit({
                    "level": "INFO",
                    "message": "  + Tearing down VNET OK"
                },
                    _callback=self.callback,
                    silent=self.silent)
            elif vnet_err and not self.force:
                iocage_lib.ioc_common.logit({
                    "level": "INFO",
                    "message": "  + Tearing down VNET FAILED"
                },
                    _callback=self.callback,
                    silent=self.silent)

                for v_err in vnet_err:
                    iocage_lib.ioc_common.logit({
                        "level": "WARNING",
                        "message": f"  {v_err}"
                    },
                        _callback=self.callback,
                        silent=self.silent)

        # A failed jail -c still owns its reservation and releases it after
        # this stop. Deleting it twice could remove a parallel start's set.
        devfs_error = None
        if self.cleanup_devfs:
            devfs_error = self._remove_devfs_ruleset()

        # Build up a jail stop command.
        cmd = ['jail', '-q']

        if debug_mode:
            cmd.append('-v')

        # We check for the existence of the jail.conf here as on iocage
        # upgrade people likely will not have these files. These files
        # will be written on the next jail start/restart.
        jail_conf_file = Path(f"/var/run/jail.ioc-{self.uuid}.conf")

        if jail_conf_file.is_file():
            cmd.extend(['-f', str(jail_conf_file)])

        cmd.extend(['-r', self.expected_jid or f'ioc-{self.uuid}'])

        stop = su.Popen(
            cmd,
            stdout=su.PIPE if not debug_mode else None,
            stderr=su.PIPE if not debug_mode else None
        )
        _, stop_err = stop.communicate()

        if stop.returncode:
            msg = f'  + Removing jail process FAILED:\n' \
                f'{stop_err.decode() if stop_err else stop.returncode}'
            iocage_lib.ioc_common.logit({
                'level': 'EXCEPTION',
                'message': msg
            },
                _callback=self.callback,
                silent=self.silent
            )
        else:
            msg = '  + Removing jail process OK'
            iocage_lib.ioc_common.logit({
                'level': 'INFO',
                'message': msg
            },
                _callback=self.callback,
                silent=self.silent
            )

            # If we have issues unlinking, don't let that get in the way. The
            # jail is already stopped so we're happy.
            if jail_conf_file.is_file():
                try:
                    jail_conf_file.unlink()
                except OSError:
                    pass
        return devfs_error

    def _remove_devfs_ruleset(self):
        ruleset = iocage_lib.ioc_json.IOCJson(
            self.path, suppress_log=True).json_get_value('devfs_ruleset')
        # Finish stopping and unmounting even if the kernel rejects cleanup.
        try:
            with iocage_lib.ioc_common.devfs_ruleset_lock():
                sets = su.check_output(['devfs', 'rule', 'showsets'],
                                       stderr=su.PIPE, text=True)
                if ruleset not in sets.splitlines():
                    return
                su.run(['devfs', 'rule', '-s', ruleset, 'delset'],
                       capture_output=True, text=True, check=True)
        except (OSError, su.CalledProcessError) as error:
            detail = getattr(error, 'stderr', None) or str(error)
            return (f'Could not clean up devfs ruleset {ruleset}: '
                    f'{detail.strip()}')
        iocage_lib.ioc_common.logit({
            'level': 'INFO',
            'message': f'  + Removing devfs_ruleset: {ruleset} OK'
        }, _callback=self.callback, silent=self.silent)
