#!/usr/bin/env bash

# Surface errors in this script to CI, so they get noticed.
# See e.g. http://redsymbol.net/articles/unofficial-bash-strict-mode/ for explanation.
set -e -u -o pipefail

# Improved and more automated version of analyse.sh.
# Will re-download the information for every PR, so use with care.
# If you have the right file already, call "python3 scrape-v2.py <number> instead of this script."

number=${1:-}
if [[ -z "$number" ]]; then
    echo "invalid input, exiting: missing PR number as argument"
    echo "use as: $0 <pr-number>"
    exit 1
fi

./pr_info.sh $number > $number.json
python3 scrape-v2.py $number