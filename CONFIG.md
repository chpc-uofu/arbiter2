# Configuration
The configuration is written in [toml](https://github.com/toml-lang/toml). The default configuration file is in `etc/config.toml`, but it can be changed with the `-g CONFIGS` flag. Additional configuration files can be appended to the `-g` flag (e.g. `-g CONFIG CONFIG2 ...`. These configration files will override the previous configuration listed in a cascading manner. The additional configuration files only need headers and values that will override the previous. The headers and values are listed below.

### Cascading configurations
If Arbiter2 is being deployed to multiple clusters on the same NFS file system, it is recommended that you have a base shared config, but with per-hostname files are cascaded together. This simplifies future changes to configuration files. For example, CHPC has a setup similar to the following:

```
etc/config.toml
etc/cluster/_specifc-node1.toml
etc/cluster/_specifc-node2.toml
```

In the example above, the `etc/config.toml` is a fully complete configuration that is the first `-g` argument. In `_specific-node1.toml` and `_specific-node2.toml` (either one is appended at the end of the `-g` toml list, depending on the node), there are only the overriding headers that contain non-global settings. When a global configuration value needs to be changed, only `etc/config.toml` needs to be changed.

If you go this route, I'd recommend using the `%H` special value that inserts hostnames in systemd services so your `-g` list looks like `-g ${ARBITER_DIR}/etc/config.toml ${ARBITER_DIR}/etc/cluster/_%H.toml`.

_Tip: Make your partial/overriding configurations start with a underscore to better identify them._

### Special values

Adding the following to a string value in the configuration will replace the special key with the defined value below.

| Name | Special Value | Replacement |
| --- | --- | --- |
| Hostname | `%H` | _replaced with the machine's hostname._ |
| Environment Varaibles | `${VAR}` | _replaced with the $VAR enviroment variable or a blank space if no such variable exists_ |

### Testing the configuration

Inside of `tools/`, there is a file called `cfgparser.py`. This is a python program that you can use to test if your configuration is valid or to print out the resulting configuration with the `-p` flag. The tests run are the tests used when Arbiter2 starts up, so failing any one of these tests (including pedantic ones) implies that the resulting configuration might not work when running Arbiter2. There are a couple pedantic tests that check for directories, folders and even ping the mail server, but these can be skipped with the `--non-pedantic` flag (useful if you are testing the configuration on a different machine than the one you are deploying to). Run `cfgparser.py --help` inside of tools to see all the options.

You may also find the `--eval-specials` and `--hide-defaults` flags useful.

## General `[general]`

**debug_mode**: `boolean`

- Debug mode prevents limits from being written and emails from being sent to users and only goes to admins set in `email.admin_emails`. It also prepends debug information in emails (which is only sent to admins). Because limits/quotas are not set, so long as `pss = false` (see below), any unprivledged user can run Arbiter2 in this mode.

**arbiter_refresh**: `int` (greater or equal to 5)

- How often Arbiter2 evaluates users for violations in seconds and applies the quotas of new users.

**history_per_refresh**: `int` (greater or equal to 1)

- How many history "events" (collection of usage at a particular moment) to collect per `arbiter_refresh`.
- This, in conjuction with `arbiter_refresh` controls how detailed plots and usage information is. e.g. `plot_datapoint_length = arbiter_refresh / history_per_refresh`

**poll** _(optional/defaulted: 2)_: `int` (at least 2)

- The number of times to poll and then average into a event, (a collection of usage at a particular moment). e.g. collects information `poll` times within the `arbiter_refresh / history_per_refresh` interval.
- Out of abundance of caution, it recommended that the `arbiter_refresh / history_per_refresh / poll` length of time is greater than 1 second to allow Arbiter2 to fully complete its collection of usage in a event.

**min_uid** _(optional/defaulted: 1000)_: `int`

- The minimum uid to consider (those below will be ignored).

## Self `[self]`

**groupname**: `string`

- The name of the primary group that Arbiter2 will belong to when run. This is used if the `-s` or `--exit-file` flag is used.

## Badness `[badness]`

**max_history_kept**: `int` (greater or equal to 1)

- The maximum number of history "events" to keep at any moment.
- Plots will be generated from this information, meaning the max time length of a plot is the `max_badness_kept`. i.e.  `max_plot_timespan_in_secs = max_history_kept * history_per_refresh * arbiter_refresh`

**cpu_badness_threshold**: `float` (less than or equal to 1)

- The percentage (expressed as a fraction of 1) of a user's current status' CPU quota (more below) that a user's usage must stay below in order to not be "bad". e.g. if the threshold is .5, and their current status' quota is 400 (4 virtual CPUs), then the user must stay below 2 virtual CPUs in order to not be "bad". Any usage above that threshold starts accruing badness (which will eventually lead to a penalty). The total badness score is the `cpu_badness + mem_badness`.

**mem_badness_threshold**: `float` (less than or equal to 1)

- The percentage (expressed as a fraction of 1) of a user's current status' memory quota (more below) that a user's usage must stay below in order to not be "bad". e.g. if the threshold is .5, and their current status' quota is 4G (4 gigabytes), then the user cannot use more than 2G of memory in order to not be "bad". Any usage above that threshold starts accruing badness (which will eventually lead to a penalty). The total badness score is the `cpu_badness + mem_badness`.

**time_to_max_bad**: `int`

- If the user's usage is at their status' `quota * badness_threshold`, how long will it take in seconds for their badness scores to reach 100 (the maximum badness score). Note that the rate of increase for the badness score is relative to how high above the defined threshold the usage is (e.g. If a user's usage is the 3/4 of the quota and the threshold is 1/2 the quota, their badness score will go up 1.5x faster than it would at the threshold). Furthermore, if both CPU and memory usage is above the thresholds, the badness score will go up twice as fast (each is relative to it's threshold and are added together to form the final badness score).
    - Example: Let's say a user's CPU quota is 200% and the `cpu_badness_threshold` is 0.5 (their threshold for badness is 100% of a CPU) and their `time_to_max_bad` is 900 (15 minutes). If the user's usage is at 100% of a CPU (barring any whitelisted processes), it will take the user 15 minutes to reach 100 badness. If the user's usage is at 200% of a CPU, it will take 7.5 minutes to reach 100 badness.
- The following formula can be used to figure out how fast a user will get to 100 badness (the point at which actions are taken), assuming they're above the threshold:

```python
max_incr_per_sec = 100 / (time_to_max_bad * badness_threshold)
max_incr_per_interval = max_incr_per_sec * arbiter_refresh
badness_change_per_interval = (1 - usage / quota) * max_incr_per_interval
time_to_max_bad_with_usage = 100 / badness_change_per_interval
```

**time_to_min_bad**: `int`

- If the user is at 100 badness, how long will it take in seconds to get to 0 badness given that their usage is at 0. Note that the rate of decrease for the badness score is relative to how far below the defined threshold the usage is (e.g. If a user's usage is 1/4th the quota and the threshold is 1/2 the quota, their badness score will go down 1.5x slower than it would at 0 usage). Furthermore, if both CPU and memory usage is below the threshold, the badness score will go down twice as slow (each is relative to it's threshold and are added together to form the final badness score). This value is typically a multitude longer than `time_to_max_bad` e.g. `3 * time_to_max_bad`.
- The following formula can be used to figure out how fast a user will get to 0 badness from 100 badness (assuming they're below the threshold):
```python
max_decr_per_sec = 100 / time_to_min_bad
max_decr_per_interval = max_decr_per_sec * arbiter_refresh
badness_change_per_interval = (1 - usage / quota) * max_decr_per_interval
time_to_min_bad_with_usage = 100 / badness_change_per_interval
```

**imported_badness_timeout** _(optional/defaulted: 3600)_: `int`

- The time in seconds before badness scores cannot be imported after scores are written out. (Arbiter2 stores badness scores in the status database (more below) in case of failure/restart. If Arbiter2 fails, or is restart, it imports badness scores from this database)

**cap_badness_incr** _(optional/defaulted: true)_: `boolean`

- Whether or not to cap the badness increase per interval to the badness increase at the user's quota. This prevents erroneous data from causing badness scores from skyrocket.

## Email `[email]`

**email_domain**: `string`

- The default email domain used for sending emails if the `email_addr_of(username)` integration in `integrations.py` is not set up. For example, `utah.edu` may be used such that emails are sent to `username@utah.edu`.

**from_email**: `string`

- The outbound email address.

**admin_emails**: `list of string`

- A list of adminstrator email adresses that will recieve all emails sent.

**mail_server**: `string`

- The mail address to send mail through (using SMTP).

**keep_plots**: `boolean`

- Whether or not to keep the plots after a email has been sent.

**reply_to** _(optional/defaulted: "")_: `string`

- The email address to set as reply-to in emails to users. If blank, no reply-to will be set.

**plot_location** _(optional/defaulted: "../logs/%H"): `string`

- The location where to store the plots generated.

**plot_suffix** _(optional/defaulted: "%H\_event")_: `string`

- The text appended to the filename for plots. If the default is used, the resulting filename would look like: `YYYY-MM-DDTHH:MM:SS_username_%H_event.png`.

**plot_process_cap** _(optional/defaulted: 20)_: `int` (greater or equal to 1)

- The maximum number of processes to display in plots. Note that beyond 20 processes, the plot legend flows beyond the graph boundaries (results in a taller image).

**table_process_cap** _(optional/defaulted: 12)_: `int` (greater or equal to 1)

- The maximum number of processes to display in tables.

## Database `[database]`

_Note: the database header must still exist in the toml file, even if all the values are defaulted._

**log_location** _(optional/defaulted: "../logs/%H")_: `string`

- The location (either relative to `arbiter.py` or an abspath) where to store the status database (statuses.db), the log database (logdb.db), the rotated plaintext debug log (debug) and service log (log).

**log_rotate_period** _(optional/defaulted: 7)_: `int` (greater or equal to 1)

- How long the databases should be rotated for in days. An empty log will be created, even if there are no events for a period.

**statusdb_url**  _(optional/defaulted: "sqlite:///cfg.database.log\_location/statuses.db")_: `string`

- A url (RFC 1738) pointing to a database. If not provided, Arbiter2 will use a sqlite status database (statuses.db) at the log_location. A valid url is formatted as: `dialect+driver://username:password@host:port/database`. See below for details on each component.
    - _dialect+driver_: Arbiter2 uses the SQLAlchemy Python module to interface with various databases. This module (and thus Arbiter2) supports interfacing with PostgreSQL ("postgresql"), MySQL ("mysql"), Oracle ("oracle"), Microsoft SQL Server ("mssql") and SQLite\* ("sqlite") databases. This is the dialect part of the url. The driver on the other hand is the Python module used to drive the interfacing. The full list of drivers supported by sqlalchemy can be found at the link below. If left unspecified (e.g. `mysql://` rather than `mysql+pymysql://`), then the following Python modules are used by SQLAlchemy (and must be installed):
        - _oracle_: "cx_oracle",
        - _mssql_: "pyodbc",
        - _postgresql_: "psycopg2",
        - _mysql_: "mysqlclient" (but personally, we recommend manually specifying and using "pymysql" as in our experiences the setup is easier),
        - _sqlite3_: "" (supported without a external module)
    - _username_: The username.
    - _password_: The password. Note that Arbiter may log out the url in it\'s logs, but the password field is replaced with "REDACTED".
    - _host_: The hostname or IP of the database.
    - _port_: The port of the database. This can be left out if the database runs on a dialect's default port.
    - _database_: The name of the database to use. A good name might just be "arbiter".

- The database user must be able to create tables and modify them in the database.

- The format for a sqlite* database is different: `sqlite:///<path>`. If the path is absolute you will have four leading slashes.

See the [sqlalchemy docs](https://docs.sqlalchemy.org/en/13/core/engines.html#database-urls) for details on support for databases and Python SQL interfaces.

If multiple Arbiter2 instances point to the same database and have the same `sync_group` (see below), their statuses will be synchronized. Otherwise, they will be left to their own devices. **It is recommended that the [synchronization document](SYNCHRONIZATION.md) is read before deployment.**

_Note: The log database (logdb.db) is always stored as a local sqlite database at the configured log\_location._

_Note: If multiple Arbiter instances point to the same statusdb (e.g. for synchronization), then sqlite will not work as a shared statusdb database! This is due to the behavior of networked file systems ubiquitous in HPC environments. In this case a remote database such as MySQL, PostgreSQL or Microsoft SQL Server must be used. The [sqlite3 docs provide more details on why](https://sqlite.org/faq.html#q5)._

**statusdb_sync_group** _(optional/defaulted: "")_: `str`

- If multiple Arbiter2 instances share the same database, then instances with the configured sync group here will synchronize statuses with each other. If you do not want synchronization between instances, it is recommended that "%H" (hostname special value) is used; this is not the default due to backwards compatibility with existing sqlite statusdb databases.

## Processes `[processes]`

**memsw**: `boolean`

- Whether or not to use cgroup memory.memsw. This metric includes swap inside of usage and limiting, but the total memory of the machine is still reported without swap. Some distributions disable memsw (e.g. Ubuntu). Either disable the setting here, or turn on memsw for the machine with via CONFIG_MEMCG_SWAP=yes and either CONFIG_MEMCG_SWAP_ENABLED=yes or swapaccount=1 boot parameters. See https://www.kernel.org/doc/Documentation/cgroup-v1/memory.txt for more info.

**pss**: `boolean`

- Arbiter2 can optionally use PSS (proportional shared size) for pid memory collection, rather than RSS (resident shared size). PSS has the advantage of correctly accounting for shared memory between processes, but requires special read access to /proc/<pid>/smaps if Arbiter2 is not run as root (See the install guide). RSS counts the shared memory for every process, sometimes leading to extreme and inaccurate memory usage reporting when multiple processes share memory.

- PSS does however, cause excessive CPU usage that scales with the total amount of memory used on a particular node. The `pss_threshold` configuration below can help alleviate this.

**pss_threshold**: _(optional/defaulted: 4 MiB)_ `int`

- When PSS is enabled for pid memory collection and there are lots of processes, this collection can cause the system CPU usage to skyrocket thanks to the kernel having to walk process page tables (and holding an impactful lock while doing it). To avoid this cost, Arbiter2 only collects PSS for processes with a shared memory size above the configured threshold in bytes (i.e. for processes where the shared memory is large enough for sharing between processes to make a difference).

There are several things to consider when choosing this threshold:

1. When Arbiter2 does not collect PSS due to the reported shared memory being below this threshold, it is effectively overcounting at most the total shared memory of a particular process. If there are more than one consumer of a shared memory mapping, this overcounting is then multiplied by the number of consumers. This multiplication effect effectively means that in the worst realistic case, the overcounting for each collection of shared processes (e.g. `mpirun`, `java`) is likely this threshold times the number of consumer processes (likely the number of logical or physical cores if users are smart about their choice).

2. The kernel doesn't provide very precise shared memory information in `/proc/<pid>/{status,statm}`; The actual difference between this number and an accurate one found in `/proc/<pid>/smaps` has been observed to be on the order of megabytes on CHPC systems (and perhaps more). In effect, this forces your estimatation of the worst case overcounting detailed above to be a underestimate when this is taken into account.

3. Shared memory may not be shared. File backed memory is technically shared memory, even if only one process has mapped a particular file into memory. It is expensive for the kernel to count the consumers of a mapping, so this can result in the shared memory reported by the kernel in `/proc/<pid>/{status,statm}` (used to determine whether to use PSS) possibly being an overcount of actual usage if there is signficant use of single process file-backed memory...

The privileged tool `tools/proc_shmem_diff.sh` bundled can be used to observe some of this nuance.

A default of 4 MiB was choosen based on an observation that on CHPC systems few proceses use above 4MiB of shared memory or PSS memory and nearly all processes that significantly used shared memory and contributed to historical violations were above this threshold. Given that CHPC's memory threshold is 4GiB of memory, this is somewhat of a conservative estimate to compensate for the uncertainty of non-PSS reported shared memory, despite heavy single-consumer file-backed memory overcounting. It may be worth increasing this if you notice Arbiter2 still chewing up a lot of CPU time.

**whitelist_other_processes** _(optional/defaulted: true)_: `boolean`

- Arbiter2 can optionally label the difference between cgroup and pid usage (which can be large if there are short lived processes) as "other processes". This is intended to get around the fact that if short lived processes run between Arbiter2's collection intervals, there will be unaccounted usage that is less than the recorded cgroup usage. This "other processes" usage can be whitelisted to prevent Arbiter2 from calling out bad usage, even when it may not fully know where it is coming from, including whether the unknown usage comes from whitelisted processes. In particular, users running compiliers likely are susceptible to high ammounts of "other processes".

**whitelist** _(optional/defaulted: [])_: `list of string`

- A list of whitelisted process names. Each item in the whitelist is directly compared to the name of the command run.

**whitelist_file** _(optional/defaulted: "")_: `string`

- The filepath to the whitelist (either relative to `arbiter.py` or an abspath). Each item in the whitelist should be on a newline. See above for how the whitelist works.

**proc_owner_whitelist** _(optional/defaulted: [0])_: `list of int`

- A list of uids, where each process owned by that uid is whitelisted. This may be useful in scenarios where processes are executed using `su/sudo`, but you don't want that process usage to be assocated with the user's cgroup (all subprocesses created by you are put in your cgroup, regardness of whether you've su'd to someone else).

## Status `[status]`
A status is a state that the user is in and the specific state and its properties are called a status group. A user can only have a single status at any moment, called their "current status," as well as a "default status," which is used to restore a user from their current status. A user's default status is determined by matching their uid or gid with the first status group encountered in the `order` variable. If the user doesn't match any listed in the order variable, their default status group is the `fallback_status`.

**order**: `list of string`

- The order in which to evaluate whether a user belongs in a specific status group. See below on how to setup a status group.

**fallback_status**: `string`

- The status group that a user will fall into if they don't match any in the order variable.

**div_cpu_quotas_by_threads_per_core** _(optional/defaulted: false)_: `boolean`

- Whether or not to divide the `cpu_quota` in each status group by the threads per core. i.e. the usage/quota allowed is contained to specific physical cores, rather than virtual(hyperthreaded) cores/threads. If true, this effectively means that physical cores, rather than virtual(hyperthreaded) cores will be counted towards the user's badness scores.

### Status.groupname `[status.groupname]`
A status group can be defined by indenting and creating a new section with the name of the status group appended to the word "status" with a dot seperating the two. e.g. `[status.normal]`.

**cpu_quota**: `int`

- The CPU quota as a aggregate of a single CPU thread. e.g. 100 is 1 thread, 400 is 4 threads. _This may be divided when read by the number of threads per core_, depending on the status settings.

**mem_quota**: `int, float`

- The memory quota relative to a GB.

**whitelist** _(optional/defaulted: [])_: `list of string`

- A list of whitelisted strings. See whitelist above for details.

**whitelist_file** _(optional/defaulted: "")_: `string`

- The filepath to the whitelist (either relative to `arbiter.py` or an abspath). See whitelist above for details.

**uids** _(optional/defaulted: [])_: `list of int`

- A list uids, where each user with that uid is automatically added to the status group (based on the behavior above).

**gids** _(optional/defaulted: [])_: `list of int`

- A list gids, where each user that has membership in that group is automatically added to the status group (based on the behavior above).

### Status.Penalty `[status.penalty]`
Penalty status groups are a specical kind of a status group. Naturally, they are allowed to be a status, but their quotas can be relative to a user's default status when their status changes to a penalty status group (via `relative_quotas`). Furthermore, membership in this group is limited by each status group's `timeout`. They are restored to their default status upon timeout, and a internal counter called "occurrences" increases. This occurrences count determines what penalty the user should be in by indexing (minus 1) into the `order` list when a user gets called out for their actions. As expected, the occurrences count will cap out at the last list item. This occurences count will only decrease (by 1) after the `occur_timeout` time has been reached without any more badness. You do not accrue badness inside of a penalty status group and your badness is reset upon release.

**order**: `list of string`

- The order in which penalties should be in based on the occurrences index. The strings should be the name of the penalty, not including the sections (e.g. penalty1). See below on how to setup a penalty status group.

**occur_timeout**: `int` (greater or equal to 1)

- The amount of time in seconds for which a user keeps their current "occurrence" count (after that period it is lowered by 1). This occurrences count tracks how many times the user has been in penalty, and indexes (minus 1) into the `order` list when determining which penalty to apply after a user gets called out for their actions. e.g. a user in penalty3 (the third item in the order list) would have a occurences count of 3. The timeout starts when the user returns to their default status (from penalty) and is reset when the user has a non-zero badness.

**relative_quotas**  _(optional/defaulted: true)_: `boolean`

- Whether or not to calculate a user's quota based on their default status. If `true`, then the quotas should be expressed as a fraction of 1.

### Status.Penalty.penaltyname `[status.penalty.penaltyname]`
A penalty status group can be defined by indenting (again) and creating a new section with the name of the status group appended to the word "status.penalty" with a dot seperating the two. e.g. `[status.penalty.penalty1]`. All of the previous variables are still valid and required. Variables like `whitelist` are not used in a penalty state.

**timeout**: `int`

- Time in seconds before the user is released into their default status.

**cpu_quota**: `int, float`

- See above. If `relative_quotas` is `true`, then the quota should be expressed as a fraction of 1.

**mem_quota**: `string, int, float`

- See above. If `relative_quotas` is `true`, then the quota should be expressed as a fraction of 1.

**expression**: `string`

- The expression used to identify how bad a user's violation was in emails (typically associated with the order in which it appears in `order`). e.g. "new," "repeated," "severe," "scathing" -> Email subject: "Scathing violation of usage policy by ..."

## HighUsageWatcher `[high_usage_watcher]`
Arbiter2 can optionally watch for high usage on the machine (warning if usage exceeds a watermark) and send a email to admins about such a circumstance. Arbiter2 checks for high usage every interval.

**high_usage_watcher**: `boolean`

- Whether or not to warn about high usage.

**cpu_usage_threshold**: `float` (less than or equal to 1)

- The CPU percentage of the machine that constitues a warning, expressed in a fraction of 1 (where 1 is the 100% of the machine CPU usage). Note that the threshold can be relative to physical cores, rather than virtual(hyperthreaded) cores/threads if `div_cpu_thresholds_by_threads_per_core` is true.

**mem_usage_threshold**: `float` (less than or equal to 1)

- The memory percentage of the machine that constitues a warning, expressed in a fraction of 1 (where 1 is the 100% of the machine's memory).

**timeout**: `int`

- Once a email has been sent, how long till a new email is sent.

**div_cpu_thresholds_by_threads_per_core**: _(optional/defaulted: false)_: `boolean`

- Whether or not to divide the `cpu_usage_threshold` by the threads per core. i.e. the usage threshold allowed is relative to physical cores, rather than virtual(hyperthreaded) cores/threads.

**threshold_period** _(optional/defaulted: 1)_: `int` (greater or equal to 1)

- How many arbiter refresh intervals that the usage must be above the thresholds before a warning is triggered.

**user_count** _(optional/defaulted: 8)_: `int`

- How many users to report on in overall high usage emails.

