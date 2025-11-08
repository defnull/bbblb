# Configuration

TODO

### Environment Variables

When configuring BBBLB via environment variables, prefix all config options listed here with `BBBLB_`.
For example, to enable debug mode, put `BBBLB_DEBUG=True` in your env-file.
See `examples/bbblb.env.example` for a full list.

## Config Options

The options here are lossely ordered by topic.

<!-- snip_start -->
`DOMAIN` (type: `str`, **REQUIRED**)  
Primary domain for this service. This will be added as bbblb-origin
metadata to meetings and is used by e.g. the recording upload script
to get back at bbblb from the BBB nodes.

`SECRET` (type: `str`, **REQUIRED**)  
Secret used to sign and verify API credentials and protected callbacks.
This is NOT your BBB API secret.

`DB` (type: `str`, default: `"sqlite:////usr/share/bbblb/sqlite.db"`)  
An sqlalchemy compatible database connection string, starting with either
`sqlite://` or `postgresql://`. For example `sqlite:////path/to/file.db`
or `postgresql://user:pass@host/name`.

`PATH_DATA` (type: `Path`, default: `Path("/usr/share/bbblb/")`)  
The directory where BBBLB stores all its persistent data, including
recordings, lockfiles, logs and more. Must be fully write-able for BBBLB
and the `{PATH_DATA}/recordings` sub-directory must also be read-able by
your front-end HTTP server, if used. See docs/recording.md for details.

`TENANT_HEADER` (type: `str`, default: `"Host"`)  
For each BBB API request, the value of this header is matched against the
tenant realms to find the correct tenant. This defaults to the `Host`
header, which means each tenant needs to use a different (sub-)domain to
reach BBBLB.

`SCOPED_MEETING_IDS` (type: `bool`, default: `True`)  
If true, meeting IDs are scoped with the tenant ID to avoid conflicts between
tenants. API clients will still see the unmodified meeting ID, but the scoped
ID may end up in recording metadata and logs.

`RECORDING_THREADS` (type: `int`, default: `1`)  
Maximum number of import tasks to perform at the same timer. It is usually
not a good idea to increase this too much.

`PLAYBACK_DOMAIN` (type: `str`, default: `"{DOMAIN}"`)  
Domain where recordings are hostet. The wildcards {DOMAIN} or {REALM}
can be used to refer to the global DOMAIN config, or the realm of the
current tenant.

`POLL_INTERVAL` (type: `int`, default: `30`)  
Poll interval in seconds for the background server health and meeting checker

`POLL_FAIL` (type: `int`, default: `3`)  
Number of poll errors after which a server is marked OFFLINE and all meetings on it are considered lost.

`POLL_RECOVER` (type: `int`, default: `5`)  
Number of successfull polls in a row before a server is considered ONLINE again.

`LOADFACTOR_MEETING` (type: `float`, default: `15.0`)  
Expected base load per meeting.

`LOADFACTOR_SIZE` (type: `float`, default: `1.0`)  
Expected additional load per user in a meeting

`LOADFACTOR_VOICE` (type: `float`, default: `0.5`)  
Expected additional load per voice user

`LOADFACTOR_VIDEO` (type: `float`, default: `0.5`)  
Expected additional load per video user

`LOADFACTOR_INITIAL` (type: `float`, default: `75.0`)  
Initial load penalty for new meetings.
This value is used to predict the future load for new meetings and should
match the load of a 'typical' meeting on your cluster. The penalty will
slowly decrease over time until we can assume that the meeting won't
suddenly grow anymore.
The idea is to avoid the 'trampling herd' effect where multiple meetings
are started in a short time and would otherwise end up on the same server.

`MAX_ITEMS` (type: `int`, default: `1000`)  
Maximum number of meetings or recordings to return from APIs that
potentially return an unlimited amount of data.

`MAX_BODY` (type: `int`, default: `1024 * 1024`)  
Maximum body size for BBB API requests, both front-end and back-end.
This does not affect presentation uploads, so 1MB should be plenty.

`WEBHOOK_RETRY` (type: `int`, default: `3`)  
How often to retry webhooks if the target fails to respond.

`DEBUG` (type: `bool`, default: `False`)  
Enable debug and SQL logs

<!-- snip_end -->
