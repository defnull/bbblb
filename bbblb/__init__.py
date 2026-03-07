# Copyright (C) 2025, 2026  Marcel Hellkamp
# SPDX-License-Identifier: AGPL-3.0-or-later

import logging

__version__ = "0.0.17"
VERSION = __version__.split(".", 2)
VERSION[-1], _, BUILD = VERSION[-1].partition("-")

ROOT_LOGGER = logging.getLogger(__name__)

BRANDING = "BBBLB (AGPL-3, https://github.com/defnull/bbblb)"
