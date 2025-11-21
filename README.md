# BBBLB: BigBlueButton Load Balancer

BBBLB (BigBlueButton Load Balancer) is yet another *smart* load balancer for [BigBlueButton](https://bigbluebutton.org/). It is designed to provide a secure, scalable, and robust way to scale BBB beyond single-server installations, enabling organizations to distribute meetings across many BBB servers or offer managed BBB hosting services on shared hardware.

> :warning: BBBLB is currently in a **pre-alpha state**. It is a somewhat working prototype and **not ready for production** at this time. **APIs are not stable** and will change. There will be bugs. You have been warned.


## Documentation

Documentation can be found at https://bbblb.readthedocs.io/  or in the `./docs/` folder. This is still a work in progress. Pull requests are very welcomed!


## Features

* **Multi-Tenancy:** Allow multiple front-end applications or customers to share the same BigBlueButton cluster while keeping their meetings and recordings strictly separated.
* **Advanced Loadbalancing:** Meetings are distributed based on current and predicted utilization, taking common usage patterns into account and avoiding the infamous ‘trampling herd’ problem.
* **Recording Management:** Recordings are transferred from the BBB servers to central storage via a simple and robust post_publish script. No special configuration, ssh transfers or shared network file system necessary.
* **Callback Relay:** Callbacks registered for a meeting are properly relayed between the back-end BBB server and the front-end application with a robust retry-mechanism.
* **Management API:** BBBLB offers its own API and command line tool to fetch health information, manage tenants, servers or recordings, or perform maintenance tasks.
* **Easy to deploy:** At least easier than most other BigBlueButton Load Balancer implementations.


# Contributing

By contributing to this project, you confirm that you understand and agree to both the *Developer Certificate of Origin* and the *Contributor License Agreement*, which can be found in the `CONTRIBUTING.md` file. 


# Sponsors

* [Gesellschaft für wissenschaftliche Datenverarbeitung mbH Göttingen](https://gwdg.de)


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
