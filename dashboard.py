#!/usr/bin/env python3

# This script accepts json files as command line arguments and displays the data in an HTML dashboard.
# It assumes that for each PR N which should appear in some dashboard,
# there is a file N.json in the `data` directory, which contains all necessary detailed information about that PR.

import json
import sys
from datetime import datetime, timedelta, timezone
from enum import Enum, auto, unique
from os import path
from typing import Dict, List, NamedTuple, Tuple

from dateutil.relativedelta import relativedelta

from classify_pr_state import (CIStatus, PRState, PRStatus,
                               determine_PR_status, label_categorisation_rules)
from util import my_assert_eq, parse_datetime, parse_json_file


@unique
class Dashboard(Enum):
    """The different kind of dashboards on the created triage webpage"""

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
    # Non-draft PRs opened from a fork
    FromFork = auto()
    # "Ready" PRs whose title does not start with an abbreviation like 'feat' or 'style'
    BadTitle = auto()
    # "Ready" PRs without the CI or a t-something label.
    Unlabelled = auto()
    # This PR carries inconsistent labels, such as "WIP" and "ready-to-merge".
    ContradictoryLabels = auto()


def short_description(kind: Dashboard) -> str:
    """Describe what the table 'kind' contains, for use in a "there are no such PRs" message."""
    return {
        Dashboard.Queue: "PRs on the review queue",
        Dashboard.QueueNewContributor: "PRs by new mathlib contributors on the review queue",
        Dashboard.QueueEasy: "PRs on the review queue which are labelled 'easy'",
        Dashboard.StaleMaintainerMerge: "stale PRs labelled maintainer merge",
        Dashboard.StaleDelegated: "stale delegated PRs",
        Dashboard.StaleReadyToMerge: "stale PRs labelled auto-merge-after-CI or ready-to-merge",
        Dashboard.TechDebt: "ready PRs labelled with 'tech debt'",
        Dashboard.NeedsDecision: "PRs blocked on a zulip discussion or similar",
        Dashboard.NeedsMerge: "PRs which just have a merge conflict",
        Dashboard.StaleNewContributor: "stale PRs by new contributors",
        Dashboard.NeedsHelp: "PRs which are looking for a help",
        Dashboard.OtherBase: "ready PRs into a non-master branch",
        Dashboard.FromFork: "ready PRs opened from a fork of mathlib",
        Dashboard.Unlabelled: "ready PRs without a 'CI' or 't-something' label",
        Dashboard.BadTitle: "ready PRs whose title does not start with an abbreviation like 'feat', 'style' or 'perf'",
        Dashboard.ContradictoryLabels: "PRs with contradictory labels",
    }[kind]


def long_description(kind: Dashboard) -> str:
    """Explain what each dashboard contains: full description, for the purposes of a sub-title
    to the full PR table. This description should not be capitalised."""
    notupdated = "which have not been updated in the past"
    return {
        Dashboard.Queue: "all PRs which are ready for review: CI passes, no merge conflict and not blocked on other PRs",
        Dashboard.QueueNewContributor: "all PRs by new contributors which are ready for review",
        Dashboard.QueueEasy: "all PRs labelled 'easy' which are ready for review",
        Dashboard.NeedsMerge: "all PRs which have a merge conflict, but otherwise fit the review queue",
        Dashboard.StaleDelegated: f"all PRs labelled 'delegated' {notupdated} 24 hours",
        Dashboard.StaleReadyToMerge: f"all PRs labelled 'auto-merge-after-CI' or 'ready-to-merge' {notupdated} 24 hours",
        Dashboard.TechDebt: "all 'ready' PRs (not draft, not labelled WIP) labelled with 'tech debt' or 'longest-pole'",
        Dashboard.NeedsDecision: "all PRs labelled 'awaiting-zulip': these are blocked on a zulip discussion or similar",
        Dashboard.StaleMaintainerMerge: f"all PRs labelled 'maintainer-merge' but not 'ready-to-merge' {notupdated} 24 hours",
        Dashboard.NeedsHelp: "all PRs which are labelled 'please-adopt' or 'help-wanted'",
        Dashboard.OtherBase: "all non-draft PRs, not labelled WIP, into some branch other than mathlib's master branch",
        Dashboard.FromFork: "all non-draft PRs, not labelled WIP, opened from a fork of mathlib",
        Dashboard.StaleNewContributor: f"all PR labelled 'new-contributor' {notupdated} 7 days",
        Dashboard.Unlabelled: "all PRs without draft status or 'WIP' label without a 'CI' or 't-something' label",
        Dashboard.BadTitle: "all PRs without draft status or 'WIP' label whose title does not start with an abbreviation like 'feat', 'style' or 'perf'",
        Dashboard.ContradictoryLabels: "PRs whose labels are contradictory, such as 'WIP' and 'ready-to-merge'",
    }[kind]


def getIdTitle(kind: Dashboard) -> Tuple[str, str]:
    """Return a tuple (id, title) of the HTML anchor ID and a section name for the table
    describing this PR kind."""
    return {
        Dashboard.Queue: ("queue", "Review queue"),
        Dashboard.QueueNewContributor: (
            "queue-new-contributors",
            "New contributors' PRs on the queue",
        ),
        Dashboard.QueueEasy: ("queue-easy", "PRs on the review queue labelled 'easy'"),
        Dashboard.StaleDelegated: ("stale-delegated", "Stale delegated PRs"),
        Dashboard.StaleNewContributor: (
            "stale-new-contributor",
            "Stale new contributor PRs",
        ),
        Dashboard.StaleMaintainerMerge: (
            "stale-maintainer-merge",
            "Stale maintainer-merge'd PRs",
        ),
        Dashboard.StaleReadyToMerge: (
            "stale-ready-to-merge",
            "Stale ready-to-merge'd PRs",
        ),
        Dashboard.TechDebt: ("tech-debt", "PRs addressing technical debt"),
        Dashboard.NeedsDecision: (
            "needs-decision",
            "PRs blocked on a zulip discussion",
        ),
        Dashboard.NeedsMerge: ("needs-merge", "PRs with just a merge conflict"),
        Dashboard.NeedsHelp: ("needs-owner", "PRs looking for help"),
        Dashboard.OtherBase: ("other-base", "PRs not into the master branch"),
        Dashboard.FromFork: ("from-fork", "PRs from a fork of mathlib"),
        Dashboard.Unlabelled: ("unlabelled", "PRs without an area label"),
        Dashboard.BadTitle: ("bad-title", "PRs with non-conforming titles"),
        Dashboard.ContradictoryLabels: (
            "contradictory-labels",
            "PRs with contradictory labels",
        ),
    }[kind]


# The information we need about each PR label: its name, background colour and URL
class Label(NamedTuple):
    name: str
    """This label's background colour, as a six-digit hexadecimal code"""
    color: str
    url: str


# Basic information about a PR: does not contain the diff size, which is contained in pr_info.json instead.
class BasicPRInformation(NamedTuple):
    number: int  # PR number, non-negative
    author: dict
    title: str
    url: str
    labels: List[Label]
    # Github's answer to "last updated at"
    updatedAt: str


# All information about a single PR contained in `aggregate_pr_info.json`.
# Keep this in sync with the actual file, extending this once new data is added!
class AggregatePRInfo(NamedTuple):
    is_draft: bool
    # "pass", "fail" or "running"
    CI_status: str
    # The branch this PR is opened against: should be 'master' (for most PRs)
    base_branch: str
    # The repository this PR was opened from: should be 'leanprover-community',
    # otherwise it is a PR from a fork.
    head_repo: str
    # 'open' for open PRs, 'closed' for closed PRs
    state: str
    # Github's time when the PR was "last updated"
    last_updated: datetime
    # The PR author's github handle
    author: str
    title: str
    # All labels assigned to this PR.
    labels: List[Label]
    additions: int
    deletions: int
    number_modified_files: int
    # This field is *not* present if there is only "basic" information about this PR
    number_total_comments: int | None
    # The github handles of all users (if any) assigned to this PR
    assignees: List[str]

# Missing aggregate information will be replaced by this default item.
PLACEHOLDER_AGGREGATE_INFO = AggregatePRInfo(
    False, "fail", "master", "leanprover-community", "open", datetime.now(),
    "unknown", "unknown title", [], -1, -1, -1, None, []
)

# Information passed to this script, via various JSON files.
class JSONInputData(NamedTuple):
    # All aggregate information stored for every open PR.
    aggregate_info: dict[int, AggregatePRInfo]
    # Information about all open PRs
    all_open_prs: List[BasicPRInformation]


# Validate the command-line arguments and try to read all data passed in via JSON files.
# Any number of JSON files passed in is fine; we interpret them all as containing open PRs.
def read_json_files() -> JSONInputData:
    all_open_prs = []
    for i in range(1, len(sys.argv)):
        with open(sys.argv[i]) as prfile:
            open_prs = _extract_prs(json.load(prfile))
            all_open_prs.extend(open_prs)
    with open(path.join("..", "processed_data", "aggregate_pr_data.json"), "r") as f:
        data = json.load(f)
        label_colours = data["label_colours"]
        def toLabel(name: str) -> Label:
            url = f"https://github.com/leanprover-community/mathlib4/labels/{name}"
            if name.startswith("t-"):
                name = "t-analysis"
            elif name.startswith("blocked-by"):
                name = "blocked-by-other-PR"
            return Label(name, label_colours[name], url)
        aggregate_info = dict()
        for pr in data["pr_statusses"]:
            date = parse_datetime(pr["last_updated"])
            label_names = pr["label_names"]
            if "number_comments" in pr:
                number_all_comments = pr["number_comments"] + pr["number_review_comments"]
            else:
                number_all_comments = None
            info = AggregatePRInfo(
                pr["is_draft"], pr["CI_status"], pr["base_branch"], pr["head_repo"]["login"],
                pr["state"], date, pr["author"], pr["title"], [toLabel(name) for name in label_names],
                pr["additions"], pr["deletions"], pr["num_files"], number_all_comments, pr["assignees"]
            )
            aggregate_info[pr["number"]] = info
    return JSONInputData(aggregate_info, all_open_prs)


EXPLANATION = """
<p>To appear on the review queue, your open pull request must...</p>
<ul>
<li>be opened from the mathlib repository itself (not from a fork),</li>
<li>be based on the <em>master</em> branch of mathlib,</li>
<li>pass mathlib's CI,</li>
<li>not be blocked by another PR (as marked by the labels <em>blocked-by-other-PR</em> and similar)</li>
<li>have no merge conflict (as marked by the <em>merge-conflict</em>),</li>
<li>not be in draft status, nor labelled with one of <em>WIP</em>, <em>help-wanted</em> or <em>please-adopt</em>: these mean the PR is not fully ready yet;</li>
<li>not be labelled <em>awaiting-CI</em>, <em>awaiting-author</em> or <em>awaiting-zulip</em>,</li>
<li>not be labelled <em>delegated</em>, <em>auto-merge-after-CI</em> or <em>ready-to-merge</em>: these labels mean your PR is already approved.</li>
</ul>
<p>
The table below contains all open PRs against the <em>master</em> branch which are not in draft mode. For each PR, it shows whether the checks above are satisfied.
You can filter that list by entering terms into the search box, such as the PR number or your github username.</p>""".lstrip()


# Determine HTML code for writing a table header with entries 'entries'.
# base_indent is the indentation of the <table> tag; we add two additional space per additional level.
def _write_table_header(entries: List[str], base_indent: str) -> str:
    indent = base_indent + "  "
    body = f"\n{indent}".join([f"<th>{entry}</th>" for entry in entries])
    return f"{base_indent}<thead>\n{base_indent}<tr>\n{base_indent}{body}\n{base_indent}</tr>\n{base_indent}</thead>\n"


# Determine HTML code for writing a single table row with entries 'entries' and indentation 'indent'.
# four spaces for the table itself are hard-coded, for now
def _write_table_row(entries: List[str], base_indent: str) -> str:
    indent = base_indent + "  "
    body = f"\n{indent}".join([f"<td>{entry}</td>" for entry in entries])
    return f"{base_indent}<tr>\n{indent}{body}\n{base_indent}</tr>\n"


def _write_labels(labels: List[Label]) -> str:
    if len(labels) == 0:
        return ""
    elif len(labels) == 1:
        return label_link(labels[0])
    else:
        label_part = "\n        ".join(label_link(label) for label in labels)
        return f"\n        {label_part}\n      "


# Write a webpage with body out a file called 'outfile*.
def write_webpage(body: str, outfile: str) -> None:
    with open(outfile, "w") as fi:
        # FIXME: should the footer be adjusted?
        print(f"{HTML_HEADER}\n{body}\n{HTML_FOOTER}", file=fi)


# Print a webpage "why is my PR not on the queue" to a new file of name 'outfile'.
# 'prs' is the list of PRs on which to print information;
# 'prs_from_fork' is the list of such PRs which are opened from a fork of mathlib,
# 'CI_status' states, for each PR, whether PR passes, fails or is still running.
# If no detailed information was available for a given value, 'None' is returned.
# 'base_branch' returns the pase branch of each PR.
def print_on_the_queue_page(
    prs: List[BasicPRInformation], prs_from_fork: List[BasicPRInformation], CI_status: dict[int, str | None], base_branch: dict[int, str], outfile: str
) -> None:
    def icon(state: bool | None) -> str:
        """Return a green checkmark emoji if `state` is true, and a red cross emoji otherwise."""
        return "&#9989;" if state else "&#10060;"

    body = ""
    for pr in prs:
        if base_branch[pr.number] != "master":
            continue
        from_fork = pr in prs_from_fork
        ci_status = CI_status[pr.number]
        status_symbol = "???"
        if ci_status == "pass":
            status_symbol = icon(True)
        elif ci_status == "fail":
            status_symbol = icon(False)
        elif ci_status == "running":
            status_symbol = "&#128996;"
        is_blocked = any(lab.name in ["blocked-by-other-PR", "blocked-by-core-PR", "blocked-by-batt-PR", "blocked-by-qq-PR"] for lab in pr.labels)
        has_merge_conflict = "merge-conflict" in [lab.name for lab in pr.labels]
        is_ready = not (any(lab.name in ["WIP", "help-wanted", "please-adopt"] for lab in pr.labels))
        review = not (any(lab.name in ["awaiting-CI", "awaiting-author", "awaiting-zulip"] for lab in pr.labels))
        overall = (ci_status == "pass") and (not is_blocked) and (not has_merge_conflict) and is_ready and review
        if "login" in pr.author:
            author = pr.author
        else:
            # FIXME: fill in the user name from the actual aggregate data, which has this. Then remove the question mark.
            print(f"warning: missing author information for PR {pr.number}, its authors dictionary is {pr.author} --- was this submitted by dependabot?", file=sys.stderr)
            author = { "login": "dependabot(?)", "url": "https://github.com/dependabot"}
        entries = [
            pr_link(pr.number, pr.url), user_link(author), title_link(pr.title, pr.url),
            _write_labels(pr.labels), icon(not from_fork), status_symbol,
            icon(not is_blocked), icon(not has_merge_conflict), icon(is_ready), icon(review), icon(overall)
        ]
        result = _write_table_row(entries, "    ")
        body += result
    headings = [
        "Number", "Author", "Title", "Labels", "not from a fork?", "CI status?",
        '<a title="not labelled with blocked-by-other-PR, blocked-by-batt-PR, blocked-by-core-PR, blocked-by-qq-PR">not blocked?</a>',
        "no merge conflict?",
        '<a title="not in draft state or labelled as in progress">ready?</a>',
        '<a title="not labelled awaiting-author, awaiting-zulip, awaiting-CI">awaiting review?</a>',
        "On the review queue?",
    ]
    head = _write_table_header(headings, "    ")
    table = f"  <table>\n{head}{body}  </table>"
    # FUTURE: can this time be displayed in the local time zone of the user viewing this page?
    updated = datetime.now(timezone.utc).strftime("%B %d, %Y at %H:%M UTC")
    start = ("  <h1>Why is my PR not on the queue?</h1>\n"
      f"  <small>This page was last updated on: {updated}</small>")
    write_webpage(f"{start}\n{EXPLANATION}\n{table}", outfile)


def main() -> None:
    input_data = read_json_files()
    # Populate basic information from the input data: splitting into draft and non-draft PRs
    # (mostly, we only use the latter); extract separate dictionaries for CI status and base branch.

    # NB. We handle missing metadata by adding "default" values for its aggregate data
    # (ready for review, open, against master, failing CI and just updated now).
    aggregate_info = input_data.aggregate_info.copy()
    for pr in input_data.all_open_prs:
        if pr.number not in input_data.aggregate_info:
            print(f"warning: found no aggregate information for PR {pr.number}; filling in defaults", file=sys.stderr)
            aggregate_info[pr.number] = PLACEHOLDER_AGGREGATE_INFO
    draft_PRs = [pr for pr in input_data.all_open_prs if aggregate_info[pr.number].is_draft]
    nondraft_PRs = [pr for pr in input_data.all_open_prs if not aggregate_info[pr.number].is_draft]
    draft_prs2 = None
    with open("all-open-PRs-2.json", "r") as draftfile:
        draft_prs2 = _extract_prs(json.load(draftfile))
        draft_numbers2 = [pr.number for pr in draft_prs2]
    msg = "this page's list of draft PRs (left) with the Github API query for draft PRs (right)"
    if my_assert_eq(msg, [pr.number for pr in draft_PRs], draft_numbers2):
        print("Aggregate draft PRs and Github REST API's draft PRs match, hooray!", file=sys.stderr)
    nondraft_prs2 = None
    with open("all-open-PRs-1.json", "r") as nondraftfile:
        nondraft_prs2 = _extract_prs(json.load(nondraftfile))
        nondraft_numbers2 = [pr.number for pr in nondraft_prs2]
    msg = "this page's list of non-draft PRs (left) with the Github API query for non-draft PRs (right)"
    if my_assert_eq(msg, [pr.number for pr in nondraft_PRs], nondraft_numbers2):
        print("Aggregate non-draft PRs and Github REST API's non-draft PRs match, hooray!", file=sys.stderr)
    assert len(draft_PRs) + len(nondraft_PRs) == len(input_data.all_open_prs)

    # The only exception is for the "on the queue" page,
    # which points out missing information explicitly, hence is passed the non-filled in data.
    CI_status : dict[int, str | None] = dict()
    for pr in nondraft_PRs:
        if pr.number in input_data.aggregate_info:
            CI_status[pr.number] = input_data.aggregate_info[pr.number].CI_status
        else:
            CI_status[pr.number] = None
    base_branch: dict[int, str] = dict()
    for pr in nondraft_PRs:
        base_branch[pr.number] = aggregate_info[pr.number].base_branch
    prs_from_fork = [pr for pr in nondraft_PRs if aggregate_info[pr.number].head_repo != "leanprover-community"]
    print_on_the_queue_page(nondraft_PRs, prs_from_fork, CI_status, base_branch, "on_the_queue.html")

    prs_to_list : dict[Dashboard, List[BasicPRInformation]] = dict()
    # The 'tech debt', 'other base' and 'from fork' boards are obtained
    # from filtering the list of all non-draft PRs (without the WIP label).
    all_ready_prs = prs_without_label(nondraft_PRs, 'WIP')
    prs_to_list[Dashboard.TechDebt] = prs_with_any_label(all_ready_prs, ['tech debt', 'longest-pole'])
    prs_to_list[Dashboard.OtherBase] = [pr for pr in nondraft_PRs if base_branch[pr.number] != 'master']
    prs_to_list[Dashboard.FromFork] = prs_from_fork

    prs_to_list[Dashboard.NeedsHelp] = prs_with_any_label(nondraft_PRs, ['help-wanted', 'please_adopt'])
    prs_to_list[Dashboard.NeedsDecision] = prs_with_label(nondraft_PRs, 'awaiting-zulip')

    # Compute all PRs on the review queue (and well as several sub-filters).
    # The review queue consists of all PRs against the master branch, with passing CI,
    # that are not in draft state and not labelled WIP, help-wanted or please-adopt,
    # and have none of the other labels below.
    master_prs_with_CI = [pr for pr in nondraft_PRs if base_branch[pr.number] == 'master' and (CI_status[pr.number] == "pass")]
    master_CI_notfork = [pr for pr in master_prs_with_CI if pr not in prs_from_fork]
    other_labels = [
        # XXX: does the #queue check for all of these labels?
        "blocked-by-other-PR", "blocked-by-core-PR", "blocked-by-batt-PR", "blocked-by-qq-PR",
        "awaiting-CI", "awaiting-author", "awaiting-zulip", "please-adopt", "help-wanted", "WIP",
        "delegated", "auto-merge-after-CI", "ready-to-merge"]
    queue_or_merge_conflict = prs_without_any_label(master_CI_notfork, other_labels)
    prs_to_list[Dashboard.NeedsMerge] = prs_with_label(queue_or_merge_conflict, "merge-conflict")
    queue_prs = prs_without_label(queue_or_merge_conflict, "merge-conflict")

    queue_prs2 = None
    with open("queue.json", "r") as queuefile:
        queue_prs2 = _extract_prs(json.load(queuefile))
        queue_pr_numbers2 = [pr.number for pr in queue_prs2]
    msg = "comparing this page's review dashboard (left) with the Github #queue (right)"
    if my_assert_eq(msg, [pr.number for pr in queue_prs], queue_pr_numbers2):
        print("Review dashboard and #queue match, hooray!", file=sys.stderr)
    # XXX: When re-visiting the comparison of queues, also re-run this again for a day.
    # For now, it doesn't expose more useful information, hence disabling this.
    # needs_merge2 = None
    # with open("needs-merge.json", "r") as file:
    #     needs_merge_prs2 = _extract_prs(json.load(file))
    #     needs_merge2 = [pr.number for pr in needs_merge_prs2]
    # msg = "comparing this page's 'needs merge' dashboard (left) with the Github REST APi search (right)"
    # if my_assert_eq(msg, [pr.number for pr in prs_to_list[Dashboard.NeedsMerge]], needs_merge2):
    #     print("Needs merge dashboard: list matches the github API, hooray!", file=sys.stderr)

    # TODO: try to switch back to 'queue_prs' again, once the root causes for PR getting 'dropped'
    # by 'gather_stats.sh' are all identified and fixed.
    prs_to_list[Dashboard.Queue] = queue_prs2
    prs_to_list[Dashboard.QueueNewContributor] = prs_with_label(queue_prs, 'new-contributor')
    prs_to_list[Dashboard.QueueEasy] = prs_with_label(queue_prs, 'easy')

    a_day_ago = datetime.now() - timedelta(days=1)
    a_week_ago = datetime.now() - timedelta(days=7)
    one_day_stale = [pr for pr in nondraft_PRs if aggregate_info[pr.number].last_updated < a_day_ago]
    one_week_stale = [pr for pr in nondraft_PRs if aggregate_info[pr.number].last_updated < a_week_ago]
    prs_to_list[Dashboard.StaleReadyToMerge] = prs_with_any_label(one_day_stale, ['ready-to-merge', 'auto-merge-after-CI'])
    prs_to_list[Dashboard.StaleDelegated] = prs_with_label(one_day_stale, 'delegated')
    mm_prs = prs_with_label(one_day_stale, 'maintainer-merge')
    prs_to_list[Dashboard.StaleMaintainerMerge] = prs_without_label(mm_prs, 'ready-to-merge')
    prs_to_list[Dashboard.StaleNewContributor] = prs_with_label(one_week_stale, 'new-contributor')

    (bad_title, unlabelled, contradictory) = compute_dashboards_bad_labels_title(nondraft_PRs)
    prs_to_list[Dashboard.BadTitle] = bad_title
    prs_to_list[Dashboard.Unlabelled] = unlabelled
    prs_to_list[Dashboard.ContradictoryLabels] = contradictory
    # FUTURE: can this time be displayed in the local time zone of  the user viewing this page?
    updated = datetime.now(timezone.utc).strftime("%B %d, %Y at %H:%M UTC")
    write_main_page(aggregate_info, prs_to_list, nondraft_PRs, draft_PRs, updated)


# Write the main page for the dashboard to the file index.html.
def write_main_page(
    aggregate_info: dict[int, AggregatePRInfo], prs_to_list: dict[Dashboard, List[BasicPRInformation]],
    nondraft_PRs: list[BasicPRInformation], draft_PRs: list[BasicPRInformation], updated: str
) -> None:
    title = "  <h1>Mathlib review and triage dashboard</h1>"
    welcome = '<p>Welcome to the mathlib review and triage dashboard. This is a prototype for better exposing the currently open PRs to mathlib. Feedback (including bug reports and ideas for improvements) on this dashboard is very welcome, for instance <a href="https://github.com/jcommelin/queueboard">directly on the github repository</a>.<br>'
    "You can hover over any section header (and some table headings) to find out what they show. The same works for the table of contents below.</p>"
    body = f"{title}\n  {welcome}  <small>This dashboard was last updated on: {updated}</small>\n"

    # Print a quick table of contents.
    links = []
    for kind in Dashboard._member_map_.values():
        (id, _title) = getIdTitle(kind)
        links.append(f"<a href=\"#{id}\" title=\"{short_description(kind)}\" target=\"_self\">{id}</a>")
    body += f"<br><p>\n<b>Quick links:</b> <a href=\"#statistics\" target=\"_self\">PR statistics</a> | {str.join(' | ', links)}</p>\n"

    body += gather_pr_statistics(aggregate_info, prs_to_list, nondraft_PRs, draft_PRs)
    for kind in Dashboard._member_map_.values():
        if kind not in prs_to_list:
            print(f"error: forgot to include data for dashboard kind {kind}", file=sys.stderr)
        else:
            body += f"{write_dashboard(prs_to_list[kind], kind)}\n"
    write_webpage(body, "index.html")


# Compute the status of each PR in a given list. Return a dictionary keyed by the PR number.
# (`BasicPRInformation` is not hashable, hence cannot be used as a dictionary key.)
# 'aggregate_info' contains aggregate information about each PR's CI state,
# draft status and base branch (and more, which we do not use).
# If no detailed information was available for a given PR number, 'None' is returned.
def compute_pr_statusses(aggregate_info: dict[int, AggregatePRInfo], prs: List[BasicPRInformation]) -> dict[int, PRStatus]:
    def determine_status(aggregate_info: AggregatePRInfo, info: BasicPRInformation) -> PRStatus:
        # Ignore all "other" labels, which are not relevant for this anyway.
        labels = [label_categorisation_rules[lab.name] for lab in info.labels if lab.name in label_categorisation_rules]
        translate = { "pass": CIStatus.Pass, "fail": CIStatus.Fail, "running": CIStatus.Running }
        ci_status = translate[aggregate_info.CI_status]
        state = PRState(labels, ci_status, aggregate_info.is_draft)
        return determine_PR_status(datetime.now(), state)
    return {info.number: determine_status(aggregate_info[info.number] or PLACEHOLDER_AGGREGATE_INFO, info) for info in prs}


# If aggregate information about a PR is missing, we treat it as non-draft, failing CI and against 'master'.
# (Though, in fact, we assume that 'aggregate_info' is complete, by prior normalisation.)
def gather_pr_statistics(
    aggregate_info: dict[int, AggregatePRInfo],
    prs: dict[Dashboard, List[BasicPRInformation]],
    all_ready_prs: List[BasicPRInformation],
    all_draft_prs: List[BasicPRInformation],
) -> str:
    # We only need to check the status of all non-draft PRs: this is purely an optimisation.
    ready_pr_status = compute_pr_statusses(aggregate_info, all_ready_prs)
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
    number_prs : Dict[PRStatus, int] = {
        status : len([number for number in ready_pr_status if ready_pr_status[number] == status]) for status in statusses
    }
    number_prs[PRStatus.NotReady] += len(all_draft_prs)
    # Check that we did not miss any variant above
    for status in PRStatus._member_map_.values():
        assert status == PRStatus.Closed or status in number_prs.keys()

    # For some kinds, we have this data already: the review queue and the "not merged" kinds come to mind.
    # Let us compare with the classification logic.
    queue_prs_numbers = [pr for pr in ready_pr_status if ready_pr_status[pr] == PRStatus.AwaitingReview]
    msg = "the review queue (left) with all PRs classified as awaiting review (right)"
    if my_assert_eq(msg, queue_prs_numbers, [i.number for i in queue_prs]):
        print("review queue: two computations match, hooray", file=sys.stderr)
        # print(f"warning: the review queue and the classification differ: found {len(right)} PRs {right} on the former, but the {len(queue_prs_numbers)} PRs {queue_prs_numbers} on the latter!", file=sys.stderr)
    # TODO: also cross-check the data for merge conflicts

    number_all = len(all_ready_prs) + len(all_draft_prs)

    def link_to(kind: Dashboard, name="these ones") -> str:
        return f'<a href="#{getIdTitle(kind)[0]}" target="_self">{name}</a>'

    def number_percent(n: int, total: int, color: str = "") -> str:
        if color:
            return f'{n} (<span style="color: {color};">{n/total:.1%}</span>)'
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
    if cumulative[-1] != number_all:
        dict = '{' + ', '.join([f"{s}: {number_prs[s]}" for s in statusses]) + '}'
        msg = f'''error: statistics calculation is inconsistent, all PR kinds do not add up!
            cumulative sum of PRs is {cumulative[-1]}, while there are {number_all} PRs in total
            detailed stats dictionary is {dict}'''
        print(msg, file=sys.stderr)
        # TODO: compare these lists of PRs in detail, to verify if this exposes anything other than outdated data!
        # assert False

    return f'\n<h2 id="statistics"><a href="#statistics">Overall statistics</a></h2>\nFound <b>{number_all}</b> open PRs overall. Among these PRs\n<ul>\n{details}\n</ul><div class="piechart" style="{piechart_style}"></div>\n'


HTML_HEADER = """
<!DOCTYPE html>
<html>
<head>
<meta name="referrer" content="no-referrer">
<meta http-equiv="Content-Security-Policy" content="default-src 'self'; script-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com https://cdn.datatables.net; style-src 'self' 'unsafe-inline' https://cdn.datatables.net; form-action 'none'; base-uri 'none'">
<title>Mathlib review and triage dashboard</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/jquery/3.5.1/jquery.min.js"
    integrity="sha512-bLT0Qm9VnAYZDflyKcBaQ2gg0hSYNQrJ8RilYldYQ1FxQYoCLtUjuuRuZo+fjqhx/qtq/1itJ0C2ejDxltZVFg=="
	crossorigin="anonymous"></script>
<link rel="stylesheet" href="https://cdn.datatables.net/2.1.8/css/dataTables.dataTables.css"
    integrity="sha384-eCorNQ6xLKDT9aok8iCYVVP8S813O3kaugZFLBt1YhfR80d1ZgkNcf2ghiTRzRno" crossorigin="anonymous">
<script src="https://cdn.datatables.net/2.1.8/js/dataTables.js"
    integrity="sha384-cDXquhvkdBprgcpTQsrhfhxXRN4wfwmWauQ3wR5ZTyYtGrET2jd68wvJ1LlDqlQG" crossorigin="anonymous"></script>
<link rel='stylesheet' href='style.css'>
<base target="_blank">
</head>
<body>
""".strip()


HTML_FOOTER = """
<script>
  let diff_stat = DataTable.type('diff_stat', {
    detect: function (data) { return false; },
    order: {
      pre: function (data) {
        let parts = data.split('/', 2);
        return Number(parts[0]) + Number(parts[1]);
      }
    },
  });
$(document).ready( function () {
  $('table').DataTable({
    pageLength: 10,
    "searching": true,
    columnDefs: [{ type: 'diff_stat', targets: 4}],
  });
});
</script>
</body>
</html>
""".strip()


# An HTML link to a mathlib PR from the PR number
def pr_link(number: int, url: str) -> str:
    # The PR number is intentionally not prefixed with a #, so it is correctly
    # recognised and sorted as a number (with HTML formatting, a `html-num` type),
    # and not sorted as a string.
    return f"<a href='{url}'>{number}</a>"


# An HTML link to a GitHub user profile
def user_link(author: dict) -> str:
    login = author["login"]
    url = author["url"]
    return f"<a href='{url}'>{login}</a>"


# An HTML link to a mathlib PR from the PR title
def title_link(title: str, url: str) -> str:
    return f"<a href='{url}'>{title}</a>"


# An HTML link to a Github label in the mathlib repo
def label_link(label: Label) -> str:
    # Function to check if the colour of the label is light or dark
    # adapted from https://codepen.io/WebSeed/pen/pvgqEq
    # r, g and b are integers between 0 and 255.
    def isLight(r: int, g: int, b: int) -> bool:
        # Counting the perceptive luminance
        # human eye favors green color...
        a = 1 - (0.299 * r + 0.587 * g + 0.114 * b) / 255
        return a < 0.5

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


# Compute the table entries about a sequence of PRs.
def _compute_pr_entries(prs: List[BasicPRInformation]) -> str:
    result = ""
    for pr in prs:
        entries = [pr_link(pr.number, pr.url), user_link(pr.author), title_link(pr.title, pr.url), _write_labels(pr.labels)]
        # Detailed information about the current PR.
        pr_info = None
        filename = f"data/{pr.number}/pr_info.json"
        if path.exists(filename):
            match parse_json_file(filename, str(pr.number)):
                case dict(data):
                    pr_info = data
                case str(_error):
                    pass
        if pr_info is None:
            print(f"main dashboard: found no aggregate information for PR {pr.number}", file=sys.stderr)
            entries.extend(["-1/-1", "-1", "-1"])
        else:
            # We treat non-well-formed data as missing.
            # FUTURE: unify and centralise all these checks for bad/missing data (also for CI status)
            if "data" not in pr_info or "errors" in pr_info:
                entries.extend(["-1/-1", "-1", "-1"])
                continue
            inner = pr_info["data"]["repository"]["pullRequest"]
            entries.extend([
                "{}/{}".format(inner["additions"], inner["deletions"]), inner["changedFiles"]
            ])
            # Add the number of normal and review comments.
            number_comments = len(inner["comments"]["nodes"])
            number_review_comments = 0
            review_threads = inner["reviewThreads"]["nodes"]
            for t in review_threads:
                number_review_comments += len(t["comments"]["nodes"])
            entries.append(f"{number_comments + number_review_comments}")
        entries.append(time_info(pr.updatedAt))
        result += _write_table_row(entries, "    ")
    return result


# Write the code for a dashboard of a given list of PRs.
def write_dashboard(prs : List[BasicPRInformation], kind: Dashboard) -> str:
    # Title of each list, and the corresponding HTML anchor.
    # Explain what each dashboard contains upon hovering the heading.
    (id, title) = getIdTitle(kind)
    title = f"<h2 id=\"{id}\"><a href=\"#{id}\" title=\"{long_description(kind)}\">{title}</a></h2>"
    # If there are no PRs, skip the table header and print a bold notice such as
    # "There are currently **no** stale `delegated` PRs. Congratulations!".
    if not prs:
        return f'{title}\nThere are currently <b>no</b> {short_description(kind)}. Congratulations!\n'

    headings = [
        "Number", "Author", "Title", "Labels",
        '<a title="number of added/deleted lines">+/-</a>',
        '<a title="number of files modified">&#128221;</a>',
        '<a title="number of standard or review comments on this PR">&#128172;</a>',
        'Updated'
    ]
    head = _write_table_header(headings, "    ")
    body = _compute_pr_entries(prs)
    return f"{title}\n  <table>\n{head}{body}  </table>"


# Does a PR have a given label?
def _has_label(pr: BasicPRInformation, name: str) -> bool:
    return name in [label.name for label in pr.labels]

# Extract all PRs from a given list which have a certain label.
def prs_with_label(prs: List[BasicPRInformation], label_name: str) -> List[BasicPRInformation]:
    return [prinfo for prinfo in prs if _has_label(prinfo, label_name)]

# Extract all PRs from a given list which have any label in a certain list.
def prs_with_any_label(prs: List[BasicPRInformation], label_names: List[str]) -> List[BasicPRInformation]:
    return [prinfo for prinfo in prs if any([_has_label(prinfo, name) for name in label_names])]

# Extract all PRs from a given list which do not have a certain label.
def prs_without_label(prs: List[BasicPRInformation], label_name: str) -> List[BasicPRInformation]:
    return [prinfo for prinfo in prs if not _has_label(prinfo, label_name)]

# Extract all PRs from a given list which do not have any label among a given list.
def prs_without_any_label(prs: List[BasicPRInformation], label_names: List[str]) -> List[BasicPRInformation]:
    return [prinfo for prinfo in prs if all([not _has_label(prinfo, name) for name in label_names])]

def has_contradictory_labels(pr: BasicPRInformation) -> bool:
    # Combine common labels.
    canonicalise = {
        "ready-to-merge": "bors", "auto-merge-after-CI": "bors",
        "blocked-by-other-PR": "blocked", "blocked-by-core-PR": "blocked", "blocked-by-batt-PR": "blocked", "blocked-by-qq-PR": "blocked",
    }
    normalised_labels = [(canonicalise[label.name] if label.name in canonicalise else label.name) for label in pr.labels]
    # Test for contradictory label combinations.
    if 'awaiting-review-DONT-USE' in normalised_labels:
        return True
    # Waiting for a decision contradicts most other labels.
    elif "awaiting-zulip" in normalised_labels and any(
            [lab for lab in normalised_labels if lab in ["awaiting-author", "delegated", "bors", "WIP"]]):
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
        topic_labels = [label for label in pr.labels if label.name in ['CI', 'IMO'] or label.name.startswith("t-")]
        return len(topic_labels) >= 1
    prs_without_topic_label = [pr for pr in nonwip_prs if pr.title.startswith("feat") and not has_topic_label(pr)]
    prs_with_contradictory_labels = [pr for pr in nonwip_prs if has_contradictory_labels(pr)]
    return (with_bad_title, prs_without_topic_label, prs_with_contradictory_labels)

main()
