"""
# Computing a better estimate how long a PR was been awaiting review.

**Problem** We would like a way to track the progress of PRs, and especially learn which PRs
have been waiting long for review. Currently, we have no good way of obtaining this information:
we use the crude heuristic of
"this PR was last updated X ago, and is awaiting review now" as a metric for "waiting for time X".

That metric is imperfect because
- not everything "updating" a PR is meaningful for our purposes
If somebody edits the PR description to describe the change better
or tweaks the code to make it better understood --- without other activity
and not in response to feedback --- that is a good thing,
but does not change the PR's review status.
- a PR's time on the review queue is often interrupted by having merge conflicts.
This is usually only a temporary state, but means long streaks of no changes are much more rare.
In particular, this disadvantages conflict-prone PRs.

**A better metric** would be to track the PRs state over time, and compute
e.g. the total amount of time this PR was awaiting review.
This is what we attempt to do.

## Input data
This algorithm process a sequence of events "on X, this PR changed in this and that way"
and returns a list of all times when the PRs state changed:
for the purposes of our analysis, this could be "a PR became blocked on another PR",
"a PR became unblocked", "a PR was marked as waiting on author", "a PR incurred a merge conflict".

From this information, we can compute the total time a PR was waiting for review, for instance.

## Implementation notes

This algorithm is just a skeleton: it contains the *analysis* of the given input data,
but does not parse the input data from any other input. (That is a second step.)
"""

from datetime import datetime
from enum import Enum, auto
from typing import List, NamedTuple, Tuple

from dateutil.relativedelta import relativedelta

from classify_pr_state import (CIStatus, LabelKind, PRState, PRStatus,
                               determine_PR_status, label_categorisation_rules,
                               label_to_prstatus)


# Something changed on a PR which we care about:
# - a new label got added or removed
# - the PR was (un)marked draft: omitting this for now
# - the PR status changed (passing or failing to build)
#
# The most elegant design would be using sum types, i.e. encoding the data for
# each variant directly within the enum.
# As Python does not have these, we use a dictionary of extra data.
class PRChange(Enum):
    LabelAdded = auto()
    """A new label got added"""

    LabelRemoved = auto()
    """An existing label got removed"""

    LabelAddedRemoved = auto()
    '''A set of labels was added, and some set of labels was removed
    Note that a given label can be added and removed at the same time'''

    MarkedDraft = auto()
    """This PR was marked as draft"""
    MarkedReady = auto()
    """This PR was marked as ready for review"""

    CIStatusChanged = auto()
    """This PR's CI state changed"""


# Something changed on this PR.
class Event(NamedTuple):
    time: datetime
    change: PRChange
    # Additional details about what changed.
    # For CIStatusChanged, this contains the new state.
    # For Label{Added,Removed}, this contains the name of the label added resp. removed.
    # For LabelsAddedRemoved, this contains two lists of the labels added resp. removed.
    extra: dict

    @staticmethod
    def add_label(time: datetime, name: str):
        return Event(time, PRChange.LabelAdded, {"name": name})

    @staticmethod
    def remove_label(time: datetime, name: str):
        return Event(time, PRChange.LabelRemoved, {"name": name})

    @staticmethod
    def add_remove_labels(time: datetime, added: List[str], removed: List[str]):
        return Event(time, PRChange.LabelAddedRemoved, {"added": added, "removed": removed})

    @staticmethod
    def draft(time: datetime):
        return Event(time, PRChange.MarkedDraft, {})

    @staticmethod
    def undraft(time: datetime):
        return Event(time, PRChange.MarkedReady, {})

    @staticmethod
    def update_ci_status(time: datetime, new: CIStatus):
        return Event(time, PRChange.CIStatusChanged, {"new_state": new})


# Update the current PR state in light of some change.
def update_state(current: PRState, ev: Event) -> PRState:
    #print(f"current state is {current}, incoming event is {ev}")
    if ev.change == PRChange.MarkedDraft:
        return PRState(current.labels, current.ci, True)
    elif ev.change == PRChange.MarkedReady:
        return PRState(current.labels, current.ci, False)
    elif ev.change == PRChange.CIStatusChanged:
        return PRState(current.labels, ev.extra["new_state"], current.draft)
    elif ev.change == PRChange.LabelAdded:
        # Depending on the label added, update the PR status.
        lname = ev.extra["name"]
        if lname in label_categorisation_rules:
            label_kind = label_categorisation_rules[lname]
            return PRState(current.labels + [label_kind], current.ci, current.draft)
        else:
            # Adding an irrelevant label does not change the PR status.
            if not lname.startswith("t-") and lname != "CI":
                print(f"found irrelevant label: {lname}")
            return current
    elif ev.change == PRChange.LabelRemoved:
        lname = ev.extra["name"]
        if lname in label_categorisation_rules:
            # NB: make sure to *copy* current.labels using [:], otherwise that state is also modified!
            new_labels = current.labels[:]
            new_labels.remove(label_categorisation_rules[lname])
            return PRState(new_labels, current.ci, current.draft)
        else:
            # Removing an irrelevant label does not change the PR status.
            return current
    elif ev.change == PRChange.LabelAddedRemoved:
        added = ev.extra["added"]
        removed = ev.extra["removed"]
        # Remove any label which is both added and removed, and filter out irrelevant labels.
        both = set(added) & set(removed)
        added = [l for l in added if l in label_categorisation_rules and l not in both]
        removed = [l for l in removed if l in label_categorisation_rules and l not in both]
        # Any remaining labels to be removed should exist.
        new_labels = current.labels[:]
        for r in removed:
            new_labels.remove(label_categorisation_rules[r])
        return PRState(new_labels + [label_categorisation_rules[l] for l in added], current.ci, current.draft)
    else:
        print(f"unhandled event variant {ev.change}")
        assert False


# Determine the evolution of this PR's state over time.
# Return a list of pairs (timestamp, s), where this PR moved into state *s* at time *timestamp*.
# The first item corresponds to the PR's creation.
def determine_state_changes(
    creation_time: datetime, events: List[Event]
) -> List[Tuple[datetime, PRState]]:
    result = []
    #print(f"determine_state_changes: events passed are {events}")
    # XXX: we currently assume the PR was created in passing state, not in draft mode
    # and with no labels. (Otherwise, this function expects a "label change" event right at the beginning.)
    curr_state = PRState([], CIStatus.Pass, False)
    result.append((creation_time, curr_state))
    for event in events:
        #print(event.time)
        new_state = update_state(curr_state, event)
        result.append((event.time, new_state))
        curr_state = new_state
        #print(f"appended state is {result}")
    #print(f"determine_state_changes: result is {result}")
    return result


# Determine the evolution of this PR's status over time.
# Return a list of pairs (timestamp, s), where this PR moved into status *s* at time *timestamp*.
# The first item corresponds to the PR's creation.
def determine_status_changes(
    creation_time: datetime, events: List[Event]
) -> List[Tuple[datetime, PRStatus]]:
    evolution = determine_state_changes(creation_time, events)
    #print(f"state changes are {evolution}")
    res = []
    for time, state in evolution:
        res.append((time, determine_PR_status(time, state)))
    return res


########### Overall computation #########


def total_time_in_status(creation_time: datetime, now: datetime, events: List[Event], status: PRStatus) -> relativedelta:
    '''Determine the total amount of time this PR was in a given status,
    from its creation to the current time.'''
    total = relativedelta(days=0)
    evolution_status = determine_status_changes(creation_time, events)
    # The PR creation should be the first event in `evolution_status`.
    assert len(evolution_status) == len(events) + 1
    for i in range(len(evolution_status) - 1):
        (old_time, old_status) = evolution_status[i]
        (new_time, _new_status) = evolution_status[i + 1]
        if old_status == status:
            total += new_time - old_time
    (last, last_status) = evolution_status[-1]
    if last_status == status:
        total += now - last
    return total


# Determine the total amount of time this PR was awaiting review.
#
# FUTURE ideas for tweaking this reporting:
#  - ignore short intervals of merge conflicts, say less than a day?
#  - ignore short intervals of CI running (if successful before and after)?
def total_queue_time(creation_time: datetime, now: datetime, events: List[Event]) -> relativedelta:
    return total_time_in_status(creation_time, now, events, PRStatus.AwaitingReview)


# FUTURE: this could be exposed to the dashboard using the following API
# better_updated_at(number: int, data) -> relativedelta
# return the total time since this PR's last status change
def last_status_update(creation_time: datetime, now: datetime, events: List[Event]) -> relativedelta:
    '''Compute the total time since this PR's state changed last.'''
    # FUTURE: should this ignore short-lived merge conflicts? for now, it does not
    evolution_status = determine_status_changes(creation_time, events)
    # The PR creation should be the first event in `evolution_status`.
    assert len(evolution_status) == len(events) + 1
    last : datetime = evolution_status[-1][0]
    return relativedelta(last, now)


# UX for the generated dashboards: expose both total time and current time in the current state
# review time for the queue, "merge"/"delegated" for the stale "XY" dashboard; "merge conflict" for the merge conflict list
# allow filtering by both the "current streak" and the "total time" in this status


######### Some basic unit tests ##########

# Helper methods to reduce boilerplate


def april(n: int) -> datetime:
    return datetime(2024, 4, n)


def sep(n: int) -> datetime:
    return datetime(2024, 9, n)


# These tests are just some basic smoketests and not exhaustive.
def test_determine_state_changes() -> None:
    def check(events: List[Event], expected: PRState) -> None:
        compute = determine_state_changes(datetime(2024, 7, 15), events)
        actual = compute[-1][1]
        assert expected == actual, f"expected PR state {expected} from events {events}, got {actual}"
    check([], PRState([], CIStatus.Pass, False))
    dummy = datetime(2024, 7, 2)
    # Drafting or undrafting; changing CI status.
    check([Event.draft(dummy)], PRState([], CIStatus.Pass, True))
    check([Event.draft(dummy), Event.undraft(dummy)], PRState([], CIStatus.Pass, False))
    # Additional "undraft" or "draft" events are ignored.
    check([Event.undraft(dummy)], PRState([], CIStatus.Pass, False))
    check([Event.undraft(dummy), Event.undraft(dummy), Event.draft(dummy)], PRState([], CIStatus.Pass, True))
    check([Event.undraft(dummy), Event.draft(dummy), Event.draft(dummy)], PRState([], CIStatus.Pass, True))
    # Updating the CI status.
    check([Event.update_ci_status(dummy, CIStatus.Running)], PRState([], CIStatus.Running, False))
    check([Event.update_ci_status(dummy, CIStatus.Fail)], PRState([], CIStatus.Fail, False))
    check([Event.update_ci_status(dummy, CIStatus.Pass)], PRState([], CIStatus.Pass, False))
    check([Event.update_ci_status(dummy, CIStatus.Pass), Event.update_ci_status(dummy, CIStatus.Fail)], PRState([], CIStatus.Fail, False))
    check([Event.update_ci_status(dummy, CIStatus.Pass), Event.draft(dummy), Event.update_ci_status(dummy, CIStatus.Running), Event.undraft(dummy)], PRState([], CIStatus.Running, False))

    # Adding and removing labels.
    check([Event.add_label(dummy, "WIP")], PRState.with_labels([LabelKind.WIP]))
    check([Event.add_label(dummy, "awaiting-author")], PRState.with_labels([LabelKind.Author]))
    # Non-relevant labels are not recorded here.
    check([Event.add_label(dummy, "t-data")], PRState.with_labels([]))
    check([Event.add_label(dummy, "t-data"), Event.add_label(dummy, "WIP")], PRState.with_labels([LabelKind.WIP]))
    check([Event.add_label(dummy, "t-data"), Event.add_label(dummy, "WIP"), Event.remove_label(dummy, "t-data")], PRState.with_labels([LabelKind.WIP]))
    # Adding two labels.
    check([Event.add_label(dummy, "awaiting-author")], PRState([LabelKind.Author], CIStatus.Pass, False))
    check([Event.add_label(dummy, "awaiting-author"), Event.add_label(dummy, "WIP")], PRState.with_labels([LabelKind.Author, LabelKind.WIP]))
    check([Event.add_label(dummy, "awaiting-author"), Event.remove_label(dummy, "awaiting-author")], PRState.with_labels([]))
    check([Event.add_label(dummy, "awaiting-author"), Event.remove_label(dummy, "awaiting-author"), Event.add_label(dummy, "awaiting-zulip")], PRState.with_labels([LabelKind.Decision]))
    # TODO: better tests for add-remove
    # - equivalent to individual additions; with irrelevant labels; same for removal
    # - adding and removing same label is a no-op
    # - test that intermediate states are - no errors and - no contradictory states
    #   => need to test intermediate ones -> need the full sequence of states to test?
    check([Event.add_remove_labels(dummy, ["WIP"], ["WIP"])], PRState.with_labels([]))


def test_determine_status() -> None:
    # NB: this only tests the new handling of awaiting-review status.
    default_date = datetime(2024, 8, 1)
    def check(labels: List[LabelKind], expected: PRStatus) -> None:
        state = PRState.with_labels(labels)
        actual = determine_PR_status(default_date, state)
        assert expected == actual, f"expected PR status {expected} from labels {labels}, got {actual}"
    # This version takes a PR state instead.
    def check2(state: PRState, expected: PRStatus) -> None:
        actual = determine_PR_status(default_date, state)
        assert expected == actual, f"expected PR status {expected} from state {state}, got {actual}"
    # Check if the PR status on a given list of labels in one of several allowed values.
    # If successful, returns the actual PR status computed.
    def check_flexible(labels: List[LabelKind], allowed: List[PRStatus]) -> PRStatus:
        state = PRState(labels, CIStatus.Pass, False)
        actual = determine_PR_status(default_date, state)
        assert actual in allowed, f"expected PR status in {allowed} from labels {labels}, got {actual}"
        return actual

    # Tests for handling draft and CI state.
    # These take precedence over any other labels.
    check2(PRState([], CIStatus.Pass, True), PRStatus.NotReady)
    check2(PRState([], CIStatus.Fail, False), PRStatus.NotReady)
    check2(PRState([], CIStatus.Fail, True), PRStatus.NotReady)
    # Running CI is treated as "passing" for the purposes of our classification.
    check2(PRState([], CIStatus.Running, False), PRStatus.AwaitingReview)
    check2(PRState([LabelKind.WIP], CIStatus.Fail, False), PRStatus.NotReady)
    check2(PRState([LabelKind.MergeConflict], CIStatus.Fail, False), PRStatus.NotReady)

    # All label kinds we distinguish.
    ALL = LabelKind._member_map_.values()
    # For each combination of labels, the resulting PR status is either contradictory
    # or the status associated to some label.
    # The order of adding labels does not matter.
    check([], PRStatus.AwaitingReview)
    check([LabelKind.Other], PRStatus.AwaitingReview)
    check([LabelKind.Other, LabelKind.Other], PRStatus.AwaitingReview)
    check([LabelKind.Other, LabelKind.Other, LabelKind.Other], PRStatus.AwaitingReview)
    for a in ALL:
        if a != LabelKind.Other:
            check([a], label_to_prstatus(a))
        for b in ALL:
            statusses = [label_to_prstatus(l) for l in [a, b] if l != LabelKind.Other]
            # The "other" kind has no associated PR state: continue if all labels are "other"
            if not statusses:
                continue
            actual = check_flexible([a, b], statusses + [PRStatus.Contradictory])
            check([b, a], actual)
            result_ab = actual
            for c in ALL:
                # Adding further labels to some contradictory status remains contradictory.
                if result_ab == PRStatus.Contradictory:
                    check([a, b, c], PRStatus.Contradictory)
                else:
                    statusses = [label_to_prstatus(l) for l in [a, b, c] if l != LabelKind.Other]
                    if not statusses:
                        continue
                    actual = check_flexible([a, b, c], statusses + [PRStatus.Contradictory])
                    check([a, c, b], actual)
                    check([b, a, c], actual)
                    check([b, c, a], actual)
                    check([c, a, b], actual)
                    check([c, b, a], actual)
    # One specific sanity check, which fails in the previous implementation.
    check([LabelKind.Blocked, LabelKind.Review], PRStatus.Blocked)
    check([LabelKind.Review, LabelKind.Blocked], PRStatus.Blocked)


def smoketest() -> None:
    def check_basic(created: datetime, now:datetime, events: List[Event], expected: relativedelta) -> None:
        wait = total_queue_time(created, now, events)
        assert wait == expected, f"basic test failed: expected total time of {expected} in review, obtained {wait} instead"

    # these pass and behave well
    check_basic(sep(1), sep(10), [Event.add_label(sep(1), 'blocked-by-other-PR')], relativedelta(days=0))
    check_basic(sep(1), sep(10), [Event.add_label(sep(1), 'blocked-by-other-PR'), Event.add_label(sep(6), 'merge-conflict')], relativedelta(days=0))

    # adding and removing a label yields a BUG: all intermediate lists of labels are empty
    # fixed now, wohoo!
    check_basic(sep(1), sep(10), [Event.add_label(sep(1), 'blocked-by-other-PR'), Event.remove_label(sep(6), "blocked-by-other-PR")], relativedelta(days=4))
    # the add_label afterwards was and is fine
    check_basic(sep(1), sep(10), [Event.add_label(sep(1), 'blocked-by-other-PR'), Event.remove_label(sep(6), "blocked-by-other-PR"), Event.add_label(sep(8), "WIP")], relativedelta(days=2))

    # trying a variant
    check_basic(sep(1), sep(20), [Event.add_label(sep(1), 'blocked-by-other-PR'), Event.remove_label(sep(8), 'blocked-by-other-PR'), Event.add_label(sep(10), 'WIP')], relativedelta(days=2))
    # current failure, minimized
    check_basic(sep(1), sep(10), [Event.add_label(sep(1), 'blocked-by-other-PR'), Event.remove_label(sep(8), 'blocked-by-other-PR')], relativedelta(days=2))

    # Doing nothing in April: not ready for review. In September, it is!
    check_basic(april(1), april(3), [], relativedelta(days=0))
    check_basic(sep(1), sep(3), [], relativedelta(days=2))
    # Applying an irrelevant label.
    check_basic(sep(1), sep(5), [Event.add_label(sep(1), "CI")], relativedelta(days=4))
    # Removing it again.
    check_basic(
        sep(1), sep(12),
        [Event.add_label(sep(1), "CI"), Event.remove_label(sep(3), "CI")],
        relativedelta(days=11),
    )

    # After September 8th, this PR is in WIP status -> only seven days in review.
    check_basic(sep(1), sep(10), [Event.add_label(sep(1), 'CI'), Event.remove_label(sep(3), 'CI'), Event.add_label(sep(8), 'WIP')], relativedelta(days=7))

    # A PR getting blocked.
    check_basic(sep(1), sep(10), [Event.add_label(sep(1), 'blocked-by-other-PR'), Event.add_label(sep(8), 'easy')], relativedelta(days=0))
    # A PR getting unblocked again.
    check_basic(sep(1), sep(10), [Event.add_label(sep(1), 'blocked-by-other-PR'), Event.remove_label(sep(8), 'blocked-by-other-PR')], relativedelta(days=2))

    # xxx Applying two irrelevant labels.
    # then removing one...
    # more complex tests to come!


test_determine_state_changes()
test_determine_status()
smoketest()
