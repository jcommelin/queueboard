# Overall architecture of this repository

This document describes the high-level architecture of the review dashboard. If you want to familiarize yourself with the code base, you are just in the right place!

**High-level overflow** This repository contains code and a github workflow to generate the mathlib review dashboard.
The high-level overview of the workflow is the following.
- step 1: query github's API for a list of relevant open pull requests
- step 2: generate a static webpage from this information
- step 3: publish this webpage using github pages
These steps are repeated regularly, using a cronjob. Currently (as of October 8, 2024), a workflow run takes about 5 minutes, and a new job starts eight minutes after the previous run. All in all, this means the data on the dashboard has a latency of 12--15 minutes.

**Relevant files**
`dashboard.sh` (a shell script) is the main entry point:
- it queries for the data above and creates a number of JSON files containing the relevant data
- it then calls `dashboard.py` (with the JSON files passed as explicit arguments) to create a dashboard.
That script, in turn, queries the `gh-mathlib-metadata` repository on github to retrieve detailed information about each relevant pull request. That information is "cached" on that repo (downloaded and updated automatically), hence avoiding additional calls to Github's API. (See cross-cutting concerns below.)

`dashboard.py` is where the core logic of creating the dashboard lives. It is a Python script, taking the JSON files from the previous step as input. It prints the HTML code for the dashboard page.

`.github/workflows/publish-dashboard.yml` defines the workflow updating the dashboard. It runs the above scripts to generate an up-to-date dashboard, and commits the updated HTML file on the `gh-pages` branch. That branch is deployed by github pages to create the webpage.

`style.css` contains all style rules for the generated webpage
`dashboard.html` is a redirect: the dashboard used to live there, but now lives at `index.html`

**classify_pr_state.py** contains logic to classify a pull request as ready for review, awaiting action by the author, blocked on another PR, etc. This is used to generate a statistics section on the dashboard. It is called directly by `dashboard.py`.




# Cross-cutting concerns

## Testing
There are several levels at which this project can be tested. Currently, there are no *automated* tests, but there is infrastructure for manual testing.

- `classify_pr_state.py` has unit tests: to run them, use e.g. `nose` (which will pick them up automatically), or uncomment all methods named `test_xxx` and run `python3 classify_pr_state.py`
- changes to just `dashboard.py` can be tested using the JSON files in `test`, as follows.
Run the following, *inside* the `test` directory:
`python3 ../dashboard.py all-nondraft-PRs.json all-draft-PRs.json queue.json ready-to-merge.json please-adopt.json new-contributor.json needs-merge.json needs-decision.json maintainer-merge.json help-wanted.json delegated.json automerge.json > ../expected.html`,
once (before the changes) to create a file `../expected.html`, and again afterwards for a file `../actual.html`.
You can then use `diff` to look for any changes to the generated output.
(It is important that the output file be in the top-level directory.)

## Limiting github API calls
Each github API call is expensive: github (understandably) adds rate limiting to each call to its API: successive calls can happen at most once per second. This imposes a natural lower bound on the execution time of this script.
In particular, each query for each dashboard takes one second: if easily possible/the data for a particular dashboard can be easily derived (e.g., filtered) from existing data, avoiding an API call and a separate JSON file is preferred.
