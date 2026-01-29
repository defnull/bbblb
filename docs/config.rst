=============
Configuration
=============

BBBLB can be configured via environment variables or :ref:`command line <cli>` parameters. Here is a list of all possible configuration sources, ordered from highest to lowest priority:

* Individual settings defined in via command line parameters (e.g. `-c debug=True` or `--config debug=True`).
* Environment variables starting with `BBBLB_` (e.g. `BBBLB_DEBUG=True`).
* Settings loaded from a file (e.g `-C /etc/bbblb.env` or `--config-file /etc/bbblb.env`).
* Built-in defaults.

Environment variables must be uppercase and start with `BBBLB_` to be recognized (e.g. `BBBLB_DEBUG=True`). The special environment variable `BBBLB_CONFIG` can be used instead of the `--config-file` cli parameter to load values from a file.

The config file should follow a simplified env-file syntax, similar to how variables are defined in shell scripts: One `NAME=value` pair per line. Empty lines or lines starting with `#` are ignored. The variable name must be uppercase and can optionally start with `BBBLB_`. Leading or tailing whitespace is removed from the value. Quotes are also removed, but can be used to preserve whitespace.

Config Options
--------------

This is an auto-generated list of all available config options, ordered loosely by topic.

.. include:: _config.rst

