#!/bin/sh
. /app/.venv/bin/activate

if [ -n "$BBBLB_RECORDING_PATH" ]; then
    mkdir -p "$BBBLB_RECORDING_PATH"
    chown bbblb "$BBBLB_RECORDING_PATH" "$BBBLB_RECORDING_PATH"/*
fi

exec runuser -u bbblb -- "$@"