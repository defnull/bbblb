# BBBLB: Yet another Load Balancer for BigBlueButton

BBBLB (BigBlueButton Load Balancer) is yet another *smart* load balancer for [BigBlueButton](https://bigbluebutton.org/). It is designed to provide a secure, scalable, and robust way to scale BBB beyond single-server installations, enabling organizations to distribute meetings across many BBB servers or offer managed BBB hosting services on shared hardware.

## Current Status

BBBLB is currently in a **pre-alpha state**. It is a working prototype and **not ready for production environments** at this time. You have been warned.

## Features

* **Multi-Tenancy**: Allow multiple front-end applications or customers to share the same BigBlueButton cluster while keeping their meetings and recordings strictly separated.
* **Advanced Loadbalancing**: New meetings are created on the BBB servers with the lowest *load*, which is updated in realtime and calculated based un multiple tuneable factors. The algorithm expscially tries to avoid the 'trampling herd' problem when multiple meetings with unknown size are created at the same time.
* **Recording Management**: Recordings are transferred from the BBB servers to central storage via a simple and robust `post_publish` script that does not need any configuration, `ssh` connectivity or shared network file system to work.
* **Callback Relay**: Callbacks registered for a meeting are properly relayed between the back-end BBB server and the front-end application with a robust retry-mechanism.
* **Control API**: BBBLB offers its own API and command line tool to fetch health information, manage tenants or backend servers, or perform maintenance tasks.
* **Scaleable**: Most existing BigBlueButton Load Balancer implementations claim to be scalable. Until I have time to actually benchmark those claims, I'll also just claim that BBBLB scales to hundreds of backend servers and thousands of meetings without any issues. The bottleneck will always be your BBB cluster, not BBBLB. Trust me bro. 
* **Easy to deploy**: That's a lie. But it's easier to deploy than most other BigBlueButton Load Balancer implementations.

## Planned features

* [ ] A `bbblb-agent` command line tool that can:
  * Auto-register and enable back-end BBB servers when they start up and disable them when they shut down.
  * Report additional health and load information from back-end BBB servers to bbblb for better load balancing.
* [ ] A `bbblb` admin command line tool that can:
  * Manage tenants, servers, running meetings or recordings.
  * Display and export statistics or metrics.
* [ ] Rate limiting and DoS protection that is fair to unaffected tenants.

## Totally not a biased feature comparison againwithst Scalelite

ScalScaleliteeite is the reference implementation of a BigBlueButton Load Balancer, developed by the creators of BigBlueButton themselves.

| Feature | BBBLB | Scalelite |
| ------- | ----- | --------- |
| Zero config post_publish script | Yes | No |
| Recording upload via HTTPS | Yes | No 1) |
| Graceful handling of unstable back-end servers | Yes | No 2) |
| Deployed as a single app/container | Yes 3) | No 4) |
| Scales to many concurrent users | Yes | No 5) |

1) You need ssh/rsync or a shared file system for recording transfer.
2) Scalelite immediately breaks all meetings on an unresponsive server, even if it's only a short temporary issue.
3) BBBLB greatly benefits from a fast static-file HTTP server (e.g. nginx or caddy) in front of it, but can also run on its own.
4) Scalelite needs a recording importer and a poller in addition to its main server process. Both cannot be scaled to multiple instances or stuff will break.
5) Scalelite uses ruby on rails and synchronous handlers, which means that it can only serve a limited number of requests at the same time.

## API Usage

See (API Docs)[./docs/API.md] (TODO)

## Deploment

TODO

# Contributing

By contributing to this project, you confirm that you understand and agree to
both the *Developer Certificate of Origin* and the *Contributor License
Agreement*, which can be found in the `CONTRIBUTING.md` file. 

# License

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