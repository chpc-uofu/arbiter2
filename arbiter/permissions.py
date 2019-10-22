# SPDX-License-Identifier: GPL-2.0-only
import subprocess
import logging
import time
import cinfo
import os
import pwd

startup_logger = logging.getLogger("arbiter_startup")
base_path = "/sys/fs/cgroup/{}/user.slice/user-{}.slice/{}"
req_write_files = [
    ("cpu", "cpu.cfs_quota_us"),
    ("memory", "memory.limit_in_bytes")
]
memsw_write_files = [
    ("memory", "memory.memsw.limit_in_bytes")
]


def has_write_permissions(uid, memsw=True):
    """
    Returns whether arbiter has permissions to write to cgroup files.

    uid: int
        The uid of the user.
    memsw: bool
        Whether or not to check for permissions on memsw files.
    """
    # Don't include memsw files if turned off
    to_check = req_write_files + (memsw_write_files if memsw else [])
    return all(
        os.access(base_path.format(controller, uid, filename), os.W_OK)
        for controller, filename in to_check
    )


def has_pss_permissions(pid=1):
    """
    Returns whether arbiter has permissions to read from /proc/<pid>/smaps (
    requires root or CAP_SYS_PTRACE cababilities).

    pid: int
        The pid to check. Defaults to 1 (systemd) since it will always exist.
    """
    try:
        with open("/proc/1/smaps", "r") as smaps:
            smaps.readlines()
        return True
    except PermissionError:
        return False


def check_permissions(sudoers, debug_mode, pss, memsw, groupname="arbiter", min_uid=0):
    """
    Checks whether arbiter has sufficient permissions to run.
    """
    write_perm = lambda u: has_write_permissions(u, memsw)
    sudoers_chown_perm = lambda u: set_file_permissions(u, groupname, memsw)

    sufficient = True
    if pss and not has_pss_permissions():
        startup_logger.error(
            "Arbiter does not have sufficient permissions to read "
            "/proc/<pid>/smaps (requires root or CAP_SYS_PTRACE "
            "capabilities) with pss = true"
        )
        sufficient = False

    # Skip cgroup permission tests if in debug mode (no limiting)
    if debug_mode:
        return sufficient

    if sudoers:
        # Adminstrators may want to completely ignore users below a threshold
        # (e.g. below 1000 to ignore service users) to prevent allow arbiter
        # from  tracking or limiting them. As a result, the sudoers file may
        # not have entries to allow changing ownership of cgroup files for
        # these users, so we shouldn't check them here.
        if not cinfo.safe_check_on_any_uid(sudoers_chown_perm, min_uid=min_uid):
            startup_logger.error(
                "Arbiter does not have sufficient permissions to use the "
                "requisite sudo calls for changing permissions on cgroups. "
                "See the sudoers file."
            )
            sufficient = False
    elif not cinfo.safe_check_on_any_uid(write_perm):
        # ^ (See comment and call above), it doesn't matter here since we
        # aren't using sudoers and we're only looking at the files.
        write_files = req_write_files + (memsw_write_files if memsw else [])
        euid = os.geteuid()
        username = pwd.getpwuid(euid)[0] if cinfo.passwd_entry(euid) else "?"
        startup_logger.error(
            "Arbiter does not have sufficient permissions to write out to "
            "all of the required cgroup files as %s (%s [effective]): %s.",
            username, euid, write_files
        )
        sufficient = False
    return sufficient


def turn_on_cgroups_acct(inactive_uid):
    """
    Turns on cgroup accounting for all user-$UID.slices by turning on
    accounting for a single inactive user. This is done by manually forcing a
    slice to be created. Since accounting for a cgroup implicitly turns it on
    for cgroups on the same level, this turns on accounting for all users.
    This works since slices are destroyed when the user logs out (or reboot).
    Thus, if a slice is created manually for a user that doesn't log out (and
    accounting is turned on), it will cause new slices to also have accounting
    turned on even when it seems are no users logged in, since there's a
    persisent slice. (no users causes accounting to be turned off, since
    theres no slice that can implicitly force accounting on for other slices).
    Returns whether it was successful in doing so.

    inactive_uid: int
        The uid of the inactive user.
    """
    # Runs a simple command with the user slice, slice kept until logout
    create_persistent = (
        "sudo /usr/bin/systemd-run --uid={} --slice=user-{} sleep 10"
    ).format(inactive_uid, inactive_uid)
    set_property = (
        "sudo /bin/systemctl set-property user-{}.slice CPUAccounting=true "
        "MemoryAccounting=true --no-ask-password"
    ).format(inactive_uid)
    try:
        subprocess.check_call(create_persistent.split())
        subprocess.check_call(set_property.split())
        return True
    except subprocess.CalledProcessError as err:
        startup_logger.error("Commands that failed: %s, %s", create_persistent,
                             set_property)
        startup_logger.error(err)
        return False


def set_file_permissions(uid, groupname, memsw=True):
    """
    Runs commands to set the correct group and permissions on files requiring
    write access by the service account. The commands must be present in the
    /etc/sudoers file to prevent errors. Will throw
    subprocess.CalledProcessError if the call fails due or FileNotFoundError
    if the user disappears.

    uid: str, int
        The uid of the user to set file permissions of.
    groupname: str
        The name of the group to apply permissions to.
    files: [str, ]
        A list of files to set permissions on.
    """
    # Don't include memsw files if turned off
    to_set = req_write_files + (memsw_write_files if memsw else [])
    for controller, filename in to_set:
        path = base_path.format(controller, uid, filename)
        chgrp = "sudo /bin/chgrp {} {}".format(groupname, path)
        chmod = "sudo /bin/chmod {} {}".format("g+w", path)
        if not run_file_command(chgrp) or not run_file_command(chmod):
            return False
    return True


def run_file_command(command):
    """
    Runs a command that modifies a file and either returns whether that
    command succeeded or raises a FileNotFoundError if the file doesn't exist.
    """
    try:
        subprocess.check_output(command, stderr=subprocess.STDOUT, shell=True)
    except subprocess.CalledProcessError as err:
        if err.returncode != 0:
            if err.output and "No such file" in str(err.output):
                raise FileNotFoundError("No such file or directory")
            return False
    return True
