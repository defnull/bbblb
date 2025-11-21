===============
Getting Started
===============

.. _GITHUB: https://github.com/defnull/bbblb

There are several ways to deploy BBBLB:

* **Docker Compose:** Run BBBLB, `Postgres <hhttps://www.postgresql.org/>`_ and `Caddy <https://caddyserver.com/docs/install#docker>`_ on a single VM with `docker compose <https://docs.docker.com/compose/>`_. This is the recommended way to get started and is suitable for most production deployments. 
* **Kubernetes:** Let's be honest, most deployments do not actually benefit from the added complexity of Kubernetes, but if you absolutely need redundancy or high availability, this is they way to go. If you are in that position, you probably know already how to pull this of and won't need a tutorial. Good luck! (PRs welcome)
* **Manual:** If you hate containers and already have a Postgres database server and front-end web server up and running, you could also run BBBLB with systemd and connect the dots yourself. While not recommended, that's absolutely possible.
* **Standalone:** BBBLB *can* run as a standalone application with an embedded HTTP(S) server (uvicorn) and database (sqlite). While this is nice for quick tests and development, it is not the recommended way to run BBBLB in production.

In this document we will focus on the **Docker Compose** based deployment approach, as it is the easiest and most complete of the available options.


Docker Compose
==============

We strongly recommend to deploy BBBLB as a `docker compose <https://docs.docker.com/compose/>`_ project. All necessary services (`Postgres <hhttps://www.postgresql.org/>`_ and `Caddy <https://caddyserver.com/docs/install#docker>`_) run in containers alongside BBBLB on the same host. The operating system of the host is completely irrelevant, as long as it can run docker and linux containers. 

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

This command starts all services defined in ``docker-compose.yml`` in the background (`-d``). To check if everything runs fine, run ``docker compose ps``. To check the logs and and follow (`-f`) them in real-time, run ``docker compose logs -f``. 

Since all containers are configured with ``restart: unless-stopped`` they will start automatically after a server reboot and restart after errors or crashes.

To stop all running containers, run ``docker compose stop``. To completely reset everything and remove all docker containers and networks, run ``docker compose down``. Don’t worry, all your data lives in the ``./data/`` directory and will not be removed by docker. You can later start everything again if you need to.

Add Tenants and Servers
-----------------------

Continue with :doc:`admin`.
