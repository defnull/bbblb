#!/bin/sh
. /app/.venv/bin/activate

if [ -n "$BBBLB_PATH_DATA/recordings" ]; then
    mkdir -p "$BBBLB_PATH_DATA/recordings"
fi

chown bbblb "$BBBLB_PATH_DATA" "$BBBLB_PATH_DATA"/*

exec runuser -u bbblb -- "$@"