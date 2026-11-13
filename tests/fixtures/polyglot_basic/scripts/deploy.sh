#!/bin/sh
# Deploy the service to one host.
set -eu
TARGET_DIR=/srv/app
RETRIES=3

# Print usage and exit.
usage() {
  echo "usage: deploy.sh HOST" # } not a brace
}

function push_files {
  rsync -a . "$1:$TARGET_DIR"
}
