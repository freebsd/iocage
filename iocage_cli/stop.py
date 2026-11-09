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
"""stop module for the cli."""
import click

import iocage_lib.ioc_common as ioc_common
import iocage_lib.iocage as ioc

__rootcmd__ = True


@click.command(name='stop', help='Stops the specified jails or ALL.')
@click.option(
    '--rc', default=False, is_flag=True,
    help='Will stop all jails with boot=on, in the specified order with '
         'higher value for priority stopping first.'
)
@click.option(
    '-f', '--force', default=False, is_flag=True,
    help='Skips all pre-stop actions like stop services. Gently shuts '
         'down and kills the jail process.'
)
@click.option(
    '--ignore', '-i', default=False, is_flag=True,
    help='Suppress exceptions for jails which fail to stop'
)
@click.option(
    '--parallel', default=False, is_flag=True,
    help='Stop jails together within each priority group.'
)
@click.option(
    '--jobs', type=click.IntRange(min=1), default=None,
    help='Limit concurrent stops; requires --parallel. Default: whole group.'
)
@click.argument("jails", nargs=-1)
def cli(rc, force, jails, ignore, parallel, jobs):
    """
    Looks for the jail supplied and passes the uuid, path and configuration
    location to stop_jail.
    """
    if not jails and not rc:
        ioc_common.logit({
            "level": "EXCEPTION",
            "message": 'Usage: iocage stop [OPTIONS] JAILS...\n'
                       '\nError: Missing argument "jails".'
        })

    if jobs is not None and not parallel:
        raise click.UsageError('--jobs requires --parallel')

    if parallel:
        if rc and jails:
            raise click.UsageError('--rc cannot be combined with jail names')

        if 'ALL' in jails and len(jails) != 1:
            raise click.UsageError('ALL cannot be combined with jail names')

        ioc.IOCage(rc=rc, silent=rc).stop(
            force=force, ignore_exception=ignore, parallel=True,
            jobs=jobs, jails=jails or None
        )
    elif rc:
        ioc.IOCage(rc=rc, silent=True).stop(
            force=force, ignore_exception=ignore
        )
    else:
        for jail in jails:
            ioc.IOCage(jail=jail, rc=rc).stop(
                force=force, ignore_exception=ignore
            )
