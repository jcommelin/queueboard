import sys
from datetime import datetime
from typing import List, Tuple

from better_updated import (Event, PRChange, last_status_update, total_queue_time)
from dashboard import format_delta


# input of the form "April 30, 2024 14:07" or "April 30, 2024"
def parse_date(rep: str) -> datetime:
    rep = rep.strip()
    if ":" in rep:
        return datetime.strptime(rep, "%B %d, %Y %H:%M")
    else:
        return datetime.strptime(rep, "%b %d, %Y")


# Analyse a hackily scraped PR page to extract the github events.
# FIXME: this currently only provides daily granularity; perhaps this is good enough
def hacky_scrape(lines: List[str]) -> Tuple[datetime, List[Event]]:
    events = []
    # Assumes this file only contains interesting lines:
    # - for comments (we only care about the first one; this is when the PR was opened)
    # - changing the PR status
    # - adding or removing labels
    creation = None
    for line in lines:
        # Kind 1: changing the PR status, looks like
        # "@EtienneC30 EtienneC30 marked this pull request as ready for review April 30, 2024 14:07"
        if "marked this pull request as " in line:
            if "ready for review" in line:
                idx = line.index("ready for review") + len("ready for review ")
                events.append(Event(parse_date(line[idx:]), PRChange.MarkedReady))
            elif "draft" in line:
                idx = line.index("draft") + len("draft ")
                events.append(Event(parse_date(line[idx:]), PRChange.MarkedDraft))
            else:
                print(f"unexpected line: {line}")
                assert False
        # Kind 2: comments; we only look at the first one (for the creation date)
        elif "commented " in line:
            if creation is None:
                idx = line.index("commented ") + len("commented ")
                datepart = line[idx:].rstrip()
                if ' •' in datepart:
                    datepart = datepart[0:datepart.index(' •')]
                creation = parse_date(datepart)
        # Kind 3: adding or removing labels: there are three forms of these.
        elif "added the " in line and "label" in line:
            # @leanprover-community-mathlib4-bot leanprover-community-mathlib4-bot added the merge-conflict label May 13, 2024
            idx = line.index("added the ") + len("added the ")
            idx2 = line.index(" label")
            between = line[:idx2][idx:]
            name = between.strip()
            assert " " not in name
            name = name.replace("awaiting-review", "awaiting-review-DONT-USE")
            date = parse_date(line[idx2 + len(" label"):])
            events.append(Event.add_label(date, name))
        elif "removed the " in line and "label" in line:
            # @leanprover-community-mathlib4-bot leanprover-community-mathlib4-bot removed the merge-conflict label May 13, 2024
            idx = line.index("removed the ") + len("removed the ")
            idx2 = line.index(" label")
            between = line[:idx2][idx:]
            name = between.strip()
            assert " " not in name
            name = name.replace("awaiting-review", "awaiting-review-DONT-USE")
            date = parse_date(line[idx2 + len(" label"):])
            events.append(Event.remove_label(date, name))
        elif "labels " in line:
            # Three forms: only addition, only removals and mixed.
            (add, remove) = ("added " in line, "removed " in line)
            if add and remove:
                # @EtienneC30 EtienneC30 added awaiting-review and removed WIP labels Apr 30, 2024
                idx = line.index("added") + len("added ")
                idx2 = line.index(" and removed")
                between = line[:idx2][idx:]
                names_added = between.strip().split(" ")
                if "awaiting-review" in names_added:
                    names_added.remove("awaiting-review")
                    names_added.append("awaiting-review-DONT-USE")
                idx3 = line.index("labels ")
                between = line[:idx3][idx2 + len(" and removed "):]
                names_removed = between.strip().split(" ")
                if "awaiting-review" in names_removed:
                    names_removed.remove("awaiting-review")
                    names_removed.append("awaiting-review-DONT-USE")
                idx4 = idx3 + len("labels ")
                date = parse_date(line[idx4:])
                events.append(Event.add_remove_labels(date, names_added, names_removed))
            elif add and not remove:
                # @EtienneC30 EtienneC30 added WIP t-measure-probability labels Apr 29, 2024
                idx = line.index("added") + len("added ")
                idx2 = line.index("labels")
                between = line[:idx2][idx:]
                names = between.strip().split(" ")
                if "awaiting-review" in names:
                    names.remove("awaiting-review")
                    names.append("awaiting-review-DONT-USE")
                date = parse_date(line[idx2 + len("labels "):])
                for l in names:
                    events.append(Event.add_label(date, l))
            elif not add and remove:
                # @EtienneC30 EtienneC30 removed WIP t-measure-probability labels Apr 29, 2024
                idx = line.index("removed ") + len("removed ")
                idx2 = line.index("labels")
                between = line[:idx2][idx:]
                names = between.strip().split(" ")
                if "awaiting-review" in names:
                    names.remove("awaiting-review")
                    names.append("awaiting-review-DONT-USE")
                date = parse_date(line[idx2 + len("labels "):])
                for l in names:
                    events.append(Event.remove_label(date, l))
        elif line.startswith("Label New Contributors"):
            pass  # False positive of a simple grep
        else:
            print(f"WARNING: encountered an unexpected line: {line}")
    return (creation, events)


# Determine a rough estimate how long PR 'number' was awaiting review.
# Limitations:
# - assumes this PR is open/does not take closing the PR into account.
# - assumes CI always passed/ignores failing CI
# - label edits are only processed with a daily granularity
def analyse(number: int) -> None:
    with open(f'interesting-pr-{number}.txt', 'r', encoding='utf-8') as f:
        (created, events) = hacky_scrape(f.readlines())
        total = total_queue_time(created, datetime.now(), events)
        updated = last_status_update(created, datetime.now(), events)
        totalstr = format_delta(total).replace(" ago", "")
        print(total)
        print(f"PR {number} was in review for overall {totalstr}; was last updated {format_delta(updated)} ago")


if len(sys.argv) < 2:
    print("Usage: python3 scrape.py <pr-number>")
    sys.exit(1)
analyse(sys.argv[1])
