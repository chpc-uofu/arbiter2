import subprocess
import logging
import cinfo
import os

startup_logger = logging.getLogger("arbiter_startup")
base_path = "/sys/fs/cgroup/{}/user.slice/user-{}.slice/{}"
req_write_files = [
    ("cpu", "cpu.cfs_quota_us"),
    ("memory", "memory.limit_in_bytes")
]
memsw_write_files = [
    ("memory", "memory.memsw.limit_in_bytes")
]


def has_cgroup_permissions(uid, memsw=True):
    """
    Returns whether arbiter has permissions to write to cgroup files. May
    throw FileNotFoundError if the user disappears.

    uid: int
        The uid of the user.
    memsw: bool
        Whether or not to check for permissions on memsw files (they do not
        exist in systemd cgroups hybrid mode)
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


def check_permissions(sudo_permissions, cfg):
    """
    Checks whether arbiter has sufficient permissions to run.
    """
    sufficient = True
    # Check pss
    if cfg.processes.pss and not has_pss_permissions():
        startup_logger.error(
            "Arbiter does not have sufficient permissions to read "
            "/proc/<pid>/smaps (requires root or CAP_SYS_PTRACE "
            "capabilities) with pss = true"
        )
        sufficient = False

    # Skip cgroup permission tests if in debug mode (no limiting)
    if cfg.general.debug_mode:
        return sufficient

    groupname = cfg.self.groupname
    memsw = cfg.processes.memsw
    # Wait until there are users in the cgroup hierarchy (we'll check a user)
    uids = cinfo.wait_till_uids(min_uid=cfg.general.min_uid)
    # Check if can write to required files
    if sudo_permissions:
        try:
            set_file_permissions(uids[0], groupname, memsw)
        except subprocess.CalledProcessError as err:
            startup_logger.error(err)
            startup_logger.error(
                "Arbiter does not have sufficient permissions to use the "
                "requisite sudo calls for changing permissions on cgroups. "
                "See the sudoers file."
            )
            sufficient = False
    elif not has_cgroup_permissions(uids[0], memsw):
        # Don't include memsw files if turned off
        files = req_write_files + (memsw_write_files if memsw else [])
        startup_logger.error("Failed to set permissions on one of %s", files)
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
    /etc/sudoers file to prevent errors.

    uid: str, int
        The uid of the user to set file permissions of.
    groupname: str
        The name of the group to apply permisssions to.
    files: [str, ]
        A list of files to set permissions on.
    """
    # Don't include memsw files if turned off
    to_set = req_write_files + (memsw_write_files if memsw else [])
    for controller, filename in to_set:
        path = base_path.format(controller, uid, filename)
        if not os.path.exists(path):
            continue  # No point trying to set limits on a nonexistent path
        chgrp = "sudo /bin/chgrp {} {}".format(groupname, path)
        subprocess.check_call(chgrp.split())
        chmod = "sudo /bin/chmod {} {}".format("g+w", path)
        subprocess.check_call(chmod.split())

