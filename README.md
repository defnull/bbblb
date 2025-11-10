# BBBLB: Yet another Load Balancer for BigBlueButton

BBBLB (BigBlueButton Load Balancer) is yet another *smart* load balancer for [BigBlueButton](https://bigbluebutton.org/). It is designed to provide a secure, scalable, and robust way to scale BBB beyond single-server installations, enabling organizations to distribute meetings across many BBB servers or offer managed BBB hosting services on shared hardware.

## Current Status: Pre-alpha

> :warning: BBBLB is currently in a **pre-alpha state**. It is a somewhat working prototype and **not ready for production** at this time. **APIs are not stable** and will change. There will be bugs. You have been warned.

## Features

* **Multi-Tenancy**: Allow multiple front-end applications or customers to share the same BigBlueButton cluster while keeping their meetings and recordings strictly separated.
* **Advanced Loadbalancing**: New meetings are created on the BBB servers with the lowest *load*, which is updated in realtime and calculated based un multiple tuneable factors. The algorithm especially tries to avoid the 'trampling herd' problem when multiple meetings with unknown size are created in quick succession and may end up on the same back-end server.
* **Recording Management**: Recordings are transferred from the BBB servers to central storage via a simple and robust `post_publish` script. No special configuration, `ssh` transfers or shared network file system necessary.
* **Callback Relay**: Callbacks registered for a meeting are properly relayed between the back-end BBB server and the front-end application with a robust retry-mechanism.
* **Control API**: BBBLB offers its own API and command line tool to fetch health information, manage tenants, servers or recordings, or perform maintenance tasks.
* **Easy to deploy**: At least easi*er* than most other BigBlueButton Load Balancer implementations.

## Planned features

* A `bbblb-agent` command line tool that can:
  * Auto-register and enable back-end BBB servers when they start up and disable them when they shut down.
  * Report additional health and load information from back-end BBB servers to BBBLB for better load balancing.
* A `bbblb` admin command line tool that can:
  * Manage tenants, servers, running meetings or recordings.
  * Display and export statistics or metrics.
* Rate limiting and DoS protection that is fair to unaffected tenants.

## Feature comparison with Scalelite (totally not biased at all)

[Scalelite](https://github.com/blindsidenetworks/scalelite) is developed by [Blindside Networks](https://blindsidenetworks.com/) and is more or less the *reference implementation* for BigBlueButton Load Balancers. It is certainly not the only implementation out there, but the one all others compare against.

| Feature | BBBLB | Scalelite |
| ------- | ----- | --------- |
| Zero config post_publish script | Yes | No |
| Recording upload via HTTPS | Yes | No 1) |
| Graceful handling of unstable back-end servers | Yes | No 2) |
| Deployed as a single app/container | Yes 3) | No 4) |
| Scales to many concurrent users | Yes 5) | No 6) |

1) You need ssh/rsync or a shared file system for recording transfer.
2) Scalelite immediately breaks all meetings on an unresponsive server, even if it's only a short temporary issue.
3) BBBLB greatly benefits from a fast static-file HTTP server (e.g. nginx or caddy) in front of it, and a Postgres Database instead of sqlite, but can also run as a single self-contained application if you prefer.
4) Scalelite needs a recording importer and a poller in addition to its main server process. Both cannot be scaled to multiple instances or stuff will break.
5) Most existing BBB load balancers claim to be scalable. Until I have time to actually benchmark those claims, I'll also just claim that BBBLB scales to hundreds of backend servers and thousands of meetings without any issues. The bottleneck will always be your BBB cluster, not BBBLB. Trust me bro. 
6) Scalelite uses ruby on rails and synchronous handlers, which means that it can only serve a limited number of requests at a time. For very large clusters, this may sometimes become a bottleneck.



# Documentation

The documentation is still a work in progress. Pull requests are very welcomed!

## Deployment and Getting Started

To get started, check out the docker-compose based deployment example in [examples/bbblb-compose](https://github.com/defnull/bbblb/blob/main/examples/bbblb-compose) and refer to [examples/bbblb-compose/README.md](https://github.com/defnull/bbblb/blob/main/examples/bbblb-compose/README.md) for step by step instructions.

Docker images are available via `ghcr.io`:

* `ghcr.io/defnull/bbblb:main` Main branch
* `ghcr.io/defnull/bbblb:latest` Whatever was pushed last. DO NOT USE as this can jump to older maintenance releases.
* `ghcr.io/defnull/bbblb:X` Latest minor or patch release for the major version `X` (e.g. `1`)
* `ghcr.io/defnull/bbblb:X.Y` Latest patch release for the minor version `X.Y` (e.g. `1.2`)
* `ghcr.io/defnull/bbblb:X.Y.Z` A specific patch release `X.Y.Z` (e.g. `1.2.3`)

Manual deployments without docker are of cause also possible. We will provide documentation in the future, pull requests are very welcomed.

## Configuration Options

See [docs/config.md](https://github.com/defnull/bbblb/blob/main/docs/config.md) for a list of all available configuration parameters or [examples/bbblb.env.example](https://github.com/defnull/bbblb/blob/main/examples/bbblb.env.example) for a fully documented env-file.

## API Documentation

See [docs/API.md](https://github.com/defnull/bbblb/blob/main/docs/API.md) for details (work in progress). 

## Recording Management

See [docs/recording.md](https://github.com/defnull/bbblb/blob/main/docs/recording.md) for details.



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
