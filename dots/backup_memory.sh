#!/usr/bin/env bash
# Copy the crew's memory (runs/) to the dots-memory folder of a git checkout and push it, so a
# container reset loses nothing. Refuses if anything that looks like an Alpaca key is in it.
#   ./backup_memory.sh /home/user/static83 claude/serene-sagan-3euibb
set -euo pipefail
repo=${1:?checkout path}; branch=${2:?branch}
here=$(cd "$(dirname "$0")" && pwd)
key=$(grep -E '^ALPACA_API_KEY=' "$here/.env" 2>/dev/null | cut -d= -f2 | cut -c1-8 || true)
secret=$(grep -E '^ALPACA_SECRET_KEY=' "$here/.env" 2>/dev/null | cut -d= -f2 | cut -c1-8 || true)
rm -rf "$repo/dots-memory" && mkdir -p "$repo/dots-memory"
cp -r "$here/runs/." "$repo/dots-memory/"
for k in "$key" "$secret"; do
  if [ -n "$k" ] && grep -rlF "$k" "$repo/dots-memory" >/dev/null; then
    rm -rf "$repo/dots-memory"; echo "key material found in runs/, backup aborted"; exit 1
  fi
done
cd "$repo"
git add -A dots-memory
if git diff --cached --quiet; then echo "memory unchanged"; exit 0; fi
git -c user.name="Andrew Stuart" -c user.email="andrewfstuart@me.com" commit -q -m "dots-memory: back up casebook, trades and lessons $(TZ=America/New_York date +%Y-%m-%d\ %H:%M) ET"
for wait in 2 4 8 16; do git push -q origin "$branch" && { echo "memory backed up"; exit 0; }; sleep $wait; done
echo "push failed"; exit 1
