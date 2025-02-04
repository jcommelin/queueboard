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

from dateutil import parser, relativedelta

from classify_pr_state import (CIStatus, PRState, PRStatus,
                               determine_PR_status, label_categorisation_rules)
from state_evolution import first_time_on_queue, last_real_update, total_queue_time
from util import my_assert_eq, format_delta, timedelta_tryParse, relativedelta_tryParse


@unique
class Dashboard(Enum):
    """The different kind of dashboards on the created triage webpage"""

    # Note: the tables on the generated page are listed in the order of these variants.
    Queue = 0
    QueueNewContributor = auto()
    QueueEasy = auto()
    # All PRs on the queue which are unassigned and have not been updated in the past two weeks.
    # We use the real last update, not github's date.
    QueueStaleUnassigned = auto()
    # All assigned PRs on the review queue without any update in the past two weeks.
    # TODO: use a more refined measure of activity, such as "no comment/review comment by anybody but the author"
    QueueStaleAssigned = auto()
    # All PRs labelled "tech-debt" or "longest-pole"
    QueueTechDebt = auto()
    # All PRs labelled ready-to-merge or auto-merge-after-CI, not just the stale ones
    AllReadyToMerge = auto()
    StaleReadyToMerge = auto()
    StaleDelegated = auto()
    StaleMaintainerMerge = auto()
    # All PRs labelled maintainer-merge, not the stale ones.
    AllMaintainerMerge = auto()
    # All ready PRs (not draft, not labelled WIP) labelled with "tech debt" or "longest-pole".
    TechDebt = auto()
    # This PR is blocked on a zulip discussion or similar.
    NeedsDecision = auto()
    # PRs passes, but just has a merge conflict: same labels as for review, except we do require a merge conflict
    NeedsMerge = auto()
    # PR would be ready for review, except for a failure of some infrastructure-related CI job:
    # unless this CI modifies some mathlib infrastructure, this is not this PRs fault.
    InessentialCIFails = auto()
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
    # PRs with at least one "approved" review by a community member.
    Approved = auto()
    # Every open PR in mathlib.
    All = auto()


def short_description(kind: Dashboard) -> str:
    """Describe what the table 'kind' contains, for use in a "there are no such PRs" message."""
    return {
        Dashboard.Queue: "PRs on the review queue",
        Dashboard.QueueNewContributor: "PRs by new mathlib contributors on the review queue",
        Dashboard.QueueEasy: "PRs on the review queue which are labelled 'easy'",
        Dashboard.QueueTechDebt: "PRs on the review queue which are labelled 'tech debt' or 'longest-pole",
        Dashboard.QueueStaleAssigned: "assigned PRs on the review queue without activity in the past two weeks",
        Dashboard.QueueStaleUnassigned: "unassigned PRs on the review queue with no meaningful activity in the past two weeks",
        Dashboard.StaleMaintainerMerge: "stale PRs labelled maintainer merge",
        Dashboard.AllMaintainerMerge: "PRs labelled maintainer merge",
        Dashboard.StaleDelegated: "stale delegated PRs",
        Dashboard.AllReadyToMerge: "all PRs labelled auto-merge-after-CI or ready-to-merge",
        Dashboard.StaleReadyToMerge: "stale PRs labelled auto-merge-after-CI or ready-to-merge",
        Dashboard.TechDebt: "ready PRs labelled with 'tech debt' or 'longest-pole'",
        Dashboard.NeedsDecision: "PRs blocked on a zulip discussion or similar",
        Dashboard.NeedsMerge: "PRs which just have a merge conflict",
        Dashboard.InessentialCIFails: "PRs with just an infrastructure-related CI failure",
        Dashboard.StaleNewContributor: "stale PRs by new contributors",
        Dashboard.NeedsHelp: "PRs which are looking for a help",
        Dashboard.OtherBase: "ready PRs into a non-master branch",
        Dashboard.FromFork: "ready PRs opened from a fork of mathlib",
        Dashboard.Unlabelled: "ready PRs without a 'CI' or 't-something' label",
        Dashboard.BadTitle: "ready PRs whose title does not start with an abbreviation like 'feat', 'style' or 'perf'",
        Dashboard.ContradictoryLabels: "PRs with contradictory labels",
        Dashboard.Approved: "PRs that have an 'approved' review",
        Dashboard.All: "open PRs",
    }[kind]


def long_description(kind: Dashboard) -> str:
    """Explain what each dashboard contains: full description, for the purposes of a sub-title
    to the full PR table. This description should not be capitalised."""
    notupdated = "which have not been updated in the past"
    return {
        Dashboard.Queue: "all PRs which are ready for review: CI passes, no merge conflict and not blocked on other PRs",
        Dashboard.QueueNewContributor: "all PRs by new contributors which are ready for review",
        Dashboard.QueueEasy: "all PRs labelled 'easy' which are ready for review",
        Dashboard.QueueTechDebt: "all PRs labelled with 'tech debt' or 'longest-pole' which are ready for review",
        Dashboard.QueueStaleAssigned: "all assigned PRs on the review queue which have not been updated at all in the past two weeks",
        Dashboard.QueueStaleUnassigned: "all PRs on the review queue which are unassigned and have not seen status changes in the past two weeks",
        Dashboard.NeedsMerge: "all PRs which have a merge conflict, but otherwise fit the review queue",
        Dashboard.InessentialCIFails: "all PRs with just a failure of some infrastructure-related CI job (usually not this PR's fault), but are otherwise ready for review",
        Dashboard.StaleDelegated: f"all PRs labelled 'delegated' {notupdated} 24 hours",
        Dashboard.AllReadyToMerge: "all PRs labelled 'auto-merge-after-CI' or 'ready-to-merge'",
        Dashboard.StaleReadyToMerge: f"all PRs labelled 'auto-merge-after-CI' or 'ready-to-merge' {notupdated} 24 hours",
        Dashboard.TechDebt: "all 'ready' PRs (not draft, not labelled WIP) labelled with 'tech debt' or 'longest-pole'",
        Dashboard.NeedsDecision: "all PRs labelled 'awaiting-zulip': these are blocked on a zulip discussion or similar",
        Dashboard.StaleMaintainerMerge: f"all PRs labelled 'maintainer-merge' but not 'ready-to-merge' {notupdated} 24 hours",
        Dashboard.AllMaintainerMerge: "all PRs labelled maintainer merge but not 'ready-to-merge'",
        Dashboard.NeedsHelp: "all PRs which are labelled 'please-adopt' or 'help-wanted'",
        Dashboard.OtherBase: "all non-draft PRs, not labelled WIP, into some branch other than mathlib's master branch",
        Dashboard.FromFork: "all non-draft PRs, not labelled WIP, opened from a fork of mathlib",
        Dashboard.StaleNewContributor: f"all PR labelled 'new-contributor' {notupdated} 7 days",
        Dashboard.Unlabelled: "all PRs without draft status or 'WIP' label without a 'CI' or 't-something' label",
        Dashboard.BadTitle: "all PRs without draft status or 'WIP' label whose title does not start with an abbreviation like 'feat', 'style' or 'perf'",
        Dashboard.ContradictoryLabels: "PRs whose labels are contradictory, such as 'WIP' and 'ready-to-merge'",
        Dashboard.Approved: "PRs that have at least one 'approved' review by a community member",
        Dashboard.All: "all open PRs",
    }[kind]


def getIdTitle(kind: Dashboard) -> Tuple[str, str]:
    """Return a tuple (id, title) of the HTML anchor ID and a section name for the table
    describing this PR kind."""
    return {
        Dashboard.Queue: ("queue", "Review queue"),
        Dashboard.QueueNewContributor: (
            "queue-new-contributors",
            "New contributors' PRs on the review queue",
        ),
        Dashboard.QueueEasy: ("queue-easy", "PRs on the review queue labelled 'easy'"),
        Dashboard.QueueTechDebt: ("queue-tech-debt", "PRs on the review queue labelled 'tech debt' or 'longest-pole'"),
        Dashboard.QueueStaleAssigned: ("queue-stale-unassigned", "Stale assigned PRs on the review queue"),
        Dashboard.QueueStaleUnassigned: ("queue-stale-unassigned", "Stale unassigned PRs on the review queue"),
        Dashboard.StaleDelegated: ("stale-delegated", "Stale delegated PRs"),
        Dashboard.StaleNewContributor: (
            "stale-new-contributor",
            "Stale new contributor PRs",
        ),
        Dashboard.StaleMaintainerMerge: (
            "stale-maintainer-merge",
            "Stale maintainer-merge'd PRs",
        ),
        Dashboard.AllMaintainerMerge: ("all-maintainer-merge", "All maintainer merge'd PRs"),
        Dashboard.AllReadyToMerge: ("all-ready-to-merge", "All ready-to-merge'd PRs"),
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
        Dashboard.InessentialCIFails: ("inessential-CI-fails", "PRs with just failing CI, but only often-spurious jobs"),
        Dashboard.NeedsHelp: ("needs-owner", "PRs looking for help"),
        Dashboard.OtherBase: ("other-base", "PRs not into the master branch"),
        Dashboard.FromFork: ("from-fork", "PRs from a fork of mathlib"),
        Dashboard.Unlabelled: ("unlabelled", "PRs without an area label"),
        Dashboard.BadTitle: ("bad-title", "PRs with non-conforming titles"),
        Dashboard.ContradictoryLabels: (
            "contradictory-labels",
            "PRs with contradictory labels",
        ),
        Dashboard.Approved: ("approved", "PRs with an 'approved' review"),
        Dashboard.All: ("all", "All open PRs")
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
    # Just the author's github handle; the corresponding URL is determined automatically.
    # For dependabot PRs, this can be `None` due to quirks in the data returned by Github's REST API.
    author_name: str | None
    title: str
    url: str
    labels: List[Label]
    # Github's answer to "last updated at"
    updatedAt: datetime


class DataStatus(Enum):
    Valid = auto()
    Incomplete = auto()
    # This can happen if a PR is stubborn (i.e. no events data is collected)
    # or a PR's data is contradictory (hence ignored).
    Missing = auto()

class LastStatusChange(NamedTuple):
    status: DataStatus
    time: datetime
    delta: relativedelta.relativedelta
    current_status: PRStatus

class TotalQueueTime(NamedTuple):
    status: DataStatus
    value_td: timedelta
    value_rd: relativedelta.relativedelta
    explanation: str

# All information about a single PR contained in `open_pr_info.json`.
# Keep this in sync with the actual file, extending this once new data is added!
class AggregatePRInfo(NamedTuple):
    is_draft: bool
    CI_status: CIStatus
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
    # Github handles of all users (if any) approving this
    approvals: List[str]
    # The github handles of all users (if any) assigned to this PR
    assignees: List[str]
    # This field is *not* present if there is only "basic" information about this PR
    number_total_comments: int | None
    # The following fields are not present when there is only basic information about this PR.
    # They are also missing if a PR's events data is invalid (because github returned bogus results).
    # Otherwise, they include their validity status ("missing", "incomplete", "valid").
    last_status_change: LastStatusChange | None
    first_on_queue: Tuple[DataStatus, datetime | None] | None
    total_queue_time: TotalQueueTime | None

# Missing aggregate information will be replaced by this default item.
PLACEHOLDER_AGGREGATE_INFO = AggregatePRInfo(
    False, CIStatus.Missing, "master", "leanprover-community", "open", datetime.now(timezone.utc),
    "unknown", "unknown title", [], -1, -1, -1, [], [], None, None, None, None,
)

# Information passed to this script, via various JSON files.
class JSONInputData(NamedTuple):
    # All aggregate information stored for every open PR.
    aggregate_info: dict[int, AggregatePRInfo]
    # Information about all open PRs
    all_open_prs: List[BasicPRInformation]


# Parse the contents |data| of an aggregate json file into a dictionary pr number -> AggregatePRInfo.
def parse_aggregate_file(data: dict) -> dict[int, AggregatePRInfo]:
    label_colours = data["label_colours"]

    def toLabel(name: str) -> Label:
        url = f"https://github.com/leanprover-community/mathlib4/labels/{name}"
        if name.startswith("t-"):
            colour = label_colours["t-analysis"]
        elif name.startswith("blocked-by"):
            colour = label_colours["blocked-by-other-PR"]
        else:
            colour = label_colours[name]
        return Label(name, colour, url)

    aggregate_info = dict()
    for pr in data["pr_statusses"]:
        date = parser.isoparse(pr["last_updated"])
        label_names = pr["label_names"]
        # Some PRs only have basic information present.
        if "number_comments" in pr:
            number_all_comments = pr["number_comments"] + pr["number_review_comments"]
            # If status information is invalid, omit it.
            st = pr["last_status_change"]
            if st["status"] == "missing":
                last_status_change = None
            else:
                (status, raw_time, raw_delta, raw_current_status) = st["status"], st["time"], st["delta"], st["current_status"]
                delta = relativedelta_tryParse(raw_delta)
                current_status = PRStatus.tryFrom_str(raw_current_status)
                if delta is None:
                    print(f"error: invalid data, input {raw_delta} for 'delta' field of 'last_status_change' is invalid", file=sys.stderr)
                elif current_status is None:
                    print(f"error: invalid data, input {raw_current_status} for 'current_status' field of 'last_status_change' is invalid", file=sys.stderr)
                last_status_change = LastStatusChange(status, parser.isoparse(raw_time), delta, current_status)

            foq = pr["first_on_queue"]
            if foq["status"] == "missing":
                first_on_queue = None
            else:
                date2 = None if foq["date"] is None else parser.isoparse(foq["date"])
                first_on_queue = (foq["status"], date2)

            tqt = pr["total_queue_time"]
            if tqt["status"] == "missing":
                total_queue_time = None
            else:
                (status, value_td, value_rd, explanation) = (tqt["status"], tqt["value_td"], tqt["value_rd"], tqt["explanation"])
                td = timedelta_tryParse(value_td)
                rd = relativedelta_tryParse(value_rd)
                if rd is None:
                    print(f"error: invalid data, input {rd} for 'value_rd' field of 'total_queue_time' is invalid", file=sys.stderr)
                elif td is None:
                    print(f"error: invalid data, input {td} for 'value_td' field of 'total_queue_time' is invalid", file=sys.stderr)
                total_queue_time = TotalQueueTime(status, td, rd, explanation)
        else:
            number_all_comments = None
            last_status_change = None
            first_on_queue = None
            total_queue_time = None
        info = AggregatePRInfo(
            pr["is_draft"], CIStatus.from_string(pr["CI_status"]), pr["base_branch"], pr["head_repo"]["login"],
            pr["state"], date, pr["author"], pr["title"], [toLabel(name) for name in label_names],
            pr["additions"], pr["deletions"], pr["num_files"], pr["review_approvals"], pr["assignees"],
            number_all_comments, last_status_change, first_on_queue, total_queue_time,
        )
        aggregate_info[pr["number"]] = info
    return aggregate_info


# Validate the command-line arguments and try to read all data passed in via JSON files.
# Any number of JSON files passed in is fine; we interpret them all as containing open PRs.
def read_json_files() -> JSONInputData:
    if len(sys.argv) == 1:
        print("error: need to pass in some JSON files with open PRs")
        sys.exit(1)
    all_open_prs = []
    for i in range(1, len(sys.argv)):
        with open(sys.argv[i]) as prfile:
            open_prs = _extract_prs(json.load(prfile))
            if len(open_prs) >= 900:
                print(f"warning: file {sys.argv[i]} contains at least 900 PRs: the REST API will never return more than 1000 PRs. Please split the list into more files as necessary.", file=sys.stderr)
            all_open_prs.extend(open_prs)
    with open(path.join("processed_data", "open_pr_data.json"), "r") as f:
        aggregate_info = parse_aggregate_file(json.load(f))
    return JSONInputData(aggregate_info, all_open_prs)


# use_aggregate_queue: if True, determine the review queue (and everything depending on it)
# from the aggregate data and not queue.json
def determine_pr_dashboards(
    nondraft_PRs: List[BasicPRInformation],
    base_branch: dict[int, str],
    prs_from_fork: List[BasicPRInformation],
    CI_status: dict[int, CIStatus],
    aggregate_info: dict[int, AggregatePRInfo],
    use_aggregate_queue: bool,
) -> dict[Dashboard, List[BasicPRInformation]]:
    approved = [pr for pr in nondraft_PRs if aggregate_info[pr.number].approvals]
    prs_to_list: dict[Dashboard, List[BasicPRInformation]] = dict()
    # The 'tech debt', 'other base' and 'from fork' boards are obtained
    # from filtering the list of all non-draft PRs (without the WIP label).
    all_ready_prs = prs_without_label(nondraft_PRs, "WIP")
    prs_to_list[Dashboard.TechDebt] = prs_with_any_label(all_ready_prs, ["tech debt", "longest-pole"])
    prs_to_list[Dashboard.OtherBase] = [pr for pr in nondraft_PRs if base_branch[pr.number] != "master"]
    prs_to_list[Dashboard.FromFork] = prs_from_fork

    prs_to_list[Dashboard.NeedsHelp] = prs_with_any_label(nondraft_PRs, ["help-wanted", "please_adopt"])
    prs_to_list[Dashboard.NeedsDecision] = prs_with_label(nondraft_PRs, "awaiting-zulip")

    # Compute all PRs on the review queue (and well as several sub-filters).
    # The review queue consists of all PRs against the master branch, with passing CI,
    # that are not in draft state and not labelled WIP, help-wanted or please-adopt,
    # and have none of the other labels below.
    master_prs_with_CI = [pr for pr in nondraft_PRs if base_branch[pr.number] == "master" and (CI_status[pr.number] == CIStatus.Pass)]
    master_CI_notfork = [pr for pr in master_prs_with_CI if pr not in prs_from_fork]
    other_labels = [
        # XXX: does the #queue check for all of these labels?
        "blocked-by-other-PR",
        "blocked-by-core-PR",
        "blocked-by-batt-PR",
        "blocked-by-qq-PR",
        "awaiting-CI",
        "awaiting-author",
        "awaiting-zulip",
        "please-adopt",
        "help-wanted",
        "WIP",
        "delegated",
        "auto-merge-after-CI",
        "ready-to-merge",
    ]
    queue_or_merge_conflict = prs_without_any_label(master_CI_notfork, other_labels)
    prs_to_list[Dashboard.NeedsMerge] = prs_with_label(queue_or_merge_conflict, "merge-conflict")
    queue_prs = prs_without_label(queue_or_merge_conflict, "merge-conflict")

    interesting_CI = [pr for pr in nondraft_PRs if CI_status[pr.number] == CIStatus.FailInessential]
    foo = [pr for pr in interesting_CI if base_branch[pr.number] == "master" and pr not in prs_from_fork]
    prs_to_list[Dashboard.InessentialCIFails] = prs_without_any_label(foo, other_labels + ["merge-conflict"])

    queue_prs2 = None
    with open("queue.json", "r") as queuefile:
        queue_prs2 = _extract_prs(json.load(queuefile))
        queue_pr_numbers2 = [pr.number for pr in queue_prs2]
    msg = "comparing this page's review dashboard (left) with the Github #queue (right)"
    if my_assert_eq(msg, [pr.number for pr in queue_prs], queue_pr_numbers2):
        print("Review dashboard and #queue match, hooray!", file=sys.stderr)

    prs_to_list[Dashboard.Queue] = queue_prs if use_aggregate_queue else queue_prs2
    queue = prs_to_list[Dashboard.Queue]
    prs_to_list[Dashboard.QueueNewContributor] = prs_with_label(queue, "new-contributor")
    prs_to_list[Dashboard.QueueEasy] = prs_with_label(queue, "easy")
    prs_to_list[Dashboard.QueueTechDebt] = prs_with_any_label(queue, ["tech debt", "longest-pole"])

    a_day_ago = datetime.now(timezone.utc) - timedelta(days=1)
    a_week_ago = datetime.now(timezone.utc) - timedelta(days=7)
    two_weeks_ago = datetime.now(timezone.utc) - timedelta(days=14)
    one_day_stale = [pr for pr in nondraft_PRs if aggregate_info[pr.number].last_updated < a_day_ago]
    one_week_stale = [pr for pr in nondraft_PRs if aggregate_info[pr.number].last_updated < a_week_ago]
    prs_to_list[Dashboard.AllReadyToMerge] = prs_with_any_label(nondraft_PRs, ["ready-to-merge", "auto-merge-after-CI"])
    prs_to_list[Dashboard.StaleReadyToMerge] = prs_with_any_label(one_day_stale, ["ready-to-merge", "auto-merge-after-CI"])
    prs_to_list[Dashboard.StaleDelegated] = prs_with_label(one_day_stale, "delegated")
    mm_prs = prs_with_label(one_day_stale, "maintainer-merge")
    prs_to_list[Dashboard.StaleMaintainerMerge] = prs_without_label(mm_prs, "ready-to-merge")
    prs_to_list[Dashboard.AllMaintainerMerge] = prs_without_label(prs_with_label(nondraft_PRs, "maintainer-merge"), "ready-to-merge")
    prs_to_list[Dashboard.StaleNewContributor] = prs_with_label(one_week_stale, "new-contributor")

    stale_queue = []
    for pr in queue:
        last_real_update = aggregate_info[pr.number].last_status_change
        if last_real_update is not None and last_real_update.time < two_weeks_ago:
            stale_queue.append(pr)
    prs_to_list[Dashboard.QueueStaleUnassigned] = [pr for pr in stale_queue if not aggregate_info[pr.number].assignees]
    # TODO/Future: use a more refined measure of activity!
    prs_to_list[Dashboard.QueueStaleAssigned] = [pr for pr in stale_queue if aggregate_info[pr.number].assignees]

    (bad_title, unlabelled, contradictory) = compute_dashboards_bad_labels_title(nondraft_PRs)
    prs_to_list[Dashboard.BadTitle] = bad_title
    prs_to_list[Dashboard.Unlabelled] = unlabelled
    prs_to_list[Dashboard.ContradictoryLabels] = contradictory
    prs_to_list[Dashboard.Approved] = approved
    return prs_to_list


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
# 'extra_script' is expected to be newline-delimited and appropriately indented.
def write_webpage(body: str, outfile: str, extra_script: str | None = None) -> None:
    with open(outfile, "w") as fi:
        script = (extra_script or "") + STANDARD_SCRIPT
        footer = f"<script>{script}</script>\n</body>\n</html>"
        print(f"{HTML_HEADER}\n{body}\n{footer}", file=fi)


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

    # The only exception is for the "on the queue" page,
    # which points out missing information explicitly, hence is passed the non-filled in data.
    CI_status: dict[int, CIStatus] = dict()
    for pr in nondraft_PRs:
        if pr.number in input_data.aggregate_info:
            CI_status[pr.number] = input_data.aggregate_info[pr.number].CI_status
        else:
            CI_status[pr.number] = CIStatus.Missing
    base_branch: dict[int, str] = dict()
    for pr in nondraft_PRs:
        base_branch[pr.number] = aggregate_info[pr.number].base_branch
    prs_from_fork = [pr for pr in nondraft_PRs if aggregate_info[pr.number].head_repo != "leanprover-community"]
    all_pr_status = compute_pr_statusses(aggregate_info, input_data.all_open_prs)
    write_on_the_queue_page(all_pr_status, aggregate_info, nondraft_PRs, prs_from_fork, CI_status, base_branch)

    # TODO: try to enable |use_aggregate_queue| 'queue_prs' again, once all the root causes
    # for PRs getting 'dropped' by 'gather_stats.sh' are found and fixed.
    prs_to_list = determine_pr_dashboards(nondraft_PRs, base_branch, prs_from_fork, CI_status, aggregate_info, False)
    # FIXME: move setting this value into determine_pr_dashboards
    prs_to_list[Dashboard.All] = input_data.all_open_prs

    # FUTURE: can this time be displayed in the local time zone of the user viewing this page?
    updated = datetime.now(timezone.utc).strftime("%B %d, %Y at %H:%M UTC")
    write_overview_page(updated)
    # Future idea: add a histogram with the most common areas,
    # or dedicated tables for common areas (and perhaps one for t-algebra, because it's hard to filter)
    write_review_queue_page(updated, prs_to_list, aggregate_info)
    write_maintainers_quick_page(updated, prs_to_list, aggregate_info)
    write_help_out_page(updated, prs_to_list, aggregate_info)
    write_triage_page(updated, prs_to_list, all_pr_status, aggregate_info, nondraft_PRs, draft_PRs)
    write_main_page(aggregate_info, all_pr_status, prs_to_list, nondraft_PRs, draft_PRs, updated)


# Print a webpage "why is my PR not on the queue" to the file "on_the_queue.html".
# 'prs' is the list of PRs on which to print information;
# 'prs_from_fork' is the list of such PRs which are opened from a fork of mathlib,
# 'CI_status' states, for each PR, whether PR passes, fails or is still running (or we are missing information).
# 'base_branch' returns the pase branch of each PR.
def write_on_the_queue_page(
    all_pr_status: dict[int, PRStatus],
    aggregate_info: dict[int, AggregatePRInfo],
    prs: List[BasicPRInformation],
    prs_from_fork: List[BasicPRInformation],
    CI_status: dict[int, CIStatus],
    base_branch: dict[int, str],
) -> None:
    def icon(state: bool | None) -> str:
        """Return a green checkmark emoji if `state` is true, and a red cross emoji otherwise."""
        return "&#9989;" if state else "&#10060;"

    body = ""
    for pr in prs:
        if base_branch[pr.number] != "master":
            continue
        from_fork = pr in prs_from_fork
        status_symbol = {
            CIStatus.Pass: f'<a title="CI for this pull request passes">{icon(True)}</a>',
            CIStatus.Fail: f'<a title="CI for this pull request fails">{icon(False)}</a>',
            # TODO: change symbol, cross with a ?, with underline and explanation!
            CIStatus.FailInessential: f'<a title="CI for this pull request fails, but the failing jobs are typically spurious or related to mathlib\'s infrastructure. Unless this PR modifies that infrastructure itself, the failure is not the fault of this PR">{icon(False)}?</a>',
            CIStatus.Running: '<a title="CI for this pull request is still running">&#128996;</a>',
            CIStatus.Missing: '<a title="missing information about this PR\'s CI status">???</a>',
        }
        is_blocked = any(lab.name in ["blocked-by-other-PR", "blocked-by-core-PR", "blocked-by-batt-PR", "blocked-by-qq-PR"] for lab in pr.labels)
        has_merge_conflict = "merge-conflict" in [lab.name for lab in pr.labels]
        is_ready = not (any(lab.name in ["WIP", "help-wanted", "please-adopt"] for lab in pr.labels))
        review = not (any(lab.name in ["awaiting-CI", "awaiting-author", "awaiting-zulip"] for lab in pr.labels))
        overall = (CI_status[pr.number] == CIStatus.Pass) and (not is_blocked) and (not has_merge_conflict) and is_ready and review
        name = pr.author_name
        if name is None:
            # TODO: take the author from the aggregate information instead
            # requires refactoring this function accordingly
            name = "dependabot(?)"

        current_status = PRStatus.Closed if aggregate_info[pr.number].state == "closed" else all_pr_status[pr.number]
        (curr1, curr2) = {
            PRStatus.AwaitingBors: ("is", "awaiting bors"),
            PRStatus.AwaitingAuthor: ("is", "awaiting author"),
            PRStatus.AwaitingReview: ("is", "awaiting review"),
            PRStatus.AwaitingDecision: ("is", "awaiting a zulip discussion"),
            PRStatus.MergeConflict: ("has a", "merge conflict"),
            PRStatus.Delegated: ("is", "delegated"),
            PRStatus.HelpWanted: ("is", "looking for help"),
            PRStatus.Blocked: ("is", "blocked on another PR"),
            PRStatus.NotReady: ("is", "labelled WIP or marked draft"),
            PRStatus.Contradictory: ("has", "contradictory labels"),
            PRStatus.Closed: ("is", "closed (so shouldn't appear in this list)"),
            PRStatus.FromFork: ("is", "opened from a fork"),
        }[current_status]
        if current_status == PRStatus.NotReady:
            curr2 = "labelled WIP" if "WIP" in [l.name for l in aggregate_info[pr.number].labels] else "marked draft"
        pr_data = aggregate_info[pr.number]
        if pr_data.last_status_change is None or pr_data.total_queue_time is None:
            status = curr2
        else:
            if pr_data.last_status_change.current_status not in [PRStatus.NotReady, PRStatus.Closed]:
                if pr_data.last_status_change.current_status != current_status and pr_data.last_status_change.status == DataStatus.Valid:
                    print(
                        f"WARNING: mismatch for {pr.number}: current status (from REST API data) is {current_status}, "
                        f"but the 'last status' from the aggregate data is {pr_data.last_status_change.current_status}", file=sys.stderr
                    )
            details = f" (details: {pr_data.total_queue_time.explanation})" if pr_data.total_queue_time.explanation else ""
            hover = f"PR {pr.number} was in review for {format_delta(pr_data.total_queue_time.value_rd)} overall{details}. It was last updated {format_delta(pr_data.last_status_change.delta)} ago and {curr1} {curr2}."
            status = f'<a title="{hover}">{curr2}</a>'
            if pr_data.last_status_change.status == "incomplete" or pr_data.total_queue_time.status == "incomplete":
                status += '<a title="caution: this data is likely incomplete">*</a>'
        entries = [
            pr_link(pr.number, pr.url), user_link(name), title_link(pr.title, pr.url),
            _write_labels(pr.labels), icon(not from_fork), status_symbol[CI_status[pr.number]],
            icon(not is_blocked), icon(not has_merge_conflict), icon(is_ready), icon(review), icon(overall), status
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
        "PR's overall status",
    ]
    head = _write_table_header(headings, "    ")
    table = f"  <table>\n{head}{body}  </table>"
    # FUTURE: can this time be displayed in the local time zone of the user viewing this page?
    updated = datetime.now(timezone.utc).strftime("%B %d, %Y at %H:%M UTC")
    start = f"  <h1>Why is my PR not on the queue?</h1>\n  <small>This page was last updated on: {updated}</small>"
    write_webpage(f"{start}\n{EXPLANATION}\n{table}", "on_the_queue.html")


def write_overview_page(updated: str) -> None:
    title = "  <h1>Mathlib review and triage dashboard</h1>"
    welcome = """<p>Welcome to the mathlib review and triage webpage! There are many ways to help, what are you looking for in particular?</p>

<div class="btn-group">
  <a href="review_dashboard.html"><button>Review queue</button></a>
  <a href="maintainers_quick.html"><button>For maintainers (quick)</button></a>
  <a href="help_out.html"><button>Help out</button></a>
  <a href="triage.html"><button>Triage dashboard</button></a>
</div><p></p>
<div class="btn-group"><a href="on_the_queue.html"><button>Why is my PR not on the queue? Can I see all my PRs?</button></a></div><p></p>

<details>
  <summary>What do these buttons mean? Which page should I visit?</summary>
  <ul>
    <li>Would you like to review some pull request? The <strong><a href="review_dashboard.html">review dashboard</a></strong> contains all PRs waiting for review. There are special sections for PRs by new contributors, labelled <em>easy</em> or addressing technical debt.</li>
    <li>Would you like to find out <strong>why</strong> your PR is (not) <strong>on the review queue</strong>? Are you interested in an overview of all your PRs with their status? <a href="on_the_queue.html">This webpage</a> contains all information necessary.</li>
    <li>There is a webpage for <strong>maintainers with little time</strong>: this contains e.g. all PRs which are just awaiting maintainer approval.
    If you actually have some more time at your hands, the <a href="review_dashboard.html">page for reviewers</a> or the <a href="triage.html">triage dashboard</a> should be useful.</li>
    <li>Would you just like to <strong>help out</strong>? <a href="help_out.html">This page</a> collects PRs where help was requested, or where some quick action can be useful.</li>
    <li>Are you coming here for <strong>PR triage</strong>: looking for PRs stuck in some state, and would like to move them along? The <a href="triage.html">triage dashboard</a> has the ultimate collection of all public information.</li>
    <!-- 'hidden' page for maintainers to assign reviewers must be generated locally, by running a script -->
  </ul>
</details>"""
    welcome = "\n  ".join(welcome.splitlines())
    welcome += '\n  <p>For the <strong>old main page</strong> with all tables at one glance, <a href="index-old.html">look here</a>.</p>'
    feedback = '<p>Feedback (including bug reports and ideas for improvements) on this dashboard is very welcome, for instance <a href="https://github.com/jcommelin/queueboard">directly on the github repository</a>.</p>'
    body = f"{title}\n  {welcome}\n  {feedback}\n  <p><small>This dashboard was last updated on: {updated}</small></p>\n"
    write_webpage(body, "index.html")


def write_review_queue_page(
    updated: str,
    prs_to_list: dict[Dashboard, List[BasicPRInformation]],
    aggregate_info: dict[int, AggregatePRInfo],
) -> None:
    title = "  <h1>The mathlib review queue</h1>"
    welcome = "<p>Welcome to the mathlib review page. Everybody's help with reviewing is appreciated. Reviewing contributions is important, and everybody is welcome to review pull requests! If you're not sure how, the <a href=\"https://leanprover-community.github.io/contribute/pr-review.html\">pull request review guide</a> is there to help you.<br>\n  This page contains tables of</p>"
    items = [
        (Dashboard.Queue, "all PRs ready for review", ""),
        (
            Dashboard.QueueNewContributor,
            'among these, all PRs written by "new contributors"',
            " (i.e., everybody who has had at most five PRs merged to mathlib)",
        ),
        (Dashboard.QueueEasy, 'just the PRs labelled "easy"', ""),
        (Dashboard.QueueTechDebt, "just the PRs addressing technical debt", ""),
    ]
    list_items = [
        f'<li><a href="#{getIdTitle(kind)[0]}">{description}</a>{unlinked}</li>\n' for (kind, description, unlinked) in items
    ]
    body = f"{title}\n  {welcome}\n  <ul>{'    '.join(list_items)}  </ul>\n  <small>This dashboard was last updated on: {updated}</small>\n\n"
    dashboards = [write_dashboard(prs_to_list, kind, aggregate_info) for (kind, _, _) in items]
    body += "\n".join(dashboards) + "\n"
    write_webpage(body, "review_dashboard.html")


def write_maintainers_quick_page(
    updated: str,
    prs_to_list: dict[Dashboard, List[BasicPRInformation]],
    aggregate_info: dict[int, AggregatePRInfo],
) -> None:
    title = "  <h1>Maintainers page: short tasks</h1>"
    welcome = "<p>Are you a maintainer with just a short amount of time? The following kinds of pull requests could be relevant for you.</p>"
    items = [
        (Dashboard.StaleReadyToMerge, "stale PRs ready-to-merge", ""),
        (Dashboard.StaleMaintainerMerge, 'stale PRs labelled "maintainer merge"', ""),
        (Dashboard.AllMaintainerMerge, "all PRs labelled 'maintainer merge'", ""),
        (Dashboard.FromFork, "all PRs made from a fork", ""),
        (Dashboard.NeedsDecision, "all PRs waiting on finding consensus on zulip", ""),
        (Dashboard.QueueTechDebt, "just the PRs addressing technical debt", ""),
    ]
    list_items = [
        f'<li><a href="#{getIdTitle(kind)[0]}">{description}</a>{unlinked}</li>\n' for (kind, description, unlinked) in items
    ]
    post = 'If you realise you actually have a bit more time, you can also look at the <a href="review_dashboard.html">page for reviewers</a>, or look at the <a href="triage.html">triage page!</a>'
    body = f"{title}\n  {welcome}\n  <ul>{'    '.join(list_items)}  </ul>\n  {post}<br>\n  <small>This dashboard was last updated on: {updated}</small>\n\n"
    dashboards = [write_dashboard(prs_to_list, kind, aggregate_info) for (kind, _, _) in items]
    body += "\n".join(dashboards) + "\n"
    write_webpage(body, "maintainers_quick.html")


def write_help_out_page(
    updated: str,
    prs_to_list: dict[Dashboard, List[BasicPRInformation]],
    aggregate_info: dict[int, AggregatePRInfo],
) -> None:
    title = "  <h1>Helping out: short tasks</h1>"
    welcome = "<p>Would you like to help out at a PR, differently from reviewing? Here are some ideas:</p>"
    items = [
        (
            Dashboard.InessentialCIFails,
            "take a look at ",
            "PRs with just some mathlib-infrastructure-related CI failure",
            ": unless it's an infrastructure PR, these are spurious/not the PRs fault. If the failure is spurious, commenting to this effect can be helpful.",
        ),
        (Dashboard.NeedsHelp, "take a look at ", "PRs labelled help-wanted or please-adopt", ""),
        (
            Dashboard.NeedsMerge,
            "If the author hasn't noticed, you can ask in a PR which ",
            "just has a merge conflict, but would be reviewable otherwise",
            ". (Remember, that most contributors to mathlib are volunteers, contribute in their free time and often have other commitments — and that real-life events can happen!)",
        ),
        (
            Dashboard.FromFork,
            "post a comment on a ",
            "PR made from a fork",
            ", nicely asking them to re-submit it from a mathlib branch instead",
        ),
        (
            Dashboard.StaleNewContributor,
            "check if any ",
            "'stale' PR by a new contributor",
            " benefits from support, such as help with failing CI or providing feedback on the code",
        ),
        (
            Dashboard.StaleDelegated,
            "check if any ",
            "'stale' delegated PR",
            " benefits from support, such as by fixing merge conflicts (but be sure to ask first; the PR author might simply be very busy)",
        ),
    ]
    list_items = [
        f'<li>{pre}<a href="#{getIdTitle(kind)[0]}">{description}</a>{post}</li>\n' for (kind, pre, description, post) in items
    ]
    body = f"{title}\n  {welcome}\n  <ul>{'    '.join(list_items)}  </ul>\n  <small>This dashboard was last updated on: {updated}</small>\n\n"
    dashboards = [write_dashboard(prs_to_list, kind, aggregate_info) for (kind, _, _, _) in items]
    body += "\n".join(dashboards) + "\n"
    write_webpage(body, "help_out.html")


# Write the code for a h2 heading linking to itself, with id |id|, title |title|,
# and optional tooltip |tooltip| (implemented as an <a title> attribute).
def _make_h2(id: str, title: str, tooltip=None) -> str:
    if tooltip:
        return f'<h2 id="{id}"><a href="#{id}" title="{tooltip}">{title}</a></h2>'
    return f'<h2 id="{id}"><a href="#{id}">{title}</a></h2>'


def write_triage_page(
    updated: str,
    prs_to_list: dict[Dashboard, List[BasicPRInformation]],
    all_pr_status: dict[int, PRStatus],
    aggregate_info: dict[int, AggregatePRInfo],
    # These two are just used for generating statistics.
    nondraft_PRs: list[BasicPRInformation],
    draft_PRs: list[BasicPRInformation],
) -> None:
    title = "  <h1>Mathlib triage dashboard</h1>"
    welcome = "<p>Welcome to the PR triage page! This page is perfect if you intend to look for pull request which seem to have stalled.<br>Feedback on designing this page or further information to include is very welcome.</p>"
    welcome += f"\n  <small>This dashboard was last updated on: {updated}</small>"

    spurious_ci = (
        getIdTitle(Dashboard.InessentialCIFails)[0], "Likely-spurious CI failures",
         "PRs with just failing CI, and all failures that are very likely to be infrastructure issues, and not the fault of the PR!"
    )
    subsections = [
        ("statistics", "PR statistics"),
        ("not-yet-landed", "Not yet landed PRs"),
        ("review-status", "Review status"),
        (getIdTitle(Dashboard.QueueStaleUnassigned)[0], "Stale unassigned PRs"),
        (getIdTitle(Dashboard.QueueStaleAssigned)[0], "Stale assigned PRs"),
        ("spuriousci", ""),
        (getIdTitle(Dashboard.QueueTechDebt)[0], "Tech debt PRs"),
        getIdTitle(Dashboard.NeedsDecision),
        getIdTitle(Dashboard.NeedsMerge),
        getIdTitle(Dashboard.StaleNewContributor),
        (getIdTitle(Dashboard.OtherBase)[0], "PRs not into master"),
        (getIdTitle(Dashboard.FromFork)[0], "PRs from a fork"),
        getIdTitle(Dashboard.Approved),
        getIdTitle(Dashboard.All),
        ("other", "Other lists of PRs"),
    ]
    subsections_mapped = [
        (a, b, None) if a != "spuriousci" else spurious_ci
        for (a, b) in subsections
    ]
    # Created solely because escaping in format strings was too hard.
    def aux(tooltip: str | None) -> str:
        return " " if tooltip is None else f' title="{tooltip}"'
    items = [
        f"<a href=\"#{anchor}\"{aux(tooltip)}target=\"_self\">{title}</a>"
        for (anchor, title, tooltip) in subsections_mapped
    ]
    toc = f"<br><p>\n<b>Quick links:</b> {' | '.join(items)}"

    stats = gather_pr_statistics(all_pr_status, aggregate_info, prs_to_list, nondraft_PRs, draft_PRs, False)

    some_stale = f": <strong>{len(prs_to_list[Dashboard.StaleReadyToMerge])}</strong> of them are stale, and merit another look</li>\n"
    some_stale += write_dashboard(prs_to_list, Dashboard.StaleReadyToMerge, aggregate_info, header=False)
    no_stale = " &mdash; among these <strong>no</strong> stale ones, congratulations!</li>"
    ready_to_merge = f"<strong>{len(prs_to_list[Dashboard.AllReadyToMerge])}</strong> PRs are ready to merge{some_stale if prs_to_list[Dashboard.StaleReadyToMerge] else no_stale}"

    stale_mm = f'of which <strong>{len(prs_to_list[Dashboard.StaleMaintainerMerge])}</strong> have not been updated in a day (<a href="maintainers_quick.html#stale-maintainer-merge">these</a>)'
    no_stale_mm = "<strong>none of which</strong> has been pending for more than a day. Congratulations!"
    mm = f'<strong>{len(prs_to_list[Dashboard.AllMaintainerMerge])}</strong> PRs are waiting on maintainer approval (<a href="maintainers_quick.html#all-maintainer-merge">these</a>), {stale_mm if prs_to_list[Dashboard.StaleMaintainerMerge] else no_stale_mm}'

    no_stale_delegated = "<li><strong>no</strong> PRs have been delegated and not updated in a day, congratulations!</li>"
    stale_delegated = f"<li><details><summary><strong>{len(prs_to_list[Dashboard.StaleDelegated])}</strong> PRs have been delegated and not updated in a day</summary>\n  \n  {write_dashboard(prs_to_list, Dashboard.StaleDelegated, aggregate_info, header=False)}</details></li>"

    notlanded = f"""{_make_h2('not-yet-landed', 'Approved, not yet landed PRs')}

    At the moment,
    <ul>
      <li>{ready_to_merge}
      <li>{mm}</li>
      {stale_delegated if prs_to_list[Dashboard.StaleDelegated] else no_stale_delegated}
    </ul>"""

    # All PRs which appear on the queue for the first time in the past two weeks
    # as computed from aggregate events data.
    recent_on_queue = []
    two_weeks_ago = datetime.now(timezone.utc) - timedelta(days=14)
    for pr in prs_to_list[Dashboard.Queue]:
        first_on_queue = aggregate_info[pr.number].first_on_queue
        if first_on_queue is not None:
            (foq_status, foq_time) = first_on_queue
            if foq_time is None:
                if foq_status != DataStatus.Incomplete:
                    print(f"warning: PR {pr.number} is listed as never on queue, while it's on the queue", file=sys.stderr)
            elif two_weeks_ago <= foq_time:
                recent_on_queue.append(pr.number)

    # PRs on the queue which are unassigned and have had no meaningful update (i.e. status change) in the past two weeks.
    unassigned = len(prs_to_list[Dashboard.QueueStaleUnassigned])
    # Awaiting review, assigned and not updated in two weeks.
    # TODO/future: use a better measure of no activity, such as "no comment/review comment from anybody but the PR author".
    stale_assigned = len(prs_to_list[Dashboard.QueueStaleAssigned])
    review_heading = f"""\n{_make_h2('review-status', 'Review status')}
  <p>There are currently <strong>{len(prs_to_list[Dashboard.Queue])}</strong> {link_to(Dashboard.Queue, "PRs awaiting review", "review_dashboard.html")}. Among these,</p>
  <ul>
    <li><strong>{len(prs_to_list[Dashboard.QueueEasy])}</strong> are labelled easy ({link_to(Dashboard.QueueEasy, subpage="review_dashboard.html")}),</li>
    <li><strong>{len(prs_to_list[Dashboard.QueueTechDebt])}</strong> are addressing technical debt ({link_to(Dashboard.QueueTechDebt, "namely these", "review_dashboard.html")}), and</li>
    <li><strong>{len(recent_on_queue)}</strong> appeared on the review queue within the last two weeks.</li>
  </ul>
  <p>On the other hand, {link_to(Dashboard.QueueStaleUnassigned, f"<strong>{unassigned}</strong> PRs")} are unassigned and have not seen a status change in two weeks, and {link_to(Dashboard.QueueStaleAssigned, f"<strong>{stale_assigned}</strong> PRs")} are assigned, without recent review activity.</p>"""
    review_heading = "\n  ".join(review_heading.splitlines())

    # Write a dashboard of unassigned PRs: we can safely skip the "assignee" column.
    config = ExtraColumnSettings.with_approvals(True)
    stale_unassigned = write_dashboard(prs_to_list, Dashboard.QueueStaleUnassigned, aggregate_info, config)

    # XXX: when updating the definition of "stale assigned" PRs, make sure to update all the dashboard descriptions
    setting = ExtraColumnSettings.with_approvals(True).with_assignee(True)
    further = write_dashboard(prs_to_list, Dashboard.QueueStaleAssigned, aggregate_info, setting)

    others = [
        Dashboard.InessentialCIFails,
        Dashboard.TechDebt,
        Dashboard.NeedsDecision,
        Dashboard.NeedsMerge,
        Dashboard.StaleNewContributor,
        Dashboard.OtherBase,
        Dashboard.FromFork,
        Dashboard.Approved,
        Dashboard.All,
    ]
    for kind in others:
        further += write_dashboard(prs_to_list, kind, aggregate_info)

    # xxx: audit links; which ones should open on the same page, which ones in a new tab?

    items2 = []
    for kind in Dashboard._member_map_.values():
        # These dashboards were already put on other pages or earlier on this page:
        # no need to display them again.
        kinds_to_hide = [
            Dashboard.Queue,
            Dashboard.QueueEasy,
            Dashboard.QueueNewContributor,
            Dashboard.QueueTechDebt,
            Dashboard.QueueStaleUnassigned,
            Dashboard.QueueStaleAssigned,
            Dashboard.AllMaintainerMerge,
            Dashboard.StaleMaintainerMerge,
            Dashboard.StaleDelegated,
            Dashboard.AllReadyToMerge,
            Dashboard.StaleReadyToMerge,
            Dashboard.NeedsHelp,
        ]
        if kind not in kinds_to_hide and kind not in others:
            items2.append((kind, "", long_description(kind), ""))
    list_items = [
        f'<li>{pre}<a href="#{getIdTitle(kind)[0]}">{description}</a>{post}</li>\n' for (kind, pre, description, post) in items2
    ]
    remainder = f"""\n{_make_h2('other', 'Other lists of PRs')}
    Some other lists of PRs which could be useful:
    <ul>{'    '.join(list_items)}  </ul>
    """
    # XXX: do I want to add a giant table with all PRs and their status!

    body = f"{title}\n  {welcome}\n  {toc}\n  {stats}\n  {notlanded}\n  {review_heading}\n  {stale_unassigned}\n  {further}\n  {remainder}\n"
    setting = ExtraColumnSettings.with_approvals(kind == Dashboard.Approved).with_assignee(True)
    dashboards = [write_dashboard(prs_to_list, kind, aggregate_info, setting) for (kind, _, _, _) in items2]
    body += "\n".join(dashboards) + "\n"
    write_webpage(body, "triage.html")


# Write the main page for the dashboard to the file index-old.html.
def write_main_page(
    aggregate_info: dict[int, AggregatePRInfo],
    all_pr_statusses: dict[int, PRStatus],
    prs_to_list: dict[Dashboard, List[BasicPRInformation]],
    nondraft_PRs: list[BasicPRInformation],
    draft_PRs: list[BasicPRInformation],
    updated: str,
) -> None:
    title = "  <h1>Mathlib review and triage dashboard</h1>"
    welcome = '<p>Welcome to the mathlib review and triage dashboard. This is a prototype for better exposing the currently open PRs to mathlib. Feedback (including bug reports and ideas for improvements) on this dashboard is very welcome, for instance <a href="https://github.com/jcommelin/queueboard">directly on the github repository</a>.<br>'
    "You can hover over any section header (and some table headings) to find out what they show. The same works for the table of contents below.</p>"
    body = f"{title}\n  {welcome}  <small>This dashboard was last updated on: {updated}</small>\n"

    # Print a quick table of contents.
    links = []
    for kind in Dashboard._member_map_.values():
        if kind in [Dashboard.QueueTechDebt, Dashboard.AllMaintainerMerge]:
            continue
        (id, _title) = getIdTitle(kind)
        links.append(f'<a href="#{id}" title="{short_description(kind)}" target="_self">{id}</a>')
    body += f"<br><p>\n<b>Quick links:</b> <a href=\"#statistics\" target=\"_self\">PR statistics</a> | {str.join(' | ', links)}</p>\n"

    body += gather_pr_statistics(all_pr_statusses, aggregate_info, prs_to_list, nondraft_PRs, draft_PRs, True)
    for kind in Dashboard._member_map_.values():
        if kind in [Dashboard.QueueTechDebt, Dashboard.AllMaintainerMerge]:
            continue
        if kind not in prs_to_list:
            print(f"error: forgot to include data for dashboard kind {kind}", file=sys.stderr)
        else:
            body += f"{write_dashboard(prs_to_list, kind, aggregate_info)}\n"
    write_webpage(body, "index-old.html")


# Compute the status of each PR in a given list. Return a dictionary keyed by the PR number.
# (`BasicPRInformation` is not hashable, hence cannot be used as a dictionary key.)
# 'aggregate_info' contains aggregate information about each PR's CI state,
# draft status and base branch (and more, which we do not use).
# If no detailed information was available for a given PR number, 'None' is returned.
def compute_pr_statusses(aggregate_info: dict[int, AggregatePRInfo], prs: List[BasicPRInformation]) -> dict[int, PRStatus]:
    def determine_status(aggregate_info: AggregatePRInfo, info: BasicPRInformation) -> PRStatus:
        # Ignore all "other" labels, which are not relevant for this anyway.
        labels = [label_categorisation_rules[lab.name] for lab in info.labels if lab.name in label_categorisation_rules]
        from_fork = aggregate_info.head_repo != "leanprover-community"
        state = PRState(labels, aggregate_info.CI_status, aggregate_info.is_draft, from_fork)
        return determine_PR_status(datetime.now(timezone.utc), state)

    return {info.number: determine_status(aggregate_info[info.number] or PLACEHOLDER_AGGREGATE_INFO, info) for info in prs}

# If the link to a dashboard goes to a separate page |subpage|, open the link in a new tab.
# |force_same_page| disables that parameter, i.e. all links always go to the current page.
def link_to(kind: Dashboard, name="these ones", subpage=None, force_same_page=False) -> str:
    if subpage and not force_same_page:
        return f'<a href="{subpage or ""}#{getIdTitle(kind)[0]}">{name}</a>'
    else:
        return f'<a href="#{getIdTitle(kind)[0]}" target="_self">{name}</a>'


# If aggregate information about a PR is missing, we treat it as non-draft, failing CI and against 'master'.
# (Though, in fact, we assume that 'aggregate_info' is complete, by prior normalisation.)
def gather_pr_statistics(
    all_pr_statusses: dict[int, PRStatus],
    aggregate_info: dict[int, AggregatePRInfo],
    prs: dict[Dashboard, List[BasicPRInformation]],
    all_ready_prs: List[BasicPRInformation],
    all_draft_prs: List[BasicPRInformation],
    is_triage_board: bool,
) -> str:
    queue_prs = prs[Dashboard.Queue]
    justmerge_prs = prs[Dashboard.NeedsMerge]

    # Collect the number of PRs in each possible status.
    # NB. The order of these statusses is meaningful; the statistics are shown in the order of these items.
    statusses = [
        PRStatus.AwaitingReview, PRStatus.Blocked, PRStatus.AwaitingAuthor, PRStatus.MergeConflict,
        PRStatus.HelpWanted,PRStatus.NotReady,
        PRStatus.AwaitingDecision,
        PRStatus.FromFork,
        PRStatus.Contradictory,
        PRStatus.Delegated, PRStatus.AwaitingBors,
    ]
    numbers = [pr.number for pr in all_ready_prs]
    ready_pr_status = { n: all_pr_statusses[n] for n in numbers }
    number_prs: Dict[PRStatus, int] = {
        status: len([number for number in ready_pr_status if ready_pr_status[number] == status]) for status in statusses
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
    # TODO: also cross-check the data for merge conflicts
    # add the "needs-decision" status with the awaiting-zulip dashboard

    number_all = len(all_ready_prs) + len(all_draft_prs)

    def number_percent(n: int, total: int, color: str = "") -> str:
        if color:
            return f'{n} (<span style="color: {color};">{n/total:.1%}</span>)'
        else:
            return f"{n} (<span>{n/total:.1%}</span>)"

    instatus = {
        PRStatus.AwaitingReview: f"are awaiting review ({link_to(Dashboard.Queue, subpage='review_dashboard.html', force_same_page=is_triage_board)}), among these <b>{number_percent(len(prs[Dashboard.QueueNewContributor]), len(queue_prs))}</b> by new contributors ({link_to(Dashboard.QueueNewContributor, subpage='review_dashboard.html')})",
        PRStatus.HelpWanted: f"are labelled help-wanted or please-adopt ({link_to(Dashboard.NeedsHelp, 'roughly these', 'help_out.html', is_triage_board)})",
        PRStatus.AwaitingAuthor: "are awaiting the PR author's action",
        PRStatus.AwaitingDecision: f"are awaiting the outcome of a zulip discussion ({link_to(Dashboard.NeedsDecision)})",
        PRStatus.FromFork: f"are opened from a fork of mathlib ({link_to(Dashboard.FromFork)})",
        PRStatus.Blocked: "are blocked on another PR",
        PRStatus.Delegated: f"are delegated (stale ones are {link_to(Dashboard.StaleDelegated, 'here', 'help_out.html', is_triage_board)})",
        PRStatus.AwaitingBors: f"have been sent to bors (stale ones are {link_to(Dashboard.StaleReadyToMerge, 'here', 'maintainers_quick.html', is_triage_board)})",
        PRStatus.MergeConflict: f"have a merge conflict: among these, <b>{number_percent(len(justmerge_prs), number_prs[PRStatus.MergeConflict])}</b> would be ready for review otherwise: {link_to(Dashboard.NeedsMerge, 'these')}",
        PRStatus.Contradictory: f"have contradictory labels ({link_to(Dashboard.ContradictoryLabels)})",
        PRStatus.NotReady: "are marked as draft or work in progress",
    }
    assert set(instatus.keys()) == set(statusses)
    color = {
        PRStatus.AwaitingReview: "#33b4ec",
        PRStatus.HelpWanted: "#cc317c",
        PRStatus.AwaitingAuthor: "#f6ae9a",
        PRStatus.AwaitingDecision: "#086ad4",
        PRStatus.FromFork: "#FF8000",
        PRStatus.Blocked: "#8A6A1C",
        PRStatus.Delegated: "#689dea",
        PRStatus.AwaitingBors: "#098306",
        PRStatus.MergeConflict: "#f17075",
        PRStatus.Contradictory: "black",
        PRStatus.NotReady: "#e899cd",
    }
    assert set(color.keys()) == set(statusses)
    details = "\n".join([f"  <li><b>{number_percent(number_prs[s], number_all, color[s])}</b> {instatus[s]}</li>" for s in statusses])
    # Generate a simple pie chart showing the distribution of PR statusses.
    # Doing so requires knowing the cumulative sums, of all statusses so far.
    numbers = [number_prs[s] for s in statusses]
    cumulative = [sum(numbers[: i + 1]) for i in range(len(numbers))]
    piechart = ", ".join([f"{color[s]} 0 {cumulative[i] * 360 // number_all}deg" for (i, s) in enumerate(statusses)])
    piechart_style = f"width: 200px;height: 200px;border-radius: 50%;border: 1px solid black;background-image: conic-gradient( {piechart} );"
    if cumulative[-1] != number_all:
        dict = "{" + ", ".join([f"{s}: {number_prs[s]}" for s in statusses]) + "}"
        msg = f"""error: statistics calculation is inconsistent, all PR kinds do not add up!
            cumulative sum of PRs is {cumulative[-1]}, while there are {number_all} PRs in total
            detailed stats dictionary is {dict}"""
        print(msg, file=sys.stderr)
        # TODO: compare these lists of PRs in detail, to verify if this exposes anything other than outdated data!
        # assert False

    return f'\n{_make_h2("statistics", "Overall statistics")}\nFound <b>{number_all}</b> open PRs overall. Among these PRs\n<ul>\n{details}\n</ul><div class="piechart" style="{piechart_style}"></div>\n'


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

STANDARD_SCRIPT = """
  let diff_stat = DataTable.type('diff_stat', {
    detect: function (data) { return false; },
    order: {
      pre: function (data) {
        let parts = data.split('/', 2);
        return Number(parts[0]) + Number(parts[1]);
      }
    },
  });
  let formatted_relativedelta = DataTable.type('formatted_relativedelta', {
    detect: function (data) { return data.startsWith('<div style="display:none">'); },
    order: {
      pre: function (data) {
        let main = (data.split('</div>', 2))[0].slice('<div style="display:none">'.length);
        // If there is no input data, main is the empty string.
        if (!main.includes('-')) {
            return -1;
        }
        const [days, seconds, ...rest] = main.split('-');
        return 100000 * Number(days) + Number(seconds);
      }
    }
  })
$(document).ready( function () {
  $('table').DataTable({
    stateSave: true,
    stateDuration: 0,
    pageLength: 10,
    "searching": true,
    columnDefs: [{ type: 'diff_stat', targets: 4}],
  });
});
"""


def infer_pr_url(number: int) -> str:
    return f"https://github.com/leanprover-community/mathlib4/pull/{number}"


# An HTML link to a mathlib PR from the PR number
def pr_link(number: int, url: str, title=None) -> str:
    # The PR number is intentionally not prefixed with a #, so it is correctly
    # recognised and sorted as a number (with HTML formatting, a `html-num` type),
    # and not sorted as a string.
    return f"<a href='{url}' title='{title or ''}'>{number}</a>"


# An HTML link to a GitHub user profile
def user_link(author_name: str, details: str | None = None) -> str:
    url = f"https://github.com/{author_name}"
    title = f" title='{details}'" if details else ""
    return f"<a href='{url}'{title}>{author_name}</a>"


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


# Function to format the time of the last update
# Input is in the format: "2020-11-02T14:23:56Z" (i.e., in ISO format).
# Output is in the format: "2020-11-02 14:23 (2 days ago)"
def time_info(updatedAt: datetime) -> str:
    updated = updatedAt
    now = datetime.now(timezone.utc)
    # Calculate the difference in time
    delta = relativedelta.relativedelta(now, updated)
    # Format the output
    s = updated.strftime("%Y-%m-%d %H:%M")
    return f"{s} ({format_delta(delta)} ago)"

# Auxiliary function, used for sorting the "total time in review".
def format_delta2(delta: timedelta) -> str:
    return f"{delta.days}-{delta.seconds}"

from dateutil import tz

assert parser.isoparse("2024-04-29T18:53:51Z") == datetime(2024, 4, 29, 18, 53, 51, tzinfo=tz.tzutc())


# Extract all PRs mentioned in a data file.
def _extract_prs(data: dict) -> List[BasicPRInformation]:
    prs = []
    for page in data["output"]:
        for entry in page["data"]["search"]["nodes"]:
            name = None
            if "login" not in entry["author"]:
                print(
                    f'warning: missing author information for PR {entry["number"]}, its authors dictionary is {entry["author"]} --- was this submitted by dependabot?',
                    file=sys.stderr,
                )
            else:
                name = entry["author"]["login"]
            labels = [Label(label["name"], label["color"], label["url"]) for label in entry["labels"]["nodes"]]

            prs.append(BasicPRInformation(entry["number"], name, entry["title"], entry["url"], labels, parser.isoparse(entry["updatedAt"])))
    return prs


# Settings for which 'extra columns' to display.
class ExtraColumnSettings(NamedTuple):
    # Show which github user(s) this PR is assigned to (if any)
    show_assignee: bool
    # Show the number of users who left an approving review on this PR (with a tooltip for their github handles).
    # 'Maintainer merge/delegate' comments are inconsistently labelled as approving or not.
    # In practice, this is not an issue as 'maintainer merge'd PRs are shown separately anyway.
    show_approvals: bool
    potential_reviewers: bool
    hide_update: bool
    show_last_real_update: bool
    # Future possibilities:
    # - number of (transitive) dependencies (with PR numbers)

    @staticmethod
    def default():
        return ExtraColumnSettings(True, False, False, False, show_last_real_update=True)

    @staticmethod
    def with_approvals(val: bool):
        self = ExtraColumnSettings.default()
        return ExtraColumnSettings(self.show_assignee, val, self.potential_reviewers, self.hide_update, self.show_last_real_update)

    @classmethod
    def with_assignee(self, val: bool):
        return ExtraColumnSettings(val, self.show_approvals, self.potential_reviewers, self.hide_update, self.show_last_real_update)


# Compute the table entries about a sequence of PRs.
# 'aggregate_information' maps each PR number to the corresponding aggregate information
# (and may contain information on PRs not to be printed).
# TODO: remove 'prs' in favour of the aggregate information --- once I can ensure that the data
# in the latter is always kept updated.
def _compute_pr_entries(
    prs: List[BasicPRInformation], aggregate_information: dict[int, AggregatePRInfo],
    extra_settings: ExtraColumnSettings, potential_reviewers: dict[int, Tuple[str, List[str]]] | None=None,
) -> str:
    result = ""
    for pr in prs:
        # XXX: compare the basic and aggregate author names to see if there are any differences
        name = aggregate_information[pr.number].author
        if pr.url != infer_pr_url(pr.number):
            print(f"warning: PR {pr.number} has url differing from the inferred one:\n  actual:   {pr.url}\n  inferred: {infer_pr_url(pr.number)}", file=sys.stderr)
        entries = [pr_link(pr.number, pr.url), user_link(name), title_link(pr.title, pr.url), _write_labels(pr.labels)]
        # Detailed information about the current PR.
        pr_info = None
        if pr.number in aggregate_information:
            pr_info = aggregate_information[pr.number]
        if pr_info is None:
            print(f"main dashboard: found no aggregate information for PR {pr.number}", file=sys.stderr)
            entries.extend(["-1/-1", "-1", "-1"])
            if extra_settings.show_assignee:
                entries.append("???")
            if extra_settings.show_approvals:
                entries.append("???")
            if extra_settings.potential_reviewers and potential_reviewers is not None:
                entries.append("???")
        else:
            na = '<a title="no data available">n/a</a>'
            total_comments = na if pr_info.number_total_comments is None else str(pr_info.number_total_comments)
            entries.extend([
                "{}/{}".format(pr_info.additions, pr_info.deletions),
                str(pr_info.number_modified_files),
                total_comments,
            ])
            if extra_settings.show_assignee:
                match sorted(pr_info.assignees):
                    case []:
                        assignees = "nobody"
                    case [user]:
                        assignees = user
                    case [user1, user2]:
                        assignees = f"{user1} and {user2}"
                    case several_users:
                        assignees = ", ".join(several_users)
                entries.append(assignees)
            if extra_settings.show_approvals:
                # Deduplicate the users with approving reviews.
                # FIXME: should one indicate the number of such approvals per user instead?
                approvals_dedup = set(pr_info.approvals)
                app = ", ".join(approvals_dedup)
                approval_link = f'<a title="{app}">{len(approvals_dedup)}' if approvals_dedup else "none"
                entries.append(approval_link)
            if extra_settings.potential_reviewers and potential_reviewers is not None:
                (reviewer_str, names) = potential_reviewers[pr.number]
                entries.append(reviewer_str)
                if names:
                    # Just allow contacting the first reviewer, for now.
                    # FUTURE: add a button with a drop-down, for the various options.
                    fn = f"contactMessage('{names[0]}', {pr.number})"
                    entries.append(f'<button onclick="{fn}">Ask {names[0]} for review</button>')
                else:
                    entries.append("")
        if not extra_settings.hide_update:
            entries.append(time_info(pr.updatedAt))
        if extra_settings.show_last_real_update:
            # Always start this column with a <div> with display:none, this is important for auto-detecting the column type!
            real_update = '<div style="display:none"></div><a title="the last actual update for this PR could not be determined">unknown</a>'
            total_time = '<div style="display:none"></div><a title="this PR\'s total time in review could not be determined">unknown</a>'
            if pr_info:
                last_update = aggregate_information[pr.number].last_status_change
                if last_update is not None and last_update.status != DataStatus.Missing:
                    real_update = f'{last_update.time} ({format_delta(last_update.delta)} ago)'
                    if last_update.status == DataStatus.Incomplete:
                        real_update += '<a title="caution: this data is likely incomplete">*</a>'
                tqt = aggregate_information[pr.number].total_queue_time
                if tqt is not None and tqt.status != DataStatus.Missing:
                    prefix = f'<div style="display:none">{format_delta2(tqt.value_td)}</div> '
                    total_time = f'{prefix}<a title="{tqt.explanation}">{format_delta(tqt.value_rd)}</a>'
                    if tqt.status == DataStatus.Incomplete:
                        total_time += '<a title="caution: this data is likely incomplete">*</a>'
            entries.append(real_update)
            entries.append(total_time)
        result += _write_table_row(entries, "    ")
    return result


# Write the code for a dashboard of a given list of PRs.
# 'aggregate_information' maps each PR number to the corresponding aggregate information
# (and may contain information on PRs not to be printed).
# TODO: remove 'prs' in favour of the aggregate information --- once I can ensure that the data
# in the latter is always kept updated.
# If 'header' is false, a table header is omitted and just the dashboard is printed.
#
# |potential_reviewers| maps each PR number to a tuple (HTML code, reviewer names).
# The full string is shown on the webpage; the list of reviewer names is used for offering
# a contact button.
def write_dashboard(
    prs: dict[Dashboard, List[BasicPRInformation]], kind: Dashboard, aggregate_info: dict[int, AggregatePRInfo],
    extra_settings: ExtraColumnSettings | None=None, header=True, potential_reviewers: dict[int, Tuple[str, List[str]]]|None =None
) -> str:
    def _inner(
        prs: List[BasicPRInformation], kind: Dashboard, aggregate_info: dict[int, AggregatePRInfo],
        extra_settings: ExtraColumnSettings, add_header: bool
    ):
        # Title of each list, and the corresponding HTML anchor.
        # Explain what each dashboard contains upon hovering the heading.
        if add_header:
            (id, title) = getIdTitle(kind)
            title = _make_h2(id, title, long_description(kind))
            # If there are no PRs, skip the table header and print a bold notice such as
            # "There are currently **no** stale `delegated` PRs. Congratulations!".
            if not prs:
                return f"{title}\nThere are currently <b>no</b> {short_description(kind)}. Congratulations!\n"
        else:
            title = ""
        headings = [
            "Number", "Author", "Title", "Labels",
            '<a title="number of added/deleted lines">+/-</a>',
            '<a title="number of files modified">&#128221;</a>',
            '<a title="number of standard or review comments on this PR">&#128172;</a>',
        ]
        if extra_settings.show_assignee:
            headings.append('<a title="github user(s) this PR is assigned to (if any)">Assignee(s)</a>')
        if extra_settings.show_approvals:
            headings.append('<a title="github user(s) who have left an approving review of this PR (if any)">Approval(s)</a>')
        if extra_settings.potential_reviewers and potential_reviewers is not None:
            headings.append("Potential reviewers")
            headings.append("Contact")
        if not extra_settings.hide_update:
            headings.append("<a title=\"this pull request's last update, according to github\">Updated</a>")
        if extra_settings.show_last_real_update:
            # TODO: are there better headings for this and the other header?
            headings.append('<a title="The last time this PR\'s status changed from e.g. review to merge conflict, awaiting-author">Last status change</a>')
            headings.append("total time in review")
        head = _write_table_header(headings, "    ")
        body = _compute_pr_entries(prs, aggregate_info, extra_settings, potential_reviewers)
        return f"{title}\n  <table>\n{head}{body}  </table>"

    if extra_settings is None:
        extra_settings = ExtraColumnSettings.default()
    return _inner(prs[kind], kind, aggregate_info, extra_settings, header)


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
    if "awaiting-review-DONT-USE" in normalised_labels:
        return True
    elif "bors" in normalised_labels and ("awaiting-author" in normalised_labels or "awaiting-zulip" in normalised_labels or "WIP" in normalised_labels):
        return True
    elif "WIP" in normalised_labels and "awaiting-review" in normalised_labels:
        return True
    return False


# Determine all PRs in `prs` which are not labelled `WIP` and
# - are feature PRs without a topic label,
# - have a badly formatted title (we currently only check some of the conditions in the guidelines),
# - have contradictory labels.
def compute_dashboards_bad_labels_title(
    prs: List[BasicPRInformation],
) -> Tuple[List[BasicPRInformation], List[BasicPRInformation], List[BasicPRInformation]]:
    # Filter out all PRs which have a WIP label.
    nonwip_prs = prs_without_label(prs, "WIP")
    with_bad_title = [pr for pr in nonwip_prs if not pr.title.startswith(("feat", "chore", "perf", "refactor", "style", "fix", "doc"))]

    # Whether a PR has a "topic" label.
    def has_topic_label(pr: BasicPRInformation) -> bool:
        topic_labels = [label for label in pr.labels if label.name in ["CI", "IMO"] or label.name.startswith("t-")]
        return len(topic_labels) >= 1

    prs_without_topic_label = [pr for pr in nonwip_prs if pr.title.startswith("feat") and not has_topic_label(pr)]
    prs_with_contradictory_labels = [pr for pr in nonwip_prs if has_contradictory_labels(pr)]
    return (with_bad_title, prs_without_topic_label, prs_with_contradictory_labels)


if __name__ == "__main__":
    main()
