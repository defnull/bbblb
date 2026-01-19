============
Installation
============

.. _GITHUB: https://github.com/defnull/bbblb

There are several ways to deploy BBBLB:

* **Docker Compose:** Run BBBLB, `Postgres <hhttps://www.postgresql.org/>`_ and `Caddy <https://caddyserver.com/docs/install#docker>`_ on a single VM with `docker compose <https://docs.docker.com/compose/>`_. This is the recommended way to get started and is suitable for most production deployments. 
* **Kubernetes:** Let's be honest, most deployments do not actually benefit from the added complexity of Kubernetes, but if you absolutely need redundancy or high availability, this is they way to go. If you are in that position, you probably know already how to pull this of and won't need a tutorial. Good luck! (PRs welcome)
* **Manual:** If you hate containers and already have a Postgres database server and front-end web server up and running, you could also run BBBLB with systemd and connect the dots yourself. While not recommended, that's absolutely possible.
* **Standalone:** BBBLB *can* run as a standalone application with an embedded HTTP(S) server (uvicorn) and database (sqlite). While this is nice for quick tests and development, it is not the recommended way to run BBBLB in production.

In this document we will focus on the **Docker Compose** based deployment approach, as it is the easiest and most complete of the available options.


Docker Compose
==============

We strongly recommend to deploy BBBLB as a `docker compose <https://docs.docker.com/compose/>`_ project. All necessary services (`Postgres <hhttps://www.postgresql.org/>`_ and `Caddy <https://caddyserver.com/docs/install#docker>`_) run in containers alongside BBBLB on the same host. The operating system of the host does not matter, as long as it can run docker and linux containers. 

Prerequisites
-------------

Before we begin, ensure you have `Docker <https://docs.docker.com/engine/install/>`__ installed and also the ``docker-compose-plugin`` package that comes with it. Do not use the legacy ``docker-compose`` command, but the ``docker compose`` plugin.

Copy example files
---------------------

Clone the `repository <GITHUB_>`_ and copy the example project files from ``examples/bbblb-compose`` to your project directory (e.g. ``/opt/bbblb-compose``). Enter the project directory and rename ``bbblb.env.example`` to ``bbblb.env``. You can delete the cloned repository afterwards, we do not need it.

.. code:: bash

    git clone https://github.com/defnull/bbblb.git /tmp/bbblb
    cp -r /tmp/bbblb/examples/bbblb-compose /opt/bbblb-compose
    # Optional: rm -r /tmp/bbblb
    cd /opt/bbblb-compose
    mv bbblb.env.example bbblb.env

Inspect ``docker-compose.yml``
-----------------------------

Open ``docker-compose.yml`` in an editor and try to understand how everything fits together.
You will find three services:

* ``bbblb`` is the star of the show. We use the pre-built images (main branch by default) and store all files in `./data/bbblb`. Configuration is loaded from `bbblb.env`, with the exception of `BBBLB_DB` and `BBBLB_PATH_DATA` because those need to match other parts of the compose file.
* ``caddy`` acts as the front-end web server and handle SSL/TLS for us. It will also serve static files (e.g. recordings) directly from disc for efficiency. In this example we build and use a modified image that contains a copy of the BBB presentation player.
* ``postgres`` is our database. Nothing special here. We can get away with trivial credentials because the database is not reachable from the outside and is used exclusively by BBBLB. 

The example project will store any persistent data in the ``./data/`` host folder. This leaves us with a neat and self-contained deployment with everything in one directory. You can migrate to a larger server or NFS storage later if you need to.

Configure BBBLB
---------------

Open ``bbblb.env`` in an editor and change configuration as needed. The example file you copied earlier from ``examples/bbblb-compose/bbblb.env.example`` contains all available config options and their documentation. Have a look for the parameters marked with ``REQUIRED``.

Do not change ``BBBLB_PATH_DATA`` or ``BBBLB_DB`` for now. Both are overridden in the ``docker-compose.yml`` file because they need to match other parts of the compose file.

There is one parameter not present in the example config: `WEB_CONCURRENCY`. It defaults to your CPU count and controls how many worker processes the container will spawn to serve requests. You usually do not need to change this, BBBLB can handle a ton of requests per second with default settings.

Configure Caddy
---------------

Open ``./caddy/Caddyfile`` in an editor and change the domains Caddy should listen to. You may also want to have a look at the rest of the file and tweak it to your needs.

If you plan to follow the `Cluster Proxy Configuration <https://docs.bigbluebutton.org/administration/cluster-proxy/>`_ steps on your BBB nodes, then you need to add a bunch of *Caddyfile* rules for every single back-end server. There may be better ways to do it, but I could not make it work without repeating those rules. If you have a lot of back-end servers and they change a lot, you may want to generate the Caddyfile with a script or template engine. You can reload the caddy configuration at runtime without downtime. 

Starting or Stopping the Services
---------------------

If you followed all the previous steps, the only thing left to do is to start everything up. Navigate to the directory containing the ``docker-compose.yml``, then run:

.. code:: bash

    docker compose up --build --pull always -d

This command starts all services defined in ``docker-compose.yml`` in the background (``-d``). To check if everything runs fine, run ``docker compose ps``. To check the logs and and follow (`-f`) them in real-time, run ``docker compose logs -f``. 

Since all containers are configured with ``restart: unless-stopped`` they will restart automatically after a reboot or crash.

To stop all running containers, run ``docker compose stop``. To completely reset everything and remove all docker containers and networks, run ``docker compose down``. Don’t worry, all your data lives in the ``./data/`` directory and will not be removed by docker. You can later start everything again if you need to.

Add Tenants and Servers
-----------------------

Continue with :doc:`admin`.

Preparing BBB Servers
=====================

In a cluster setup, recordings usually do not stay on the BBB nodes there were created on, but are transferred to the loadbalancer and made available via the same URL users and applications use to access the cluster. Here is how this works:

.. _post-publish:

Install the `post_publish` hook
-------------------------------

To automatically import recordings from meetings created via BBBLB, we can hook into the ``post_publish`` script hook mechanism of BBB. Put the `examples/post_publish_bbblb.rb <https://raw.githubusercontent.com/defnull/bbblb/refs/heads/main/examples/post_publish_bbblb.rb>`_ script into the ``/usr/local/bigbluebutton/core/scripts/post_publish/`` directories on all your BBB back-end servers and make them executable. The script will run every time BBB finishes generating a recording, and automatically upload new recording to the BBBLB server that created the meeting. No additional configuration necessary.

On the BBB server, run::

    sudo curl https://raw.githubusercontent.com/defnull/bbblb/refs/heads/main/examples/post_publish_bbblb.rb \
      -o /usr/local/bigbluebutton/core/scripts/post_publish/post_publish_bbblb.rb
    chmod 755 /usr/local/bigbluebutton/core/scripts/post_publish/post_publish_bbblb.rb



.. _serving-recordings:

Serving Recordings
==================

To allow clients to watch recordings, you need to serve the *media files* from the ``https://{PLAYBACK_DOMAIN}/playback/*`` URL and also host a copy of the *presentation player* singel page application (SPA). This is a bit tricky to get up and running, but mo worries, the docker-compose example already handles most if it and BBBLB helps where it can. If you want to understand how everything works, or configure it manually, read on.

Media Files
-----------

Recording media files are stored in ``{PATH_DATA}/recordings/storage/``. When a recording is published, BBBLB creates a relative symlink in ``{PATH_DATA}/recordings/public/`` which points to the correct storage directory. For clients to be able to watch recordings, a webserver should expose the contents of the ``public/`` directory as ``https://{PLAYBACK_DOMAIN}/playback/*`` and follow symlinks to the actual storage location.

BBBLB does the right thing by default. Serving recordings through BBBLB simplifies the deployment and is fine for small clusters, but proper web servers or proxies (e.g. nginx or caddy) are usually way more efficient in serving static files than BBBLB could ever be.

To reduce load on the BBBLB process, configure your front-end web server to answer all requests to ``/playback/*`` directly from the ``public/`` directory, instead of forwarding them to BBBLB. The ``storage/`` directory and all the other directories should remain private, but the web server still needs *see* those directories, or it won’t be able to follow relative symlinks from ``public/`` to the actual ``storage/`` location.

In nginx, this would look like this::

    location /playback {
        alias /path/to/recordings/public/;
        #disable_symlinks off;  # default: off
        #autoindex off;         # default: off
    }

The same for caddy::

    handle_path /playback/* {
        root * /path/to/recordings/public
        file_server
    }


Presentation Player
-------------------

The *presentation* recording format is special. It needs a player that is not part of the recroding and must be served separately from the ``https://{PLAYBACK_DOMAIN}/playback/presentation/2.3/`` URL. This player also assumes the recording data files are found under ``/presentation/{record_id}/*`` instead of the standard ``/playback/presentation/{record_id}/*`` path for whatever reason, and it is an SPA (single page application) that needs special configuration in the webserver.

There are multiple ways to tackle this:

.. rubric:: Option 1: Piggyback on BBB

BBB already ships and serves the presentation player, so why not use that? Forward all requests for `/playback/presentation/2.3/*` to one of your BBB back-end servers.

In nginx, this would look like this::

    location /playback/presentation/2.3/ {
        proxy_pass https://bbb01.example.com;
    }

The same for caddy::

    reverse_proxy /playback/presentation/2.3/* https://bbb01.example.com;

The player that comes with BBB expects media files in ``/presentation/{record_id}/*`` but that's fine, as BBBLB will answer those requests with a redirect to ``/playback/presentation/{record_id}/*``. You can of cause add your own redirect rules or additional aliases in your front-end web server to save the additional round trip per request.

.. rubric:: Option 2: Build and serve your own

You can of cause also build and serve your own copy of `bbb-playback <https://github.com/bigbluebutton/bbb-playback>`__. The docker-compose API does exactly that. This has the edded benefit that you can set ``REACT_APP_MEDIA_ROOT_URL=/playback/presentation/`` during build and skip the redirect from `/presentation/` explained earlier.

Remember to regularly check for updates, because the palyer evolves alongside BBB and old versions may not be able to playback new recordings.

Serving Static Files
====================

BBBLB will serve static files placed in ``{PATH_DATA}/htdocs`` under the root ``/`` path. The folder is empty by default, but useful if you want to serve a custom `favicon.ico`, `robots.txt` or `index.html` with contact or privacy information. This is also a good place for a `.well-known/` directory if you are using certbot for ACME certificates.
