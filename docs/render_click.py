import inspect
from bbblb.cli import main
import click

def strformat(callable, ctx):
    fmt = ctx.make_formatter()
    callable(ctx,fmt)
    return fmt.getvalue().strip()

def print_table(title, header: list[str], rows: list[list[str]]):
    widths = [max([len(header[col])] + [len(row[col]) for row in rows]) for col in range(len(header))]
    lines = [max(len(c.splitlines()) for c in row) for row in rows]
    print(f".. table:: {title}")
    print("  :width: 100%")
    print("")
    print("  " + '  '.join("="*w for w in widths))
    print("  " + '  '.join(t + " "*(w-len(t)) for w,t in zip(widths, header)))
    print("  " + '  '.join("="*w for w in widths))
    for rn, row in enumerate(rows):
        for ln in range(lines[rn]):
            lrow = [(t.splitlines()[ln] if len(t.splitlines()) > ln else "") for t in row]
            print("  " + '  '.join(t + " "*(w-len(t)) for w,t in zip(widths, lrow)))
    print("  " + '  '.join("="*w for w in widths))


def print_recursive(cmd: click.Command, parent=None):
    ctx = click.Context(cmd, info_name=cmd.name, parent=parent, max_content_width=60, **cmd.context_settings)
    print(f"{' '.join(ctx.command_path.split()[1:] or ["bbblb"])}")
    print("~" * 80)
    print()
    print(f"``{' '.join(s.strip() for s in strformat(cmd.format_usage, ctx).splitlines())}``")
    print()
    if cmd.help is not None:
        # truncate the help text to the first form feed
        help = inspect.cleandoc(cmd.help).partition("\f")[0]
        print(help)
        print()

    opts = []
    for param in cmd.get_params(ctx):
        if param is cmd.get_help_option(ctx):
            continue
        rv = param.get_help_record(ctx)
        if rv is None and isinstance(param, click.Argument):
            rv = (param.human_readable_name, f"{"Required" if param.required else "Optional"} argument")
        if rv is not None:
            opts.append(rv)
    if opts:
        print_table("Options", ["Option", "Help"], [[o, h] for o, h in opts])
        print()

    if isinstance(cmd, click.Group):
        print_table("Sub-Commands", ["Command", "Help"], [[x.name or "", inspect.cleandoc(x.help or "").partition("\n")[0] or ""] for x in cmd.commands.values()])
        print()

    if isinstance(cmd, click.Group):
        for name, sub in cmd.commands.items():
            print_recursive(sub, ctx)

print_recursive(main)