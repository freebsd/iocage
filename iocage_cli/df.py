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
"""df module for the cli."""

import click
import iocage_lib.ioc_common as ioc_common
import iocage_lib.iocage as ioc
import texttable


@click.command(name="df", help="Show resource usage of all jails.")
@click.option(
    "--header",
    "-h",
    "-H",
    is_flag=True,
    default=True,
    help="For scripting, use tabs for separators.")
@click.option(
    "--long",
    "-l",
    "_long",
    is_flag=True,
    default=False,
    help="Show the full uuid.")
@click.option(
    "--sort",
    "-s",
    "_sort",
    default="name",
    nargs=1,
    help="Sorts the list by the given type")
def cli(header, _long, _sort):
    """Allows a user to show resource usage of all jails."""
    table = texttable.Texttable(max_width=0)
    jail_list = ioc.IOCage().df()

    sort = ioc_common.ioc_sort("df", _sort)
    jail_list.sort(key=sort)

    if header:
        table.header(["NAME", "CRT", "RES", "QTA", "USE", "AVA"])
        # We get an infinite float otherwise.
        table.set_cols_dtype(["t", "t", "t", "t", "t", "t"])
        table.add_rows(jail_list, header=False)

        ioc_common.logit({"level": "INFO", "message": table.draw()})
    else:
        for jail in jail_list:
            ioc_common.logit({"level": "INFO", "message": "\t".join(jail)})
