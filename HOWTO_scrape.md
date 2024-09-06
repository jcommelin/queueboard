# How to hackily scrape a github PR page
Run `analyse.sh 12519`, if 12519 is the pull request you wish to analyse.

This script will guide you through the necessary steps, namely the following:
1. [automatic] Load the page for the PR.
2. [manual] Make sure there are no hidden items. (Otherwise, expand them until done.)
3. [manual] Download the webpage as plain text: use ctrl-a, ctrl-c, ctrl-v to select the entire page (as text) and paste it into a text editor
This mangles formatting etc; that is part of the point: it allows parsing dates well enough (at the cost of reducing granularity to a day)
4. [manual] Save the source to a file, say `pr-12519-text.txt`.
5. [automatic] Extract any interesting lines from that file by running `cat pr-12519-text.txt | grep "marked this pull request|label|commented" > interesting-pr-12519.txt`.
6. [automatic] Analyse that file using the script `scrape.py`; this will output the total time of that PR on the review queue.

(I considered downloading the HTML source code in step 2 instead, but that is quite ugly and requires lots of parsing. The plain text is simpler. This code is a hack anyway.)