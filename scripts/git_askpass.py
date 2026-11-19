#!/usr/bin/env python3
"""Give Git host-specific HTTPS credentials without putting tokens in URLs."""
import os
import re
import sys
from urllib.parse import urlparse


def answer(prompt: str, environment) -> str:
    match = re.search(r"https://[^'\s]+", prompt)
    host = urlparse(match.group(0)).hostname if match else None
    credentials = {'github.com': ('x-access-token', 'G_TOKEN'),
                   'gitlab.com': ('oauth2', 'GITLAB_TOKEN')}
    if host not in credentials:
        raise ValueError('Unsupported Git credential host')
    username, field = credentials[host]
    if prompt.lower().startswith('username'):
        return username
    if not prompt.lower().startswith('password') or not environment.get(field):
        raise ValueError('Git credential is unavailable')
    return environment[field]


if __name__ == '__main__':
    try:
        print(answer(' '.join(sys.argv[1:]), os.environ))
    except ValueError:
        raise SystemExit(1) from None
