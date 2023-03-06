# -*- mode: ruby -*-
# vi: set ft=ruby :

# TODO: Implement https://github.com/oscar-stack/vagrant-hosts

Vagrant.require_version ">= 2.3.0"

# All Vagrant configuration is done below. The "2" in Vagrant.configure
# configures the configuration version (we support older styles for
# backwards compatibility). Please don't change it unless you know what
# you're doing.
Vagrant.configure("2") do |config|

  # The most common configuration options are documented and commented below.
  # For a complete reference, please see the online documentation at
  # https://docs.vagrantup.com.

  # Check for vagrant-vbguest plugin:
  if !Vagrant.has_plugin?('vagrant-vbguest')
    puts 'ERROR: vagrant-vbguest plugin required.'
    puts 'To install run `vagrant plugin install vagrant-vbguest`'
    abort
  else
    # (Not ideal) Extra steps necessary for the Rocky 8.5 guest OS and vbguest installation
    #
    # See https://github.com/dotless-de/vagrant-vbguest/issues/423).
    config.vbguest.installer_options = {
      allow_kernel_upgrade: true,
      auto_reboot: true
    }
    config.vbguest.installer_hooks[:before_install] = [
      "dnf -y install bzip2 elfutils-libelf-devel gcc kernel kernel-devel kernel-headers libX11 libXext libXmu libXt make perl tar",
      "sleep 2"
    ]
  end

  ##############################################################
  # Create the nodes.                                          #
  ##############################################################
  (1..2).each do |i|

    config.vm.define "node#{i}" do |node|

      # Specify the Vagrant Box, version, and update check:
      node.vm.box = "rockylinux/8"
      node.vm.box_version = "5.0.0"
      node.vm.box_check_update = "false"

      # Customize the hostname:
      node.vm.hostname = "node#{i}"

      # Customize the network:
      node.vm.network "private_network", ip: "192.168.56.1#{i}", netmask: "255.255.255.0"

      # Disable default mount:
      node.vm.synced_folder '.', '/vagrant', disabled: true

      # VirtualBox Provider
      node.vm.provider "virtualbox" do |vb|
        # Customize the number of CPUs on the VM:
        vb.cpus = 2

        # Customize the amount of memory on the VM:
        vb.memory = 4096

        # Customize the name that appears in the VirtualBox GUI:
        vb.name = "node#{i}"
      end

      # Provision with shell scripts.
      node.vm.provision "shell", inline: <<-SHELL
        echo 'Hello world'
      SHELL
    end
  end

end
