"""Import only release credentials from the existing data-only myenv export."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import sys
import urllib.request

from dotenv import dotenv_values

URL = 'https://api.github.com/repos/nikhilmenghani/myenv/contents/.env?ref=main'


def release_environment(raw: bytes) -> dict[str, str]:
    if len(raw) > 1024 * 1024:
        raise ValueError('myenv export is unexpectedly large')
    if raw.lstrip().startswith(b'{'):
        document = json.loads(raw)
        document = document.get('secrets', document)
    else:
        document = dotenv_values(stream=io.StringIO(raw.decode('utf-8')), interpolate=False)
    values = {
        'GITLAB_TOKEN': document.get('NIKGAPPS_GITLAB_TOKEN') or document.get('GITLAB_TOKEN'),
        'SF_PWD': document.get('SF_PWD'),
        'USER_NAME': document.get('USER_NAME') or 'NikGapps',
        'EMAIL': document.get('USER_EMAIL') or document.get('EMAIL') or 'nikgapps@gmail.com',
        'SF_USER': document.get('SF_USER') or os.environ.get('SF_USER') or 'nikhilmenghani',
    }
    for name, value in values.items():
        if not isinstance(value, str) or not value or any(c in value for c in '\r\n\0'):
            raise ValueError(f'myenv is missing a valid single-line release field: {name}')
    publisher = document.get('NIKGAPPS_GITHUB_TOKEN')
    if publisher is not None:
        if not isinstance(publisher, str) or not publisher or any(c in publisher for c in '\r\n\0'):
            raise ValueError('myenv has an invalid NIKGAPPS_GITHUB_TOKEN')
        values['PACKAGES_GITHUB_TOKEN'] = publisher
    return values


def load(token: str) -> dict[str, str]:
    if not token:
        raise ValueError('G_TOKEN is required to read myenv')
    request = urllib.request.Request(URL, headers={
        'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github.raw+json'})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read(1024 * 1024 + 1)
    except Exception:
        raise ValueError('Cannot read myenv; check G_TOKEN access and expiry') from None
    return release_environment(raw)


def export(values: dict[str, str], destination: Path) -> None:
    for name in ('GITLAB_TOKEN', 'SF_PWD', 'PACKAGES_GITHUB_TOKEN'):
        if name in values:
            print(f"::add-mask::{values[name].replace('%', '%25')}", flush=True)
    with destination.open('a', encoding='utf-8', newline='\n') as stream:
        for name, value in values.items():
            stream.write(f'{name}={value}\n')


def main():
    try:
        destination = os.environ.get('GITHUB_ENV')
        if not destination:
            raise ValueError('Run this exporter inside GitHub Actions (GITHUB_ENV is required)')
        export(load(os.environ.get('G_TOKEN', '')), Path(destination))
        print('Loaded release credentials and commit identity from myenv')
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
