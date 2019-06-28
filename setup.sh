#!/bin/bash
# Sets up Arbiter2 to run as a daemon

# SETTING THIS WILL CAUSE NO PROMPT TO APPEAR AND ARBITER WILL DO EVERYTHING
always_yes=false

ask_if() {
  if $always_yes; then
    return 0
  fi
  read -p "$1 (y, n, q) [default: $2]: " ynq

  # if blank, go with default
  if [ "$ynq" = "" ]; then
    ynq="$2"
  fi

  # yes!
  if [ "$ynq" = "y" ] || [ "$ynq" = "Y" ]; then
    return 0

  # no.
  elif [ "$ynq" = "n" ] || [ "$ynq" = "N" ]; then
    return 1

  # quit
  elif [ "$ynq" = "q" ] || [ "$ynq" = "Q" ]; then
    echo "Quitting."
    exit 1

  # repeat
  else
    ask_if "$1"
  fi
}


if ! ask_if "Have you installed Python 3.6+? (Will fail if otherwise)" "y"; then
  exit 1
fi

if ask_if "Do you want to install matplotlib, toml and requests? (pip3 install matplotlib toml requests)" "y"; then
  pip3 install matplotlib toml requests
fi

arbdir=`pwd`
if ask_if "Do you want to clone Arbiter2 to cwd? (if no, will ask what directory is it in)" "y"; then
  git clone https://gitlab.chpc.utah.edu/arbiter2/arbiter2.git

elif read -p "What dir is it in? " arbdir && [ -d "$arbdir" ]; then
  cd "$arbdir"
fi

arbname="root"
if ask_if "Do you want to add a service account named arbiter? (if no, will set arbiter to run as root)" "y"; then
  arbname="arbiter"
  # No home directory, no group, system user, no shell
  useradd -M -N -r -s /bin/false -c "System account for Arbiter2" "$arbname"

  if read -p "What group do you want to add to arbiter? (req to generate sudoers): " arbgroup; then
    usermod -a -G "$arbgroup" "$arbname"

    if ask_if "Do you want to generate /etc/sudoers.d/arbiter2 with arbiter and the groupname? (python3 tools/make_sudoers.py -u arbname -g arbgroup > /etc/sudoers.d/arbiter2)" "y"; then
      flags="-u $arbname -g $arbgroup"
      sudoers_loc=/etc/sudoers.d/arbiter2
      python3 tools/make_sudoers.py $flags > $sudoers_loc
      chmod 440 $sudoers_loc
    fi
  fi
fi

if ask_if "Do you want to setup the default log location (logs/hostname)?" "y"; then
  # Make a log directory and set the owner and permissions
  mkdir -p logs/`hostname`
  chown $arbname logs/`hostname`
  chmod 773 logs/`hostname`
fi

# If systemd v239+
if [ "`systemd --version | head -1 | cut -d ' ' -f2`" -ge "239" ] && ask_if "Do you want to turn on cgroups accounting?" "y"; then
  systemctl set-property user-`id -u`.slice CPUAccounting=true MemoryAccounting=true
fi

echo "Assuming the configuration and integrations are set up."

service=arbiter2.service
if ask_if "Do you want to setup $service with the settings above?" "y"; then
  sed -i "s/ARBITER_DIR=/ARBITER_DIR=$arbdir/g" "$service"
  # WorkingDirectory must be a abs path, cannot have environment vars in it
  sed -i "s;WorkingDirectory=/home/arbiter/;WorkingDirectory=$arbdir;g" "$service"
  exec_start=`grep /arbiter/arbiter.py $service`

  # If running as root
  if [ "$arbname" = "root" ]; then
    sed -i "s;User=arbiter;User=root;" $service

  # Otherwise insert the uid of arbiter into Slice=
  else
    arbuid=`id -u $arbname`
    sed -i "s/<ARBITER UID>/$arbuid/g" "$service"
  fi
fi

if ask_if "Do you want to copy the service file to /etc/systemd/system/$service?" "n"; then
  cp $service /etc/systemd/system/$service
  chmod 664 /etc/systemd/system/$service
  echo "You can now run systemctl daemon-reload && systemctl start $service."
fi

echo "Please double check the configuration and make sure the groupname in there is the same as the groupname used for permissions."
echo "After Arbiter2 has started, you'll want to run tools/allusers_corraller.sh to corral existing user pids into their cgroups."
echo "Also, double check the install guide for things like PSS and CentOS7/RHEL7 compatability issues"
echo "Done!"
