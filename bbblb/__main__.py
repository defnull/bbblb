import secrets
import sys
import time
import jwt

from bbblb.settings import config

config.populate()


def cmd_maketoken(subject, maxage, *scopes):
    payload = {
        "sub": subject,
        "exp": int(time.time() + int(maxage)),
        "scope": " ".join(scopes),
        "jti": secrets.token_hex(8),
    }
    print(payload, file=sys.stderr)
    print(jwt.encode(payload, config.SECRET))


cmd = sys.argv[1]
locals()[f"cmd_{cmd}"](*sys.argv[2:])
