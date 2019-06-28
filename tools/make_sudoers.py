import argparse


def generate_sudoers(name, groupname, digits, run_uid=None):
    """
    Prints out a sudoers file with the commands and files.
    """
    files = [
        "/sys/fs/cgroup/cpu/user.slice/user-{}.slice/cpu.cfs_quota_us",
        "/sys/fs/cgroup/memory/user.slice/user-{}.slice/memory.limit_in_bytes",
        "/sys/fs/cgroup/memory/user.slice/user-{}.slice/memory.memsw.limit_in_bytes"
    ]
    commands = ["/bin/chmod g+w", f"/bin/chgrp {groupname}"]
    intro = f"{name} ALL=(ALL) NOPASSWD:"
    filler = "[0-9]"
    # if specific uid used for systemd-run
    if run_uid and run_uid != -1:
        print("# systemd-run")
        print(intro, f"/usr/bin/systemd-run --uid={run_uid} --slice=user-{run_uid} sleep 10")
    # uid 0-digit, might be smart to use 4+ digits for uids less than 1000
    for d in range(1, digits + 1):
        digit_string = filler * d
        print("#", d, "digit(s)")
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
        help="The number of possible uid digits to generate for each command.",
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
    generate_sudoers(args.user, args.group, args.digits, run_uid=args.run_uid)
