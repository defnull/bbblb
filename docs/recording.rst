Recording Internals
===============================

In a cluster setup, recordings usually do not stay on the BBB nodes
there were created on, but are transferred to the loadbalancer and made
available via the same URL users and applications use to access the
cluster. Here is how that works.

Directory Structure
-------------------

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

Auto-Import from BBB
--------------------

To automatically import recordings from meetings created via BBBLB, we
can hook into the ``post_publish`` script hook mechanism of BBB. See :ref:`post-publish`.

The uploaded recordings will end up in
``{PATH_DATA}/recordings/storage/{tenant}/{record_id}/{format}`` and BBB
will list the recording as ``unpublished`` by default. After publishing
a recording via the BBB API, BBBLB will create a symlink in
``{PATH_DATA}/recordings/public/{format}/{record_id}`` that points to
the corresponding ``storage`` directory. Only the ``public`` directory
is served to clients, so they won't be able to access unpublished
recordings.

Serving recordings to users
---------------------------

See :ref:`serving-recordings`.

Migrate old recordings
----------------------

See :ref:`migrate-recordings`

Error Recovery
--------------

Retry a failed import
~~~~~~~~~~~~~~~~~~~~~

Failed imports will be moved to the ``{PATH_DATA}/recordings/failed/``
directory. This does not mean that they failed completely, *some* of the
contained recordings may have been successfully imported. Check the logs
and try to fix the issue, then upload the (fixed) recording again.

Recover from a crash
~~~~~~~~~~~~~~~~~~~~

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
