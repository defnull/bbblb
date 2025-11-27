``DOMAIN`` (type: ``str``, **REQUIRED**)

Primary domain for this service. This will be added as bbblb-origin
metadata to meetings and is used by e.g. the recording upload script
to get back at bbblb from the BBB nodes.

``SECRET`` (type: ``str``, **REQUIRED**)

Secret used to sign and verify API credentials and protected callbacks.
This is NOT your BBB API secret.

``DB`` (type: ``str``, default: ``"sqlite:////usr/share/bbblb/sqlite.db"``)

An sqlalchemy compatible database connection string, starting with either
`sqlite://` or `postgresql://`. For example `sqlite:////path/to/file.db`
or `postgresql://user:pass@host/name`.

``DB_CREATE`` (type: ``bool``, default: ``True``)

Create database if it does not exist on startup (postgres only).

``DB_MIGRATE`` (type: ``bool``, default: ``True``)

Run database schema migrations automatically on startup.

``PATH_DATA`` (type: ``Path``, default: ``Path("/usr/share/bbblb/")``)

The directory where BBBLB stores all its persistent data, including
recordings, lockfiles, logs and more. Must be fully write-able for BBBLB
and the `{PATH_DATA}/recordings` sub-directory must also be read-able by
your front-end HTTP server, if used. See docs/recording.md for details.

``TENANT_HEADER`` (type: ``str``, default: ``"Host"``)

For each BBB API request, the value of this header is matched against the
tenant realms to find the correct tenant. This defaults to the `Host`
header, which means each tenant needs to use a different (sub-)domain to
reach BBBLB.

``SCOPED_MEETING_IDS`` (type: ``bool``, default: ``True``)

If true, meeting IDs are scoped with the tenant ID to avoid conflicts between
tenants. API clients will still see the unmodified meeting ID, but the scoped
ID may end up in recording metadata and logs.

``RECORDING_THREADS`` (type: ``int``, default: ``1``)

Maximum number of import tasks to perform at the same timer. It is usually
not a good idea to increase this too much.

``PLAYBACK_DOMAIN`` (type: ``str``, default: ``"{DOMAIN}"``)

Domain where recordings are hostet. The wildcards {DOMAIN} or {REALM}
can be used to refer to the global DOMAIN config, or the realm of the
current tenant.

``POLL_INTERVAL`` (type: ``int``, default: ``30``)

Poll interval in seconds for the background server health and meeting checker

``POLL_FAIL`` (type: ``int``, default: ``3``)

Number of failed create calls or health checks after which we give up on an
UNSTABLE server and mark it as OFFLINE. All remaining meetings are dropped,
so they can be re-created on another server.

``POLL_RECOVER`` (type: ``int``, default: ``5``)

Number of successfull health checks in a row after which an OFFLINE or UNSTABLE
server is considered to be AVAILABLE again.

``LOAD_BASE`` (type: ``float``, default: ``5.0``)

Base load counted for each meeting.

``LOAD_USER`` (type: ``float``, default: ``1.0``)

Additional load counted for each user in a meeting.

``LOAD_VOICE`` (type: ``float``, default: ``0.5``)

Additional load counted for each voice user in a meeting.

``LOAD_VIDEO`` (type: ``float``, default: ``0.5``)

Additional load counted for each video user in a meeting.

``LOAD_PENALTY`` (type: ``float``, default: ``20.0``)

Additional load penalty for new meetings.
New meetings are counted with a higher load until their actual
load has stabilized to avoid uneven distribution during peak
hours.
The load penalty should roughly match the load of a 'typical'
meeting on your cluster and will decrease over time based on
meeting age. See LOAD_COOLDOWN.

``LOAD_COOLDOWN`` (type: ``float``, default: ``30``)

Number of minutes after which new meetings are no longer impacted
by LOAD_PENALTY. The applied penalty decreases linearly over time.

``MAX_ITEMS`` (type: ``int``, default: ``1000``)

Maximum number of meetings or recordings to return from APIs that
potentially return an unlimited amount of data.

``MAX_BODY`` (type: ``int``, default: ``1024 * 1024``)

Maximum body size for BBB API requests, both front-end and back-end.
This does not affect presentation uploads, so 1MB should be plenty.

``WEBHOOK_RETRY`` (type: ``int``, default: ``3``)

How often to retry webhooks if the target fails to respond.

``DEBUG`` (type: ``bool``, default: ``False``)

Enable debug and SQL logs

