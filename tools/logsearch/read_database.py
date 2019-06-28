import sqlite3
import os.path
import sys
import logdb
import database
import datetime
import os
import collections


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


def verify_file_exists(filename):
    """
    Returns True if a file exists, False otherwise.
    """
    if os.path.isfile(filename):
        return True
    return False


def get_table_info(filename, table, map_to):
    """
    Returns an object associated with a given log database filename and table
    name (or None if the file does not exist). This is a simplification of
    get_log_info() to avoid parsing the whole file and could likely be
    consolidated further (to avoid repetition).
    """
    db_exists = verify_file_exists(filename)
    if not db_exists:
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
    db_exists = verify_file_exists(filename)
    if not db_exists:
        return None
    # Map of table names (schema) to objects in logdb.py
    tables = collections.OrderedDict()
    tables["actions"] = logdb.Action
    tables["general"] = logdb.General
    tables["process"] = logdb.Process
    # Return a list of object lists (not pretty)
    return([get_objects_by_id(filename, table, tables[table])
            for table in tables])
