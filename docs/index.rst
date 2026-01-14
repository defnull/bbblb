BBBLB Documentation
==================================


BBBLB is yet another load balancer for `BigBlueButton <https://bigbluebutton.org/>`__. It is designed to
provide a secure, scalable, and robust way to scale BBB beyond
single-server installations, enabling organizations to distribute
meetings across many BBB servers or offer managed BBB hosting services
on shared hardware.

.. .. image:: https://github.com/defnull/bbblb/actions/workflows/docker.yaml/badge.svg
..     :target: https://github.com/defnull/bbblb/actions/workflows/docker.yaml
..     :alt: Docker Images

.. image:: https://img.shields.io/pypi/v/bbblb.svg?color=%2334D058
    :target: https://pypi.python.org/pypi/bbblb/
    :alt: Latest Version

.. image:: https://img.shields.io/pypi/l/bbblb.svg?color=%2334D058
    :target: https://github.com/defnull/bbblb/blob/main/LICENSE.md
    :alt: License

.. image:: https://img.shields.io/badge/github-sources-blue?logo=github
    :target: https://github.com/defnull/bbblb/
    :alt: Sources

.. image:: https://img.shields.io/badge/docker-images-blue?logo=docker
    :target: https://hub.docker.com/r/defnull/bbblb
    :alt: Container Images

.. caution::

   BBBLB is not ready for production just yet unless you know how to fix
   bugs yourself. It works well enough, but APIs and features are not
   stable yet and upgrades may break things. If you are looking for a
   reliable solution that *just works*, better wait for the 1.0 release.


Features
--------

-  **Multi-Tenancy**: Allow multiple front-end applications or customers
   to share the same BigBlueButton cluster while keeping their meetings
   and recordings strictly separated.
-  **Meeting parameter overrides**: Enforce meeting parameters or change
   defaults based on tenant preferences or limits.
-  **Advanced Loadbalancing:** Meetings are distributed based on current
   and predicted utilization, taking common usage patterns into account
   and avoiding the infamous ‘trampling herd’[#th]_ problem.
-  **Recording Management**: Recordings are transferred from the BBB
   servers to central storage via a simple [#spp]_ and robust ``post_publish``
   script. 
-  **Callback Relay**: Callbacks registered for a meeting are properly
   relayed between the back-end BBB server and the front-end application
   with a robust retry-mechanism.
-  **Admin API and CLI**: BBBLB offers both an API and a command line
   interface to fetch health information, manage tenants, servers or
   recordings and perform maintenance tasks.
-  **Easy to deploy**: At least easi\ *er* than most other BigBlueButton
   Load Balancer implementations.

Documentation
-------------

.. toctree::
   :maxdepth: 3

   install.rst
   config.rst
   admin.rst
   api.rst
   cli.rst
   recording.rst


License: AGPL 3.0
=================

BBBLB - BigBlueButton Load Balancer
Copyright (C) 2025  Marcel Hellkamp

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU Affero General Public License as
published by the Free Software Foundation, either version 3 of the
License, or (at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU Affero General Public License for more details.

You should have received a copy of the GNU Affero General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.



Footnotes
=========

.. [#th] Load per meeting usually starts low and increases over time
   once users start to join. The actual load is not known at the time the
   meeting is created. When multiple meetings are created in short
   succession, they may all end up on the same server while their
   combined load is still low. This can drastically impact cluster
   balancing and even cause stability issues. BBBLB tries to avoid this
   issue by taking into account the predicted future load load of new
   meetings.
.. [#spp] Recordings are transferred directly via HTTPS. No configuration,
   credentials, ``ssh`` keys or shared network file system necessary.


