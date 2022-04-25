#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2019-2020 Center for High Performance Computing <helpdesk@chpc.utah.edu>
#
# SPDX-License-Identifier: GPL-2.0-only

"""
A starting wrapper around main.py that gets arguments and sets the etc
modules context.
"""

import argparse
import logging
import os
import sys
import time
import toml

import cfgparser
import cginfo
import logger
import permissions

startup_logger = logging.getLogger("arbiter_startup")
startup_logger.setLevel(logging.DEBUG)
service_logger = logging.getLogger("arbiter_service")
debug_logger = logging.getLogger("arbiter")


def arguments():
    """
    Defines the arguments that Arbiter2 takes in and returns them.
    """
    desc = ("Version 2 of Arbiter; uses cgroups for monitoring and managing "
            "behavior.")
    parser = argparse.ArgumentParser(description=desc)
    parser.add_argument("-a", "--accounting",
                        type=int,
                        nargs="?",
                        const=str(os.getuid()),
                        help="Turn on cgroup cpu and memory accounting for "
                             "users on startup and after a period when there "
                             "are no users. This is done by turning on "
                             "accounting for a specific inactive user, which "
                             "implicitly turns it on for other users. The "
                             "user must not log in, since logging out "
                             "destroys its permanent slice. See the install "
                             "guide for more details. Defaults to the uid of "
                             "user who runs the program.",
                        dest="acct_uid")
    parser.add_argument("-e", "--etc",
                        type=str,
                        help="Set the directory in which configurable modules "
                             "are loaded from (e.g. integrations.py). If a "
                             "required module does not exist in the new "
                             "directory, the default module will be loaded "
                             "from ../etc",
                        dest="etc")
    parser.add_argument("--exit-file",
                        metavar="FILE",
                        type=str,
                        help="If the specified file is updated in any way, "
                             "as well as being owned by the group name "
                             "specified in the configuration, Arbiter will "
                             "exit as a failure. This allows for a hacky way "
                             "to restart Arbiter with changes using systemd.",
                        dest="exit_file")
    parser.add_argument("--rhel7-compat",
                        action="store_true",
                        help="Deprecated; custom RHEL/CentOS 7 compatability "
                             "mode no longer needed. Among other things, "
                             "this flag still causes Arbiter2 to internally "
                             "replace memory usage from cgroups with process "
                             "memory usage data.",
                        dest="rhel7_compat")
    parser.add_argument("-s", "--sudo",
                        action="store_true",
                        help="Use sudoers permissions to write out limits and "
                             "to enable cgroup accounting. See the install "
                             "guide for more information on how to set this "
                             "up.",
                        dest="sudo_permissions")
    parser.add_argument("-p", "--print",
                        action="store_true",
                        help="Print out the application logging to stdout. By "
                             "default, logging is automatically logged out to "
                             "a file specified in the config, regardless of "
                             "this flag.",
                        dest="print_logs")
    parser.add_argument("--version",
                        action="store_true",
                        help="Show version information of this arbiter.py "
                             "instance.",
                        dest="version")
    env = parser.add_mutually_exclusive_group()
    env.add_argument("-q", "--quiet",
                     action="store_true",
                     help="Only outputs critical information to stdout if the "
                          "-p flag is used. Startup logging ignores this.",
                     dest="quiet")
    env.add_argument("-v", "--verbose",
                     action="store_true",
                     help="Turns on debugging output to stdout if the -p flag "
                          "is used.",
                     dest="verbose")
    parser.add_argument("-g", "--config",
                        type=str,
                        nargs="+",
                        default=["../etc/config.toml"],
                        help="The configuration files to use. Configs will be "
                             "cascaded together starting at the leftmost (the "
                             "primary config) going right (the overwriting "
                             "configs).",
                        dest="configs")
    args = parser.parse_args()

    if args.version:
        try:
            with open("version.txt") as vf:
                print(vf.readline().strip())
        except FileNotFoundError:
            print("No version file found.")
            sys.exit(1)
        sys.exit(0)

    # --rhel7-compat is not longer needed since we get cgroup data from the
    # memory.stat file now, rather than memory.usage_in_bytes which used to
    # include kernel memory usage that couldn't be subtracted out due to a
    # bug only in CentOS 7
    if args.rhel7_compat:
        startup_logger.warning("--rhel7-compat is deprecated; custom "
                               "RHEL/CentOS 7 compatability mode is no longer "
                               "needed (but the effect is still applied).")

    # insert the default etc/ path, used as a fallback
    insert("../etc")
    if args.etc:
        # insert the new etc/ path
        insert(args.etc)
    return args


def insert(context):
    """
    Inserts a path to into the python path.

    context: str
        The path to insert into the python path.
    """
    context_path = os.path.dirname(__file__)
    sys.path.insert(0, os.path.abspath(os.path.join(context_path, context)))


def setup_config(config_files):
    """
    Loads the given configs. If there is a problem, exits with a error code of
    2.

    config_files: iter
        A iterable of configuration files to load.
    """
    startup_logger.info("Importing and validating configuration...")
    try:
        if not cfgparser.load_config(*config_files):
            startup_logger.error("There was a problem with the configuration. "
                                 "Please check above.")
            sys.exit(2)
    except (TypeError, toml.decoder.TomlDecodeError) as err:
        startup_logger.error("Configuration error: %s", str(err))
        sys.exit(2)


def setup_logging(args, cfg, shared):
    """
    Sets up logging for the rest of arbiter based on a given ArgumentParser
    and the configuration.

    args: ArgumentParser()
        The arguments from a argument parser.
    cfg: cfgparser.Configuration()
        The configuration for arbiter.
    shared: cfgparser.Configuration()
        The shared values for arbiter.
    """
    if args.print_logs:
        stream_level = logging.INFO
        if args.quiet:
            stream_level = logging.CRITICAL
        elif args.verbose:
            stream_level = logging.DEBUG
        logger.add_stream(debug_logger, level=stream_level)
        logger.add_stream(service_logger, level=stream_level)

    log_location = cfg.database.log_location
    if not os.path.exists(log_location):
        startup_logger.error("%s directory does not exist!", log_location)
        sys.exit(2)
    elif not os.access(cfg.database.log_location, os.W_OK):
        startup_logger.error("Not enough permissions to write to %s!", log_location)
        sys.exit(2)

    logger.add_rotating_file(
        debug_logger,
        cfg.database.log_location + "/" + shared.debuglog_prefix,
        shared.log_datefmt + ".log",
        cfg.database.log_rotate_period,
        level=logging.DEBUG
    )
    logger.add_rotating_file(
        service_logger,
        cfg.database.log_location + "/" + shared.servicelog_prefix,
        shared.log_datefmt + ".log",
        cfg.database.log_rotate_period,
        fmttr=logger.service_fmttr,
        level=logging.INFO
    )
    level_str = "CRITICAL" if args.quiet else "DEBUG" if args.verbose else "INFO"
    startup_logger.info("Operational logging (arbiter.module) will be sent "
                        "to %s%s with a verbosity of %s.",
                        log_location,
                        " and stdout" if args.print_logs else "",
                        level_str)


def pre_run(args):
    """
    Makes preparations before main.run() is ran.
    """
    # Load the config
    setup_config(args.configs)
    cfg = cfgparser.cfg

    # Turn on accounting
    startup_logger.info("Checking that cgroup accounting is enabled...")
    if args.acct_uid:
        startup_logger.info("Attempting to turn on accounting...")
        try:
            success = permissions.turn_on_cgroups_acct(
                args.acct_uid,
                logger_instance=startup_logger
            )
        except OSError as err:
            logger.debug(err)
            success = False
        if not success:
            startup_logger.error("Failed to turn on accounting. Exiting.")
            sys.exit(2)

    # Make sure cgroup hierarchy already exists
    elif not acct_on():
        startup_logger.error("cgroup hierarchy doesn't exist. See install "
                             "guide. Exiting.")
        sys.exit(2)

    # Check permissions
    startup_logger.info("Checking if arbiter has proper permissions...")
    has_permissions = permissions.check_permissions(
        args.sudo_permissions,
        cfg.general.debug_mode,
        cfg.processes.pss,
        cfg.processes.memsw,
        groupname=cfg.self.groupname,
        min_uid=cfg.general.min_uid,
        logger_instance=startup_logger
    )
    if not has_permissions:
        startup_logger.error("Arbiter does not have sufficient permissions "
                             "to continue. Exiting.")
        sys.exit(2)

    # Set up the opertional logger (arbiter.modulename)
    setup_logging(args, cfg, cfgparser.shared)

    if cfg.general.debug_mode:
        startup_logger.info("Permissions and quotas won't be set since debug "
                            "mode is on.")


def acct_on(controllers=("memory", "cpu", "cpuacct")):
    """
    Returns whether accounting is on for a user.

    uid: int
        The uid of the user slice cgroup to check.
    controllers: iter
        The cgroup controllers to check.
    """
    # First check that both memory, cpu and cpuacct is on globally. Then
    # check user.slice, then per-user checking
    root_slice = cginfo.SystemdCGroup("", "")
    for controller in controllers:
        if not root_slice.controller_exists(controller):
            startup_logger.error("root cgroup controller %s does not exist "
                                 "(%s).", controller,
                                 root_slice.controller_path(controller))
            return False

    allusers_slice = cginfo.AllUsersSlice()
    for controller in controllers:
        if not allusers_slice.controller_exists(controller):
            startup_logger.error("user.slice cgroup controller %s does not "
                                 "exist (%s).", controller,
                                 allusers_slice.controller_path(controller))
            return False

    our_uid = os.getuid()
    while True:
        # Hey lets not quietly spin if we're waiting for users to be on the
        # machine (unlike previously).
        #
        # FIXME? it's kinda sorta ok to spin since there are no users on the
        #        machine, but locking up Arbiter2 on startup doesn't sound
        #        like a great user experience either, especially if our checks
        #        are wrong... (they have been before).
        if not cginfo.current_cgroup_uids():
            startup_logger.info("Waiting for users on the machine to check "
                                "for whether accounting is on for them...")

        # CPUAccounting/MemoryAccounting for ourselves via the service file
        # in newer systemd versions may not work (sometimes systemd only turns
        # on acct for arbiter2.service, not for other users), so let's not
        # trust ourselves to be the authority for whether accounting is on
        for uid in cginfo.wait_till_uids(blacklist=(our_uid,)):
            user_slice = cginfo.UserSlice(uid)
            seen_controllers = tuple(filter(user_slice.controller_exists, controllers))
            is_acct_on = len(seen_controllers) == len(controllers)
            # FIXME: This is a race condition (though very unlikely). If
            #        someone logs out after they are picked for the check,
            #        but then log in after the seen_controllers check then
            #        the systemd controller will exist and the function may
            #        incorrectly return that accounting is off. I don't know
            #        how to fix this... b/c we have to check at least three
            #        different controllers atomicly
            if not is_acct_on:
                if not user_slice.controller_exists("systemd"):
                    # User isn't in systemd controller; they disappeared,
                    # don't trust the check for controllers
                    startup_logger.debug("Skipping cgroup accounting check "
                                         "with uid %s; disappeared while "
                                         "checking.", uid)
                    continue  # "for else:" will break out of double loop

                missing_controllers = set(controllers) - set(seen_controllers)
                missing_controllers_str = ", ".join(missing_controllers)
                startup_logger.error("user.slice/user-%s.slice cgroup "
                                     "controller(s) %s does not exist.", uid,
                                     missing_controllers_str)
                return False
            break  # It's all good, "for else:" won't be triggered
        else:
            # No valid users, try again
            time.sleep(0.2)
            continue
        return True


if __name__ == "__main__":
    args = arguments()
    pre_run(args)
    import main
    startup_logger.info("Arbiter has started.")
    sys.exit(main.run(args))
