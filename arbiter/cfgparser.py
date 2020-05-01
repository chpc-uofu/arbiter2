#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
import argparse
import copy
import grp
import logging
import os
import re
import socket
import sys
import pwd
import toml


logger = logging.getLogger("arbiter_startup")

# Special variables that replace values in strings. The key is a regex
# expression and the second is a function that takes in the key's first regex's
# group and returns the replacement value for the entire key.
special_vars = {
    "(%H)": lambda _: socket.gethostname(),
    r"${(\w+)}": lambda var: os.environ.get(var, "")
}


class Configuration(argparse.Namespace):
    """
    A simple object for storing a configuration.
    """

    def __init__(self, config):
        """
        Initializes the configuration with a config dictionary.

        config: dict
            The dictionary to initialize the config with.
        """
        self.add_subconfig(config)

    def add_subconfig(self, config):
        """
        Adds a subconfig dictionary.

        config: dict
            The dictionary to insert.
        """
        for key, value in config.items():
            if isinstance(value, dict):
                setattr(self, key, Configuration(value))
            else:
                setattr(self, key, value)


# The global configuration used in different modules.
cfg = Configuration({})

# A private global config used for non public settings (arbitrarily changing
# these may break the program).
shared = Configuration({
    "req_write_files": [
        "/sys/fs/cgroup/cpu/user.slice/user-{}.slice/cpu.cfs_quota_us",
        "/sys/fs/cgroup/memory/user.slice/user-{}.slice/memory.limit_in_bytes",
        "/sys/fs/cgroup/memory/user.slice/user-{}.slice/memory.memsw.limit_in_bytes"
    ],
    "other_processes_label": "other processes",
    "statusdb_name": "statuses.db",
    "status_tablename": "status",
    "badness_tablename": "badness",
    "logdb_name": "log.{}.db",
    "debuglog_prefix": "debug",
    "servicelog_prefix": "log",
    "log_datefmt": "%Y-%m-%d"
})


class ValidationProtocol():
    """
    A class for storing how to validate a given item.
    """

    def __init__(self, types, *validations, default_value=None):
        """
        Stores the allowed types, the default value and validity/warning
        checks.

        types: tuple, type
            A tuple of types, or a single type, used for checking if the
            associated value is valid.
        *validations: Validate().
            A series of Validate() objects used for checking whether the value
            is valid.
        default_value: object
            Some default value.
        """
        self.types = types if isinstance(types, tuple) else tuple([types])
        self.default_value = default_value
        self.validations = list(validations)


class Validate():
    """
    Determines whether something is invalid (will cause problems when the
    program is run).
    """

    def __init__(self, check, excuse, pedantic=False):
        """
        Stores a validitiy check and a corresponding error message.

        check: func
            A function that takes in a single parameter and returns whether it
            is valid.
        excuse: str
            A message appended to the error message.
        """
        self.check = check
        self.err_message = "{}.{} = {} is not valid since it " + excuse
        self.pedantic = pedantic


class Warn(Validate):
    """
    Determines whether something is wrong, but valid (it won't cause problems
    when the program is run).
    """

    def __init__(self, check, excuse, pedantic=False):
        """
        Stores a warning check and a corresponding error message.

        check: func
            A function that takes in a single parameter and returns whether it
            is valid.
        excuse: str
            A message appended to the error message.
        """
        super().__init__(check, "", pedantic=pedantic)
        self.err_message = "{}.{} = {} " + excuse


def check_exception(check, exception, *args, **kwargs):
    """
    Given a check function and an exception, returns the check evaluated. If
    there was a exception, returns False. The *args and **kwargs are passed
    into the check function.

    check: func
        A function that returns whether the *args and **kwargs are valid.
    exception: Exception, (Exception, )
        A exception, or a tuple of exceptions, to catch.
    """
    try:
        return check(*args, **kwargs)
    except exception:
        return False


isaboveeq5 = Validate(
    lambda num: num >= 5,
    "is not above (or equal) 5."
)
isaboveeq2 = Validate(
    lambda num: num >= 2,
    "is not above (or equal) 2."
)
isaboveeq1 = Validate(
    lambda num: num >= 1,
    "is not above (or equal) 1."
)
isbeloweq5 = Validate(
    lambda num: num <= 5,
    "is not below (or equal) 5"
)
isbeloweq1 = Validate(
    lambda num: num <= 1,
    "is not below (or equal) 1"
)
nonzerolen = Validate(
    lambda items: len(items) > 0,
    "is empty."
)
all_are_str = Validate(
    lambda items: all(isinstance(item, str) for item in items),
    "is not all strings."
)
all_are_int = Validate(
    lambda items: all(isinstance(item, int) for item in items),
    "is not all ints."
)
dir_exists = Validate(
    lambda path: os.path.exists(path) and os.path.isdir(path),
    "does not exist (or arbiter cannot see it due to permissions).",
    pedantic=True
)
file_exists = Warn(
    lambda path: path == "" or (os.path.exists(path) and os.path.isfile(path)),
    "does not exist (or arbiter cannot see it due to permissions).",
    pedantic=True
)
group_exists = Warn(
    lambda name: check_exception(
        lambda n: grp.getgrnam(n) is not None, KeyError, name
    ),
    "doesn't correspond to a group that exists.",
    pedantic=True
)
can_ping = Warn(
    lambda loc: os.system("ping -c 1 {} > /dev/null".format(loc)) == 0,
    "is not responding to a ping.",
    pedantic=True
)
uids_exist = Warn(
    lambda uids: all([
        check_exception(
            lambda u: pwd.getpwuid(u) is not None, KeyError, uid
        )
        for uid in uids
    ]),
    "not all uids exist.",
    pedantic=True
)
gids_exist = Warn(
    lambda gids: all([
        check_exception(
            lambda g: grp.getgrgid(g) is not None, KeyError, gid
        )
        for gid in gids
    ]),
    "not all gids exist.",
    pedantic=True
)
has_memsw = Validate(
    lambda memsw: os.path.exists(
            "/sys/fs/cgroup/memory/memory.memsw.usage_in_bytes"
        ) if memsw is True else True,
    "because cgroups memsw isn't available on this machine. Enable it for "
    "the machine (via CONFIG_MEMCG_SWAP=yes and either "
    "CONFIG_MEMCG_SWAP_ENABLED=yes or swapaccount=1 boot parameters), or "
    "disable it in the settings for arbiter to function correctly. See "
    "https://www.kernel.org/doc/Documentation/cgroup-v1/memory.txt for info "
    "on memsw",
    pedantic=True
)
cpu_period_us = 100000  # default for linux
valid_cpu_limit = Warn(
    # FIXME: Writing out the max long size in cpuacct.cfg_quota_us gives us
    #        18446744073709550 for some reason. The kernel code uses a int64
    #        to store the value, so I dunno why it's capped at this.
    lambda cpusize: cpusize * cpu_period_us <= 2**64-1,
    "* default_cpu_period_us > LLONG_MAX. The cgroup CPU limit may not be "
    "applied (depends on cfs_period_us)"
)
valid_mem_limit = Warn(
    lambda memsize: memsize * 1073742000 <= 2**64-1,  # kernel uses int64
    "* bytes_in_gb > LLONG_MAX. The cgroup memory limit cannot be applied."
)


# Stores the validation protocols and layout of the base configuration.
base_validation_config = {
    "general": {
        "debug_mode": ValidationProtocol(bool),
        "arbiter_refresh": ValidationProtocol(int, isaboveeq5),
        "history_per_refresh": ValidationProtocol(int, isaboveeq1),
        "poll": ValidationProtocol(int, isaboveeq2, default_value=2),
        "min_uid": ValidationProtocol(int, default_value=1000)
    },
    "self": {
        "groupname": ValidationProtocol(str, group_exists)
    },
    "badness": {
        "max_history_kept": ValidationProtocol(int, isaboveeq1),
        "cpu_badness_threshold": ValidationProtocol((float, int), isbeloweq1),
        "mem_badness_threshold": ValidationProtocol((float, int), isbeloweq1),
        "time_to_max_bad": ValidationProtocol(int),
        "time_to_min_bad": ValidationProtocol(int),
        "cap_badness_incr": ValidationProtocol(bool, default_value=True),
        "imported_badness_timeout": ValidationProtocol(int, default_value=3600)
    },
    "email": {
        "email_domain": ValidationProtocol(str),
        "from_email": ValidationProtocol(str),
        "admin_emails": ValidationProtocol(list, all_are_str),
        "mail_server": ValidationProtocol(str, can_ping),
        "keep_plots": ValidationProtocol(bool),
        "reply_to": ValidationProtocol(str, default_value=""),
        "plot_location": ValidationProtocol(
            str,
            dir_exists,
            default_value="../logs/%H"
        ),
        "plot_suffix": ValidationProtocol(str, default_value="%H_event"),
        "plot_process_cap": ValidationProtocol(int, isaboveeq1, default_value=20),
        "table_process_cap": ValidationProtocol(int, isaboveeq1, default_value=12)
    },
    "database": {
        "log_location": ValidationProtocol(
            str,
            dir_exists,
            default_value="../logs/%H"
        ),
        "log_rotate_period": ValidationProtocol(
            int,
            isaboveeq1,
            default_value=7
        )
    },
    "processes": {
        "memsw": ValidationProtocol(bool, has_memsw),
        "pss": ValidationProtocol(bool),
        "whitelist_other_processes": ValidationProtocol(
            bool,
            default_value=True
        ),
        "whitelist": ValidationProtocol(list, all_are_str, default_value=[]),
        "whitelist_file": ValidationProtocol(
            str,
            file_exists,
            default_value=""
        ),
        "proc_owner_whitelist": ValidationProtocol(
            list,
            all_are_int,
            uids_exist,
            default_value=[0]
        )
    },
    "status": {
        "order": ValidationProtocol(list, nonzerolen, all_are_str),
        "fallback_status": ValidationProtocol(str),
        "div_cpu_quotas_by_threads_per_core": ValidationProtocol(
            bool,
            default_value=False
        ),
        "penalty": {
            "order": ValidationProtocol(list, nonzerolen, all_are_str),
            "occur_timeout": ValidationProtocol(int, isaboveeq1),
            "relative_quotas": ValidationProtocol(bool, default_value=True)
        },
    },
    "high_usage_watcher": {
        "high_usage_watcher": ValidationProtocol(bool),
        "cpu_usage_threshold": ValidationProtocol((float, int), isbeloweq1),
        "mem_usage_threshold": ValidationProtocol((float, int), isbeloweq1),
        "user_count": ValidationProtocol(int, default_value=8),
        "div_cpu_thresholds_by_threads_per_core": ValidationProtocol(
            bool,
            default_value=False
        ),
        "threshold_period": ValidationProtocol(
            int,
            isaboveeq1,
            default_value=1
        ),
        "timeout": ValidationProtocol(int)
    },
}
# Stores the validation protocols and layout of a status config
status_validation = {
    "cpu_quota": ValidationProtocol((int, float), valid_cpu_limit),
    "mem_quota": ValidationProtocol((int, float), valid_mem_limit),
    "whitelist": ValidationProtocol(list, all_are_str, default_value=[]),
    "whitelist_file": ValidationProtocol(str, file_exists, default_value=""),
    "uids": ValidationProtocol(
        list,
        all_are_int,
        uids_exist,
        default_value=[]
    ),
    "gids": ValidationProtocol(
        list,
        all_are_int,
        gids_exist,
        default_value=[]
    ),
}
# Stores the validation protocols and layout of a penalty config (a extension
# of the status config)
penalty_validation = {
    "timeout": ValidationProtocol(int),
    "expression": ValidationProtocol(str, nonzerolen),
}
penalty_validation.update(status_validation)


def check_config(config, pedantic=True):
    """
    Checks and returns whether the config is valid and contains all the values
    required. Passing this function indicates that cfg can be set and used.

    config: dict
        The dictionary checked.
    pedantic: bool
        Whether or not to include pedantic checks.
    """
    # Make sure all sections are there
    if not valid_sections(config):
        return False

    # Make sure all needed values are there
    if not has_req_values(config):
        return False

    # Make sure all the values are valid.
    if not valid_config_values(config, pedantic=pedantic):
        return False
    return True


def place_optional_values(config, validation_config=base_validation_config):
    """
    Places optional values in the config if they are not overriden.

    config: dict
        The dictionary to change.
    validation_config: dict
        The dictionary to get values from; values in the dict must be either a
        sub config, or be a ValidationProtocol.
    """
    for key, value, context in context_iter(validation_config):
        if value.default_value is not None:
            try:
                inner_config = context_inner_dict(config, context)
            except KeyError as err:
                continue
            if key not in inner_config:
                inner_config[key] = value.default_value

    # Do it for the status groups
    for key, value, context in context_iter(copy.deepcopy(config)):
        if "penalty" in context and len(context) == 3:
            # New Penalty Group
            place_optional_values(context_inner_dict(config, context), penalty_validation)
        elif "status" in context and "penalty" not in context and len(context) == 2:
            # New Status Group
            place_optional_values(context_inner_dict(config, context), status_validation)


def place_special_vars(config):
    """
    Places special variables in the config.

    config: dict
        The dictionary to change.
    """
    for key, value, context in context_iter(config.copy()):
        for var, repl_func in special_vars.items():
            if isinstance(value, str):
                for match in re.finditer(var, value):
                    context_insert(
                        re.sub(var, repl_func(match.group(1)), value),
                        config,
                        context + [key]
                    )


def has_req_values(config, validation_config=base_validation_config):
    """
    Makes sure all the required key, values are in the config.

    config: dict
        The dictionary checked.
    validation_config: dict
        The dictionary to check against; values in the dict must be either a
        sub config, or be a ValidationProtocol.
    """
    # Make all values None
    blank_config = copy.deepcopy(validation_config)
    for key, value, context in context_iter(blank_config):
        inner_config = context_inner_dict(blank_config, context)
        inner_config[key] = None

    # Replace all known values (leaves None values in place if not in config)
    test_config = merge_dicts(blank_config, copy.deepcopy(config))

    # Check for None values, they are missing variables.
    isvalid = True
    for key, value, context in context_iter(test_config):
        if value is None:
            logger.error("Missing variable '%s.%s'.", ".".join(context), key)
            isvalid = False
    return isvalid


def valid_sections(config, validation_config=base_validation_config):
    """
    Returns whether the required sections exist.

    config: dict
        The dictionary checked.
    validation_config: dict
        The dictionary to check against; values in the dict must be either a
        sub config, or be a ValidationProtocol.
    """
    isvalid = True
    for key in validation_config.keys():
        if key not in config:
            logger.error("Missing section %s.", key)
            isvalid = False
    return isvalid


def valid_config_values(config, validation_config=base_validation_config,
                        context="", pedantic=True):
    """
    Validates a config against a set config to make sure the value types are
    correct and the value is valid.

    config: dict
        The dictionary checked.
    validation_config: dict
        The dictionary to check against; values in the dict must be either a
        sub config, or be a ValidationProtocol.
    context: str
        A concatenation of the keys onto a dot.
    pedantic: bool
        Whether or not to include pedantic checks.
    """
    isvalid = True
    for key, value in config.items():
        parent = context + "." + key if context != "" else key
        if key not in validation_config:
            if context == "status" and isinstance(value, dict):
                # Is a status group
                if not valid_config_values(value, status_validation, parent,
                                           pedantic=pedantic):
                    isvalid = False
            elif context == "status.penalty" and isinstance(value, dict):
                # Is a penalty status group
                if not valid_config_values(value, penalty_validation, parent,
                                           pedantic=pedantic):
                    isvalid = False
            else:
                logger.warning(
                    "Unrecognized variable '%s.%s = %s'. Will be ignored",
                    context,
                    key,
                    value,
                )
        elif isinstance(value, dict):
            if not valid_config_values(value, validation_config[key], parent,
                                       pedantic=pedantic):
                isvalid = False
        elif not valid_value(validation_config, key, value, context,
                             pedantic=pedantic):
            isvalid = False
    return isvalid


def valid_value(validation_config, key, value, context, pedantic=True):
    """
    Checks for the correct value type and valididates the value using the
    validation_config and returns whether there was any errors/warnings.

    validation_config: dict
        The dictionary to check against; values in the dict must be either a
        sub config, or be a ValidationProtocol.
    value: object
        The value to check for validity.
    context: str
        A concatenation of the keys onto a dot.
    pedantic: bool
        Whether or not to include pedantic checks.
    """
    validation_protocol = validation_config[key]
    types = validation_protocol.types

    # Check that it is a valid type
    if not isinstance(value, types):
        logger.error(
            "Invalid type in variable '%s.%s = %s'. Allowed " "Types: %s",
            context,
            key,
            value,
            ", ".join(re.match(r"<class\s'(.+)'>", str(t)).group(1) for t in types),
        )
        return False

    # Check for further validity
    for validity_check in validation_protocol.validations:
        if not pedantic and validity_check.pedantic:
            continue
        if validity_check.check(value) is not True:
            log = logger.error
            if isinstance(validity_check, Warn):
                log = logger.warning
            log(validity_check.err_message.format(
                context,
                key,
                value,
                validity_check.err_message
            ))
            # If it's only a warning, don't die
            if isinstance(validity_check, Warn):
                continue
            return False
    return True


def context_iter(dictionary, context=[]):
    """
    Iterates through the dictionary, returning the current key, value and a
    list of parent keys.

    dictionary: dict
        A dictionary to iterate over.
    context: [str, ]
        A list of strings corresponding to the keys that were used to get to
        the current config dictionary.
    """
    for key, value in dictionary.items():
        if isinstance(value, dict):
            yield from context_iter(value, context + [key])
        else:
            yield key, value, context


def context_inner_dict(dictionary, context):
    """
    Returns a inner dictionary from the given dictionary from the context
    keys.

    dictionary: dict
        A dictionary to get the value from.
    context: [str, ]
        A list of strings corresponding to the keys that were used to get to
        the desired dictionary.
    """
    innerdict = dictionary
    for child in context:
        innerdict = innerdict[child]
    return innerdict


def context_insert(item, dictionary, context):
    """
    Modifies the inner dictionary found by the context's keys.

    item: object
        The item to insert.
    dictionary: dict
        A dictionary to set the value of.
    context: [str, ]
        A list of strings corresponding to the keys that were used to get to
        the current config dictionary.
    """
    key = context.pop(0)
    if context:
        context_insert(item, dictionary[key], context)
    else:
        dictionary[key] = item


def merge_dicts(first_dict, second_dict):
    """
    Update two dicts of dicts recursively, if either mapping has leaves that
    are non-dicts, the second's leaf overwrites the first's.

    first_dict: dict
        The first dictionary to merge.
    second_dict: dict
        The second dictionary to merge.
    """
    for k, v in first_dict.items():
        if k in second_dict and all(isinstance(e, dict) for e in (v, second_dict[k])):
            second_dict[k] = merge_dicts(v, second_dict[k])
    new_dict = first_dict.copy()
    new_dict.update(second_dict)
    return new_dict


def combine_toml(*files):
    """
    Combine the toml files together into a single configuration that is
    returned.

    files: str
        A series of paths to toml files.
    """
    resulting_config = toml.load(files[0])
    for config in files[1:]:
        resulting_config = merge_dicts(resulting_config, toml.load(config))
    return resulting_config


def load_config(*config_files, check=True, pedantic=True):
    """
    Attempts to load the given configuration files into cfg and returns
    whether it was successful in doing so. May raise TypeError or
    toml.decoder.TomlDecodeError.

    config_files: str
        A series of paths to configuration files (toml).
    check: bool
        Whether or not to check the configuration for problems.
    """
    config = combine_toml(*config_files)
    place_optional_values(config)
    place_special_vars(config)
    if check and not check_config(config, pedantic=pedantic):
        return False

    cfg.add_subconfig(config)
    return True


def arguments():
    desc = "Check for errors or print the resulting config.toml."
    parser = argparse.ArgumentParser(description=desc)
    parser.add_argument(
        "configs",
        type=str,
        nargs="*",
        help="The configuration files to use. Configs will be cascaded "
             "together starting at the leftmost (the primary config) going "
             "right (the overwriting configs). Defaults to $ARBCONFIG if "
             "present or ../etc/config.toml otherwise.",
    )
    parser.add_argument(
        "-g", "--config",
        type=str,
        nargs="*",
        help="Dummy flag for the configuration files to use. For "
             "compatibility with arbiter.py.",
        dest="_configs"
    )
    parser.add_argument(
        "--print",
        "-p",
        dest="print",
        action="store_true",
        help="Print out the interpreted toml file without checking for errors.",
    )
    parser.add_argument(
        "--eval-specials",
        dest="eval_specials",
        action="store_true",
        help="If --print is invoked, evaluate special variables and put the "
             "resulting values in the printed config."
    )
    parser.add_argument(
        "--hide-defaults",
        dest="hide_defaults",
        action="store_true",
        help="If --print is invoked, don't include the implicit "
             "optional/default values in the printed config."
    )
    parser.add_argument(
        "--non-pedantic",
        dest="pedantic",
        action="store_false",
        help="Skip pedantic tests like ping, files and directories existing."
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = arguments()
    configs = args._configs if args._configs else args.configs
    if not configs:
        sys.stderr.write("{}: error: the following arguments are required: "
                         "configs\n".format(os.path.basename(sys.argv[0])))
        exit(2)
    resulting_config = combine_toml(*configs)
    if args.print:
        if not args.hide_defaults:
            place_optional_values(resulting_config)
        if args.eval_specials:
            place_special_vars(resulting_config)
        print(toml.dumps(resulting_config))
    elif load_config(*configs, pedantic=args.pedantic) is not True:
        sys.exit(2)

