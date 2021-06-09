# SPDX-FileCopyrightText: Copyright (c) 2019-2020 Center for High Performance Computing <helpdesk@chpc.utah.edu>
#
# SPDX-License-Identifier: GPL-2.0-only

"""
Functions and classes related to the using of Arbiter2's privileged
capabilities.
"""

import logging
import os
import subprocess

import sysinfo
import cginfo

startup_logger = logging.getLogger("arbiter_startup")
cgroup_path_format = cginfo.base_path + "/{}/user.slice/user-{}.slice/{}"
req_write_files = [
    ("cpu", "cpu.cfs_quota_us"),
    ("memory", "memory.limit_in_bytes")
]
memsw_write_files = [
    ("memory", "memory.memsw.limit_in_bytes")
]


def has_write_permissions(uid, memsw=True, logger_instance=startup_logger):
    """
    Returns whether arbiter has permissions to write to cgroup files. May
    raise FileNotFoundError.

    uid: int
        The uid of the user.
    memsw: bool
        Whether or not to check for permissions on memsw files.
    logger_instance: logging.Logger
        The logger to use to print out information. Named weirdly since logger
        was already taken
    """
    logger_instance.debug("Checking write cgroup permissions on uid: %s", uid)
    # Don't include memsw files if turned off
    to_check = req_write_files + (memsw_write_files if memsw else [])

    for controller, filename in to_check:
        filepath = cgroup_path_format.format(controller, uid, filename)
        try:
            # Will raise FileNotFoundError if user disappeared
            with open(filepath, "w+"):
                continue
        except PermissionError:
            return False
    return True


def has_pss_permissions(logger_instance=startup_logger):
    """
    Returns whether arbiter has permissions to read from /proc/<pid>/smaps (
    requires root or CAP_SYS_PTRACE cababilities).
    """
    logger_instance.debug("Checking root or CAP_SYS_PTRACE capability")
    try:
        with open("/proc/1/smaps", "r") as smaps:
            smaps.readlines()
        return True
    except PermissionError:
        return False


def has_sudoers_permissions(groupname, min_uid=0, memsw=True,
                            logger_instance=startup_logger):
    """
    Returns whether arbiter has permissions to execute sudoers commands to
    chown and chmod cgroup directories so it can write out quotas. It checks
    for both an arbitrary active uid, as well as the min_uid since there might
    be a inconsistency between the configured min_uid and the min uid possible
    in sudoers (e.g. if an admin assumes min_uid is not inclusive: putting 999
    but only including 4 digits in sudoers, which would cause arbiter to fail
    when it encounters user 999).
    """
    active_uid = cginfo.wait_till_uids(min_uid=min_uid)[0]
    try:
        logger_instance.debug("Checking sudoers file permissions for an "
                              "active uid: %s", min_uid)
        if not set_file_permissions(active_uid, groupname, memsw, logger_instance):
            return False
    except FileNotFoundError:
        # sudo command was successfully executed, but the user wasn't active on
        # the machine
        pass

    if min_uid == active_uid:  # No point in checking
        return True

    try:
        logger_instance.debug("Checking sudoers file permissions for min uid: "
                              "%s", min_uid)
        if not set_file_permissions(min_uid, groupname, memsw, logger_instance):
            logger_instance.error("\t ^ Failed for min uid %s, but not for "
                                  "uid %s (sudoers and min_uid config mismatch?).",
                                  min_uid, active_uid)
            return False
    except FileNotFoundError:
        pass
    return True


def check_permissions(sudoers, debug_mode, pss, memsw, groupname="arbiter",
                      min_uid=0, logger_instance=startup_logger):
    """
    Checks whether arbiter has sufficient permissions to run.
    """
    sufficient = True
    if pss and not has_pss_permissions(logger_instance):
        logger_instance.error(
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
        if not has_sudoers_permissions(groupname, min_uid=min_uid, memsw=memsw,
                                       logger_instance=logger_instance):
            logger_instance.error(
                "Arbiter does not have sufficient permissions to use the "
                "requisite sudo calls for changing permissions on cgroups. "
                "See the sudoers file (maybe requiretty?)."
            )
            sufficient = False

    # Note: the safe_check_on_any_uid() function is a function that takes in
    #       a function and executes it with a active uid. If the procedure
    #       succeeds, then the result will be returned. However, if the user
    #       is detected to have disappeared, another uid will be tried until
    #       the procedure succeeds. This ensures checks aren't subject to race
    #       conditions where users might logout during a check (but perhaps is
    #       a bit too clever, given the need for this comment).
    elif not cginfo.safe_check_on_any_uid(lambda u: has_write_permissions(u, memsw, logger_instance)):
        # ^ (See comment and call above for min uid), it doesn't matter here
        # since we aren't using sudoers and we're only looking at the files.
        write_files = req_write_files + (memsw_write_files if memsw else [])
        euid = os.geteuid()
        username = sysinfo.getpwuid_cached(euid)[0] if sysinfo.passwd_entry(euid) else "?"
        logger_instance.error(
            "Arbiter does not have sufficient permissions to write out to "
            "all of the required cgroup files as %s (%s [effective]): %s.",
            username, euid, write_files
        )
        sufficient = False
    return sufficient


def turn_on_cgroups_acct(inactive_uid, logger_instance=startup_logger):
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
    # Runs a simple command with the user slice, slice kept until login/logout
    # Also, I'm pretty sure this hack is a systemd bug as well. At the very
    # least it seems to work for the versions of systemd that have that
    # accounting disappearing bug...
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
        logger_instance.error("Commands that failed: %s, %s", create_persistent,
                              set_property)
        logger_instance.error(err)
        return False


def set_file_permissions(uid, groupname, memsw=True, logger_instance=startup_logger):
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
    logger: logging.Logger()
        A logger to log errors out to.
    """
    # Don't include memsw files if turned off
    to_set = req_write_files + (memsw_write_files if memsw else [])
    for controller, filename in to_set:
        path = cgroup_path_format.format(controller, uid, filename)
        chgrp = "sudo /bin/chgrp {} {}".format(groupname, path)
        chmod = "sudo /bin/chmod {} {}".format("g+w", path)
        if not run_file_command(chgrp, logger_instance) or not run_file_command(chmod, logger_instance):
            return False
    return True


def run_file_command(command, logger_instance=startup_logger):
    """
    Runs a command that modifies a file and either returns whether that
    command succeeded or raises a FileNotFoundError if the file doesn't exist.
    """
    try:
        subprocess.check_output(command, stderr=subprocess.STDOUT, shell=True)
    except subprocess.CalledProcessError as err:
        if err.returncode != 0:
            output = err.output.decode() if err.output else ""
            if output and "No such file" in output:
                # User probably disappeared before we could set limits
                raise FileNotFoundError("No such file or directory")
            if output:
                logger_instance.error(output)
            logger_instance.error("Command '%s' failed with exit code %s",
                                  command, err.returncode)
            return False
    return True


class AccountingUserSlice(cginfo.UserSlice):
    """
    A child cginfo.UserSlice object that can recreate a user-$UID.slice.
    cgroup using sudoers permissions (or root) if it disappears.

    This is used to ensure a user-$UID.slice cgroup always exists in
    user.slice/ since on RHEL/CentOS 7 systemd versions, systemd will
    erroneously turn off cgroup accounting for per-user instances when there
    are no user-$UID.slice instances in user.slice/.

    Furthermore, it should be noted that the only reason this code exists is
    because the way we manually create a slice on startup is fairly hacky in
    the sense that if our choosen accounting user ever logs in or out (or an
    actor authenticates via a local PAM authentication thanks to a PAM systemd
    module), then it causes systemd to cleanup the slice and we have to
    recreate it here. See turn_on_cgroups_acct() for details.
    """

    def __init__(self, acct_uid, logger_instance):
        """
        Initializes the AccountingUserSlice object.
        """
        super().__init__(acct_uid)
        self.logger = logger_instance

    def create_slice_if_needed(self):
        """
        Checks whether the accounting slice exists and recreates it if needed.
        """
        if not any(map(self.controller_exists, ("memory", "cpu"))):
            self.logger.warning("Persistent user has disappared. Attempting "
                                "to recreate the slice...")
            turn_on_cgroups_acct(self.uid, self.logger)
