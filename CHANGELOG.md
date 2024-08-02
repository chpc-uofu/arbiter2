# Changelog

## Version 2.1.0

**Upgrading to 2.1.0 requires at least 2.0.0**

**Changes:**

- Added ARCHITECTURE.md which describes in a high-level how Arbiter2 functions.

- Added CGROUPS.md which describes background knowledge on linux cgroups with some details on how Arbiter2 interacts with cgroups.

- Schema was changed to better allow for deletion of nodes, and movement of nodes to different sync groups. These actions are currently not supported within the current toolset. See[SYNCRONIZATION.md file](SYNCRONIZATION.md#administrative-cleanup-upon-instance-removal) for details on manually removing hosts. The old statuses and badness scores will not be migrated. **In order to upgrade, all instances of Arbiter2 must be stopped. The first instance of each sync group started again will migrate the schema. All other instances can be started after that point.**

- `allusers_corraller.sh` tool uses full usernames when looking for users. In the past the tool only grabbed the first few characters, and users with long names were ignored. 

- Removed code that would email localhost if a mailserver could not be reached. This never fully worked, so it is unlikely to affect usage. If an email cannot be looked up, Arbiter2 will instead send admins a warning message.

- Added the `arbupdate.py` tool. This tool makes it possible for admins to change a users status manually e.g. remove a user from penalty. 

**upgrading steps (with git):**
1. git stash
2. git pull
3. git stash pop
4. restart the arbiter service: `systemctl restart arbiter2`
5. ensure that the automatic schema change succedded (logs have more detail)

**upgrading steps (without git):**
1. clone the new update into a new directory
2. copy the old configuration file over (if applicable).
3. restart the arbiter service: `systemctl restart arbiter2`
4. ensure that the automatic schema change succedded (logs have more detail)

**example:**
```
mkdir arbiter2/2.1.0
git clone https://github.com/chpc-uofu/arbiter2.git arbiter2/2.1.0
cp arbiter2/1.3.2/etc/config.toml arbiter2/2.1.0/etc
ln -s arbiter2/2.1.0 arbiter2/latest
systemctl restart arbiter2
systemctl status arbiter2
```

## Version 2.0.0

**Changes:**

- Add multi-login node syncronization. This can be implicitly enabled by having multiple Arbiter2 instances use the same remote database (it cannot be the default sqlite database `statuses.db`, though it can be a MySQL/MariaDB, PostgreSQL or Microsoft SQL Server) via the `statusdb_url` configuration option. If this option is not set, Arbiter will default to a local sqlite database at the configured log location (which includes using existing `statuses.db` databases). Note that Arbiter does not import previous statuses (i.e. penalties) from the old statuses databases found in the log location when `statusdb_url` is first added. Furthermore, new sqlite `statuses.db` databases created by Arbiter2 version 2 are not compatabile with older Arbiter versions, however older databases are compatable with this version. The log databases (`logdb.db`) have not changed.

**Upgrading steps (with git):**
1. git stash
2. git pull
3. git stash pop
4. Optionally add a `statusdb_url` option in the `database` section of the configuration file to point to a shared remote database. See the [CONFIG.md file](CONFIG.md) for more details.
5. Install the `sqlalchemy` module (`pip3 install sqlalchemy`). If you are using a remote database for syncronization you will also need to install a sqlalchemy compatiable driver for the dialect of the remote database you have choosen. A full list of compatable databases and drivers to install can be found in the [CONFIG.md file](CONFIG.md).
6. Modify the `warning_email_body` function in `etc/integrations.py` to add an additional `warning_email_body` parameter. This parameter provides a list of hosts that this particular host is syncing with. The template `etc/integrations.py` file contains provides an example of using this.
7. Run `./tools/cfgparser.py <path-to-config> [overriding-configs]` to check that your modifications work out.
8. Restart the arbiter service: `systemctl restart arbiter2`

**Upgrading steps (without git):**
1. Clone the new update into a new directory
2. Copy the old configuration file over (if applicable).
3. Optionally add a `statusdb_url` option in the `database` section of the configuration file to point to a shared remote database. See the [CONFIG.md file](CONFIG.md) for more details.
4. Install the `sqlalchemy` module (`pip3 install sqlalchemy`). If you are using a remote database for syncronization you will also need to install a sqlalchemy compatiable driver for the dialect of the remote database you have choosen. A full list of compatable databases and drivers to install can be found in the [CONFIG.md file](CONFIG.md).
5. Modify the `warning_email_body` function in `etc/integrations.py` to add an additional `warning_email_body` parameter. This parameter provides a list of hosts that this particular host is syncing with. The template `etc/integrations.py` file contains provides an example of using this.
6. Run `./tools/cfgparser.py <path-to-config> [overriding-configs]` to check that your modifications work out.
7. Restart the arbiter service: `systemctl restart arbiter2`

**example:**
```
mkdir arbiter2/2.0.0
git clone https://github.com/chpc-uofu/arbiter2.git arbiter2/2.0.0
cp arbiter2/1.3.2/etc/config.toml arbiter2/2.0.0/etc
ln -s arbiter2/2.0.0 arbiter2/latest
pip3 install sqlalchemy  # required for v2.0.0
vim arbiter2/2.0.0/etc/config.toml
pip3 install pymysql      # if using a remote MySQL database (`statusdb_url = "mysql+pymysql://arbiter:PASSWORD@organization.edu/arbiter_statusdb"`)
systemctl restart arbiter2
```

## Version 1.4.0

**Changes:**

- Integrated load average reporting into high usage emails via the site custom `integrations.py`. **This means that if you want this feature, you must merge the latest `etc/integrations.py` and `overall_high_usage_email_template.txt` into yours**. If you do not do this, nothing bad will happen but you just will not see the load average statistics in high usage warning emails. (This is why the version number has bumped to a 1.4)
- Add configurable cap to the number of processes in plots. Defaults to the height of the plot.
- Make number of processes reported in the process table configurable.
- Introduce `ARBDIR`, `ARBETC` and `ARBCONFIG` environment variables that allow for the scripts inside of `tools/` to be used anywhere from anywhere, so long as these environment variables are set. See the README.md for more information.
- Added a --version flag to Arbiter2.
- Added additional debugging logging to help identify when collection polling is behind schedule. When this happens, history points (used in plots) are not being collected in the configurable amount of time. This may cause plots to be over a longer period of time and if a system event disrupts Arbiter's collection intervals, you may see smearing of data points in plots.
- Speed up PSS memory collection on CentOS/RHEL 8 boxes massively (and on distros with 4.4+ kernels). This will go a long way towards reducing the CPU usage of Arbiter, particularly when lots of memory is in use.
- Optimized reading of PSS memory values (for non CentOS/RHEL 8 boxes and on distros with 4.4+ kernels) by ~30%. This will reduce the CPU usage of Arbiter further.
- Log the verbosity of Arbiter's logging on startup.
- Created a proof of concept `arb2influx.py` script which pushes Arbiter statistics such as violations and badness scores to InfluxDB. The script and crontab can be found in `tools/`. Although completely functional, the script will have to be modified to point to your sites InfluxDB instance. Note that the script assumes a `logs/hostname/*` structure.
- Created Arbiter summary email reporter called `arbreport.py` that looks at violations for a configurable period of time (e.g. a week) and sends out a summary email containing a list of users who have been called out over that period and the number of violations that occurred. Note that the script assumes a `logs/hostname/*` structure. An example cron wrapper script can be found at `etc/arbreport_cron_wrapper.sh`.

**Bugfixes:**

- Fixed inaccurate memory quota reporting in emails.
- Clarified that a user's occurrences timeout is reset when they have a nonzero badness score. Previously we explained that the occur_timeout "starts when a user has a badness score of 0 and is not in a penalty status", which may or may not imply that the occurrences timeout resets when you have a nonzero badness score.
- Prevented users with non-zero occurrnces from being untracked, leading to erroneous values in Arbiter's databases. This bug revolved around the fact that Arbiter2 is supposed to untrack users when they've logged out, their statuses are their default (the same as if they just logged into the machine) and they have zero occurrences (no history of violations). Unfortunately we didn't check for the last condition when untracking users, which has lead to the statusdb (statuses.db) database being filled with erroneous entries that are never removed. Arbiter2 itself can deal with this, but it causes a problem for tools/ such as `arb2influx.py` since they read statuses.db and end up pushing out erroneous violations. **To fix this issue in existing statuses.db databases, a `cleanup-statuses.py` tool/ has been created. This should be run upon upgrade and the usage will be described below**.
- Fixed inconsistent log database rotations. New empty databases will now be created, even if there are no events during the log rotation period. Furthermore, new databases will be aligned to the previous rotation.

**Upgrading steps (with git):**
1. git stash
2. git pull
3. git stash pop
4. If you want load average reporting in your high usage emails, you must merge the latest `etc/integrations.py` and `etc/overall_high_usage_email_template` into yours.
5. Stop the arbiter service: `systemctl stop arbiter2`
6. run `./tools/cleanup-statuses.py -d logs/**/statuses.db` (the last argument(s) point to your Arbiter's status databases)
7. Restart the arbiter service: `systemctl restart arbiter2`

**Upgrading steps (without git):**
1. Clone the new update into a new directory
2. Copy the old configuration file over (if applicable).
3. If you want load average reporting in your high usage emails, you must merge the latest `etc/integrations.py` and `overall_high_usage_email_template` into yours.
4. Stop the arbiter service: `systemctl stop arbiter2`
5. Run `cd tools; ./cleanup-statuses.py -g ../etc/config.toml -d logs/**/statuses.db` (the last argument(s) point to your status databases)
6. Restart the arbiter service: `systemctl restart arbiter2`

**Example:**
```
mkdir arbiter2/1.4.0
git clone https://github.com/chpc-uofu/arbiter2.git arbiter2/1.4.0
cp arbiter2/1.3.3/etc/config.toml arbiter2/1.4.0/etc
# Merge new load average reporting in integrations.py and overall_high_usage_email_template.txt into previous files
ln -s arbiter2/1.4.0 arbiter2/latest
systemctl stop arbiter2
cd tools
./cleanup-statuses.py -d ../logs/**/statuses.db  # Assumes a logs/hostname/ structure
systemctl restart arbiter2
```



## Version 1.3.3

**Changes:**

- License Arbiter with GPLv2!
- Log out usernames along with uids for easier grepping.
- Log out that Arbiter2 has started. This makes it easier to see whether Arbiter2 is waiting on permissions checks or just taking a second to collect user usage.

**upgrading steps (with git):**
1. git stash
2. git pull
3. git stash pop
4. restart the arbiter service: `systemctl restart arbiter2`

**upgrading steps (without git):**
1. clone the new update into a new directory
2. copy the old configuration file over (if applicable).
3. restart the arbiter service: `systemctl restart arbiter2`

**example:**
```
mkdir arbiter2/1.3.3
git clone https://github.com/chpc-uofu/arbiter2.git arbiter2/1.3.3
cp arbiter2/1.3.2/etc/config.toml arbiter2/1.3.3/etc
ln -s arbiter2/1.3.3 arbiter2/latest
systemctl restart arbiter2
```

## Version 1.3.2

**Bugfixes:**

- Prevent race conditions with startup permissions and accounting checks.
- Cleanly handle not being able to connect to the mail server.
- Fix bug where Arbiter2 crashes if -s flag is not used.
- Fix a lot of dumb problems with alluser_corraller.sh:
    - Remove assumption that /cgroup was symlinked to /sys/fs/cgroup.
    - Move processes to the systemd cgroup since Arbiter2 pulls from this cgroup to get pids. This unfortunately meant that previously, running this script wouldn't actually make existing user sessions visible to Arbiter2 like it's supposed to. See the install guide for why this script needs to be run.
    - Fix bug where usernames < 8 characters would break things.

**Changes:**
- Use user.slice for high usage warnings, rather than the sum of our collected user-$UID.slice usage. This will make the high usage warnings more accurate.
- Add a badsingal.py tool that tells you whether Arbiter2 detects badness on the machine.
- Add a process owner whitelist, where processes owned by a configured owner will always be whitelisted. This is meant to get around the fact that if you `su` to someone, the processes you create under that new user will still be in your original user's cgroup. **By default, processes owned by root are whitelisted**. This can be changed by adding a `proc_owner_whitelist = [0, 60081]` in the `[processes]` section of your config file. See CONFIG.md for more details.
- Lots of internal code cleanup.
- Speed up Arbiter2 a little bit.

**Upgrading steps (with git):**
1. git stash
2. git pull
3. git stash pop (May have to merge)
4. Optionally add the `proc_owner_whitelist` to the config file.
5. Restart the arbiter service: `systemctl restart arbiter2`

**Upgrading steps (without git):**
1. Clone the new update into a new directory
2. Copy the old configuration file over (if applicable).
3. Optionally add the `proc_owner_whitelist` to the config file.
4. Restart the arbiter service: `systemctl restart arbiter2`

**Example:**
```
mkdir arbiter2/1.3.2
git clone https://github.com/chpc-uofu/arbiter2.git arbiter2/1.3.2
cp arbiter2/1.3.1/etc/config.toml arbiter2/1.3.2/etc
# Optionally add proc_owner_whitelist
ln -s arbiter2/1.3.2 arbiter2/latest
systemctl restart arbiter2
```

## Version 1.3.1

**Bugfixes:**

- Fix arbiter failing when it sees a user with no passwd entry.
- Add mitigations to permission checks that are subject to race conditions.
- Fix process counts always starting at 1.
- Prevent arbiter from calling itself out
- Better handle a race condition where a cgroup/pid disappears and reappears in between polling.:

**Changes:**

- Misc code improvements.
- In badness score calculations, use averaged data, rather than instantaneous.
- In high usage warnings, use averaged data, rather than instantaneous.
- Make the configuration the final source for determining the default status (rather than the status database).
- Optionally cap the badness increase based on the max usage at the quota.
- Improve logging of high usage warnings.
- Removed whitelist pattern matching. This was removed since it was a O(n^4) operation every interval and used too much CPU.

## Version 1.3

Initial release.

