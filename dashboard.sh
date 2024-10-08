#!/usr/bin/env bash

# Surface errors in this script to CI, so they get noticed.
# See e.g. http://redsymbol.net/articles/unofficial-bash-strict-mode/ for explanation.
set -e -u -o pipefail

# The date and time, 24 hours ago, in the ISO8601 format
yesterday=$(date -u -d "24 hours ago" '+%Y-%m-%dT%H:%M:%SZ')
# The date and time, 7 days ago, in the ISO8601 format
aweekago=$(date -u -d "7 days ago" '+%Y-%m-%dT%H:%M:%SZ')

prepare_query () {
	echo "
query(\$endCursor: String) {
  search(query: \"repo:leanprover-community/mathlib4 $1\", type: ISSUE, first: 25, after: \$endCursor) {
    pageInfo {
      hasNextPage
      endCursor
    }
    nodes {
      ... on PullRequest {
        number
	url
	author { ... on User { login url } }
	title
        state
	updatedAt
        labels(first: 10, orderBy: {direction: DESC, field: CREATED_AT}) {
          nodes {
            name
	    color
	    url
          }
        }
      }
    }
  }
}
	";
}

# Query Github API for all pull requests that are on the #queue.
# So we want to list all pull requests that are
# - open, not draft
# - has status:success (which excludes failing or in-progress CI)
# - do not have any of the following labels: blocked-by-other-PR, merge-conflict, awaiting-CI, WIP, awaiting-author, awaiting-zulip, help-wanted, please-adopt delegated, auto-merge-after-CI, ready-to-merge
queue_labels_but_merge="-label:blocked-by-other-PR -label:awaiting-CI -label:awaiting-author -label:awaiting-zulip -label:please-adopt -label:help-wanted -label:WIP -label:delegated -label:auto-merge-after-CI -label:ready-to-merge"
QUERY_QUEUE=$(prepare_query "sort:updated-asc is:pr state:open -is:draft status:success base:master $queue_labels_but_merge -label:merge-conflict")
gh api graphql --paginate --slurp -f query="$QUERY_QUEUE" | jq '{"output": .}' > queue.json

# Query Github API for all pull requests with a merge conflict, that would be otherwise ready for review.
QUERY_QUEUE_BUT_MERGE_CONFLICT=$(prepare_query "sort:updated-asc is:pr state:open -is:draft status:success base:master $queue_labels_but_merge label:merge-conflict")
gh api graphql --paginate --slurp -f query="$QUERY_QUEUE_BUT_MERGE_CONFLICT" | jq '{"output": .}' > needs-merge.json

# Query Github API for all pull requests that are labeled `ready-to-merge` and have not been updated in 24 hours.
QUERY_READYTOMERGE=$(prepare_query "sort:updated-asc is:pr state:open label:ready-to-merge updated:<$yesterday")
gh api graphql --paginate --slurp -f query="$QUERY_READYTOMERGE" | jq '{"output": . }' > ready-to-merge.json
# Query Github API for all pull requests that are labeled `auto-merge-after-CI` and have not been updated in 24 hours.
QUERY_AUTOMERGE=$(prepare_query "sort:updated-asc is:pr state:open label:auto-merge-after-CI updated:<$yesterday")
gh api graphql --paginate --slurp -f query="$QUERY_AUTOMERGE" | jq '{"output": . }' > automerge.json

# Query Github API for all pull requests that are labeled `maintainer-merge` but not `ready-to-merge` and have not been updated in 24 hours.
QUERY_MAINTAINERMERGE=$(prepare_query "sort:updated-asc is:pr state:open label:maintainer-merge -label:ready-to-merge updated:<$yesterday")
gh api graphql --paginate --slurp -f query="$QUERY_MAINTAINERMERGE" | jq '{"output": .}' > maintainer-merge.json

# Query Github API for all ready pull requests that are labeled `awaiting-zulip`.
QUERY_NEEDS_DECISION=$(prepare_query "sort:updated-asc is:pr -is:draft state:open label:awaiting-zulip")
gh api graphql --paginate --slurp -f query="$QUERY_NEEDS_DECISION" | jq '{"output": .}' > needs-decision.json

# Query Github API for all pull requests that are labeled `delegated` and have not been updated in 24 hours.
QUERY_DELEGATED=$(prepare_query "sort:updated-asc is:pr state:open label:delegated updated:<$yesterday")
gh api graphql --paginate --slurp -f query="$QUERY_DELEGATED" | jq '{"output": .}' > delegated.json

# Query Github API for all pull requests that are labeled `new-contributor` and have not been updated in seven days.
# Sadly, this includes all PRs which are in the review queue...
QUERY_NEWCONTRIBUTOR=$(prepare_query "sort:updated-asc is:pr state:open label:new-contributor updated:<$aweekago")
gh api graphql --paginate --slurp -f query="$QUERY_NEWCONTRIBUTOR" | jq '{"output": .}' > new-contributor.json

# Query Github API for all pull requests that are labeled `help-wanted`.
QUERY_HELP_WANTED=$(prepare_query "sort:updated-asc is:pr state:open label:help-wanted")
gh api graphql --paginate --slurp -f query="$QUERY_HELP_WANTED" |	jq '{"output": .}' > help-wanted.json
# Query Github API for all pull requests that are labeled `please-adopt`.
QUERY_PLEASE_ADOPT=$(prepare_query "sort:updated-asc is:pr state:open label:please-adopt")
gh api graphql --paginate --slurp -f query="$QUERY_PLEASE_ADOPT" | jq '{"output": .}' > please-adopt.json

QUERY_OTHER_BASE_BRANCH=$(prepare_query "sort:updated-asc is:pr state:open -base:master -is:draft")
gh api graphql --paginate --slurp -f query="$QUERY_OTHER_BASE_BRANCH" | jq '{"output": .}' > other-base-branch.json

# Query Github API for all open pull requests which are marked as ready for review
QUERY_NONDRAFT=$(prepare_query 'sort:updated-asc is:pr -is:draft state:open')
gh api graphql --paginate --slurp -f query="$QUERY_NONDRAFT" | jq '{"output": .}' > all-nondraft-PRs.json

# Query Github API for all open pull requests which are in draft stage
QUERY_DRAFT=$(prepare_query 'sort:updated-asc is:pr is:draft state:open')
gh api graphql --paginate --slurp -f query="$QUERY_DRAFT" | jq '{"output": .}' > all-draft-PRs.json

# List of JSON files: their order does not matter for the generated output.
# NB: we purposefully do not add 'all-nondraft-PRs' or 'all-draft-PRs' to this list,
# as they do not correspond to a dashboard.
json_files=("queue.json" "needs-merge.json" "ready-to-merge.json" "automerge.json" "maintainer-merge.json" "needs-decision.json" "delegated.json" "new-contributor.json" "help-wanted.json" "please-adopt.json" "other-base-branch.json")

python3 ./dashboard.py "all-nondraft-PRs.json" "all-draft-PRs.json" ${json_files[*]} > ./index.html

rm *.json

