API Documentation
=================

BBBLB implements the `BBB
API <https://docs.bigbluebutton.org/development/api/>`__ and serves
those routes under the standard ``/bigbluebutton/api/*`` path.

It also provides its own management API under the ``/bbblb/api/v1/*``
path. This API is described here.

.. _tokenauth:

Token Authentication
--------------------

Most non-public BBBLB APIs are protected with JWT tokens that need to be
signed by a trusted party and provided as an
``Authorization: bearer <token>`` header.

There are three types of tokens:

-  **Admin Tokens** are sigend with the global ``BBBLB_SECRET`` and allow
   admins or automation tools to manage and control BBBLB at runtime.
   Only BBBLB admins can create those tokens. Their permissions can be
   limited with *scopes* (see below).
-  **Tenant Tokens** are signed with the tenant-specific API secret,
   which allows tenants to create their own tokens. Those are of cause
   limited to tenant-owned resources.
-  **Server Tokens** are signed with the back-end server API secret,
   which allows back-end BBB servers to generate their own tokens. Those
   are limited to very specific actions, e.g. uploading recordings or
   signaling server state.

You can use the ``bbblb maketoken`` command to create all three types
of token.

.. code:: sh

   # Admin Token
   $ bbblb maketoken -v --expire 600 admin1 admin
   Token Header: {}
   Token Payload: {'sub': 'admin1', 'aud': 'bbb.example.com', 'scope': 'admin', 'jti': 'd31651f68f8b7a4c', 'exp': 1763460734}
   eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJhZG1p[...]
   
   # Server Token
   $ bbblb maketoken -v --expire 600 --server node01.example.com bbb1
   Token Header: {'kid': 'bbb:node01.example.com'}
   Token Payload: {'sub': 'bbb1', 'aud': 'bbb.example.com', 'jti': 'b9a1383f61b39585', 'exp': 1763460734}
   eyJhbGciOiJIUzI1NiIsImtpZCI6ImJiYjpub2RlMDEuZXhhbXBsZ[...]

   # Tenant Token
   $ bbblb maketoken -v --expire 600 --tenant default default1
   Token Header: {'kid': 'tenant:default'}
   Token Payload: {'sub': 'default1', 'aud': 'bbb.example.com', 'jti': '7e48835a2bef9315', 'exp': 1763460734}
   eyJhbGciOiJIUzI1NiIsImtpZCI6InRlbmFudDpkZWZhdWx0Iiwid[...]
   
Token Scopes
~~~~~~~~~~~~

The ``scope`` claim can be used to limit permissions for *Admin tokens*. The claim should be a space-separates string of scopes.

Scopes have no effect for tenant- oder server-tokens. Those have hard-coded scopes and are limited to very specific actions.

-  ``rec`` Manage recordings. This parent scope implies all ``rec:*`` scopes.
 
   -  ``rec:list`` List recordings.
   -  ``rec:create`` Import new recordings.
   -  ``rec:update`` Edit or publish/unpublish recordings.
   -  ``rec:delete`` Delete recordings.

-  ``tenant`` Manage tenants. This parent scope implies all ``tenant:*`` scopes.

   -  ``tenant:list`` List tenants.
   -  ``tenant:create`` Create new tenants.
   -  ``tenant:update`` Update tenants.
   -  ``tenant:delete`` Delete tenants.
   -  ``tenant:secret`` View and change the tenant secret.

-  ``server`` Manage backend servers. This parent scope implies all ``server:*`` scopes.

   -  ``server:list`` List all servers.
   -  ``server:create`` Register new servers.
   -  ``server:update`` Update servers and server state.
   -  ``server:delete`` Delete servers.

-  ``self:tenant`` Special scope that is assigned to self-signed tenant tokens.
-  ``self:server`` Special scope that is assigned to self-signed server tokens.
-  ``admin`` This scope grants full access. It implies all other scopes.


API Endpoints
-------------

BBB API
~~~~~~~

.. _BBBAPI: https://docs.bigbluebutton.org/development/api/

BBBLB implements the official `BBB API <BBBAPI_>`_ available under the
standard ``/bigbluebutton/api/*`` path. With BBBLB, each tenant uses
their own private API secret to generate the `checksum` for BBB API calls. 

To learn how to access this API and how to authenticate, please refer to the `official documentation <BBBAPI_>`_.

.. rubric:: Limitations 

Some BBB APIs cannot be fully implemented by a load balancer or require additional work. None of this should affect the typical front-end application, but here is a list of all known limitations or deviations: 

* ``getJoinUrl`` is an internal API used exclusively by HTML5client and not available to front-end applications. See `Issue #24212 <https://github.com/bigbluebutton/bigbluebutton/issues/24212>`_.
* ``putRecordingTextTracks`` and ``getRecordingTextTracks`` require processing steps on the back-end BBB server that would not be reflected in already transferred recordings. We may find a way to implement this in the future. Scalelite does not implement those APIs.
* The root resource at ``/bigbluebutton/api/`` will only return minimal information and not the recently added graphql endpoint information. Those are used exclusively by HTML5client and should not be of any interest for front-end applications.
* The ``getRecordings`` listing has an upper limit of ``BBBLB_MAX_ITEMS`` even if the client requested more items.
* The ``join`` API will redirect the client twice. The first redirect will point to a new join url on the actual BBB server. This only affects clients that handle the redirect target in some special way instead of just following it.

Webhooks
~~~~~~~~~~~~~~~~~~~~~~

BBB defines a bunch of webhooks that front-ends can use to get notified
about ended meetings or finished recordings. BBBLB hooks into those
webhooks and forwards them to the actual front-end. 

Most newer webhooks in BBB are protected by wrapping their entire
payload into a non-standard JWT token that is signed with the BBB API
secret. Since BBBLB sits in between the front-end and the actual BBB
server, it has to intercept those webhooks, verify the token against the
back-end secret, re-sign the token with the tenant-specific font-end
secret, and then forward the request to the original target url. This
happens automatically for all *known* callbacks, and can be enabled for
additional callbacks if needed.

The ``endMeetingURL`` and ``meta_endMeetingURL`` webhooks are usually
not authenticated at all. BBBLB intercepts the ``endMeetingURL`` webhook
to quickly clean up its own meeting state after a meeting ends. To
protect this webhook from abuse, BBBLB will create a *signed* URL using
its own ``BBBLB_SECRET``. This is transparent to the front-end, tenants
are allowed to use both webhooks without limitations.


Recording Upload
~~~~~~~~~~~~~~~~

* **POST** `/bbblb/api/v1/recording/upload` (Content-Type: *application/x-tar*)

The recording upload API expects an *application/x-tar* archive of a
recording directory, optionally compressed with gzip. The tar archive can
actually contain multiple recordings or recording formats. Any directory
that contains a `metadata.xml` is considered during the import process.

.. rubric:: Authentication

This endpoint requires a :ref:`Server Token <tokenauth>`, or an :ref:`Admin Token <tokenauth>` with the `record:create` scope.

.. rubric:: Parameters

==============  ========  ===========
Parameter       Location  Description
==============  ========  ===========
`Content-Type`  header    Must be `application/x-tar`.
`*`             body      A tar or tar.gz archive with recording data.
`tenant`        query     Assign all records to a specific tenant instead of auto-detecting the tenant from recording metadata.
==============  ========  ===========

.. rubric:: Response

Recording import triggers background tasks for the actual import. A
``202 Accepted`` response will indicate that the upload was successfull,
but the actual import will happen later.

.. rubric:: Examples

.. code-block:: bash

   API="https://bbblb.example.com/bbblb/api/v1/recording/upload"
   TOKEN="..."
   MEETING="d48a35e5c9bef7d5ca0f507c969e3d17a6005abf-1763452847184"
   FORMAT="presentation"

   tar -c "/var/bigbluebutton/published/$FORMAT/$MEETING/" \
   | curl -H "Authorization: Bearer $TOKEN" \
          -H "Content-Type: application/x-tar" \
          -X POST -T - "$API"
          

Tenant Management
~~~~~~~~~~~~~~~~~

TODO (API is not stable yet)

Server Management
~~~~~~~~~~~~~~~~~

TODO (API is not stable yet)
