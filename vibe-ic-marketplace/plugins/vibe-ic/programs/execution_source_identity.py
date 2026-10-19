"""Source identity for Git development and officially installed Ultra packages.

Only normal Ultra installation preparation contacts the fixed upstream tag.
Runtime readers consume that installer-owned manifest offline. This is a local
installation trust boundary, not a signature or a hostile-host security service.
Default execution does not need this receipt or installation preparation.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import urllib.request

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
PREFIX = 'vibe-ic-marketplace/plugins/vibe-ic/'
REPOSITORY = 'https://github.com/vibeic/vibe-ic.git'
API = 'https://api.github.com/repos/vibeic/vibe-ic'


def _encoded(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n').encode()


def _sha(content):
    return hashlib.sha256(content).hexdigest()


def _blob(content):
    return hashlib.sha1(b'blob ' + str(len(content)).encode() + b'\0' + content).hexdigest()


def source_root(package_root=None):
    package = Path(package_root or PACKAGE_ROOT).resolve()
    for ancestor in package.parents:
        if (ancestor / PREFIX).resolve() == package and (ancestor / '.git').exists():
            return ancestor
    return package


def receipt_path(package_root=None):
    package = Path(package_root or PACKAGE_ROOT).resolve()
    cache = Path(os.environ.get('XDG_CACHE_HOME') or Path.home() / '.cache')
    return cache / 'vibe-ic/source-identities/installed' / (_sha(str(package).encode()) + '.json')


def _official_path(version, package_root=None):
    return receipt_path(package_root).parents[1] / 'official' / (version + '.json')


def _version(package):
    value = json.loads((package / '.claude-plugin/plugin.json').read_text()).get('version')
    if not isinstance(value, str) or not re.fullmatch(r'\d+\.\d+\.\d+', value):
        raise RuntimeError('SOURCE_AUTHORITY_UNAVAILABLE: invalid installed version')
    return value


def _official_json(url):
    """The one installation transport seam; callers cannot select a repository."""
    request = urllib.request.Request(url, headers={'Accept': 'application/vnd.github+json',
                                                 'User-Agent': 'vibe-ic-installation'})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def _fetch_official(version):
    target = _official_json(API + '/git/ref/tags/v' + version)['object']
    while target['type'] == 'tag':
        target = _official_json(API + '/git/tags/' + target['sha'])['object']
    if target['type'] != 'commit' or not re.fullmatch('[0-9a-f]{40}', target['sha']):
        raise RuntimeError('SOURCE_AUTHORITY_UNAVAILABLE: official tag is not a commit')
    revision = target['sha']
    commit = _official_json(API + '/git/commits/' + revision)
    tree_sha = (commit.get('tree') or {}).get('sha')
    if not isinstance(tree_sha, str) or not re.fullmatch('[0-9a-f]{40}', tree_sha):
        raise RuntimeError('SOURCE_AUTHORITY_UNAVAILABLE: official commit tree missing')
    tree = _official_json(API + '/git/trees/' + tree_sha + '?recursive=1')
    if tree.get('truncated') is not False or tree.get('sha') != tree_sha:
        raise RuntimeError('SOURCE_AUTHORITY_UNAVAILABLE: incomplete official tree')
    blobs = {row['path'][len(PREFIX):]: row['sha'] for row in tree['tree']
             if row.get('type') == 'blob' and row.get('mode') in ('100644', '100755')
             and row.get('path', '').startswith(PREFIX)}
    package_tree = next(row['sha'] for row in tree['tree']
                        if row.get('path') == PREFIX.rstrip('/') and row.get('type') == 'tree')
    if not blobs or any(not re.fullmatch('[0-9a-f]{40}', value) for value in blobs.values()):
        raise RuntimeError('SOURCE_AUTHORITY_UNAVAILABLE: official package objects missing')
    return dict(schema=1, repository=REPOSITORY, version=version, tag='v' + version,
                revision=revision, tree=tree['sha'], package_tree=package_tree, blobs=blobs)


def _write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_bytes(content)
    os.replace(temporary, path)


def prepare_installed(package_root=None):
    """Complete normal installed Ultra preparation once, before issuing a run.

    Never enroll current working bytes as their own authority. The fixed
    official tag supplies every expected blob; invalid existing state refuses.
    """
    package = Path(package_root or PACKAGE_ROOT).resolve()
    if source_root(package) != package:
        return {'kind': 'git', 'package_root': str(package)}
    version = _version(package)
    path = receipt_path(package)
    if path.is_file():
        prior = json.loads(path.read_text())
        if prior.get('version') == version:
            value = current(package)
            for relative in value.blobs:
                value.verify(package / relative)
            return prior
    official_path = _official_path(version, package)
    if official_path.is_file():
        official = json.loads(official_path.read_text())
    else:
        official = _fetch_official(version)
    if (official.get('repository') != REPOSITORY or official.get('version') != version or
            official.get('tag') != 'v' + version):
        raise RuntimeError('SOURCE_AUTHORITY_UNAVAILABLE: official installation metadata mismatch')
    files = {}
    for relative, expected in official['blobs'].items():
        file = package / relative
        if file.is_symlink() or not file.is_file() or _blob(file.read_bytes()) != expected:
            raise RuntimeError('SOURCE_AUTHORITY_DIRTY: ' + relative)
        data = file.read_bytes()
        files[relative] = dict(blob=expected, sha256=_sha(data), bytes=len(data))
    official_bytes = _encoded(official)
    _write(official_path, official_bytes)
    record = dict(schema=1, kind='installed', package_root=str(package), version=version,
                  revision=official['revision'], tree=official['tree'],
                  official_sha256=_sha(official_bytes), files=files)
    record['receipt_sha256'] = _sha(_encoded(record))
    _write(path, _encoded(record))
    return record


class SourceIdentity:
    def __init__(self, package):
        self.package_root = package
        self.root = source_root(package)
        self.kind = 'git' if self.root != package else 'installed'
        self._blobs = None
        if self.kind == 'git':
            self.revision = self._git('rev-parse', 'HEAD').strip()
            self.tree = self._git('rev-parse', 'HEAD^{tree}').strip()
            return
        try:
            path = receipt_path(package)
            self._receipt_bytes = path.read_bytes()
            record = json.loads(self._receipt_bytes)
            version = _version(package)
            self._official_bytes = _official_path(version, package).read_bytes()
            official = json.loads(self._official_bytes)
            digest = record.pop('receipt_sha256', None)
            if (digest != _sha(_encoded(record)) or record.get('schema') != 1 or
                    record.get('kind') != 'installed' or record.get('package_root') != str(package) or
                    record.get('version') != version or official.get('version') != version or
                    official.get('repository') != REPOSITORY or official.get('tag') != 'v' + version or
                    record.get('official_sha256') != _sha(self._official_bytes) or
                    any(record.get(key) != official.get(key) or
                        not re.fullmatch('[0-9a-f]{40}', str(record.get(key, '')))
                        for key in ('revision', 'tree')) or
                    {p: row.get('blob') for p, row in record['files'].items()} != official['blobs']):
                raise RuntimeError('SOURCE_AUTHORITY_UNAVAILABLE: installed receipt binding invalid')
            self.revision, self.tree = record['revision'], record['tree']
            self._blobs = dict(official['blobs'])
            self._files = record['files']
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise RuntimeError('SOURCE_AUTHORITY_UNAVAILABLE: installed source receipt required') from exc

    def _git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.root), *args], text=True)

    @property
    def blobs(self):
        if self._blobs is None:
            raw = subprocess.check_output(['git', '-C', str(self.root), 'ls-tree', '-r', '-z', self.revision])
            self._blobs = {}
            for row in raw.split(b'\0'):
                if row:
                    header, name = row.split(b'\t', 1)
                    mode, kind, blob = header.decode().split()
                    if kind == 'blob' and mode in ('100644', '100755'):
                        self._blobs[name.decode()] = blob
        return self._blobs

    def verify_current(self):
        if self.kind == 'git':
            if self._git('rev-parse', 'HEAD').strip() != self.revision or self._git('rev-parse', 'HEAD^{tree}').strip() != self.tree:
                raise RuntimeError('SOURCE_AUTHORITY_STALE')
        elif (receipt_path(self.package_root).read_bytes() != self._receipt_bytes or
              _official_path(_version(self.package_root), self.package_root).read_bytes() != self._official_bytes):
            raise RuntimeError('SOURCE_AUTHORITY_STALE: installation changed')

    def verify(self, path):
        self.verify_current()
        path = Path(path)
        if path.is_symlink() or not path.is_file():
            raise RuntimeError('SOURCE_AUTHORITY_UNAVAILABLE: ' + str(path))
        relative = path.resolve().relative_to(self.root).as_posix()
        data = path.read_bytes()
        if self.blobs.get(relative) != _blob(data):
            raise RuntimeError('SOURCE_AUTHORITY_DIRTY: ' + relative)
        if self.kind == 'installed':
            row = self._files[relative]
            if row.get('sha256') != _sha(data) or row.get('bytes') != len(data):
                raise RuntimeError('SOURCE_AUTHORITY_DIRTY: ' + relative)
        return self.blobs[relative]


def current(package_root=None):
    return SourceIdentity(Path(package_root or PACKAGE_ROOT).resolve())
