import json
import sys
from datetime import datetime
from typing import List, Tuple

from better_updated import Event, format_delta, last_status_update, total_queue_time

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

def parse_data(data: dict) -> Tuple[datetime, List[Event]]:
    creation_time = parse_datetime(data["data"]["repository"]["pullRequest"]["createdAt"])
    events = []
    events_data = data["data"]["repository"]["pullRequest"]["timelineItems"]["nodes"]
    for event in events_data:
        type = event["__typename"]
        if type in ["PullRequestCommit", "IssueComment", "PullRequestReview", "RenamedTitleEvent", "AssignedEvent", "CrossReferencedEvent"]:
            pass
        elif type == "LabeledEvent":
            ev = Event.add_label(parse_datetime(event["createdAt"]), event["label"]["name"])
            events.append(ev)
        elif type == "UnlabeledEvent":
            ev = Event.remove_label(parse_datetime(event["createdAt"]), event["label"]["name"])
            events.append(ev)
        elif type == "ReadyForReviewEvent":
            ev = Event.undraft(parse_datetime(event["createdAt"]))
            events.append(ev)
        # TODO: is there also an event for "marked draft"? there should be...
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
    updated = last_status_update(createdAt, datetime.now(), events)
    print(f"PR {number} was in review for overall {format_delta(total)}; was last updated {format_delta(updated)} ago")



main()