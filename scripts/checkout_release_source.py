"""Fetch one release source revision and hydrate its Git LFS payload."""

import argparse
import os
from pathlib import Path
import re
import subprocess


def checkout(url: str, destination: Path, commit: str = '') -> str:
    if commit and not re.fullmatch(r'[0-9a-fA-F]{40}', commit):
        raise ValueError('The source override must be a full 40-character commit SHA')
    if destination.exists():
        raise ValueError(f'Source checkout already exists: {destination}')

    destination.mkdir(parents=True)

    def git(*args, capture=False):
        return subprocess.run(
            ['git', '-C', str(destination), *args],
            check=True, text=True, stdout=subprocess.PIPE if capture else None,
            env={**os.environ, 'GIT_LFS_SKIP_SMUDGE': '1'},
        ).stdout

    git('init', '--quiet')
    git('remote', 'add', 'origin', url)
    git('fetch', '--depth=1', '--no-tags', 'origin', commit.lower() or 'refs/heads/main')
    revision = git('rev-parse', 'FETCH_HEAD^{commit}', capture=True).strip()
    if commit and revision != commit.lower():
        raise ValueError(f'Expected {commit}, fetched {revision}')
    git('checkout', '--quiet', '--detach', revision)
    git('lfs', 'pull', 'origin')
    if git('status', '--porcelain', capture=True).strip():
        raise ValueError(f'Source checkout is dirty after Git LFS download: {destination}')
    return revision


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('url')
    parser.add_argument('destination', type=Path)
    parser.add_argument('env_key', choices=('STABLE_COMMIT', 'OVERLAY_SOURCE_COMMIT'))
    parser.add_argument('--commit', default='')
    args = parser.parse_args()

    try:
        revision = checkout(args.url, args.destination, args.commit)
    except ValueError as exc:
        parser.error(str(exc))
    print(f'{args.env_key}={revision}', flush=True)
    with Path(os.environ['GITHUB_ENV']).open('a', encoding='utf-8') as output:
        output.write(f'{args.env_key}={revision}\n')


if __name__ == '__main__':
    main()
