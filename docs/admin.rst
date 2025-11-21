======================
Cluster Administration
======================

Manage Tenants
==============

Before we can actually start our first meeting, we need to add *Tenants* and *Servers* to our cluster. This can be fully automated via the :doc:`API <api>` but for now, we will use the ``bbbctl`` admin command line tool instead.

.. note::

    If you followed the :doc:`docker compose based deployment <deploy>`, you can use the ``bbbctl.sh`` wrapper ro tun ``bbbctl`` inside the container.

Add your first Tenant
~~~~~~~~~~~~~~~~~~~~~

To create your first "example" tenant, run:

.. code:: bash

    ./bbblb.sh tenant create --secret SECRET --realm bbb.example.com example

Replace ``SECRET`` with a suitable tenant secret, ``example`` with a short but meaningful tenant name, and ``bbb.example.com`` with the primary domain of your BBBLB instance.

**Realms are used to associate API requests with tenants.** BBBLB checks the request ``Host`` header by default and matches it against all configured tenants and their realms. Requests that do not have a matching tenant cannot be checksum-verified and are rejected.

In this example we associate the ‘example’ tenant with the primary domain. To add more tenants, associate each one with a unique domain or subdomain as their realm, so they can be told apart.

Override create parameters
~~~~~~~~~~~~~~~~~~~~~~~~~~

The BBB API for creating new meetings accepts a ton of parameters and allows front-ends to control the featureset and many other aspects of a meeting. You can enforce or extend those parameters for each tenant using overrides::

    Usage: bbblb override set [OPTIONS] TENANT NAME=VALUE

You can define any number of create parameter overrides per tenant as ``PARAM=VALUE`` pairs. ``PARAM`` should match a BBB create call API parameter and the given ``VALUE`` will be enforced on all future create-calls issued by this tenant. If ``VALUE`` is empty, then the parameter will be removed from create-calls.

Instead of the ``=`` operator you can also use ``?`` to define a fallback, ``<`` to define a maximum value for numeric parameters (e.g. *duration*, *maxParticipants*), or ``+`` to add items to a comma separated list parameter (e.g. *disabledFeatures*).

Examples::

    # Limit the 'free' tenant to 100 participants and 90 minutes
    # per meeting, and prevent recordings
    bbblb override set free "duration<90" "maxParticipants<100" "record="

    # Set a different default presentation for the 'moodle' tenant
    bbblb override set moodle "preUploadedPresentation?https://dl.example.com/school1.pdf"

    # Disable chat for the 'interview' tenant
    bbblb override set interview "disabledFeatures+chat" "disabledFeaturesExclude="

Manage Servers
==============

Before we can actually start our first meeting, we need to add *Tenants* and *Servers* to our cluster. This can be fully automated via the :doc:`API <api>` but for now, we will use the ``bbbctl`` admin command line tool instead.

.. attention::

    The ``bbbctl`` tool needs to be able to connect to the same database and access the same configuration and storage paths as your running BBBLB service. If you deployed BBBLB in a container, then use the bundled ``./bbblb.sh`` script as a shortcut to run commands in the container.

Add your first Server
~~~~~~~~~~~~~~~~~~~~~

Let's assume you already have some BBB servers up and running. 

.. attention::
    
    Make sure to install the ``./examples/post_publish_bbblb.rb`` script on BBB servers *before* attaching them to your cluster, or recordings won’t be transferred.

To attach your first BBB server, run:

.. code:: bash

   ./bbblb.sh server create --secret=SECRET server1.example.com

Replace ``SECRET`` with the BBB server API secret and ``server1.example.com`` with its domain.

It may take up to 50 seconds (5 times the poll interval default) until the server is *actually* available for new meetings. Check `./bbblb.sh server list` to see all servers and their state.

That’s it. Your ‘example’ tenant should now be able to start and manage meetings in your cluster via BBBLB.

Import old Recordings
~~~~~~~~~~~~~~~~~~~~~

TODO

Cluster Maintenance
~~~~~~~~~~~~~~~~~~~~~

TODO