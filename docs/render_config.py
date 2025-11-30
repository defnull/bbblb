import sys, textwrap

format = sys.argv[1]
if format not in ("md", "env", "rst"):
    raise RuntimeError("Mode must be either md or env")

def render_option(option, typedef, default, comments):
    if format == 'md':
        default_phrase = f"default: `{default}`" if default else "**REQUIRED**"
        comments = comments or "*No documentation (TODO)*"
        print(f"`{option}` (type: `{typedef}`, {default_phrase})  ")
        for line in comments:
            print(line)
        print()

    elif format == 'rst':
        default_phrase = f"default: ``{default}``" if default else "**REQUIRED**"
        comments = comments or "*No documentation (TODO)*"
        print(f"``{option}`` (type: ``{typedef}``, {default_phrase})")
        print()
        for line in comments:
            print(line)
        print()

    elif format == 'env':
        default_phrase = f"default: {default}" if default else "REQUIRED"
        comments = comments or "No documentation (TODO)"
        for line in comments:
            print("# " + line)
        print(f"# ({default_phrase}; type: {typedef})")
        print(f"#BBBLB_{option}=")
        print()

if __name__ == '__main__':
    phase = "START"
    comments = []
    with open('bbblb/settings.py', 'r') as fp:
        for line in fp:
            if phase == "START":
                if "class BBBLBConfig" in line:
                    phase = "BODY"
            elif phase == "BODY":
                line = line.strip()
                if not line: continue
                if line.startswith("def ") or line.startswith("async def"):
                    break

                if line.startswith("#: ") or line.startswith("# "):
                    comments.append(line.split(None, 1)[1].strip())
                elif line.split()[0].isupper():
                    option, _ ,typedef = line.partition(":")
                    typedef, _ ,default = typedef.partition("=")
                    render_option(option.strip(), typedef.strip(), default.strip(), comments)
                    del comments[:]
                else:
                    del comments[:]

