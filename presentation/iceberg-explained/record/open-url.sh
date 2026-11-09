#!/usr/bin/env bash
# BROWSER for the demo shell: the URL goes to the director's queue instead of a desktop browser.
echo "$1" >> "$(dirname "$0")/../recordings/open-url.txt"
