#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""
A module used for interfacing with a database (enabling the underlining
database to be changed). As such, this should be the single point for
interacting with a database.
"""

import sqlite3
# Defined here: https://www.python.org/dev/peps/pep-0249/#exceptions
from sqlite3 import OperationalError, Warning, Error, DatabaseError, \
                    IntegrityError, ProgrammingError, NotSupportedError


def create_database(filename, schema, tablename, trigger=None):
    """
    Creates a database with the specified schema and the given filename.

    filename: str
        The name and path of the database.
    schema: {str: str of type(), } or [str, ]
        The given schema used to create the database. The schmea provided must
        either be a dictionary, where the key is the header for a column and
        the value is a str of a type() ("int", "str", "None", etc...), or a
        list of headers for columns (with no type specified).
    tablename: str
        The name of the table.
    trigger: str
        A string to associate a trigger with a table; used to limit the number
        of rows in a given table (optional).

    >>> create_database("testing.db",
                        {"time": "int", "uid": "int", "mem": "int"},
                        "test")
    True
    >>> create_database("testing.db",
                        ["time", "uid", "mem", "cpu0", "cpu1", "cpu2", "cpu3"],
                        "test2")
    True
    """
    connection = sqlite3.connect(filename)
    cursor = connection.cursor()

    if isinstance(schema, dict):
        db_schema = ", ".join([key + " " + val for key, val in schema.items()])
    elif isinstance(schema, list):
        db_schema = ", ".join(schema)
    else:
        raise TypeError("Schema provided must be a list or a dictionary")

    cursor.execute("create table {} ({})".format(tablename, db_schema))
    if trigger is not None:
        cursor.execute(trigger)

    connection.commit()
    connection.close()


def execute_command(filename, command, *params):
    """
    Executes the command given on the database filename specified and returns
    the results.

    filename: str
        The name and path to the database.
    command: str
        The command to execute (with the optional params).
    """
    connection = sqlite3.connect(filename)
    cursor = connection.cursor()
    cursor.execute(command, tuple(params))
    results = cursor.fetchall()
    connection.commit()
    connection.close()
    return results, cursor.lastrowid


def get_last_rowid(filename):
    """
    Returns the rowid of the last modified row.

    filename: str
        The name and path to the database.
    """
    return sqlite3.connect(filename).cursor().lastrowid


def read_table(filename, tablename):
    """
    Returns all entries in a table as a list of dictionary, where the headers
    are keys.

    filename: str
        The name and path to the database.
    tablename: str
        The name of the table.
    """
    connection = sqlite3.connect(filename)
    cursor = connection.cursor()

    # Get column headers and fields from table.
    cursor.execute("select * from " + tablename)
    headers = [member[0] for member in cursor.description]
    fields = cursor.fetchall()
    connection.close()

    # Convert each row to a dictionary with headers as keys.
    entries = []
    for row in fields:
        output = {}
        for field, value in enumerate(row):
            # Update row-level dictionary with field.
            output[headers[field]] = value
        entries.append(output)

    return entries
