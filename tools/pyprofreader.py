#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2020 Idaho National Laboratory
#
# SPDX-License-Identifier: GPL-2.0-only
#
# Given a Python cprofile from (e.g. from python3 -m cProfile -o arbcprofile.bin),
# reads the binary profile and prints out the profile ordered by the time spent
# per-function.
#
# Written by Dylan Gardner.
# ./pyprofreader.py profile.cprofile
import pstats
import sys
p = pstats.Stats(sys.argv[1])
p.strip_dirs()
p.sort_stats(pstats.SortKey.TIME)
p.print_stats()
