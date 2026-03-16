# Copyright (C) 2025, 2026  Marcel Hellkamp
# SPDX-License-Identifier: AGPL-3.0-or-later

import ast
from enum import Enum
import inspect
from itertools import pairwise
from pathlib import Path
import sys
import textwrap
import types
import typing
import bbblb.settings


def render_option(option, typedef, default, comments):
    if format == "md":
        default_phrase = f"default: `{default}`" if default else "**REQUIRED**"
        comments = comments or "*No documentation (TODO)*"
        print(f"`{option}` (type: `{typedef}`, {default_phrase})  ")
        print(comments)
        print()

    elif format == "rst":
        default_phrase = f"default: ``{default}``" if default else "**REQUIRED**"
        comments = comments or "*No documentation (TODO)*"
        print(f"``{option}`` (type: ``{typedef}``, {default_phrase})")
        print()
        print(comments)
        print()

    elif format == "env":
        default_phrase = f"default: {default}" if default else "REQUIRED"
        comments = comments or "No documentation (TODO)"
        for line in comments.splitlines():
            print("# " + line)
        print(f"# ({default_phrase}; type: {typedef})")
        print(f"#BBBLB_{option}=")
        print()


def typedef_to_str(tdef):
    if tdef in (str, int, float, bool):
        return tdef.__name__
    if tdef is Path:
        return "Path"
    if tdef is None or tdef is type(None):
        return "None"
    if isinstance(tdef, type) and issubclass(tdef, Enum):
        return f"{'|'.join(tdef._member_names_)}"
    if typing.get_origin(tdef) in (typing.Union, types.UnionType):
        return '|'.join(map(typedef_to_str, typing.get_args(tdef)))
    return repr(tdef)


def value_to_str(value):
    if isinstance(value, (str, int, float, bool)):
        return repr(value)
    if isinstance(value, Path):
        return repr(str(value))
    if isinstance(value, Enum):
        return value._name_
    return repr(value)


if __name__ == "__main__":
    format = sys.argv[1]
    if format not in ("md", "env", "rst"):
        raise RuntimeError("Mode must be either md or env")

    for klass in reversed(bbblb.settings.BBBLBConfig.__mro__):
        if not issubclass(klass, bbblb.settings.BaseConfig):
            continue
        instance = klass()
        node = ast.parse(textwrap.dedent(inspect.getsource(klass))).body[0]
        assert isinstance(node, ast.ClassDef)

        for a, b in pairwise(node.body):
            if not isinstance(a, (ast.Assign, ast.AnnAssign)):
                continue
            if (
                not isinstance(b, ast.Expr)
                or not isinstance(b.value, ast.Constant)
                or not isinstance(b.value.value, str)
            ):
                continue

            target = a.targets[0] if isinstance(a, ast.Assign) else a.target
            if not isinstance(target, ast.Name) or target.id not in instance._options:
                continue

            name = target.id
            docs = inspect.cleandoc(b.value.value).strip()
            typedef = typedef_to_str(instance._options[name])
            default = (
                value_to_str(instance.__dict__[name])
                if name in instance.__dict__
                else None
            )
            render_option(target.id, typedef, default, docs)
