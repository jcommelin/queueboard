#!/usr/bin/env python3

# This script accepts json files as command line arguments and displays the data in an HTML dashboard

import json
import sys
from datetime import datetime, timezone
from enum import Enum, auto, unique
from os import path
from urllib import error, request
from typing import List, NamedTuple, Tuple

from dateutil.relativedelta import relativedelta

from classify_pr_state import (CIStatus, PRState, PRStatus,
                               determine_PR_status, label_categorisation_rules)


@unique
class Dashboard(Enum):
    '''The different kind of dashboards on the created triage webpage'''
    # Note: the tables on the generated page are listed in the order of these variants.
    Queue = 0
    QueueNewContributor = auto()
    QueueEasy = auto()
    StaleReadyToMerge = auto()
    StaleDelegated = auto()
    StaleMaintainerMerge = auto()
    # All ready PRs (not draft, not labelled WIP) labelled with "tech debt".
    TechDebt = auto()
    # This PR is blocked on a zulip discussion or similar.
    NeedsDecision = auto()
    # PRs passes, but just has a merge conflict: same labels as for review, except we do require a merge conflict
    NeedsMerge = auto()
    StaleNewContributor = auto()
    # Labelled please-adopt or help-wanted
    NeedsHelp = auto()
    # Non-draft PRs into some branch other than mathlib's master branch
    OtherBase = auto()
    # "Ready" PRs whose title does not start with an abbreviation like 'feat' or 'style'
    BadTitle = auto()
    # "Ready" PRs without the CI or a t-something label.
    Unlabelled = auto()
    # This PR carries inconsistent labels, such as "WIP" and "ready-to-merge".
    ContradictoryLabels = auto()


# All input files this script expects. Needs to be kept in sync with `dashboard.sh`,
# but this script will complain if something unexpected happens.
EXPECTED_INPUT_FILES = {
    "queue.json" : Dashboard.Queue,
    "ready-to-merge.json" : Dashboard.StaleReadyToMerge,
    "automerge.json" : Dashboard.StaleReadyToMerge,
    "needs-merge.json" : Dashboard.NeedsMerge,
    "maintainer-merge.json" : Dashboard.StaleMaintainerMerge,
    "needs-decision.json" : Dashboard.NeedsDecision,
    "delegated.json" : Dashboard.StaleDelegated,
    "other-base-branch.json" : Dashboard.OtherBase,
    "new-contributor.json" : Dashboard.StaleNewContributor,
    "please-adopt.json" : Dashboard.NeedsHelp,
    "help-wanted.json" : Dashboard.NeedsHelp,
}

def short_description(kind : Dashboard) -> str:
    '''Describe what the table 'kind' contains, for use in a "there are no such PRs" message.'''
    return {
        Dashboard.Queue : "PRs on the review queue",
        Dashboard.QueueNewContributor : "PRs by new mathlib contributors on the review queue",
        Dashboard.QueueEasy : "PRs on the review queue which are labelled 'easy'",
        Dashboard.StaleMaintainerMerge : "stale PRs labelled maintainer merge",
        Dashboard.StaleDelegated : "stale delegated PRs",
        Dashboard.StaleReadyToMerge : "stale PRs labelled auto-merge-after-CI or ready-to-merge",
        Dashboard.TechDebt : "ready PRs labelled with 'tech debt'",
        Dashboard.NeedsDecision : "PRs blocked on a zulip discussion or similar",
        Dashboard.NeedsMerge : "PRs which just have a merge conflict",
        Dashboard.StaleNewContributor : "stale PRs by new contributors",
        Dashboard.NeedsHelp : "PRs which are looking for a help",
        Dashboard.OtherBase : "ready PRs into a non-master branch",
        Dashboard.Unlabelled : "ready PRs without a 'CI' or 't-something' label",
        Dashboard.BadTitle : "ready PRs whose title does not start with an abbreviation like 'feat', 'style' or 'perf'",
        Dashboard.ContradictoryLabels : "PRs with contradictory labels",
    }[kind]

def long_description(kind : Dashboard) -> str:
    '''Explain what each dashboard contains: full description, for the purposes of a sub-title
    to the full PR table. This description should not be capitalised.'''
    notupdated = "which have not been updated in the past"
    return {
        Dashboard.Queue : "all PRs which are ready for review: CI passes, no merge conflict and not blocked on other PRs",
        Dashboard.QueueNewContributor : "all PRs by new contributors which are ready for review",
        Dashboard.QueueEasy : "all PRs labelled 'easy' which are ready for review",
        Dashboard.NeedsMerge : "all PRs which have a merge conflict, but otherwise fit the review queue",
        Dashboard.StaleDelegated : f"all PRs labelled 'delegated' {notupdated} 24 hours",
        Dashboard.StaleReadyToMerge : f"all PRs labelled 'auto-merge-after-CI' or 'ready-to-merge' {notupdated} 24 hours",
        Dashboard.TechDebt : "all ready PRs (not draft, not labelled WIP) labelled with 'tech debt'",
        Dashboard.NeedsDecision : "all PRs labelled 'awaiting-zulip': these are blocked on a zulip discussion or similar",
        Dashboard.StaleMaintainerMerge : f"all PRs labelled 'maintainer-merge' but not 'ready-to-merge' {notupdated} 24 hours",
        Dashboard.NeedsHelp : "all PRs which are labelled 'please-adopt' or 'help-wanted'",
        Dashboard.OtherBase : "all non-draft PRs into some branch other than mathlib's master branch",
        Dashboard.StaleNewContributor : f"all PR labelled 'new-contributor' {notupdated} 7 days",
        Dashboard.Unlabelled : "all PRs without draft status or 'WIP' label without a 'CI' or 't-something' label",
        Dashboard.BadTitle : "all PRs without draft status or 'WIP' label whose title does not start with an abbreviation like 'feat', 'style' or 'perf'",
        Dashboard.ContradictoryLabels : "PRs whose labels are contradictory, such as 'WIP' and 'ready-to-merge'",
    }[kind]

def getIdTitle(kind : Dashboard) -> Tuple[str, str]:
    '''Return a tuple (id, title) of the HTML anchor ID and a section name for the table
    describing this PR kind.'''
    return {
        Dashboard.Queue : ("queue", "Review queue"),
        Dashboard.QueueNewContributor : ("queue-new-contributors", "New contributors' PRs on the queue"),
        Dashboard.QueueEasy : ("queue-easy", "PRs on the review queue labelled 'easy'"),
        Dashboard.StaleDelegated : ("stale-delegated", "Stale delegated PRs"),
        Dashboard.StaleNewContributor : ("stale-new-contributor", "Stale new contributor PRs"),
        Dashboard.StaleMaintainerMerge : ("stale-maintainer-merge", "Stale maintainer-merge'd PRs"),
        Dashboard.StaleReadyToMerge : ("stale-ready-to-merge", "Stale ready-to-merge'd PRs"),
        Dashboard.TechDebt : ("tech-debt", "Ready PRs labelled technical debt"),
        Dashboard.NeedsDecision : ("needs-decision", "PRs blocked on a zulip discussion"),
        Dashboard.NeedsMerge : ("needs-merge", "PRs with just a merge conflict"),
        Dashboard.NeedsHelp : ("needs-owner", "PRs looking for help"),
        Dashboard.OtherBase : ("other-base", "PRs not into the master branch"),
        Dashboard.Unlabelled : ("unlabelled", "PRs without an area label"),
        Dashboard.BadTitle : ("bad-title", "PRs with non-conforming titles"),
        Dashboard.ContradictoryLabels : ("contradictory-labels", "PRs with contradictory labels"),
    }[kind]


# The information we need about each PR label: its name, background colour and URL
class Label(NamedTuple):
    name : str
    '''This label's background colour, as a six-digit hexadecimal code'''
    color : str
    url : str


# Basic information about a PR: does not contain the diff size, which is contained in pr_info.json instead.
class BasicPRInformation(NamedTuple):
    number : int # PR number, non-negative
    author : dict
    title : str
    url : str
    labels : List[Label]
    # Github's answer to "last updated at"
    updatedAt : str


# Information passed to this script, via various JSON files.
class JSONInputData(NamedTuple):
    # Not every dashboard need be covered!
    prs_on_boards : dict[Dashboard, List[BasicPRInformation]]
    # Information about all non-draft PRs
    nondraft_prs: List[BasicPRInformation]
    # Information about all PRs marked as draft
    draft_prs: List[BasicPRInformation]


# Validate the command-line arguments and try to read all data passed in via JSON files.
def read_json_files() -> JSONInputData:
    # Check if the user has provided the correct number of arguments
    if len(sys.argv) < 3:
        print("Usage: python3 dashboard.py <all-nondraft-prs.json> <all-draft-PRs.json> <json_file1> <json_file2> ...")
        sys.exit(1)
    # Dictionary of all PRs to include in a given dashboard.
    # This data is given by the json files provided by the user.
    prs_to_list : dict[Dashboard, List[BasicPRInformation]] = dict()
    # Iterate over the json files provided by the user
    for i in range(3, len(sys.argv)):
        filename = sys.argv[i]
        if filename not in EXPECTED_INPUT_FILES:
            print(f"bad argument: file {filename} is not recognised; did you mean one of these?\n{', '.join(EXPECTED_INPUT_FILES.keys())}")
            sys.exit(1)
        with open(filename) as f:
            prs = _extract_prs(json.load(f))
            kind = EXPECTED_INPUT_FILES[filename]
            prs_to_list[kind] = prs_to_list.get(kind, []) + prs
    with open(sys.argv[1]) as ready_file, open(sys.argv[2]) as draft_file:
        all_nondraft_prs = _extract_prs(json.load(ready_file))
        all_draft_prs = _extract_prs(json.load(draft_file))
    return JSONInputData(prs_to_list, all_nondraft_prs, all_draft_prs)


EXPLANATION = '''
<p>To appear on the review queue, your open pull request must
<ul>
<li>be based on the <em>master</em> branch of mathlib</li>
<li>pass mathlib's CI</li>
<li>not be blocked by another PR (as marked by the labels <em>blocked-by-other-PR</em> and similar)</li>
<li>have no merge conflict (as marked by the <em>merge-conflict</em>)</li>
<li>not be in draft status, nor labelled with one of <em>WIP</em>, <em>help-wanted</em> or <em>please-adopt</em>: these mean the PR is not fully ready yet</li>
<li>not be labelled <em>awaiting-CI</em>, <em>awaiting-author</em> or <em>awaiting-zulip</em></li>
<li>not be labelled <em>delegated</em>, <em>auto-merge-after-CI</em> or <em>ready-to-merge</em>: these labels mean your PR is already approved</li>
</ul>
</p><p>
The table below contains all open PRs against the `master` branch, with information on these individual checks.
You can filter that list as you like, such as by entering the PR number or your github username.</p>'''.lstrip()


# Print a webpage "why is my PR not on the queue" to a new file of name 'outfile'.
def print_on_the_queue_page(outfile : str) -> None:
    def icon(state: bool) -> str:
        return '&#9989;' if state else '&#10060;'
    input_data = read_json_files()
    prs = input_data.nondraft_prs
    body = ""
    for pr in prs:
        result = ""
        labels = "".join(label_link(label) for label in pr.labels)
        result = (f"<tr>\n      <td>{pr_link(pr.number, pr.url)}</td>\n      <td>{user_link(pr.author)}</td>\n" +
          f"      <td>{title_link(pr.title, pr.url)}</td>\n      <td>{labels}</td>""")
        status = "pass" # TODO! need advanced file information for this...
        result += f"      <td>{status}</td>\n"
        is_blocked = any(lab.name in ["blocked-by-other-PR", "blocked-by-core-PR", "blocked-by-batt-PR", "blocked-by-qq-PR"] for lab in pr.labels)
        result += f"      <td>{icon(not is_blocked)}</td>\n"
        has_merge_conflict = "merge-conflict" in [lab.name for lab in pr.labels]
        result += f"      <td>{icon(not has_merge_conflict)}</td>\n"
        is_ready = not (any(lab.name in ["WIP", "help-wanted", "please-adopt"] for lab in pr.labels))
        result += f"      <td>{icon(is_ready)}</td>\n"
        review = not (any(lab.name in ["awaiting-CI", "awaiting-author", "awaiting-zulip"] for lab in pr.labels))
        result += f"      <td>{icon(review)}</td>\n"
        overall = (not is_blocked) and (not has_merge_conflict) and is_ready and review
        result += f"      <td>{icon(overall)}</td>\n"
        body += result + "    </tr>"
    table = f"""  <table>
    <thead>
    <tr>
    <th>Number</th>
    <th>Author</th>
    <th>Title</th>
    <th>Labels</th>
    <th>CI status?</th>
    <th><a title="not labelled with blocked-by-other-PR, blocked-by-batt-PR, blocked-by-core-PR, blocked-by-qq-PR">not blocked?</a></th>
    <th>no merge conflict?</th>
    <th><a title="not in draft state or labelled as in progress">ready?</a></th>
    <th><a title="not labelled awaiting-author, awaiting-zulip, awaiting-CI">awaiting review?</a></th>
    <th>On the review queue?</th>
    </tr>
    </thead>
    {body}
    </table>"""
    with open(outfile, "w") as fi:
        print(HTML_HEADER, file=fi)
        print("  <h1>Why is my PR not on the queue?</h1>", file=fi)
        # FUTURE: can this time be displayed in the local time zone of the user viewing this page?
        updated = datetime.now(timezone.utc).strftime("%B %d, %Y at %H:%M UTC")
        print(f"  <small>This page was last updated on: {updated}<br>", file=fi)
        print(EXPLANATION, file=fi)
        print(table, file=fi)
        print(HTML_FOOTER, file=fi)


def main() -> None:
    input_data = read_json_files()
    print_on_the_queue_page("on_the_queue.html")

    print_html5_header()
    # Print a quick table of contents.
    links = []
    for kind in Dashboard._member_map_.values():
        (id, _title) = getIdTitle(kind)
        links.append(f"<a href=\"#{id}\" title=\"{short_description(kind)}\" target=\"_self\">{id}</a>")
    print(f"<br><p>\n<b>Quick links:</b> <a href=\"#statistics\" target=\"_self\">PR statistics</a> | {str.join(' | ', links)}</p>")

    # List of all PR numbers which should have further information presented:
    # basically, any PR on one of the dashboards, except for the "bad title", "missing labels" etc. ones.
    prs_with_info : List[int] = []
    prs_to_list : dict[Dashboard, List[BasicPRInformation]] = input_data.prs_on_boards
    for kind in prs_to_list:
        prs_with_info.extend([pr.number for pr in prs_to_list[kind]])
    queue_prs = prs_to_list[Dashboard.Queue]
    prs_to_list[Dashboard.QueueNewContributor] = prs_with_label(queue_prs, 'new-contributor')
    prs_to_list[Dashboard.QueueEasy] = prs_with_label(queue_prs, 'easy')

    print(gather_pr_statistics(prs_to_list, input_data.nondraft_prs, input_data.draft_prs))
    all_ready_prs = prs_without_label(input_data.nondraft_prs, 'WIP')
    prs_to_list[Dashboard.TechDebt] = prs_with_label(all_ready_prs, 'tech debt')
    prs_with_info.extend([pr.number for pr in prs_to_list[Dashboard.TechDebt]])

    # Get advanced information about all PRs to consider.
    repo_url = "https://github.com/jcommelin/gh-mathlib-metadata"
    for pr_number in set(prs_with_info):
        url = f"{repo_url}/blob/master/data/{pr_number}/pr_info.json"
        try:
            request.urlretrieve(url, f"{pr_number}.json")
            # print(f"Downloading information for PR {pr_number} was successful!")
        except error.URLError:
            print(f"Found no data for PR {pr_number}", file=sys.stderr)

    (bad_title, unlabelled, contradictory) = compute_dashboards_bad_labels_title(input_data.nondraft_prs)
    prs_to_list[Dashboard.BadTitle] = bad_title
    prs_to_list[Dashboard.Unlabelled] = unlabelled
    prs_to_list[Dashboard.ContradictoryLabels] = contradictory
    for kind in Dashboard._member_map_.values():
        print_dashboard(prs_to_list.get(kind, []), kind)

    print(HTML_FOOTER)


# Compute the status of each PR in a given list. Return a dictionary keyed by the PR number.
# (`BasicPRInformation` is not hashable, hence cannot be used as a dictionary key.)
def compute_pr_statusses(prs: List[BasicPRInformation]) -> dict[int, PRStatus]:
    def determine_status(info: BasicPRInformation, is_draft: bool) -> PRStatus:
        # Ignore all "other" labels, which are not relevant for this anyway.
        labels = [label_categorisation_rules[l.name] for l in info.labels if l.name in label_categorisation_rules]
        state = PRState(labels, CIStatus.Pass, is_draft)
        return determine_PR_status(datetime.now(), state)
    # FIXME: we assume every PR is passing CI, which is too optimistic.
    return {
       info.number: determine_status(info, False) for info in prs
    }


def gather_pr_statistics(prs: dict[Dashboard, List[BasicPRInformation]], all_ready_prs: List[BasicPRInformation], all_draft_prs: List[BasicPRInformation]) -> str:
    ready_pr_status = compute_pr_statusses(all_ready_prs)
    queue_prs = prs[Dashboard.Queue]
    justmerge_prs = prs[Dashboard.NeedsMerge]

    # Collect the number of PRs in each possible status.
    # NB. The order of these statusses is meaningful; the statistics are shown in the order of these items.
    statusses = [
        PRStatus.AwaitingReview, PRStatus.Blocked, PRStatus.AwaitingAuthor, PRStatus.MergeConflict,
        PRStatus.HelpWanted,PRStatus.NotReady,
        PRStatus.AwaitingDecision,
        PRStatus.Contradictory,
        PRStatus.Delegated, PRStatus.AwaitingBors,
    ]
    number_prs : dict[PRStatus, int] = {
        status : len([number for number in ready_pr_status if ready_pr_status[number] == status]) for status in statusses
    }
    number_prs[PRStatus.NotReady] += len(all_draft_prs)
    # Check that we did not miss any variant above
    for status in PRStatus._member_map_.values():
        assert status == PRStatus.Closed or status in number_prs.keys()

    # For some kinds, we have this data already: the review queue and the "not merged" kinds come to mind.
    # Let us compare with the classification logic.
    queue_prs_numbers = [pr for pr in ready_pr_status if ready_pr_status[pr] == PRStatus.AwaitingReview]
    if queue_prs_numbers != [i.number for i in queue_prs]:
        right = [i.number for i in queue_prs]
        print(f"warning: the review queue and the classification differ: found {len(right)} PRs {right} on the former, but the {len(queue_prs_numbers)} PRs {queue_prs_numbers} on the latter!", file=sys.stderr)
    # TODO: also cross-check the data for merge conflicts

    number_all = len(all_ready_prs) + len(all_draft_prs)
    def link_to(kind: Dashboard, name="these ones") -> str:
        return f"<a href=\"#{getIdTitle(kind)[0]}\" target=\"_self\">{name}</a>"
    def number_percent(n: int , total: int, color: str = "") -> str:
        if color:
            return f"{n} (<span style=\"color: {color};\">{n/total:.1%}</span>)"
        else:
            return f"{n} (<span>{n/total:.1%}</span>)"
    instatus = {
        PRStatus.AwaitingReview: f"are awaiting review ({link_to(Dashboard.Queue)})",
        PRStatus.HelpWanted: f"are labelled help-wanted or please-adopt ({link_to(Dashboard.NeedsHelp, 'roughly these')})",
        PRStatus.AwaitingAuthor: "are awaiting the PR author's action",
        PRStatus.AwaitingDecision: f"are awaiting the outcome of a zulip discussion ({link_to(Dashboard.NeedsDecision)})",
        PRStatus.Blocked: "are blocked on another PR",
        PRStatus.Delegated: f"are delegated (stale ones are {link_to(Dashboard.StaleDelegated, 'here')})",
        PRStatus.AwaitingBors: f"have been sent to bors (stale ones are {link_to(Dashboard.StaleReadyToMerge, 'here')})",
        PRStatus.MergeConflict: f"have a merge conflict: among these, <b>{number_percent(len(justmerge_prs), number_all)}</b> would be ready for review otherwise: {link_to(Dashboard.NeedsMerge, 'these')}",
        PRStatus.Contradictory: f"have contradictory labels ({link_to(Dashboard.ContradictoryLabels)})",
        PRStatus.NotReady: "are marked as draft or work in progress",
    }
    assert set(instatus.keys()) == set(statusses)
    color = {
        PRStatus.AwaitingReview: "#33b4ec",
        PRStatus.HelpWanted: "#cc317c",
        PRStatus.AwaitingAuthor: "#f6ae9a",
        PRStatus.AwaitingDecision: "#086ad4",
        PRStatus.Blocked: "#8A6A1C",
        PRStatus.Delegated: "#689dea",
        PRStatus.AwaitingBors: "#098306",
        PRStatus.MergeConflict: "#f17075",
        PRStatus.Contradictory: "black",
        PRStatus.NotReady: "#e899cd",
    }
    assert set(color.keys()) == set(statusses)
    details = '\n'.join([f"  <li><b>{number_percent(number_prs[s], number_all, color[s])}</b> {instatus[s]}</li>" for s in statusses])
    # Generate a simple pie chart showing the distribution of PR statusses.
    # Doing so requires knowing the cumulative sums, of all statusses so far.
    numbers = [number_prs[s] for s in statusses]
    cumulative = [sum(numbers[:i+1]) for i in range(len(numbers))]
    piechart = ', '.join([f'{color[s]} 0 {cumulative[i] * 360 // number_all}deg' for (i, s) in enumerate(statusses)])
    piechart_style=f"width: 200px;height: 200px;border-radius: 50%;border: 1px solid black;background-image: conic-gradient( {piechart} );"

    return f"\n<h2 id=\"statistics\"><a href=\"#statistics\">Overall statistics</a></h2>\nFound <b>{number_all}</b> open PRs overall. Disregarding their CI state, of these PRs\n<ul>\n{details}\n</ul><div class=\"piechart\" style=\"{piechart_style}\"></div>\n"


HTML_HEADER = '''
<!DOCTYPE html>
<html>
<head>
<title>Mathlib review and triage dashboard</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/jquery/3.5.1/jquery.min.js"
    integrity="sha512-bLT0Qm9VnAYZDflyKcBaQ2gg0hSYNQrJ8RilYldYQ1FxQYoCLtUjuuRuZo+fjqhx/qtq/1itJ0C2ejDxltZVFg=="
	crossorigin="anonymous"></script>
<link rel="stylesheet" type="text/css" href="https://cdn.datatables.net/1.10.22/css/jquery.dataTables.css">
<script type="text/javascript" charset="utf8" src="https://cdn.datatables.net/1.10.22/js/jquery.dataTables.js"></script>
<link rel='stylesheet' href='style.css'>
<base target="_blank">
</head>
<body>
'''.strip()

def print_html5_header() -> None:
    print(HTML_HEADER)
    print("  <h1>Mathlib review and triage dashboard</h1>")
    # FUTURE: can this time be displayed in the local time zone of the user viewing this page?
    updated = datetime.now(timezone.utc).strftime("%B %d, %Y at %H:%M UTC")
    print(f"""  <small>This dashboard was last updated on: {updated}<br>
    Feedback on this dashboard is welcome, for instance <a href="https://github.com/jcommelin/queueboard">directly on the github repository</a>.</small>""")


HTML_FOOTER = '''
<script>
$(document).ready( function () {
  $('table').DataTable({
    pageLength: 10,
    "searching": true,
  });
});
</script>
</body>
</html>
'''.strip()


# An HTML link to a mathlib PR from the PR number
def pr_link(number: int, url: str) -> str:
    # The PR number is intentionally not prefixed with a #, so it is correctly
    # recognised and sorted as a number (with HTML formatting, a `html-num` type),
    # and not sorted as a string.
    return f"<a href='{url}'>{number}</a>"

# An HTML link to a GitHub user profile
def user_link(author: dict) -> str:
    login = author["login"]
    url   = author["url"]
    return f"<a href='{url}'>{login}</a>"

# An HTML link to a mathlib PR from the PR title
def title_link(title: str, url: str) -> str:
    return f"<a href='{url}'>{title}</a>"


# An HTML link to a Github label in the mathlib repo
def label_link(label:Label) -> str:
    # Function to check if the colour of the label is light or dark
    # adapted from https://codepen.io/WebSeed/pen/pvgqEq
    # r, g and b are integers between 0 and 255.
    def isLight(r : int, g: int, b: int) -> bool:
        # Counting the perceptive luminance
        # human eye favors green color...
        a = 1 - (0.299 * r + 0.587 * g + 0.114 * b) / 255
        return (a < 0.5)

    bgcolor = label.color
    fgcolor = "000000" if isLight(int(bgcolor[:2], 16), int(bgcolor[2:4], 16), int(bgcolor[4:], 16)) else "FFFFFF"
    return f"<a href='{label.url}'><span class='label' style='color: #{fgcolor}; background: #{bgcolor}'>{label.name}</span></a>"


def format_delta(delta: relativedelta) -> str:
    if delta.years > 0:
        return f"{delta.years} years"
    elif delta.months > 0:
        return f"{delta.months} months"
    elif delta.days > 0:
        return f"{delta.days} days"
    elif delta.hours > 0:
        return f"{delta.hours} hours"
    elif delta.minutes > 0:
        return f"{delta.minutes} minutes"
    else:
        return f"{delta.seconds} seconds"


# Function to format the time of the last update
# Input is in the format: "2020-11-02T14:23:56Z"
# Output is in the format: "2020-11-02 14:23 (2 days ago)"
def time_info(updatedAt: str) -> str:
    updated = datetime.strptime(updatedAt, "%Y-%m-%dT%H:%M:%SZ")
    now = datetime.now()
    # Calculate the difference in time
    delta = relativedelta(now, updated)
    # Format the output
    s = updated.strftime("%Y-%m-%d %H:%M")
    return f"{s} ({format_delta(delta)} ago)"


# Extract all PRs mentioned in a data file.
def _extract_prs(data: dict) -> List[BasicPRInformation]:
    prs = []
    for page in data["output"]:
        for entry in page["data"]["search"]["nodes"]:
            labels = [Label(label["name"], label["color"], label["url"]) for label in entry["labels"]["nodes"]]
            prs.append(BasicPRInformation(
                entry["number"], entry["author"], entry["title"], entry["url"], labels, entry["updatedAt"]
            ))
    return prs


# Print table entries about a sequence of PRs.
def _print_pr_entries(prs : List[BasicPRInformation]) -> None:
    for pr in prs:
        print("<tr>")
        print("<td>{}</td>".format(pr_link(pr.number, pr.url)))
        print("<td>{}</td>".format(user_link(pr.author)))
        print("<td>{}</td>".format(title_link(pr.title, pr.url)))
        print("<td>")
        for label in pr.labels:
            print(label_link(label))
        print("</td>")
        # Detailed information about the current PR.
        pr_info = None
        filename = f"{pr.number}.json"

        if path.exists(filename):
            with open(f"{pr.number}.json", "r") as file:
                pr_info = json.load(file)
        if pr_info is None:
            print("<td>-1/-1</td>\n<td>-1</td>\n<td>-1</td>")
        else:
            inner = pr_info["data"]["repository"]["pullRequest"]
            print(f'<td>{inner["additions"]}/{inner["deletions"]}</td>')
            print(f'<td>{inner["changedFiles"]}</td>')
            # Add the number of normal and review comments.
            number_comments = len(inner["comments"]["nodes"])
            number_review_comments = 0
            review_threads = inner["reviewThreads"]["nodes"]
            for t in review_threads:
                number_review_comments += len(t["comments"]["nodes"])
            print(f"<td>{number_comments + number_review_comments}</td>")
        print("<td>{}</td>".format(time_info(pr.updatedAt)))
        print("</tr>")


# Print a dashboard of a given list of PRs.
def print_dashboard(prs : List[BasicPRInformation], kind: Dashboard) -> None:
    # Title of each list, and the corresponding HTML anchor.
    # Explain what each dashboard contains upon hovering the heading.
    (id, title) = getIdTitle(kind)
    print(f"<h2 id=\"{id}\"><a href=\"#{id}\" title=\"{long_description(kind)}\">{title}</a></h2>")
    # If there are no PRs, skip the table header and print a bold notice such as
    # "There are currently **no** stale `delegated` PRs. Congratulations!".
    if not prs:
        print(f'There are currently <b>no</b> {short_description(kind)}. Congratulations!\n')
        return

    print("""<table>
    <thead>
    <tr>
    <th>Number</th>
    <th>Author</th>
    <th>Title</th>
    <th>Labels</th>
    <th><a title="number of added/deleted lines">+/-</a></th>
    <th><a title="number of files modified">&#128221;</a></th>
    <th><a title="number of standard or review comments on this PR">&#128172;</a></th>
    <th>Updated</th>
    </tr>
    </thead>""")
    _print_pr_entries(prs)

    # Print the footer
    print("</table>")


# Extract all PRs from a given list which have a certain label.
def prs_with_label(prs: List[BasicPRInformation], label_name: str) -> List[BasicPRInformation]:
    return [prinfo for prinfo in prs if label_name in [l.name for l in prinfo.labels]]

# Extract all PRs from a given list which do not have a certain label.
def prs_without_label(prs: List[BasicPRInformation], label_name: str) -> List[BasicPRInformation]:
    return [prinfo for prinfo in prs if label_name not in [l.name for l in prinfo.labels]]


def has_contradictory_labels(pr: BasicPRInformation) -> bool:
    # Combine common labels.
    canonicalise = {
        "ready-to-merge": "bors", "auto-merge-after-CI": "bors",
        "blocked-by-other-PR": "blocked", "blocked-by-core-PR": "blocked", "blocked-by-batt-PR": "blocked", "blocked-by-qq-PR": "blocked",
    }
    normalised_labels = [(canonicalise[l.name] if l.name in canonicalise else l.name) for l in pr.labels]
    # Test for contradictory label combinations.
    if 'awaiting-review-DONT-USE' in normalised_labels:
        return True
    # Waiting for a decision contradicts most other labels.
    elif "awaiting-zulip" in normalised_labels and any(
            [l for l in normalised_labels if l in ["awaiting-author", "delegated", "bors", "WIP"]]):
        return True
    elif "WIP" in normalised_labels and ("awaiting-review" in normalised_labels or "bors" in normalised_labels):
        return True
    elif "awaiting-author" in normalised_labels and "awaiting-zulip" in normalised_labels:
        return True
    elif "bors" in normalised_labels and "WIP" in normalised_labels:
        return True
    return False

# Determine all PRs in `prs` which are not labelled `WIP` and
# - are feature PRs without a topic label,
# - have a badly formatted title (we currently only check some of the conditions in the guidelines),
# - have contradictory labels.
def compute_dashboards_bad_labels_title(prs : List[BasicPRInformation]) -> Tuple[List[BasicPRInformation], List[BasicPRInformation], List[BasicPRInformation]]:
    # Filter out all PRs which have a WIP label.
    nonwip_prs = prs_without_label(prs, 'WIP')
    with_bad_title = [pr for pr in nonwip_prs if not pr.title.startswith(("feat", "chore", "perf", "refactor", "style", "fix", "doc"))]
    # Whether a PR has a "topic" label.
    def has_topic_label(pr: BasicPRInformation) -> bool:
        topic_labels = [l for l in pr.labels if l.name in ['CI', 'IMO'] or l.name.startswith("t-")]
        return len(topic_labels) >= 1
    prs_without_topic_label = [pr for pr in nonwip_prs if pr.title.startswith("feat") and not has_topic_label(pr)]
    prs_with_contradictory_labels = [pr for pr in nonwip_prs if has_contradictory_labels(pr)]
    return (with_bad_title, prs_without_topic_label, prs_with_contradictory_labels)

main()
