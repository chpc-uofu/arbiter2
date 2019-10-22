# Changelog

## Version 1.3.3

**Changes:**

- License Arbiter with GPLv2!
- Log out usernames along with uids for easier grepping.
- Log out that Arbiter2 has started. This makes it easier to see whether Arbiter2 is waiting on permissions checks or just taking a second to collect user usage.

**Upgrading steps (with git):**
1. git stash
2. git pull
3. git stash pop
4. Restart the arbiter service: `systemctl restart arbiter2`

**Upgrading steps (without git):**
1. Clone the new update into a new directory
2. Copy the old configuration file over (if applicable).
3. Restart the arbiter service: `systemctl restart arbiter2`

**Example:**
```
mkdir arbiter2/1.3.3
git clone https://gitlab.chpc.utah.edu/arbiter2/arbiter2.git arbiter2/1.3.3
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
git clone https://gitlab.chpc.utah.edu/arbiter2/arbiter2.git arbiter2/1.3.2
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

