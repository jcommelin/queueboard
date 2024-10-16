#!/usr/bin/env python3

"""
This script looks at all files in the `data` directory and creates a JSON file,
containing information about all PRs described in that directory. For each PR,
we list
- whether it's in draft stage (as opposed being marked as "ready for review")
- whether mathlib's CI passes on it
- the branch it is based on (usually "master")
"""

import json
import os
import sys
from datetime import datetime, timezone
from typing import List


# Parse the JSON file 'name' for PR 'number'. Returned the parsed file if successful,
# and an error message describing what went wrong otherwise.
def parse_json_file(name: str, pr_number: str) -> dict | str:
    data = None
    with open(name, "r") as fi:
        try:
            data = json.load(fi)
        except json.decoder.JSONDecodeError:
            return f"error: the pr_info file for PR {pr_number} is invalid JSON, ignoring"
    if "errors" in data:
        return f"warning: the data for PR {pr_number} is incomplete, ignoring"
    elif "data" not in data:
        return f"warning: the data for PR {pr_number} is incomplete (perhaps a time out downloading it), ignoring"
    return data


# commit_nodes is an array of all checks for all the commits
def determine_ci_status(number, CI_check_nodes: dict) -> bool:
    # We consider CI to be passing if no job fails, and every job succeeds
    # or is skipped. (In the future, we may exclude inessential runs.)
    for r in CI_check_nodes:
        # Ignore bors runs: these don't contain status information (and are not interesting for us).
        if "context" in r:
            continue
        if r["conclusion"] in ["FAILURE", "CANCELLED"]:
            # Future: exclude "inessential" runs?
            return False
        elif r["conclusion"] in ["SUCCESS", "SKIPPED", "NEUTRAL"]:
            continue
        elif r["conclusion"] is None and r["status"] in ["IN_PROGRESS", "QUEUED"]:
            continue # TODO!
        else:
            print(f'CI run \"{r["name"]}\" for PR {number} has interesting data: {r}"')
    return True


def get_aggregate_data(pr_data: dict, _only_basic_info: bool) -> dict:
    inner = pr_data["data"]["repository"]["pullRequest"]
    number = inner["number"]
    head_repo = inner["headRepositoryOwner"]
    base_branch = inner["baseRefName"]
    is_draft = inner["isDraft"]
    state = inner["state"].lower()
    last_updated = inner["updatedAt"]
    # We assume the author URL is determined by the github handle: in practice, it is.
    author = inner["author"]["login"]
    title = inner["title"]
    additions = inner["additions"]
    deletions = inner["deletions"]
    # Number of files modified by this PR.
    files = len(inner["files"]["nodes"])
    # Names of all labels applied to this PR: missing the background colour!
    labels = [lab["name"] for lab in inner["labels"]["nodes"]]
    assignees = [ass["login"] for ass in inner["assignees"]["nodes"]]
    CI_passes = False
    # Get information about the latest CI run. We just look at the "summary job".
    CI_passes = determine_ci_status(number, inner["statusCheckRollup"]["contexts"]["nodes"])
    # NB. When adding future fields, pay attention to whether the 'basic' info files
    # also contain this information --- otherwise, it is fine to omit it!
    return {
        "number": number,
        "is_draft": is_draft,
        "CI_passes": CI_passes,
        "head_repo": head_repo,
        "base_branch": base_branch,
        "state": state,
        "last_updated": last_updated,
        "author": author,
        "title": title,
        "additions": additions,
        "deletions": deletions,
        "num_files": files,
        "label_names": labels,
        "assignees": assignees,
    }


def main():
    output = dict()
    updated = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    output["timestamp"] = updated
    pr_data = []
    # A few files are known to have broken detailed information.
    # They can be found in the file "stubborn_prs.txt".
    known_erronerous: List[str] = []
    with open("stubborn_prs.txt", "r") as error_prs:
        for line in error_prs:
            if not line.startswith("--"):
                known_erronerous.append(line.rstrip())
    # Read all pr info files in the data directory.
    pr_dirs: List[str] = sorted(os.listdir("data"))
    for pr_dir in pr_dirs:
        only_basic_info = "basic" in pr_dir
        pr_number = pr_dir.removesuffix("-basic")
        filename = f"data/{pr_dir}/basic_pr_info.json" if only_basic_info else f"data/{pr_dir}/pr_info.json"
        match parse_json_file(filename, pr_number):
            case str(err):
                if pr_number not in known_erronerous:
                    print(f"attention: found an unexpected error!\n{err}", file=sys.stderr)
            case dict(data):
                if (pr_number in known_erronerous) and not only_basic_info:
                    print(f"warning: PR {pr_number} had fine data, but was listed as erronerous: please remove it from that list", file=sys.stderr)
                pr_data.append(get_aggregate_data(data, only_basic_info))
    output["pr_statusses"] = pr_data
    with open("processed_data/aggregate_pr_data.json", "w") as f:
        print(json.dumps(output, indent=4), file=f)


main()
