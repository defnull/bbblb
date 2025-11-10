#!/bin/sh
cd $(dirname $0)
exec docker compose exec bbblb /entrypoint.sh bbblb "$@"
