# SPDX-License-Identifier: GPL-2.0-only
import datetime
import collections
import logging
import itertools
import matplotlib
import copy
matplotlib.use("Agg")  # Required for server (no displays)
import matplotlib.pyplot as plt
import pidinfo
import usage


logger = logging.getLogger("arbiter." + __name__)


def make_multi_stackplot(filepath, title, x, y_cpu, y_mem, proc_names,
                         cpu_ylimit, mem_ylimit, cpu_threshold=None,
                         mem_threshold=None):
    """
    Creates a stackplot with memory and CPU utilization.

    filepath: str
        The name and path of the file to save to.
    title: str
        The title of the plot.
    x: [int, ]
        The x-axis; epoch time stamps matching the outer list of y_cpu/mem.
    y_cpu: [[float, ], ]
        The CPU usage of each process, where the outer list represents unique
        processes and the inner list represents time.
    y_mem: [[float, ], ]
        The memory usage of each process, where the outer list represents
        unique processes and the inner list represents time.
    proc_names: [str, ]
        Process names. The order should correspond to the y-axes.
    cpu_ylimit: int
        The y limit of the cpu plot.
    mem_ylimit: int
        The y limit of the memory plot.
    cpu_threshold: int, None
        The y cpu value to place a optional threshold horizontal bar.
    mem_threshold: int, None
        The y mem value to place a optional threshold horizontal bar.
    """
    if (not y_cpu or not y_mem or len(y_cpu[0]) < 2 or len(y_mem[0]) < 2):
        logger.warning("Image could not be created with 0 usage values")
        return

    # Prepare axes
    x = [datetime.datetime.fromtimestamp(int(i)) for i in x]

    plt_format, axes = plt.subplots(2, sharex=True, figsize=(7, 7))

    # Create plots with accompanying text
    try:
        axes[0].stackplot(x, y_cpu, labels=proc_names)
        plt.xlabel("Time")
        axes[0].set_ylabel("Core usage (%)")
        axes[0].set_title(title)
        axes[1].stackplot(x, y_mem, labels=proc_names)
        axes[1].set_ylabel("Memory usage (GB)")

        # Set axis limits for CPU and memory to the hard quota
        axes[0].set_ylim([0, cpu_ylimit])
        axes[1].set_ylim([0, mem_ylimit])

        label = "Threshold"
        if cpu_threshold:
            axes[0].axhline(y=cpu_threshold, xmin=0.0, xmax=1.0, color="r",
                            label=label)
            label = None
        if mem_threshold:
            axes[1].axhline(y=mem_threshold, xmin=0.0, xmax=1.0, color="r",
                            label=label)

        # Add a legend to the plot
        axes[0].legend(frameon=False, bbox_to_anchor=(1, 1), loc=2,
                       title="Processes\n(ordered by usage)")

        # Save images
        plt_format.autofmt_xdate()
        plt.savefig(filepath, bbox_inches="tight")
        plt.close()
    except IndexError:
        logger.warning("Email image could not be created. There was a "
                       "problem with indexing the axes.")
        logger.debug(exc_info=True)
        return


def events_to_metric_lists(events, cpu_quota, mem_quota):
    """
    Transforms a events dictionary (where the keys are timestamps and the
    values are lists of StaticProcess()s) into a list of sorted events by cpu,
    mem and process names. Every index in the outer list represents a unique
    process and every index in the inner list represents a event.

    events: {int: [StaticProcecss(), ... ]}
        A dictionary of events; the value is a list of processes that are
        associated with that time event.
    cpu_quota: float
        The cpu quota.
    mem_quota: float
        The mem quota.
    """
    # Combine process obj with same name for each event
    combo_events = {
        event: set(pidinfo.combo_procs_by_name(procs))
        for event, procs in events.items()
    }
    all_procs = set(itertools.chain.from_iterable(combo_events.values()))
    event_mold = [0.0] * len(combo_events.keys())

    # Create dict of processes, with values being usage at every event
    proc_cpu_events = {
        proc.name: copy.deepcopy(event_mold)
        for proc in all_procs
    }
    proc_mem_events = copy.deepcopy(proc_cpu_events)
    for i, event in enumerate(combo_events):
        for proc in combo_events[event]:
            # Update usage for this event
            proc_cpu_events[proc.name][i] = proc.usage["cpu"]
            proc_mem_events[proc.name][i] = proc.usage["mem"]

    proc_names = [proc.name for proc in all_procs]
    proc_cpu_events_list = list(proc_cpu_events.values())
    proc_mem_events_list = list(proc_mem_events.values())
    sorted_usage = usage.rel_sorted(
        zip(proc_cpu_events_list, proc_mem_events_list, proc_names),
        cpu_quota, mem_quota,
        key=lambda z: (sum(z[0]), sum(z[1])),  # cpu, memory
        reverse=True
    )
    return map(list, zip(*sorted_usage))


def multi_stackplot_from_events(filepath, title, events, general_usage,
                                cpu_quota, mem_quota, cpu_threshold=None,
                                mem_threshold=None):
    """
    Makes a mutli-stackplot from a events dictionary. The plot will be saved
    to the specified filepath. Note that the stackplot takes in processes and
    plots their usage relative to the overall usage (it scales the usage to
    fit the overall usage in the graph).

    filepath: str
        The name and path to the file to save the plot to.
    title: str
        The title of the plot.
    events: {int: [StaticProcecss(), ... ]}
        A dictionary of events; the value is a list of processes that are
        associated with that time event.
    general_usage:
        Overall usage of the user in the form [timestamps, CPU, memory], where
        the metrics are lists containing values for every event.
    cpu_quota: float
        The cpu quota.
    mem_quota: float
        The memory quota.
    cpu_threshold: int, None
        The y cpu value to place a optional threshold horizontal bar.
    mem_threshold: int, None
        The y mem value to place a optional threshold horizontal bar.
    """
    timestamps, gen_cpu_events, gen_mem_events = general_usage
    metric_lists = events_to_metric_lists(events, cpu_quota, mem_quota)
    proc_cpu_events, proc_mem_events, proc_names = metric_lists

    # cgroup usage is used for badness score calculations (minus whitelisted
    # process usage), but the process information we're given may not sum up
    # to the cgroup usage. Since the cgroup usage is the authority for
    # violations (it's more accurate), we'll scale up the process usage to fit
    # the cgroup usage. With the addition of "other processes**", this scaling
    # should only account for processes missing due to the proc count cutoff.
    # Ultimately the manipulated plot will justify us calling users out, even
    # when the process data is not completely accurate.
    for i in range(len(timestamps)):
        total_proc_event_cpu = sum(proc_cpu_event[i] for proc_cpu_event in proc_cpu_events)
        total_proc_event_mem = sum(proc_mem_event[i] for proc_mem_event in proc_mem_events)
        for proc_index in range(len(proc_names)):
            proc_cpu_events[proc_index][i] = _fit_usage_to(
                proc_cpu_events[proc_index][i],
                total_proc_event_cpu,
                gen_cpu_events[i]
            )
            proc_mem_events[proc_index][i] = _fit_usage_to(
                proc_mem_events[proc_index][i],
                total_proc_event_mem,
                gen_mem_events[i]
            )

    padding = 1.2
    mem_ylimit = mem_quota * padding
    cpu_ylimit = cpu_quota * padding
    make_multi_stackplot(
        filepath,
        title,
        timestamps,
        proc_cpu_events,
        proc_mem_events,
        proc_names,
        cpu_ylimit,
        mem_ylimit,
        cpu_threshold,
        mem_threshold
    )


def _fit_usage_to(usage, total_usage, target_usage):
    """
    Returns a fitted usage in relation to total usage by scaling the usage to
    match the ratio between the usage and the total usage. Returns 0.0 if the
    total_usage is 0.

    usage: float
        The current value of the usage (without scaling).
    total_usage: float
        The current maximum usage.
    target_usage: float
        The desired maximum usage.

    >>> _fit_usage_to(0.1, 0.5, 1.0)
    0.2
    """
    try:
        return usage * target_usage / total_usage
    except ZeroDivisionError:
        return 0.0
