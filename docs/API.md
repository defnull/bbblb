# API Documentation

BBBLB implements the [BBB API](https://docs.bigbluebutton.org/development/api/)
and serves those routes under the standard `/bigbluebutton/api/*` path.

It also provides its own management API under the `/bbblb/api/v1/*` path. This API is
described here.

## Token Authentication

Most non-public BBBLB APIs are protected with JWT tokens that need to be signed
by a trusted party and provided as an `Authorization: bearer <token>` header.

There are three types of tokens:

* **API Tokens** are sigend with the BBBLB `{SECRET}` and allow admins or
  automation tools to manage and control BBBLB at runtime. They can be limited
  by their scopes, and associated with a tenant or server to further limit their
  capabilities.
* **Tenant Tokens** are signed with a tenant-specific pre-shared key and limited
  to resources owned by that tenant. 
* **Server Tokens** are signed with a back-end server secret and limited to
  very specific actions, e.g. uploading recordings or signaling server state.

You can create new *API tokens* with the command line interface.
Call `bbblb maketoken --expire <sec> <sub> <scope> [<scope> ...]` to
generate and print a new token. The `sub` parameter should identify the token
owner and can later be used to revoke tokens without waiting for them to expire.

The `scope` claims limit for what the token can be used for:

* `rec` Manage recordings
  * `rec:list` List recordings
  * `rec:create` Import new recordings
  * `rec:update` Edit or publish/unpublish recordings
  * `rec:delete` Delete recordings
* `tenant` Manage tenants
  * `tenant:list` List tenants
  * `tenant:create` create tenants
  * `tenant:update` Update tenants
  * `tenant:delete` Delete tenants
  * `tenant:secret` View and change the tenant secret
* `server` Manage backend servers.
  * `server:list` List all servers.
  * `server:create` Register new servers.
  * `server:update` Update servers and server state.
  * `server:delete` Delete servers.
  * `server:state` Change server state (ONLINE / DRAIN / OFFLINE).

Tenant- or Server-tokens can be created with the `--tenant` or `--server`
parameters. The scopes are mostly ignored in that case.

### Protecting `endMeetingURL` webhooks

The the `endMeetingURL` and `meta_endMeetingURL` webhooks are usually not
authenticated at all. BBBLB intercepts the `endMeetingURL` webhook to quickly
clean up its own meeting state after a meeting ends. To protect this webhook
from abuse, BBBLB will create a *signed* URL using the global `SECRET`. The
value the of `endMeetingURL` webhook is not public and only the BBB server
hosting the meeting will be able to trigger it.

### Protecting other BBB webhooks

Most newer webhooks in BBB are protected by wrapping their entire payload into a
non-standard JWT token that is signed with the BBB API secret. Since BBBLB sits
in between the front-end and the actual BBB server, it has to verify the token
against the back-end secret, and then re-sign the token with the tenant-specific
font-end secret. This is done automatically for all *known* callbacks, and can
be enabled for additional callbacks if needed.

### Protecting Recording Upload

The `api/v1/recording/upload` API is special, because back-end BBB servers need
to be able to authenticate against BBBLB in their `post_publish` recording hooks
and it would be a hassle to create individual tokens for each BBB node.

Fortunately we already have a shared secret, namely the BBB API secret, that we
can use here: To authenticate, the BBB server creates a JWT signed with its own
secret. An additional `kid` claim in the JWT header identifies the server and
thus the secret that BBBLB should use to verify the JWT.

## API Endpoints

TODO (API is not stable yet)