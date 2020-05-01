# SPDX-License-Identifier: GPL-2.0-only
"""
Logs events and actions that arbiter takes to a database.
"""

import os
import pathlib
import re
import datetime
import database
import sys
import collections

actions_schema = [
    "id INTEGER PRIMARY KEY AUTOINCREMENT",
    "action",
    "user",
    "time INTEGER"
]
general_schema = [
    "actionid INTEGER",
    "mem",
    "cpu",
    "time INTEGER",
    "FOREIGN KEY(actionid) REFERENCES actions(id) ON DELETE CASCADE"
]
process_schema = [
    "actionid INTEGER",
    "name",
    "mem",
    "cpu",
    "uptime INTEGER",
    "timestamp INTEGER",
    "FOREIGN KEY(actionid) REFERENCES actions(id) ON DELETE CASCADE"
]


def last_rotation_date(path_fmt, date_fmt):
    """
    Returns a datetime.date when the last rotation took place. If no
    rotation has taken place, a minimum date is returned.

    path_fmt: str
        A filepath with a {} in it, where the date goes.
    date_fmt: str
        A time.strftime format used to look at previous logs.
    """
    latest_log_date = datetime.date.min
    fglob = os.path.basename(path_fmt).replace("{}", "*")
    path = pathlib.Path(path_fmt).parent
    for db in path.glob(fglob):
        try:
            found_date_str = re.search(fglob.replace("*", "(.*)"), db.name).group(1)
            found_date = datetime.datetime.strptime(found_date_str, date_fmt).date()
            if found_date > latest_log_date:
                latest_log_date = found_date
        except (AttributeError, ValueError):
            continue
    return latest_log_date


def rotated_filename(path_fmt, date_fmt):
    """
    Returns a filename formatted with a date that is correctly rotated given
    the number of days.

    path_fmt: str
        A filepath with a {} in it, where the date goes.
    date_fmt: str
        A time.strftime format used to look at previous logs.
    """
    last_rotation = last_rotation_date(path_fmt, date_fmt)
    return path_fmt.format(last_rotation.strftime(date_fmt))


def create_log_database(filename):
    """
    Create a database for storing log information.

    filename: str
        The path to the database file to be used.
    """
    database.create_database(filename, actions_schema, "actions")
    database.create_database(filename, general_schema, "general")
    database.create_database(filename, process_schema, "process")


def add_action(action, user, historic_events, timestamp, filename):
    """
    Adds an action to the logging database.

    action: str
        The action that was taken.
    user: str
        The user associated with the action.
    historic_events: collections.deque
        The historic (general) usage of the user (see triggers.py).
    timestamp: int
        The time (epoch) at which the action was taken.
    filename: str
        The name of the logdb file to write out to.
    """
    action_obj = Action(action, user, timestamp)
    for event in historic_events:
        general_obj = General(event["mem"], event["cpu"], event["time"])
        pids = event["pids"]

        # Add each PID object to the action
        for pid in pids:
            process = Process(pids[pid].name, pids[pid].usage["mem"],
                              pids[pid].usage["cpu"], pids[pid].uptime,
                              event["time"])
            action_obj.add_process(process)

        # Add each general object to the action
        action_obj.add_general(general_obj)

    _add_log_entry(action_obj, filename)


def _add_log_entry(action, filename):
    """
    Adds an entry to the logging database. Each entry is an "action" with
    (potentially) multiple "general" historical states, and (potentially)
    multiple "process" states. This allows the database to be searched quickly
    while keeping considerable historical information.

    action: Action(object)
        The action that has been taken.
    filename: str
        The path to the database file to be used.
    """
    # Enable foreign key constraints to have correct secondary tables
    database.execute_command(filename, "pragma foreign_keys = ON")

    # Add the top level of the hierarchy
    cmd = "insert into actions(action, user, time) values(?, ?, ?)"
    result, ident = database.execute_command(filename, cmd, action.action,
                                             action.user, action.timestamp)

    # Add each middle level of the hierarchy
    for general in action.general:
        cmd = "insert into general(actionid, mem, cpu, time) values(?, ?, ?, ?)"
        database.execute_command(filename, cmd, ident, general.mem,
                                 general.cpu, general.time)

    # Add each low level of the hierarchy
    for proc in action.process:
        cmd = ("insert into process(actionid, name, mem, cpu, uptime, "
               "timestamp) values(?, ?, ?, ?, ?, ?)")
        database.execute_command(filename, cmd, ident, proc.name, proc.mem,
                                 proc.cpu, proc.uptime, proc.timestamp)
    return True  # FIXME; catch errors!


# An action (high-level)
class Action(object):
    __slots__ = ["action", "user", "timestamp", "general", "process"]

    def __init__(self, action, user, timestamp):
        self.action = action
        self.user = user
        self.timestamp = timestamp
        self.general = []
        self.process = []

    # General (cgroup)
    def add_general(self, general):
        self.general.append(general)

    # Process-specific
    def add_process(self, process):
        self.process.append(process)


# General usage (mid-level)
class General(object):
    __slots__ = ["mem", "cpu", "time"]

    def __init__(self, mem, cpu, time):
        self.mem = mem
        self.cpu = cpu
        self.time = time


# Process usage (low-level)
class Process(object):
    __slots__ = ["name", "mem", "cpu", "uptime", "timestamp"]

    def __init__(self, name, mem, cpu, uptime, timestamp):
        self.name = name
        self.mem = mem
        self.cpu = cpu
        self.uptime = uptime
        self.timestamp = timestamp


def object_mapper(content, map_to):
    """
    Map information in a list to an arbitrary object. This is used to take the
    information in a log file and return it to the original object from which
    it was generated.
    """
    id_object_map = []
    for num, row in enumerate(content):
        try:
            mapped = map_to(*row[1])
            id_object_map.append([row[0], mapped])
        except:
            continue
    return id_object_map


def table_by_id(filename, tablename):
    """
    Extract the information from tablename in database filename. Return it as
    a list with the primary key in the first location and all other fields in
    the second.
    """
    values = database.execute_command(
        filename,
        "SELECT * FROM {}".format(tablename)
    )
    results_by_id = []
    for results in values:
        try:
            for result in results:
                try:
                    identity = result[0]
                    others = result[1:]
                    results_by_id.append([identity, others])
                except:
                    continue
        except:
            continue
    return results_by_id


def get_objects_by_id(filename, tablename, object_to_map):
    """
    Returns a list of objects from a database filename with table tablename
    and an object object_to_map.
    """
    return object_mapper(table_by_id(filename, tablename), object_to_map)

def get_table_info(filename, table, map_to):
    """
    Returns an object associated with a given log database filename and table
    name (or None if the file does not exist). This is a simplification of
    get_log_info() to avoid parsing the whole file and could likely be
    consolidated further (to avoid repetition).
    """
    if not os.path.isfile(filename):
        return None
    tables = collections.OrderedDict()
    tables[table] = map_to
    return([get_objects_by_id(filename, table, tables[table])
            for table in tables])


def get_log_info(filename):
    """
    Returns Action, General, and Process objects for a given log database
    filename (or None if the file does not exist).
    """
    if not os.path.isfile(filename):
        return None
    # Map of table names (schema) to objects in logdb.py
    tables = collections.OrderedDict()
    tables["actions"] = Action
    tables["general"] = General
    tables["process"] = Process
    # Return a list of object lists (not pretty)
    return([get_objects_by_id(filename, table, tables[table])
            for table in tables])
