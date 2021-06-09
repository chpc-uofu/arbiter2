#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2019-2020 Center for High Performance Computing <helpdesk@chpc.utah.edu>
#
# SPDX-License-Identifier: GPL-2.0-only

"""
A module used for interfacing with a database (enabling the underlining
database to be changed). As such, this should be the single point for
interacting with a database.
"""
import collections
import contextlib
import types
import urllib
# Defined here: https://docs.sqlalchemy.org/en/13/core/exceptions.html
import sqlalchemy
from sqlalchemy.exc import DBAPIError, SQLAlchemyError, NoSuchTableError, \
                           ProgrammingError, OperationalError


class Database(types.SimpleNamespace):
    """
    A base object for manipulating a database.
    """

    def __init__(self, url):
        """
        Initializes the database object with the given url.

        url: str
            A RFC-1738 database url. For example,
            dialect+driver://username:password@host:port/database
        """
        self.url = url
        self.engine = None
        self.reset(self.url)

    def redacted_url(self):
        """
        Returns the database URL with the password as REDACTED.
        """
        parsed_result = urllib.parse.urlparse(self.url)
        if parsed_result.password:
            return self.url.replace(parsed_result.password, "REDACTED")
        return self.url

    def reset(self, new_url):
        """
        Reinitializes the database object with the given url. This is useful
        for rotating sqlite databases.

        new_url: str
            A RFC-1738 database url. For example,
            dialect+driver://username:password@host:port/database
        """
        self.url = new_url
        if self.engine:
            self.engine.dispose()

        # In testing it appears that sqlite on NFS with sqlalchemny (rather
        # than the sqlite3 module) occasionally freezes up. Preliminary
        # testing points to pooling being a problem, so we'll disable it here
        if self.url.startswith("sqlite"):
            self.engine = sqlalchemy.create_engine(
                self.url,
                poolclass=sqlalchemy.pool.NullPool
            )
        else:
            self.engine = sqlalchemy.create_engine(self.url)

    def column_in_table(self, tablename, columnname):
        """
        Returns whether the given column exists in the given table.

        tablename: str
            The name of the table.
        columnname: str
            The name of the column.
        """
        iengine = sqlalchemy.inspect(self.engine)
        tablenames = iengine.get_table_names()

        # For some reason doing "get_columns" of iengine doesn't fail, it just
        # returns a empty list, so we'll raise the error here so that callers
        # know the table doesn't exist, rather than just that the column
        # doesn't exist.
        if tablename not in tablenames:
            raise NoSuchTableError("Could not find " + tablename)

        columns = iengine.get_columns(tablename)
        return any(col["name"] == columnname for col in columns)

    def create_database(self, schema, tablename):
        """
        Creates a database with the specified schema.

        schema: [str, ]
            The given schema used to create the database. The schmea provided
            must be a list of sql headers for columns.
        tablename: str
            The name of the table.

        >>> db = Database("sqlite:///test.db")
        >>> create_database("sqlite:///test.db", [
                    "uid INTEGER NOT NULL",
                    "hostname VARCHAR(64) NOT NULL",
                ],
                "test"
            )
        """
        connection = self.engine.raw_connection()
        cursor = connection.cursor()

        if isinstance(schema, list):
            db_schema = ", ".join(schema)
        else:
            raise TypeError("Schema provided must be a list")

        cursor.execute("create table {} ({})".format(tablename, db_schema))
        connection.commit()
        connection.close()

    def execute_command(self, command, params=None):
        """
        Executes the command given on the database filename specified and
        returns the results.

        command: str
            The command to execute (with the optional params).
        params: dict
            sqlalchemy paramters, where each key is specified in the command .
            e.g. ":key" -> {"key": ...}
        """
        connection = self.engine.raw_connection()
        cursor = connection.cursor()
        cursor.execute(command, params if params else {})
        results = cursor.fetchall()
        connection.commit()
        connection.close()
        return results, cursor.lastrowid

    def execute_commands(self, commands, paramss=None):
        """
        Executes the commands given on the database filename specified and
        returns a list of the results.

        commands: [str, ]
            A list of commands to execute (with the optional params).
        paramss: [dict, ]
            A list of sqlalchemy paramters, where each key is specified in the
            command. e.g. ":key" -> {"key": ...}
        """
        connection = self.engine.raw_connection()
        cursor = connection.cursor()
        all_results = []
        paramss = paramss if paramss else [{}]
        for command, params in zip(commands, paramss):
            cursor.execute(command, params)
            all_results.append(cursor.fetchall())

        connection.commit()
        connection.close()
        return all_results

    def read_table(self, tablename, **constraints):
        """
        Returns all entries in a table matching the given constraints if
        given, as a list of dictionaries, where the headers are keys.

        tablename: str
            The name of the table.
        constriants: dict
            A dictionary of constraints where the keys are column names and
            values are the corresponding column value.
        """
        connection = self.engine.raw_connection()
        cursor = connection.cursor()

        # Get column headers and fields from table.
        select = "select * from {}".format(tablename)
        for i, key_and_value in enumerate(constraints.items()):
            if i == 0:
                select += " where"
            else:
                select += " and"
            select += " {} = '{}'".format(*key_and_value)

        cursor.execute(select)

        headers = [member[0] for member in cursor.description]
        fields = cursor.fetchall()
        connection.close()

        # Convert each row to a dictionary with headers as keys.
        entries = []
        for row in fields:
            # Callers may rely on the ordering of .values() being consistent with
            # the schema; not needed in Python 3.6+, but better safe than sorry
            output = collections.OrderedDict()
            for field, value in enumerate(row):
                # Update row-level dictionary with field.
                output[headers[field]] = value
            entries.append(output)

        return entries
