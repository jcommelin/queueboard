#!/usr/bin/env bash

# Surface errors in this script to CI, so they get noticed.
# See e.g. http://redsymbol.net/articles/unofficial-bash-strict-mode/ for explanation.
set -e -u -o pipefail

number=${1:-}
if [[ -z "$number" ]]; then
    echo "invalid input, exiting: missing PR number as argument"
    echo "use as: $0 <pr-number>"
    exit 1
fi

# Ask the user to download this page's text themselves.
echo "You'd like to analyse PR $number: you have to perform the first step yourself"
echo "Opening the PR page in the default browser..."
xdg-open https://github.com/leanprover-community/mathlib4/issues/$number
read -p "Make sure there are no 'hidden items': expand them if necessary; press enter to continue"
read -p "Select everything (e.g. using Ctrl-A), copy it (e.g. using Ctrl-C) and paste it into a plain text file. Save this file as pr-text-$number.txt. Press enter if you have done this."

cat pr-$number-text.txt | rg "marked this pull request|label|commented" > interesting-pr-$number.txt
python3 scrape.py $number
