#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2019-2020 Center for High Performance Computing <helpdesk@chpc.utah.edu>
#
# SPDX-License-Identifier: GPL-2.0-only
import argparse


def generate_sudoers(name, groupname, digits, run_uid=None, min_uid=0):
    """
    Prints out a sudoers file with the commands and files.
    """
    # if specific uid used for systemd-run
    if run_uid and run_uid != -1:
        print("# systemd-run")
        print(intro, f"/usr/bin/systemd-run --uid={run_uid} --slice=user-{run_uid} sleep 10")

    # generate the digits with the length of min uid, but limit the range,
    # unlike in the next section
    #
    # e.g. min_uid = 1002
    # arbiter ALL=(ALL) NOPASSWD: /bin/systemctl ... user-[1-9][0-9][0-9][2-9].slice
    # ...
    min_uid_digit_len = len(str(min_uid))
    digit_string = ""
    for digit_index in range(min_uid_digit_len):
        digit = int(str(min_uid)[digit_index])  # e.g. 1 for 1000 or 9 for 999
        if digit == 9:
            digit_string += str(digit)
        else:
            digit_string += f"[{digit}-9]"

    digit_msg = "# " + str(min_uid_digit_len) + " digit(s)"
    if min_uid > 0:
        digit_msg += ", but min uid " + str(min_uid) + " and above"
    print(digit_msg)
    print_for_digit_string(digit_string, name, groupname)

    # generate _all_ the digit range with the length greater than the min uid,
    # up to the max digits given
    # e.g. min_uid = 1000 -> range(5, digits + 1)
    # arbiter ALL=(ALL) NOPASSWD: /bin/systemctl ... user-[0-9][0-9][0-9][0-9][0-9].slice
    # ...
    for d in range(min_uid_digit_len + 1, digits + 1):
        digit_string = "[0-9]" * d
        print("#", d, "digit(s)")
        print_for_digit_string(digit_string, name, groupname)


def print_for_digit_string(digit_string, name, groupname, run_uid=None):
    """
    Generates sudoers lines for the given digit string.

    digit_string: str
        A string to put in place of digits. e.g. '[0-9][0-9][0-9][0-9]' for
        all four digit uids.
    """
    files = [
        "/sys/fs/cgroup/cpu/user.slice/user-{}.slice/cpu.cfs_quota_us",
        "/sys/fs/cgroup/memory/user.slice/user-{}.slice/memory.limit_in_bytes",
        "/sys/fs/cgroup/memory/user.slice/user-{}.slice/memory.memsw.limit_in_bytes"
    ]
    commands = ["/bin/chmod g+w", f"/bin/chgrp {groupname}"]
    intro = f"{name} ALL=(ALL) NOPASSWD:"
    for cmd in commands:
        for f in files:
            print(intro, cmd, f.format(digit_string))

    print(intro, f"/bin/systemctl set-property user-{digit_string}.slice CPUAccounting=true "
                 "MemoryAccounting=true --no-ask-password")
    if run_uid == -1:
        print(intro, f"/usr/bin/systemd-run --uid={digit_string} --slice=user-{digit_string} sleep 10")


def arguments():
    desc = "Generate a sudoers file for Arbiter2."
    parser = argparse.ArgumentParser(description=desc)
    parser.add_argument(
        "--user",
        "-u",
        dest="user",
        default="arbiter",
        help="The user who will run sudo commands.",
    )
    parser.add_argument(
        "--group",
        "-g",
        dest="group",
        default="arbiter",
        help="The group to own cgroup files.",
    )
    parser.add_argument(
        "--num-digits",
        "-n",
        dest="digits",
        type=int,
        default=15,
        help="The maximum number of possible uid digits to generate for each command. Defaults to 15.",
    )
    parser.add_argument(
        "--min-uid",
        "-m",
        dest="min_uid",
        type=int,
        default=1000,
        help="The minimum uid that will be considered. Digits less than "
             "this will not be included in the outputted sudoers file. The "
             "given uid must be consistent with the configuration's "
             "general.min_uid setting. Defaults to 1000.",
    )
    parser.add_argument(
        "--run-uid",
        "-r",
        dest="run_uid",
        nargs="?",
        const=-1,
        type=int,
        help="Sets the uid that will be used to turn on cgroup accounting (see install guide).",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = arguments()
    generate_sudoers(args.user, args.group, args.digits, run_uid=args.run_uid,
                     min_uid=args.min_uid)
