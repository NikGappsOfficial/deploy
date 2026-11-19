"""Read-only release preflight: confirm source/catalog and publication access."""
import json
import os
import sys
import urllib.error
import urllib.request


def read(url, token, *, gitlab=False):
    headers = {'PRIVATE-TOKEN': token} if gitlab else {'Authorization': 'Bearer ' + token}
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as response:
            return json.load(response)
    except Exception:
        raise ValueError('Release access check failed for ' + url) from None


def check(environment, fetch=read):
    for field in ('G_TOKEN', 'GITLAB_TOKEN', 'SF_PWD'):
        if not environment.get(field):
            raise ValueError('Missing release credential: ' + field)
    for repository in ('nikgapps/tracker', 'nikgapps/nikgapps.github.io'):
        data = fetch('https://api.github.com/repos/' + repository, environment['G_TOKEN'])
        if not data.get('permissions', {}).get('push'):
            raise ValueError('G_TOKEN needs push access to ' + repository)
        print('GitHub push access verified: ' + repository)
    if environment.get('PACKAGES_GITHUB_TOKEN'):
        repository = environment.get('GITHUB_PACKAGES_REPOSITORY') or 'nikgapps/nikgapps-packages'
        data = fetch('https://api.github.com/repos/' + repository, environment['PACKAGES_GITHUB_TOKEN'])
        if not data.get('permissions', {}).get('push'):
            raise ValueError('NIKGAPPS_GITHUB_TOKEN needs push access to ' + repository)
        print('GitHub package mirror access verified: ' + repository)
    catalog = environment.get('CATALOG_PROJECT') or '85036487'
    if not catalog.isdigit():
        raise ValueError('CATALOG_PROJECT must be a numeric GitLab project ID')
    for project, minimum in ((catalog, 30), ('nikgapps%2F17_stable', 20), ('nikgapps%2Foverlays_17_source', 20)):
        data = fetch('https://gitlab.com/api/v4/projects/' + project, environment['GITLAB_TOKEN'], gitlab=True)
        roles = [role['access_level'] for role in data.get('permissions', {}).values() if role]
        if max(roles, default=0) < minimum:
            raise ValueError('GitLab token lacks release access to project ' + project)
        print('GitLab access verified: ' + data['path_with_namespace'])
    print('Read-only preflight passed; no packages, metadata or files were published')


if __name__ == '__main__':
    try:
        check(os.environ)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from None
