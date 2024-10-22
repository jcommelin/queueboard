#!/usr/bin/env bash

# Download PRs which were manually marked as outdated or needed for re-download.

# Surface errors in this script to CI, so they get noticed.
# See e.g. http://redsymbol.net/articles/unofficial-bash-strict-mode/ for explanation.
set -e -u -o pipefail

# GitHub repository details
REPO="leanprover-community/mathlib4"
API_URL="https://api.github.com/repos/$REPO/pulls"

CURRENT_TIME=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

# Parse the list of all stubborn PRs. This is newline-separated,
# but for our purposes, that is fine.
stubborn_prs=$(cat stubborn_prs.txt | grep --invert-match "^-")

# Re-download data if missing. Take care to not ask for too much at once!
# FIXME: this is only somewhat robust --- improve this to ensure to avoid
# re-re-downloading in a loop!
for pr in $(cat "redownload.txt"); do
  echo "About to re-download PR $pr"
  if [[ $stubborn_prs == *$pr* ]]; then
    dir="data/$pr-basic"
    mkdir -p "$dir"
    ./basic_pr_info.sh "$pr" | jq '.' > "$dir/basic_pr_info.json"
    echo "$CURRENT_TIME" > "$dir/timestamp.txt"
  else
    dir="data/$pr"
    mkdir -p "$dir"
    # Run pr_info.sh and pr_reactions.sh and save the output.
    ./pr_info.sh "$pr" | jq '.' > "$dir/pr_info.json"
    ./pr_reactions.sh "$pr" | jq '.' > "$dir/pr_reactions.json"
    # Save the current timestamp.
    echo "$CURRENT_TIME" > "$dir/timestamp.txt"
  fi
done
echo "" > redownload.txt
echo "Successfully re-downloaded all planned PRs (if any)"

# In case there are PRs which got "missed" somehow, backfill
# data for up to one of them.
i=0
for pr in $(cat "missing_prs.txt"); do
  # Check if the directory exists
  if [ -d "data/$pr" ]; then
    echo "[skip] Data exists for #$pr: $CURRENT_TIME"
    continue
  fi
  echo "Attempting to backfill data for PR $pr"
  dir="data/$pr"
  mkdir -p "$dir"
  # Run pr_info.sh and pr_reactions.sh and save the output.
  ./pr_info.sh "$pr" | jq '.' > "$dir/pr_info.json"
  ./pr_reactions.sh "$pr" | jq '.' > "$dir/pr_reactions.json"
  # Save the current timestamp.
  echo "$CURRENT_TIME" > "$dir/timestamp.txt"
  i=$((i+1))
  if [ $i -eq 1 ]; then
    echo "Backfilled one PR successfully, exiting"
    break;
  fi
done

# Do the same for at most one stubborn PR.
j=0
for pr in $stubborn_prs; do
  dir="data/$pr-basic"
  # Check if the directory exists.
  if [ -d $dir ]; then
    echo "[skip] Data exists for 'stubborn' PR #$pr: $CURRENT_TIME"
    continue
  fi
  echo "Attempting to backfill data for 'stubborn' PR $pr"
  mkdir -p "$dir"
  ./basic_pr_info.sh "$pr" | jq '.' > "$dir/basic_pr_info.json"
  echo "$CURRENT_TIME" > "$dir/timestamp.txt"
  j=$((j+1))
  if [ $j -eq 1 ]; then
    echo "Backfilled one 'stubborn' PR successfully, exiting"
    break;
  fi
done
