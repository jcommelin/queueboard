#!/usr/bin/env bash

# Download key metadata for all open PRs from github, to compare them with
# the metadata in `data/`. The goal is to detect PRs whose saved data is
# not up to date with the real data on github.

# Surface errors in this script to CI, so they get noticed.
# See e.g. http://redsymbol.net/articles/unofficial-bash-strict-mode/ for explanation.
set -e -u -o pipefail

OWNER=leanprover-community
REPO=mathlib4

PR_NUMBER=$1

gh api graphql -f owner=$OWNER -f repo=$REPO -F query=@open_prs_with_metadata.graphql
