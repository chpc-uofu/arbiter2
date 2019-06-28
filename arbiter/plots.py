import matplotlib
matplotlib.use("Agg")  # Required for server (no displays)
import matplotlib.pyplot as plt
import datetime
import collections
import logging
import datetime
import copy

logger = logging.getLogger("arbiter." + __name__)


def _iso_from_epoch(epoch):
    """
    Returns an ISO-formatted time string from an epoch time stamp.

    epoch: int
        An epoch timestamp.
    """
    return datetime.datetime.isoformat(_epoch_datetime(int(epoch)))


def _epoch_datetime(epoch):
    """
    Returns a datetime object from an epoch timestamp.

    epoch: int
        An epoch timestamp.
    """
    return datetime.datetime.fromtimestamp(int(epoch))


def make_multi_stackplot(plot_filepath, x, y_cpu, y_mem, proc_names, hostname,
                         username, mem_ylimit, cpu_ylimit, mem_threshold=None,
                         cpu_threshold=None):
    """
    Creates a stackplot with memory and CPU utilization. Saves the file at
    plot_filepath.

    plot_filepath: str
        The name and path of the file to save to.
    x: [int, ]
        The x-axis; epoch time stamps matching the outer list of y_cpu/mem.
    y_cpu: [[float, ], ]
        The CPU usage of each process, where the outer list represents time.
    y_mem: [[float, ], ]
        The memory usage of each process, where the outer list represents time.
    proc_names: [str, ]
        Process names. The order should correspond to the y-axes.
    hostname: str
        The hostname on which the event took place.
    username: str
        The username of the user responsible for the violation.
    mem_ylimit: int
        The y limit of the memory plot.
    cpu_ylimit: int
        The y limit of the cpu plot.
    mem_threshold: int, None
        The y mem value to place a optional threshold horizontal bar.
    cpu_threshold: int, None
        The y cpu value to place a optional threshold horizontal bar.
    """
    if (not y_cpu or not y_mem or len(y_cpu[0]) < 2 or len(y_mem[0]) < 2):
        logger.warning("Image could not be created with 0 usage values")
        return

    # Prepare axes
    x = [_epoch_datetime(i) for i in x]

    plt_format, axes = plt.subplots(2, sharex=True, figsize=(7, 7))

    # Create plots with accompanying text
    try:
        axes[0].stackplot(x, y_cpu, labels=proc_names)
        plt.xlabel("Time")
        axes[0].set_ylabel("Core usage (%)")
        axes[0].set_title("Utilization of " + username + " on " + hostname)
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
        plt.savefig(plot_filepath, bbox_inches="tight")
        plt.close()
    except IndexError:
        logger.warning("Email image could not be created. There was a "
                       "problem with indexing the axes.")
        logger.debug(exc_info=True)
        return


def multi_stackplot_from_procs(plot_filepath, hostname, username,
                               relevant_events, overall_usage, mem_ylimit,
                               cpu_ylimit, mem_threshold=None,
                               cpu_threshold=None):
    """
    Makes a mutli-stackplot from a relevant_events dictionary. The plot will
    be saved to the specified plot_filepath. The stackplot takes in processes
    and plots their usage relative to the total usage for both CPU and memory.

    plot_filepath: str
        The name and path to the file to save the plot to.
    hostname: str
        The name of the host.
    username: str
        The username of the owner of the StaticProcess().
    relevant_events: {int: [StaticProcecss(), ... ]}
        A dictionary of events; the value is a list of processes that are
        associated with that time event.
    overall_usage:
        Overall usage of the user in the form [timestamps, CPU, memory].
        See actions.output for more information.
    mem_ylimit: int
        The y limit of the memory plot.
    cpu_ylimit: int
        The y limit of the cpu plot.
    mem_threshold: int, None
        The y mem value to place a optional threshold horizontal bar.
    cpu_threshold: int, None
        The y cpu value to place a optional threshold horizontal bar.
    """
    # Get all the processes and their names
    all_procs = [proc for procs in relevant_events.values() for proc in procs]
    proc_names = set([proc.name for proc in all_procs])

    # Create a dictionary of timestamps that contain usage values
    gen_timestamps, gen_cpu, gen_mem = overall_usage
    proc_total_mem_usages = {timestamp: 0.0 for timestamp in gen_timestamps}
    proc_total_cpu_usages = copy.deepcopy(proc_total_mem_usages)

    # Generate molds with default values and later fill them in
    proc_proportional_usages = {name: [[0.0] * len(gen_timestamps),  # CPU
                                       [0.0] * len(gen_timestamps)]  # Memory
                                for name in proc_names}

    for pos, timestamp in enumerate(gen_timestamps):
        # For each timestamp, add the usage of all the processes. This is what
        # is used to find proportional usage for each process in the same
        # timestamp.
        for proc in relevant_events[timestamp]:
            proc_total_cpu_usages[timestamp] += proc.usage["cpu"]
            proc_total_mem_usages[timestamp] += proc.usage["mem"]
            proc_proportional_usages[proc.name][1][pos] += proc.usage["mem"]
            proc_proportional_usages[proc.name][0][pos] += proc.usage["cpu"]

    # Finally, generate the proportional usage values and put into lists
    mem_usages = []
    cpu_usages = []
    proc_names = []
    for proc_name in proc_proportional_usages:
        proc_mem_usages = proc_proportional_usages[proc_name][1]
        proc_cpu_usages = proc_proportional_usages[proc_name][0]

        # For each time event, fit the usage to the overall usage
        for num in range(len(proc_mem_usages)):
            ts = gen_timestamps[num]
            proc_mem_usages[num] = _fit_usage_to(proc_mem_usages[num],
                                                 proc_total_mem_usages[ts],
                                                 gen_mem[num])

            proc_cpu_usages[num] = _fit_usage_to(proc_cpu_usages[num],
                                                 proc_total_cpu_usages[ts],
                                                 gen_cpu[num])

        mem_usages.append(proc_proportional_usages[proc_name][1])
        cpu_usages.append(proc_proportional_usages[proc_name][0])
        proc_names.append(proc_name)

    sorted_cpu, sorted_mem, sorted_names = _sort_based_on_usage(cpu_usages,
                                                                mem_usages,
                                                                proc_names)

    make_multi_stackplot(plot_filepath,
                         gen_timestamps,
                         sorted_cpu,
                         sorted_mem,
                         sorted_names,
                         hostname,
                         username,
                         mem_ylimit,
                         cpu_ylimit,
                         mem_threshold,
                         cpu_threshold)


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


def _sort_based_on_usage(cpu_usages, mem_usages, proc_names):
    """
    Sorts the three different lists of process properties by the sum of CPU
    and memory usage per process.

    cpu_usages: [float, ]
        CPU usage values.
    mem_usages: [float, ]
        Memory usage values. The order should match the above.
    proc_names: [str, ]
        Process names. The order should match the above.
    """
    combined_usage = zip(cpu_usages, mem_usages, proc_names)
    sorted_zip_usage = sorted(combined_usage,
                              key=lambda x: sum(x[0]) + sum(x[1]),
                              reverse=True)
    if not zip(*sorted_zip_usage):
        return [], [], []
    return zip(*sorted_zip_usage)
