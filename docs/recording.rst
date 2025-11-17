Recording Management
====================

In a cluster setup, recordings usually do not stay on the BBB nodes
there were created on, but are transferred to the loadbalancer and made
available via the same URL users and applications use to access the
cluster. Here is how that works.

Getting Started
---------------

Recording Storage and Directory Structure
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

BBBLB stores recordings and temporary files in subdirectories relative
to the ``{PATH_DATA}/recordings/`` directory. The entire ``recordings``
directory and all its subdirectores *should* be on the same file system
to ensure that move operations are atomic and fast.

.. warning:: If you run multiple worker processes with BBBLB, all of them
   MUST see the same ``{PATH_DATA}`` directory. Use a shared directory or
   a network share if needed.

After the first start, you will find a bunch of directories inside the
``{PATH_DATA}`` directory. You can pre-create those if you are careful
with file permissions. BBBLB needs to be able to write to all those
directories and all their subdirectories.

-  ``{PATH_DATA}/recordings/``: All recording related data lives here.

   -  ``inbox/``: New recording imports wait here for processing.
   -  ``failed/``: When a recording import (partly) fails, it is moved
      to this directory. You may want to check this directory from time
      to time.
   -  ``work/``: Work directory for imports that are currently
      processed.
   -  ``storage/``: Directory for unpacked recordings, separated by
      tenant. This folder should NOT be served to clients, but the
      frontend webserver should be able to read from it so it can
      resolve symlinks from the ``public`` directory.
   -  ``public/``: Directory with symlinks to published recordings. This
      folder should be served by a front-end web server (e.g. nginx or
      caddy) directly to clients.
   -  ``deleted/``: Deleted recordings are move here from the storage
      directory. You may want to clear out this directory from time to
      time.

Auto-import recordings to BBBLB
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

To automatically import recordings from meetings created via BBBLB, we
can hook into the ``post_publish`` script hook mechanism of BBB. Put the
``examples/post_publish_bbblb.rb`` script into the
``/usr/local/bigbluebutton/core/scripts/post_publish/`` directories on
all your BBB back-end servers and make them executable. The script will
run every time BBB finishes generating a recording, and automatically
upload new recording to the BBBLB server that created the meeting.

The recordings will end up in
``{PATH_DATA}/recordings/storage/{tenant}/{record_id}/{format}`` and BBB
will list the recording as ``unpublished`` by default. After publishing
a recording via the BBB API, BBBLB will create a symlink in
``{PATH_DATA}/recordings/public/{format}/{record_id}`` that points to
the corresponding ``storage`` directory. Only the ``public`` directory
ths served to clients, to ensure they cannot access unpublished
recordings.

Serving recordings to users
~~~~~~~~~~~~~~~~~~~~~~~~~~~

To allow your users to actually watch those recordings, you need to
serve the ``{PATH_DATA}/recordings/public/`` directory as
``https://{PLAYBACK_DOMAIN}/playback/``. BBBLB can also serve those
files, but it is usually way more efficient to let a front-end web
server (e.g. nginx or caddy) serve those files directly. Remember to
only serve the ``public`` directory, the ``storage`` directory should be
private. The webserver still needs to be able to access both, or it
won’t be able to follow symlinks pointing from one to the other.

The ``presentation`` format is special. It needs a player that is not
part of the recroding and must be served separately from the
``https://{PLAYBACK_DOMAIN}/playback/presentation/2.3/`` URL. This
player also assumes the recording data files to be served under
``/presentation/{record_id}/*`` instead of the standard
``/playback/presentation/{record_id}/*`` path.

There are two ways to tackle this: You can either build and serve your
own copy of
`bbb-playback <https://github.com/bigbluebutton/bbb-playback>`__ and set
``REACT_APP_MEDIA_ROOT_URL=/playback/presentation/`` during build, or
you could forward all requests to the player path to one (or all) of
your BBB back-end servers. The second route is less of a hassle, but
requires you to add a redirect from ``/presentation/*`` to
``/playback/presentation/*`` so the pre-built player can find the
recording files.

Long story short:

-  Serve ``/playback/*`` from ``{PATH_DATA}/recordings/public/``. Make
   sure the webserver can follow relative symlinks to
   ``{PATH_DATA}/recordings/storage/``, but **do not serve the storage
   directory** to clients.
-  Serve ``/playback/presentation/2.3/*`` from a real BBB server, or a
   folder that contains a build version of the
   `bbb-playback <https://github.com/bigbluebutton/bbb-playback>`__
   package.
-  If needed by the presentation player, serve ``/presentation/*`` from
   ``{PATH_DATA}/recordings/public/presentation/`` or add a redirect
   from ``/presentation/*`` to ``/playback/presentation/*``.

Examples for different web servers can be found in the ``./examples/``.

Maintenance
-----------

How to fix a failed import
~~~~~~~~~~~~~~~~~~~~~~~~~~

Failed imports will be moved to the ``{PATH_DATA}/recordings/failed/``
directory. This does not mean that they failed completely, *some* of the
contained recordings may have been successfully imported. Check the logs
and try to fix the issue, then upload the (fixed) recording again.

How to recover from a crash?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The importer will pick up all tasks from the
``{PATH_DATA}/recordings/inbox/`` directory on startup, so even after a
crash, the is usually no need to intervene. If you notice that some
files in the inbox directory are not processed after a crash, follow
these steps:

-  Shutdown all BBBLB processes. Make sure they are really stopped.
-  Remove all directories in the ``{PATH_DATA}/recordings/work/``
   directory. Those may prevent the importer from picking up old tasks
   from the inbox directory on start-up.
-  If the crash was caused by disk issues, check your
   ``{PATH_DATA}/recordings/failed/`` directory for recent files and
   move them back to ``{PATH_DATA}/recordings/inbox/``.
-  Optionally scan the entire ``{PATH_DATA}/recordings/`` for ``*.temp``
   files or directories and remove those. They do not cause any harm,
   but consume space and are not needed anymore.
-  Now start BBBLB again and watch your ``inbox`` and ``failed``
   directories as well
-  as your logs. Your inbox should clear quickly.

How to migrate old recordings?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

If you have old meetings from a different system that do not have tenant
information, upload them via the API as the admin user, but set the
``tenant`` query parameter. This tells BBBLB to ignore any recording
metadata and associate the recording with the given tenant.

If you want to migrate recordings from one tenant to another, you have
to follow three steps:

First, create a tar-archive from the original recording found in the
``./storage/`` directory, then delete the recording via the API, then
upload the tar-archives again and set the ``tenant`` parameter during
upload. Note that the new recording will be ``unpublished`` by default.
TODO: We might provide an API or commandline tool to help with that
use-case in the future.

If you changed the ``DOMAIN`` ir ``PLAYBACK_DOMAIN`` settings in BBBLB,
you do not have do do anything. The API will translate all URLs found in
the original ``metadata.xml`` files automatically.

Internals
---------

What happens during a recording import?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

When uploading a recording to the API, BBBLB generates a unique ``task``
ID and creates an ``inbox/{task}.tar`` file. After a successfull upload,
the actual import task will be scheduled to execute in the background.

The import task will try to create a ``work/{task}/`` directory and
cancel itself if that directory already exists. This may happen when
multiple processes pick up the same file from the inbox during a service
restart and is usually not an error.

Once the work directory is *claimed*, the actual import will start. The
import worker first unpacks ``inbox/{task}.tar`` into ``work/{task}/``
and searches for directories that contain a ``metadata.xml``, then
process each of them one by one.

After some basic sanity checks, all recording files will be moved to
``storage/{tenant}/{record_id}/{format}/``. If the target directory
already exists, then no files are copied or overwritten. We assume this
is an accidental re-import of an existing recording. To actually replace
an already imported recording, delete it first.

Next, the database entries for the recording and individual playback
formats are created. If they already exists, they are not updated. The
frontend may have already published or updated the recording, we to not
want to overwrite those changes. To actually replace an already imported
recording, delete it first.

In a last step, the recording is published or unpublished based on the
current state of the database entry. The default state for new
recordings is always ``unpublished``.

If there was at least one recording in the archive and all of the
recordings were imported successfully, then ``inbox/{task}.tar`` is
removed. We are done!

If the task was canceled, then all temporary files are cleaned up, but
the ``inbox/{task}.tar`` file is left in the inbox. We assume that the
process is restarted later and will pick up all files from the inbox
again.

If there was no recording in the inbox archive or if anything goes wrong
during import, then ``inbox/{task}.tar`` is moved to
``failed/{task}.tar`` for human inspection. It can be moved back to the
inbox (or uploaded again) to try again.
