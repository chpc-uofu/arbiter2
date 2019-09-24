"""
A collection of methods for getting and applying statuses. Statuses files are
stored in a database at the configured location.

Functions with "lookup" in their name don't do any database queries/changes.
Everything else does.

See CONFIG.md for information relating to what statuses are.
"""
import time
import pwd
import os
import cinfo
import logging
import collections
import database
import decorators
from cfgparser import cfg, shared, Configuration

logger = logging.getLogger("arbiter." + __name__)

# A object for storing the status of a user
Status = collections.namedtuple("Status", [
    "current",
    "default",
    "occurrences",
    "timestamp",
    "occur_timestamp"
])
StatusConfig = collections.namedtuple("StatusConfig", [
    "status_loc",
    "status_table"
])
defaults = [
    cfg.database.log_location + "/" + shared.statusdb_name,
    shared.status_tablename
]
default_config = StatusConfig(*defaults)
statusdb_key_map = {
    "current_status": "current",
    "default_status": "default",
    "occurrences": "occurrences",
    "timestamp": "timestamp",
    "occurrences_timestamp": "occur_timestamp"
}


def lookup_is_penalty(status_group):
    """
    Returns whether the status group is a penalty status group.
    """
    return status_group in cfg.status.penalty.order


def lookup_quotas(uid, status_group):
    """
    Returns the user's status group quotas. The quotas are returned as a pct
    of the machine, rather than the configured values!

    uid: str
        The user's uid.
    status_group: str
        The user's current status group.
    """
    status_prop = lookup_status_prop(status_group)
    quotas = [
        status_prop.cpu_quota,
        status_prop.mem_quota / cinfo.bytes_to_gb(cinfo.total_mem) * 100
    ]
    if cfg.status.div_cpu_quotas_by_threads_per_core:
        quotas[0] /= cinfo.threads_per_core

    if lookup_is_penalty(status_group) and cfg.status.penalty.relative_quotas:
        default_prop = lookup_status_prop(lookup_default_status_group(uid))
        quotas[0] = quotas[0] * default_prop.cpu_quota
        quotas[1] = quotas[1] * default_prop.mem_quota
    return quotas


def lookup_status_prop(status_group):
    """
    Looks up the status group properties from the config, and returns the
    properties as a Configuration() object. If the group doesn't exist, returns
    a empty Configuration object. The quotas follow the configuration values.

    status_group: str
        The user's current status group.
    """
    context = cfg.status
    if lookup_is_penalty(status_group):
        context = cfg.status.penalty
    return getattr(context, status_group, Configuration({}))


def lookup_default_status_group(uid):
    """
    Looks up the default status group of the user, matching in the order a
    status group appears in config. The fallback status group specified
    in config will be returned if the user doesn't match any groups.

    uid: int
        The user's uid.
    """
    # Cast types to make sure arguments are integers
    uid = int(uid)
    gids = query_gids(uid)

    # Loop through all groups but the last
    for status_group in cfg.status.order:
        status_prop = lookup_status_prop(status_group)
        # Check if gids or uids match
        if uid in status_prop.uids or any(gid in status_prop.gids for gid in gids):
            return status_group
    return cfg.status.fallback_status


def query_gids(uid):
    """
    Queries the gids of the groups that the user belongs to.

    uid: int
        The user's uid.
    """
    user_info = pwd.getpwuid(uid)
    username = str(user_info.pw_name)
    return os.getgrouplist(username, user_info.pw_gid)


@decorators.retry((database.OperationalError), logger)
def read_status(status_config=default_config):
    """
    Return a dictionary of users with a list containing their current status
    group, default status group, occurrences, a timestamp when their current
    status was updated in epoch and a timestamp when their occurrences was last
    changed.

    >>> read_status()
    {"1001": Status(current="penalty1", default="normal", occurrences=0,
                    timestamp=1534261840, occur_timestamp=1534261840)}
    """
    status_dict = {}
    table = database.read_table(status_config.status_loc,
                                status_config.status_table)
    for row in table:
        uid = row.pop("uid")
        status_dict[uid] = Status(
            **{statusdb_key_map[k]: v for k, v in row.items()}
        )
        status_dict[uid] = enforce_cfg_db_consistency(uid, status_dict[uid])
    return status_dict


@decorators.retry((database.OperationalError), logger)
def get_status(uid, status_config=default_config):
    """
    Returns the user's current and default status group, occurrences, a
    timestamp when their current status was updated in epoch and a timestamp
    for when their occurrences was changed from the status file as a named
    tuple. If their status is not in the status table, returns their default
    status group as the current and default, plus 0 occurrences, 0 and 0 for
    both timestamps.

    uid: int, str
        The user's uid.

    >>> get_status("1001")
    Status(current="normal", default="normal", occurrences=0,
           timestamp=1534261840, occur_timestamp=1534261840)
    >>> get_status("1049")  # isn't in status file
    Status(current="penalty1", default="normal", occurrences=0, timestamp=0,
           occur_timestamp=0)
    """
    uid = int(uid)
    get_statuses = 'SELECT * FROM {} WHERE "uid" IS (?);'
    get_default_property = get_statuses.format(status_config.status_table)
    try:
        result, _ = database.execute_command(status_config.status_loc,
                                             get_default_property, int(uid))
        return enforce_cfg_db_consistency(uid, Status(*result[0][1:]))
    except (IndexError, KeyError):
        default_status = lookup_default_status_group(uid)
        return Status(default_status, default_status, 0, 0, 0)


def enforce_cfg_db_consistency(uid, status):
    """
    Ensures that the given Status()'s default and current status is consistent
    with the user's configured status groups. If both the current and default
    statuses are the same and inconsistent, both statuses will be changed to
    the configured values. Otherwise, only the default status will be changed.
    The resulting status will be returned. This is meant to be called on
    statuses coming from the status database.

    The motivation for this is that occasionally the configuration is changed,
    but a user has a entry in statusdb with their old status (thus, the user's
    correct status couldn't be applied). This function enforces that the
    configuration is the ultimate source for determining a user's default
    status (note it is not the source for the current status).

    uid: int
        The user's uid.
    status: Status()
        The status to enforce consistency on.
    """
    default_status = lookup_default_status_group(uid)
    if status.default != default_status:
        current_status = status.current
        if status.current == status.default:
            current_status = default_status
        status = Status(default_status, current_status, status.occurrences,
                        status.timestamp, status.occur_timestamp)
    return status


@decorators.retry((database.OperationalError), logger)
def update_occurrences(uid, increase, update_timestamp=True,
                       status_config=default_config):
    """
    Updates the occurrences of the user by the increase amount. The occurrences
    timstamp is updated if specifed. Returns whether the operatation was
    sucessful.

    uid: str
        The user's uid.
    increase: int
        The amount to increase/decrease the occurrences of the user.
    update_timestamp: bool
        Whether to update the occurrences timestamp of the user.
    """
    if not in_status_file(uid):
        return False

    update = ["UPDATE status SET occurrences = occurrences + ?,"]
    args = [increase]
    if update_timestamp:
        update.append("occurrences_timestamp = ?")
        args.append(int(time.time()))
    update.append("WHERE uid = ?;")
    args.append(int(uid))
    database.execute_command(status_config.status_loc, "".join(update),
                             *tuple(args))
    return True


@decorators.retry((database.OperationalError), logger)
def in_status_file(uid, status_config=default_config):
    """
    Returns whether the user is in the status file.

    uid: int
        The user's uid.
    """
    exists_cmd = "SELECT EXISTS(SELECT 1 FROM {} WHERE UID = ?);"
    exists_cmd = exists_cmd.format(status_config.status_table)
    return bool(database.execute_command(status_config.status_loc,
                                         exists_cmd, int(uid))[0])


@decorators.retry((database.OperationalError), logger)
def add_user(uid, current_status, default_status,
             status_config=default_config):
    """
    Adds a user to the status file, replacing the user's current properties if
    applicable or adding the user and its properties if otherwise. The default
    occurrence when a user isn't in the status file is 0.

    uid: str
        The user's uid associated with the properties.
    current_status: str
        The user's current status group.
    default_status: str
        The user's default status group.
    """
    status_schema = ("uid, current_status, default_status, occurrences, "
                     "timestamp, occurrences_timestamp")
    prev_status = get_status(uid)
    timestamp = int(time.time())
    if prev_status[0] == current_status:
        timestamp = prev_status[3]

    # Insert a new row or ingore the row if the row already exists
    insert = "INSERT OR IGNORE INTO {}({}) VALUES(?, ?, ?, ?, ?, ?);"
    insert = insert.format(status_config.status_table, status_schema)
    insert_args = (int(uid), current_status, default_status, 0,
                   int(time.time()), 0)
    # Add the increase (and update other values) since we know the row exists
    update = ("UPDATE status SET current_status = ?,"
              "default_status = ?,"
              "timestamp = ? "
              "WHERE uid = ?;")
    update_args = (current_status, default_status, timestamp, int(uid))

    database.execute_command(status_config.status_loc, insert, *insert_args)
    database.execute_command(status_config.status_loc, update, *update_args)


@decorators.retry((database.OperationalError), logger)
def remove_user(uid, status_config=default_config):
    """
    Removes the user if the user has 0 occurrences and in both cases, returns
    the user's default status group. If the user is not in the status file,
    returns None.

    uid: str
        The user's uid.
    """
    commands = []
    commands.append('SELECT "default_status" FROM {} WHERE "uid" IS ?;'
                    ''.format(status_config.status_table))
    commands.append('DELETE FROM {} WHERE "uid" IS ?;'.format(
                    status_config.status_table))

    # Return the result of the default status query and delete the row
    for cmd in commands:
        result, _ = database.execute_command(status_config.status_loc, cmd,
                                             int(uid))
    try:
        # First row and value returned from the tuple in the first column
        return result[0][0]
    except IndexError:
        return None


def read_badness(status_config=default_config):
    """
    Return a dictionary of users with a list containing a timestamp when their
    badness was last updated, their cpu and memory badness.

    >>> read_badness()
    {"1001": {"timestamp": 1533229326, "cpu": 0.0, "mem": 0.0}}
    """
    table = database.read_table(status_config.status_loc,
                                shared.badness_tablename)
    badness = {}
    for row in table:
        uid = int(row.pop("uid"))
        # Change cpu_badness and mem_badness to cpu and mem
        badness[uid] = {col.rstrip("_badness"): val for col, val in row.items()}
    return badness


def add_badness(uid, timestamp, badness, status_config=default_config):
    """
    Add a user's badness score to the badness table in the status database. The
    previous badness score will be overridden if a previous score exists.

    uid: str
        The user's uid associated with the properties.
    timestamp: int
        A timestamp of when the badness score was calculated.
    badness: {"mem": float, "cpu": float}
        A badness dictionary with both memory and cpu values.
    """
    badness_schema = "uid, timestamp, cpu_badness, mem_badness"

    # Insert a new row or replace the row belonging to the user
    insert = "INSERT OR REPLACE INTO {}({}) VALUES(?, ?, ?, ?);"
    insert = insert.format(shared.badness_tablename, badness_schema)
    args = (int(uid), timestamp, badness["cpu"], badness["mem"])
    database.execute_command(status_config.status_loc, insert, *args)


def create_status_database(path, status_table, badness_table):
    """
    Create a database at the specified path/file with a status and badness
    table.

    path: str
        The location of the database file.
    status_table: str
        The name of the status table.
    badness_table: str
        The name of the badness table.
    """
    # Create status table
    database.create_database(path, ["uid TEXT NOT NULL UNIQUE",
                                    "current_status TEXT NOT NULL",
                                    "default_status TEXT NOT NULL",
                                    "occurrences INTEGER NOT NULL",
                                    "timestamp INTEGER NOT NULL",
                                    "occurrences_timestamp INTEGER NOT NULL",
                                    "PRIMARY KEY(uid)"], status_table)
    # Create badness table
    database.create_database(path, ["uid TEXT NOT NULL UNIQUE",
                                    "timestamp INTEGER NOT NULL",
                                    "cpu_badness REAL NOT NULL",
                                    "mem_badness REAL NOT NULL",
                                    "PRIMARY KEY(uid)"], badness_table)

