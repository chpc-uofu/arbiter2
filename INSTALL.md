# Installing Arbiter2

Arbiter2 is designed for CentOS 7 and 8 systems at the University of Utah Center for High Performance Computing (CHPC). It can run in other environments, provided the operating system uses a version of systemd with cgroups v1 (or a "hybrid" hierarchy, which will work with some functionality restrictions). In general, the software can be installed by

1. Downloading the Arbiter2 source files
3. Installing Python 3.6+ and the `matplotlib`, `toml` and `requests` modules
2. Creating or configuring a user to run the service, such as root or a user with limited superuser privileges
4. Updating the configuration file (`etc/config.toml`) and the integration code (`etc/integrations.py`)
5. Updating and installing the service file

A sample script called [setup.sh](setup.sh) can be used a reference for installing (but it is not recommended to be used directly).

### Acquiring the Arbiter2 source files

You can either grab the code from git or from source files [here](https://gitlab.chpc.utah.edu/arbiter2/arbiter2).

```bash
VERSION=1.4.1  # See https://gitlab.chpc.utah.edu/arbiter2/arbiter2, likely a newer version

PREFIX="/opt/"
mkdir -p $PREFIX/src/
git clone https://gitlab.chpc.utah.edu/arbiter2/arbiter2.git $PREFIX/src/Arbiter2
cd $PREFIX/src/Arbiter2
git checkout latest
```

Alternatively,

```bash
VERSION=1.4.1  # See https://gitlab.chpc.utah.edu/arbiter2/arbiter2, likely a newer version

mkdir $PREFIX/src/Arbiter2/
cd $PREFIX/src/Arbiter2
wget https://gitlab.chpc.utah.edu/arbiter2/arbiter2/-/archive/$VERSION/arbiter2-$VERSION.tar.gz
ln -s latest arbiter2-$VERSION
cd latest
```

See [UPGRADING.md](UPGRADING.md) for details on how Arbiter2 will be updated. Instructions for how to upgrade from the previous versions will be found in [CHANGELOG.md](CHANGELOG.md).

### Updating service file with install location

Arbiter2 is typically run from a systemd service and the provided service file will need be updated to reflect your installation.

```bash
# Update the install location of arbiter2.service
ARBBASEDIR="`pwd`"
sed -i "s,<ARBITER DIR>,$ARBBASEDIR,g" arbiter2.service
```

## Installing Python

Python 3 (version 3.6 or higher) is required to run Arbiter2.

### Installing from repositories

It's likely a recent version of Python 3.6 (or a more recent version of Python 3) is available as a package for your system that will work.

```bash
yum install python36
PYTHONEXE=/usr/bin/python3.6
```

### Installing from source

In general, the following steps can be taken to install Python 3.8.5, though thare are likely more recent versions of Python available.

```bash
# Install required libs and tools
yum install gcc openssl-devel bzip2-devel libffi-devel make

# Specify the installation directory for Python
PREFIX="/opt"
mkdir -p $PREFIX/src/python
cd $PREFIX/src/python

# Get the source for Python 3.8
PYVERSION=3.8.5
wget https://www.python.org/ftp/python/$PYVERSION/Python-$PYVERSION.tar.xz
tar xJf Python-$PYVERSION.tar.xz
cd Python-$PYVERSION
./configure --prefix=$PREFIX
make
make install
PYTHONEXE="$PREFIX/bin/python3"
```

### Updating service file with python location

The service file will also need to be updated with the python location.

```
sed -i "s,<PYTHON EXE>,$PYTHONEXE,g" arbiter2.service
```

### Installing Python modules

The `matplotlib` and `toml` external modules are required for Arbiter2 to function. At CHPC, we use `requests` in etc/integrations.py to fetch custom email addresses (See "Integrating things a bit more" section for more details).

```bash
$PYTHONEXE -m ensurepip --default-pip
$PYTHONEXE -m pip install -r requirements.txt
```

_Note: User-based Python module installations will also work (e.g. using the --user flag with pip), provided that the pip command is run by the user Arbiter2 will run under (See below for details on that user)_

## Setting up a user to run the script

Arbiter2 requires limited access to files generally managed by root. In the interest of security, a system account with special sudo permissions is recommended for deployment (Arbiter2 can still be run as root without any additional runtime flags). This can be used via the `User=` option in the systemd servce for running the script. If the user is not root, the `-s`, or `--sudo`, flag is required to indicate to Arbiter2 that it should make sudo calls to modify things.

```bash
# Create user with:
# -M (no home dir)
# -r (create system account)
# -s /bin/false (cannot log in)
ARBUSER=arbiter
ARBGROUP=$ARBUSER
groupadd $ARBGROUP
useradd -M -r -s /bin/false -c "System account for Arbiter2" "$ARBUSER" -g $ARBGROUP
```

_Note: A user in LDAP/NIS may also work, as long as you're careful about the `Wants=` and `After=` in the service file._

### Allowing cgroup files to be edited without root via sudoers

Arbiter2 can get around permanently being root by assuming that it can execute certain required calls, allowed by the etc/sudoers and etc/sudoers.d/\* files (and indicated to Arbiter2 with the `-s` or `--sudo` flag). These allowed calls are mostly `chmod` or `chgrp` commands that change the permissions on the cgroup hierarchy to allow Arbiter2 to write to requried files. The sudoers file (which can be directed to /etc/sudoers.d/arbiter2 for the sake of organization) can be generated by the `tools/make_sudoers.py` tool.

The make_sudoers.py script has several (optional) arguments:

1. The `--user` (`-u`) flag specifies the user Arbiter2 runs under.

2. The `--group` (`-g`) flag is used to set the group that will be set as the owner of cgroup files to allow them to be modified (e.g. Arbiter2 user's primary group).

3. The `--num-digits` (`-n`) flag sets the maximum length of a uid that is allowed. This is defaulted to 15 if the flag isn't provided.

4. The `--min-uid` (`-m`) flag is used to specify the minimum uid that Arbiter2 can write quotas out to. The default if ommited is `1000` so no service accounts or root get throttled.

_Note: the number you put here will need to be reflected in your configuration file (specifically, general.min\_uid, which defaults to 1000), but you'll be reminded later in this guide._

5. The `--run-uid` (`-r`) flag sets the uid that will be used to turn on cgroup accounting if the `-a` flag is used (see section on accounting, required unless `systemctl --version` says v239+ or CentOS 7). If it is not included, the corresponding commands will not be included in the sudoers file, thus the `-a` flag cannot be used. If it is included but no value is set, accounting can be turned on with any user. If it is included and a uid is set, accounting will be turned on using that specific user.

```bash
python3 tools/make_sudoers.py -u $ARBUSER -g $ARBGROUP -m 1000 -r > arbiter2_sudoers
vim arbiter2_sudoers # Trust but verify
mkdir -p /etc/sudoers.d
cp arbiter2_sudoers /etc/sudoers.d/arbiter2
```

#### Disabling requiretty for the Arbiter2 user

If Arbiter2 uses sudoers as specified above, then the user Arbiter2 runs under must have an exception for `requiretty` in `/etc/sudoers` (granted this isn't [disabled already](https://bugzilla.redhat.com/show_bug.cgi?id=1350922)). It appears that [support for this setting in `/etc/sudoers.d` is spotty](https://unix.stackexchange.com/questions/79960/how-to-disable-requiretty-for-a-single-command-in-sudoers#comment416647_79975), so it's recommended this is done in `/etc/sudoers` (and thus is not generated by `make_sudoers.py`). The sudoers line to disable `requiretty` for Arbiter2 should look like:

```
Defaults:<$ARBUSER>      !requiretty
```

e.g.

```
visudo
```

```
...

# Allow members of group sudo to execute any command
%sudo   ALL=(ALL:ALL) ALL

# Allow arbiter to execute sudo commands without a tty
Defaults:arbiter        !requiretty

...
```

## Setting the right permissions for Arbiter2 files
Arbiter2 should be able to read/write the `logs/` directory and be able to read everything else in the repo. It is also recommended that most users cannot read the logs/ directory, as it contains details regarding user badness scores.

```
mkdir -p logs/`hostname`/plots  # Arbiter2 defaults to writing here
chown -R $ARBUSER:$ARBGROUP .  # Make Arbiter2 own everything
chmod 770 ./logs  # Only Arbiter2 can see logs
sudo -u $ARBUSER ls `pwd`/{arbiter,logs,etc}  # Check that Arbiter2 can reach these paths
```

### Updating service file with user

The service file will also need to be updated with the user Arbiter2 is running under.

```bash
sed -i "s/User=arbiter/User=$ARBUSER/g" arbiter2.service
ARBUID=`id -u $ARBUSER`
sed -i "s/<ARBITER UID>/$ARBUID/g" arbiter2.service
```

If running as root, the `-s` flag should be removed:

```bash
sed -i "s/ -s//g" arbiter2.service
sed -i "s/<ARBITER UID>/0/g" arbiter2.service
```

## Turning on cgroups accounting

CPU and memory cgroup accounting is required for Arbiter2. Systemd hasn't fully supported turning on cgroup accounting for all user cgroups it creates until systemd v239 (RHEL/CentOS 8), so unfortunately there are some systemd version-specific hacks that have to be done if you are on a older distribution.

The systemd version can be found by running `systemctl --version`.

### Enabling on systemd v219 (RHEL/CentOS 7)

It appears that setting `User=$ARBUSER` in `arbiter2.service` so that the service runs in a cgroup at `/sys/fs/cgroup/<controller>/user.slice/user-$ARBUID.slice` (rather than under `system.slice`), in addition to setting `CPUAccounting=true` and `MemoryAccounting=true`, forces systemd to implicitly turn on accounting for all users\*. This is partially done in the service file (so no further steps are needed), just make sure `User=arbiter` is changed to the correct user you are running under.

In some future systemd version, systemd stopped propogating accounting to all users when a service runs in `user.slice`, leaving just the systemd service with accounting on, so I think this only works for CentOS 7. If you plan on upgrading to CentOS 8 in the future, I'd recommend creating those same files shown below so that your upgrade is easier.

\*since there is a rule with cgroups that if one cgroup has accounting enabled, all others at the same level must also have it enabled. Thus, since users are under the `user.slice/` cgroup, they implicitly got accounting turned on for them.

### Enabling on systemd v239+ (RHEL/CentOS 8, etc)

```bash
cat <<EOF > /etc/systemd/system/user-.slice.d/50-CPUAccounting.conf
[Slice]
CPUAccounting=true
EOF

cat <<EOF > /etc/systemd/system/user-.slice.d/50-MemoryAccounting.conf
[Slice]
MemoryAccounting=true
EOF
```

```bash
systemctl daemon-reload
```

It is recommend that this is done regardless of the systemd version to make upgrading easier.

### Enabling elsewhere

There is a rule with cgroups that if a single cgroup has accounting turned on for it, all other cgroups on the same level have to also have the same cgroup accounting enabled on for them as well. We can use this rule to hack around a lack of systemd support by always forcing a user cgroup to exist have CPU and memory accounting turned on for them. Arbiter2 can do this for us with the `-a UID` flag by automatically forcing systemd to create a cgroup for the given `-a UID` user when it starts up\*, implicitly enabling accounting for all other users.

The UID you pick for the `-a` flag must be special. Because of the transient nature of systemd user cgroups, this user cannot log out (provided they log in) or else the cgroup will disappear, leading to no users being on the machine and accounting being turned off*. The Arbiter2 user will work for this, provided you won't be creating new sessions as them (e.g. SSH-ing in will cause problems, but sudo is fine).

This flag requires that the user Arbiter2 is run under has a sudoers entry for the corresponding command (generated via the -r flag in tools/make_sudoers.py), assuming that Arbiter2 user isn't root.

_\*if Arbiter2 notices that the cgroup it created disappears with the `-a` flag for some reason, then Arbiter2 will actually try and re-create the same cgroup again, but I wouldn't rely on this behavior._

```
ARBUID=`id -u $ARBUSER`  # Retrieve Arbiter2's users uid
vim arbiter2.service  # Add the -a $ARBUID flag
```

## cgroup proccess inheritance

**Warning:** It is important to note that processes created under an existing cgroup will _always_ land in that existing cgroup. This means that `sudo -u` or variants of that do not cause systemd to create cgroups for the corresponding user you sudoing to. You must have a new SSH or physical session created by a process that communicates with systemd (e.g. sshd) for a `user-$UID.slice` to be created or updated with that user's process.

### Corralling processes

When you first turn on cgroups accounting for users, you effectively tell systemd to start placing _new_ user sessions into their own user cgroup (after this is done, child processes spawned by that session are automatically put in the cgroup by the kernel). This will always be done so long as accounting is kept on. That being said, systemd doesn't move existing user sessions and their processes to their proper user cgroups when you enable accounting. **Such processes are effectively invisible to Arbiter2** (and as such will not accrue badness: child processes of the shells, which are not associated with particular cgroup slices, will not be counted). [A tool to "corral" processes](tools/allusers_corraller.sh) is provided to move existing user sessions and their processes to the appropriate cgroup. Another approach can be to restart the node, but make sure the Arbiter2 service is enabled to start again on reboot (or another mechanism will turn on accounting on startup).

```bash
cd tools/
sudo ./allusers_corraller.sh  # Must be root to move all the processes
cd -
```

## Checking the configuration

The configuration files used to tweak Arbiter2 are [toml](https://github.com/toml-lang/toml) files. The documentation for configuration is located at [CONFIG.md](CONFIG.md). A default configuration is also provided at `../etc/config.toml`. The configuration can be checked with the `tools/cfgparser.py` tool, which allows you to either check your configurations for value based validity (they cascade, allowing for simple overrides), or to print the resulting config. Please check and modify ./etc/config.toml (and any other configuration files that are relevant) and verify that the options are correct using [CONFIG.md](CONFIG.md). Other changes to site-specific functions (such as email lookups) may be necessary in `etc/integrations.py`, see "Integrating things a bit more".

### Sudoers and the configuration's min\_uid consistency

Please note that the min\_uid setting that you set via the `--min-uid` (`-m`) flag (or no min\_uid if not provided) when generating the sudoers file with `make_sudoers.py` must be less or equal than your configuration's `general.min_uid` setting. If the sudoers setting is not less than or equal to the configuration's setting, then Arbiter2 may fail to write out quotas for some users.

### Testing emails

To ensure the configured mail server and administrator emails are set up properly, you can use the with [tools/test\_email.py](tools/test_email.py) tool. The email configuration-testing tool requires the `-e` flag (the etc/ directory) and the `-g` flag (the configuration files). By default, the configurations will be used to determine the message headers and contents. It is possible to overwrite most options with command-line arguments (see them with `test_email.py --help`).

```bash
python3 test_email.py -e ../etc/ -g ../etc/config.toml  # from the tools/ directory
```

## Integrating things a bit more

There are some aspects of Arbiter2 that can be easily changed, such as looking up email addresses and creating custom formatted emails, without having the modify Arbiter2's core code. These tweaks can be done in the `etc/integrations.py` file. The following things can be modified:

- Warning Email Subject (`warning_email_subject`). This is the subject for the email sent to the user warning about their behavior.

- Warning Email Body (`warning_email_body`). This is the body of the warning email; it defaults to reading `etc/warning_email_template.txt`.

- Nice Email Subject (`nice_email_subject`). This is the subject for the email sent to the user telling them that their penalty has been removed.

- Nice Email Body (`nice_email_body`). This is the body of the nice email; it defaults to reading `etc/nice_email_template.txt`.

- Email Address Lookups (`email_addr_of`). This is the email address of the user in penalty. It defaults to `username@` the configured email domain in the `email` section of the configuration.

- Real Name Lookups: (`get_user_metadata`). This returns a custom tuple with the user's real name and their email address from `email_addr_of`.

## Running the script

The key parts to change are shown in the service file with the "TODO:" prefix. Part of this is making sure that the [arguments](#notable_args) are correct. If a system account is used, that user should be assigned via the `User=` setting and the `Slice=` uid should be changed, as well as making sure that the `-s` or `--sudo` flag is provided (and sudoers is set up). Notably, there is also a `CAP_SYS_PTRACE` [capability](http://man7.org/linux/man-pages/man7/capabilities.7.html) assigned to the service, which allows Arbiter2 to read /proc/\<pid\>/smaps to get PSS memory values. See below for more details.

When the service file has been created and the files are in place, run `systemctl daemon-reload` and then `systemctl start arbiter2` to start the service. (You can also `enable` it when you've finished testing). Check the status with `systemctl status arbiter2`.

```bash
# Check for validity
vim arbiter2.service
cp arbiter2.service /etc/systemd/system
systemctl daemon-reload
systemctl start arbiter2
systemctl enable arbiter2
```

### <span id="notable_args"></span>Notable arguments for Arbiter2

If you want to change the location of etc/, you can do so with the `-e` flag. Naturally this means that Arbiter2 will no longer look for configuration files `../etc/*`. Logging can be controlled with the `-p`/`-q`/`-v` flags. See the logging section below for details. Most importantly, the `-g` flag controls the configuration(s) provided to Arbiter2. See `python3 arbiter.py --help` for all options.

#### Exiting when a file is touched

The purpose of the optional `--exit-file` flag is to allow the easy updating of Arbiter2 across multiple clusters/nodes when passwordless ssh is not enabled. When the specified file (which must be owned by the Arbiter2 groupname set in the config) is touched after Arbiter2 starts, Arbiter2 will exit with a specific error code that can be caught in each Arbiter2 systemd service across nodes and force a restart of the service. If set up correctly, multiple Arbiter2 instances can look for the same file on a network file system and all restart. This enables adminstrators (and potentially user services teams if permissions are set correctly) to change things like whitelists, or even the entire code, and have all Arbiter2 instances across different nodes update without having to log into each of them individually and restart them there. It's a bit of a hack, but a somewhat useful one at that.

### Running directly (for testing purposes)

Arbiter2 should be run directly from the default directory structure by changing to the `./arbiter` directory and running `python3 arbiter.py` with appropriate flags (e.g. `python3 arbiter.py -s -a 1019 -g /path/to/config.toml`, which turns on accounting using uid 1019, uses sudo and sets the location of the configuration files). Note that running with the working directory set outsidde of `arbiter/` may cause problems.

### Using PSS vs RSS for PID memory collection

Arbiter2 can use either PSS or RSS as its process memory metric (PSS can be used to correctly account for shared memory but requires access to /proc/\<pid\>/smaps). In order to use PSS (which is more accurate), the program or service must have a `CAP_SYS_PTRACE` ambient [capability](http://man7.org/linux/man-pages/man7/capabilities.7.html) (or be run as root), as well as having it enabled in the config `pss = true`. This can be done for the systemd service by adding `AmbientCapabilities=CAP_SYS_PTRACE` to the `[Service]` section of the systemd service file. Ambient capabilities (which are required because Arbiter2 is not a binary) are explained [in this article](https://lwn.net/Articles/636533/). Further resources on why we need ambient capabilities for Python can be found [in this question-and-answer page](https://stackoverflow.com/questions/38321220/script-with-cap-net-bind-service-cant-listen-on-port-80).

## Aside: Previous RHEL 7/CentOS 7 Issues

_Note: Arbiter2 used to require the `--rhel7-compat` flag for RHEL/CentOS 7 nodes, but that flag and the workaround enabled by it is no longer necessary. That flag was introduced because kernel memory accounting was disabled by Red Hat and Arbiter2 historically used this kernel memory accounting to subtract out kernel page caching present in the cgroup memory controller's `memory.usage_in_bytes` file. A better understanding of the cgroup memory subsystem has led to a better solution that no longer relies on kernel memory measurements and the workaround is no longer needed._

## Testing Arbiter2 in a permissive mode

Arbiter2 has a mode called `debug_mode` that stops limits and quotas from being set, as well as sending emails to only the configured administrators. This is useful when Arbiter2 is first deployed for testing out the limits you may want to set. In this mode, any user can run Arbiter2 without any special permissions, as long as `pss = false` (since that requires a `CAP_SYS_PTRACE` capability). PSS can easily be disabled by appending the `../etc/_noperms.toml` partial config to the end of the config list (`-g` or `--config`) when starting Arbiter2. Debug mode can also easily be temporarily set by appending the `../etc/_debug.toml` partial config.

## Logging

By default, Arbiter2 automatically logs various things like its startup configuration and permission checks to stdout. Arbiter2 also logs all non-startup logging events to the rotating debug file and sends violation logging to the rotating service logs (e.g. who has nonzero badness and their corresponding badness score) at `log`. The location of these log files is configurable, but defaults to ../logs/ (with respect to arbiter/arbiter.py). If desired, non-startup logging can also be sent to stdout using the `-p` flag (without any additional flags, this sets the minimum logging level to INFO). The verbosity of this can be controlled with the `-v` and `-q` flags, which sets the minimum logging level to DEBUG and CRITICAL, respectively. If Arbiter2 is run in a service, this output can be viewed using `journalctl`.

### Levels (and what types of messages appear with that level)

| Level | Types of messages | Example |
| --- | --- | --- |
| DEBUG | Anything of significance. | 1000 is new and has status: Status(...) |
| INFO | Operational messages. | User 1000 was put in: penalty2 |
| WARNING | Problems that Arbiter2 is having, but can safely deal with. | Image could not be found or attached to email |
| CRITICAL | Not used. | Not applicable. |
| ERROR | Full stop issue. Arbiter2 will probably exit. | Arbiter2 does not have sufficient permissions to read /proc/\<pid\>/smaps (requires root or CAP_SYS_PTRACE capabilities) with pss = true |

## Debugging issues

If the system service fails (`systemctl status arbiter2`), start with the service output (`journalctl -u arbiter2`). If this failure occurs quickly during startup, it is likely Arbiter2 is failing because there is an issue with the config or with not sufficient permissions. This will be indicated there with the `arbiter_startup` logger printed out to stdout. If that isn't the case, look at the debug logs to try and see what logging took place before exiting. The stacktrace of a failure is typically sent to stderr, meaning `journalctl` is the likely place to look for that (Note: You may have to check in `journalctl -xe` since systemd often doesn't capture the last messages of a service in older versions). If all else fails, you can try running the program youself. You can also see if it's a permissions issue by checking that it works in debug mode (you can just append `../etc/_debug.toml` to the end of the -g config list) and by disabling PSS (you can also append `../etc/_noperms.toml`).

In addition to those steps, you may also use the `tools/badsignal.py` tool to check that Arbiter2 is seeing badness on the node. This tool is essentially a mini Arbiter2 that contains just the core collection and badness calculations functions and by default, will print out any badness it sees over a internal. You may also find the `--debug` flag to be useful as it prints out the usage that Arbiter2 is seeing for every user though it is quite verbose.

```
cd tools/
python3 badsignal.py --help
python3 badsignal.py -g ../etc/config.toml --debug
```
