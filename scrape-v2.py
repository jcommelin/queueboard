import json
import sys
from datetime import datetime
from typing import List, Tuple

from better_updated import Event, PRStatus, format_delta, last_status_update, total_queue_time

# Second version of my scraping script: read in a file <number>.json
# which is meant to contain all the information I need.


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 scrape.py <pr-number>")
        sys.exit(1)

    number = sys.argv[1]
    # Assume a file called <number>.json exists.
    with open(f'{number}.json', 'r', encoding='utf-8') as f:
        data = json.load(f)
        analyse(number, data)


# input of the form "2024-04-29T18:53:51Z"
# The "Z" suffix means it's a time in UTC.
def parse_datetime(rep: str) -> datetime:
    return datetime.strptime(rep, "%Y-%m-%dT%H:%M:%SZ")

assert parse_datetime("2024-04-29T18:53:51Z") == datetime(2024, 4, 29, 18, 53, 51)


# Canonicalise a label name to its current one.
# Github's events data uses the label names at that time.
def canonicalise_label(name: str) -> str:
    return "awaiting-review-DONT-USE" if name == "awaiting-review" else name


def parse_data(data: dict) -> Tuple[datetime, List[Event]]:
    creation_time = parse_datetime(data["data"]["repository"]["pullRequest"]["createdAt"])
    events = []
    events_data = data["data"]["repository"]["pullRequest"]["timelineItems"]["nodes"]
    for event in events_data:
        type = event["__typename"]
        if type == "LabeledEvent":
            name = event["label"]["name"]
            ev = Event.add_label(parse_datetime(event["createdAt"]), canonicalise_label(name))
            events.append(ev)
        elif type == "UnlabeledEvent":
            name = event["label"]["name"]
            ev = Event.remove_label(parse_datetime(event["createdAt"]), canonicalise_label(name))
            events.append(ev)
        elif type == "ReadyForReviewEvent":
            ev = Event.undraft(parse_datetime(event["createdAt"]))
            events.append(ev)
        elif type == "ConvertToDraftEvent":
            events.append(Event.draft(parse_datetime(event["createdAt"])))
        elif type in ["ClosedEvent", "BaseRefChangedEvent", "HeadRefForcePushedEvent", "HeadRefDeletedEvent", "PullRequestCommit", "IssueComment", "PullRequestReview", "RenamedTitleEvent", "AssignedEvent", "ReferencedEvent", "CrossReferencedEvent", "MentionedEvent", "SubscribedEvent", "UnsubscribedEvent"]:
            pass
        else:
            print(f"unhandled event kind: {type}")
    return (creation_time, events)


# Determine a rough estimate how long PR 'number' was awaiting review.
# 'data' is a JSON object containing all information about a PR.
# Limitations:
# - assumes this PR is open/does not take closing the PR into account.
# - assumes CI always passed/ignores failing CI
def analyse(number: int, data: dict) -> None:
    (createdAt, events) = parse_data(data)
    total = total_queue_time(createdAt, datetime.now(), events)
    (current_status, updated) = last_status_update(createdAt, datetime.now(), events)
    current = {
        PRStatus.AwaitingBors: "is awaiting bors",
        PRStatus.AwaitingAuthor: "is awaiting author",
        PRStatus.AwaitingReview: "is awaiting review",
        PRStatus.AwaitingDecision: "is awaiting a zulip discussion",
        PRStatus.MergeConflict: "has a merge conflict",
        PRStatus.Delegated: "is delegated",
        PRStatus.HelpWanted: "is looking for help",
        PRStatus.Blocked: "is blocked on another PR",
        PRStatus.NotReady: "is labelled WIP or marked draft",
        PRStatus.Contradictory: "has contradictory labels",
    }
    print(f"PR {number} was in review for {format_delta(total)} overall. It was last updated {format_delta(updated)} ago and {current[current_status]}")


main()