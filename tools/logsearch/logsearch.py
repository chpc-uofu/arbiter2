# This program requires Flask, which is not a requirement of Arbiter2 itself

from flask import Flask
from flask import render_template
from io import BytesIO
import sys
sys.path.append("../../arbiter")
import logdb
import datetime
import os
import base64
import matplotlib
import matplotlib.pyplot as plt
import matplotlib.dates as mdate
import matplotlib.cm as cm
import datetime
import read_database
app = Flask(__name__)


def nested_sum(inputlist):
    return sum(nested_sum(x) if isinstance(x, list) else x for x in inputlist)


# Specific path is requested (all users/events)
@app.route("/log/<path:location>")
def bypath(location=None):
    if not location:
        return "Invalid location"
    location = "/" + location
    logfile_exists = read_database.verify_file_exists(location)
    if not logfile_exists:
        return "The log file doesn't exist: " + location
    log_info = read_database.get_log_info(location)
    actions, general, process = log_info[0], log_info[1], log_info[2]
    uids = [str(i[1].user) for i in actions]

    uid_counts = {}
    for uid in uids:
        if uid in uid_counts:
            uid_counts[uid] += 1
        else:
            uid_counts[uid] = 1
    sorted_uids = [(k, uid_counts[k]) for k in sorted(uid_counts,
                                                      key=uid_counts.get,
                                                      reverse=True)]

    urls = []
    for uid in sorted_uids:
        url = "/user/" + str(uid[0]) + location
        event_text = "events" if int(uid[1]) > 1 else "event"
        urls.append("<a href=\"" + url + "\">" + str(uid[0]) + " ("
                    + str(uid[1]) + " " + event_text + ")</a>")
    return render_template("logview.html",
                           userlist=", ".join(urls),
                           location=location)


# Specific user is requested
@app.route("/user/<uid>/<path:location>")
def primary(uid=None, location=None):
    if not uid or not location:
        return "Not enough information specified"
    location = "/" + location

    # List the associated events
    assoc_events = []

    # Get the information from the log file
    logfile_exists = read_database.verify_file_exists(location)
    if not logfile_exists:
        return "The log file doesn't exist: " + location
    log_info = read_database.get_log_info(location)
    actions, general, process = log_info[0], log_info[1], log_info[2]

    # Extract relevant information from processes and general usage metrics
    rel_processes = []
    rel_general = []
    for item in actions:
        num, obj = item[0], item[1]
        if str(obj.user) != str(uid):
            continue
        for proc in process:
            if proc[0] == num:
                rel_processes.append(proc)
        for gen in general:
            if gen[0] == num:
                rel_general.append(gen)
        assoc_events.append(num)

    time = []
    cpu = []
    mem = []
    # Get CPU and memory usage over time
    for general in rel_general:
        time.append(general[1].time)
        cpu.append(general[1].cpu)
        mem.append(general[1].mem)
    # Sort things by time
    time, cpu, mem = zip(*sorted(zip(time, cpu, mem)))
    time = [datetime.datetime.utcfromtimestamp(i) for i in time]

    proc_usages = {}
    for proc in rel_processes:
        name = proc[1].name
        timestamp = proc[1].timestamp
        proc_mem = float(proc[1].mem)
        proc_cpu = float(proc[1].cpu)
        if not name in proc_usages:
            proc_usages[name] = {}
        proc_usages[name][timestamp] = [proc_cpu, proc_mem]

    # Output overall CPU and memory utilization plots
    fig, ax = plt.subplots()
    ax.plot_date(time, cpu)
    ax.set(xlabel="Time",
           ylabel="Core utilization (%)",
           title="CPU Utilization by Time")
    stringio = BytesIO()
    date_fmt = "%H:%M"
    fig.autofmt_xdate()
    # Use a DateFormatter to set the data to the correct format
    date_formatter = mdate.DateFormatter(date_fmt)
    ax.xaxis.set_major_formatter(date_formatter)
    fig.savefig(stringio, format="png", bbox_inches="tight")
    stringio.seek(0)
    base64img1 = ("data:image/png;base64,"
                  + base64.b64encode(stringio.read()).decode())

    fig, ax = plt.subplots()
    ax.plot_date(time, mem)
    ax.set(xlabel="Time",
           ylabel="Memory utilization (GB)",
           title="Memory Utilization by Time")
    stringio = BytesIO()
    date_fmt = "%H:%M"
    fig.autofmt_xdate()
    # Use a DateFormatter to set the data to the correct format
    date_formatter = mdate.DateFormatter(date_fmt)
    ax.xaxis.set_major_formatter(date_formatter)
    fig.savefig(stringio, format="png", bbox_inches="tight")
    stringio.seek(0)
    base64img2 = ("data:image/png;base64,"
                  + base64.b64encode(stringio.read()).decode())

    # Not a real sort: the dimensions on CPU and memory plots aren't the same
    # so I can't add the two directly. I do it anyway.
    proc_usages = sorted(proc_usages.items(),
                         key=lambda x: nested_sum(x[1].values()) / len(x[1]),
                         reverse=True)
    fig, ax = plt.subplots()
    fig2, ax2 = plt.subplots()
    labels = []
    for num, proc in enumerate(proc_usages):
        times = []
        values_cpu = []
        values_mem = []
        for timestep in proc[1]:
            times.append(timestep)
            values_cpu.append(proc[1][timestep][0])
            values_mem.append(proc[1][timestep][1])
        times = [datetime.datetime.utcfromtimestamp(i) for i in times]
        c = cm.rainbow(1 - num / len(proc_usages))
        ax.plot_date(times, values_cpu, color=c)
        ax2.plot_date(times, values_mem, color=c)
        # Avoid gigantic plot legends
        if not proc[0] in labels:
            labels.append(proc[0])

    ax.set(xlabel="Time",
           ylabel="Core utilization (%)",
           title="Process CPU Utilization by Time")
    # Put a legend below current axis
    ax.legend(labels, loc="upper center", bbox_to_anchor=(0.5, -0.25),
              fancybox=False, shadow=False, ncol=4)
    stringio = BytesIO()
    date_fmt = "%H:%M"
    fig.autofmt_xdate()
    # Use a DateFormatter to set the data to the correct format
    date_formatter = mdate.DateFormatter(date_fmt)
    ax.xaxis.set_major_formatter(date_formatter)
    fig.savefig(stringio, format="png", bbox_inches="tight")
    stringio.seek(0)
    base64img3 = ("data:image/png;base64,"
                  + base64.b64encode(stringio.read()).decode())

    ax2.set(xlabel="Time",
            ylabel="Memory utilization (GB)",
            title="Process Memory Utilization by Time")
    ax2.legend(labels, loc="upper center", bbox_to_anchor=(0.5, -0.25),
               fancybox=False, shadow=False, ncol=4)
    stringio = BytesIO()
    date_fmt = "%H:%M"
    fig2.autofmt_xdate()
    # Use a DateFormatter to set the data to the correct format
    date_formatter = mdate.DateFormatter(date_fmt)
    ax2.xaxis.set_major_formatter(date_formatter)
    fig2.savefig(stringio, format="png", bbox_inches="tight")
    stringio.seek(0)
    base64img4 = ("data:image/png;base64,"
                  + base64.b64encode(stringio.read()).decode())

    # Add descriptive text to the page
    # FIXME: Add links to individual actions
    if len(assoc_events) < 1:
        event_text = "No further event information is known."
    else:
        assoc_events = [str(i) for i in assoc_events]
        event_text = ("The associated events (in the log database) are: "
                      + ", ".join(assoc_events) + ".")

    return render_template("primary.html",
                           uid="the user " + str(uid),
                           location=location,
                           cpu=base64img1,
                           mem=base64img2,
                           cpubyproc=base64img3,
                           membyproc=base64img4,
                           further_info=event_text)


@app.route("/<start>/<end>/<path:location>")
def homepage(start=None, end=None, location=None):
    if not start or not end or not location:
        return("The information is not sufficient to search logs.")

    # Update location to be an absolute path and set defaults
    location = "/" + location
    output = "No output found."

    # FIXME: This depends on the structure written in the log file
    start_log = "log."
    end_log = ".db"

    start_date_obj = datetime.datetime.strptime(start, "%Y-%m-%d")
    end_date_obj = datetime.datetime.strptime(end, "%Y-%m-%d")

    output_items = []
    bad_users = {}
    users_nodes_dates = {}
    for root, dirs, files in os.walk(location):
        for filename in files:
            try:
                datestring = filename.split(start_log)[1].split(end_log)[0]
            except IndexError:
                continue  # Skip invalid filenames without checking everything
            date_obj = datetime.datetime.strptime(datestring, "%Y-%m-%d")

            # Skip dates that aren't in the time range
            if not date_obj > start_date_obj or not date_obj < end_date_obj:
                continue

            # Get a link to every individual log file
            node = root.split(os.sep)[-1]
            output_items.append("<a href=\"/log" + root + "/" + filename
                                + "\">" + node + "/" + filename + "</a>")

            # Read the Actions table in each relevant log
            log_info = read_database.get_table_info(root + "/" + filename,
                                                    "actions",
                                                    logdb.Action)[0]

            # Update the number of counts for each user on each node
            for event in log_info:
                action_obj = event[1]
                user = action_obj.user
                if user not in bad_users:
                    bad_users[user] = {}
                if node not in bad_users[user]:
                    bad_users[user][node] = 0
                if user not in users_nodes_dates:
                    users_nodes_dates[user] = {}
                if node not in users_nodes_dates[user]:
                    users_nodes_dates[user][node] = []
                bad_users[user][node] += 1
                user_path = "/user/" + str(user) + root + "/" + filename
                url = "<a href=\"" + user_path + "\">" + datestring + "</a>"
                users_nodes_dates[user][node].append(url)

    # Get the total number of bad events for each user
    bad_users_total = {}
    for user in bad_users:
        if user not in bad_users_total:
            bad_users_total[user] = 1
        for node in bad_users[user]:
            bad_users_total[user] += bad_users[user][node]

    # Get text for the number of bad events
    sorted_users = [(k, bad_users_total[k])
                    for k in sorted(bad_users_total,
                                    key=bad_users_total.get,
                                    reverse=True)]
    user_names = []
    user_usages = []
    for user in sorted_users:
        key = user[0]
        bad_count_by_node = bad_users[key]

        text_strings = []
        for node in bad_count_by_node:
            count_text = node + ": " + str(bad_count_by_node[node])
            try:
                date_text = ", ".join(
                    [date for date in
                     sorted(set(users_nodes_dates[key][node]))]
                )
            except:
                date_text = "no dates"
            text_strings.append(count_text + " (" + date_text + ")")

        text = ", ".join(text_strings)
        user_usages.append(text)
        user_names.append(key)
    user_output = []
    for i in range(len(user_names)):
        user_output.append("<h2>" + str(user_names[i]) + "</h2>" + "<p>"
                           + str(user_usages[i]) + "</p>")

    output = ("".join(user_output)
              + "<p class=\"small\"><em>Matching log files</em>: "
              + ", ".join(sorted(output_items)) + "</p>")

    return render_template("homepage.html",
                           startdate=start,
                           enddate=end,
                           logpath=location,
                           output=output)


@app.route("/")
def hello_world():
    return "Hello, world!"
