#!/usr/bin/env python3
"""Import immutable builds from the official module source repositories."""
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import urllib.request
import zipfile
import registry as r

REPOSITORIES = {mid: 'PaNasMs/module-' + mid for mid in ('files', 'terminal', 'cloud-sync')}
REGISTRY = 'PaNasMs/module-registry'
SIGNER = 'panasms-ci'


def gh(*args, data=None):
    command = ['gh', *args]
    if data is not None:
        command += ['--input', '-']
    result = subprocess.run(command, input=None if data is None else json.dumps(data),
                            text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError(result.stderr)
    return result.stdout


def api(path, data=None, method=None):
    args = ['api', path]
    if method:
        args += ['-X', method]
    return json.loads(gh(*args, data=data))


def signed_archive(raw, mid, version, key):
    r.require(len(raw) <= r.MAX_ARCHIVE, 'Build payload too large')
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        entries = z.infolist()
        names = [i.filename for i in entries]
        r.require(len(names) == len(set(names)) and len(names) <= 2001, 'Duplicate or excessive payload entries')
        r.require(sum(i.file_size for i in entries) <= 256 * 1024 * 1024, 'Expanded payload too large')
        r.require(z.getinfo('manifest.json').file_size <= 256 * 1024, 'Manifest too large')
        m = json.loads(z.read('manifest.json'))
        r.validate_manifest(m)
        r.require(m['id'] == mid and m['version'] == version and m['architecture'] == 'arm64', 'Build identity mismatch')
        r.require(set(names) == {'manifest.json', *m['files']}, 'Unexpected payload files')
        payload = {}
        for name, digest in m['files'].items():
            payload[name] = z.read(name)
            r.require(hashlib.sha256(payload[name]).hexdigest() == digest, 'Build hash mismatch')
    m['signer'] = SIGNER
    manifest = json.dumps(m, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
    with tempfile.TemporaryDirectory() as folder:
        p = Path(folder)
        (p/'manifest').write_bytes(manifest)
        subprocess.run(['openssl','pkeyutl','-sign','-inkey',str(key),'-rawin','-in',str(p/'manifest'),'-out',str(p/'signature')],check=True)
        signature = (p/'signature').read_bytes()
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('bundle.json', json.dumps({'root': mid}))
        prefix = 'modules/' + mid + '/'
        z.writestr(prefix+'manifest.json', manifest)
        z.writestr(prefix+'signature', signature)
        for name, data in sorted(payload.items()):
            z.writestr(prefix+name, data)
    raw = out.getvalue()
    r.inspect_archive(raw)
    return raw


def publish_build(release, mid, repo, key):
    tag = release['tag_name']
    if release['draft'] or release['prerelease'] or not re.fullmatch('v'+r.VERSION, tag):
        return False
    version = tag[1:]
    entry = r.ROOT/'entries'/f'{mid}-{version}-arm64.json'
    if entry.exists():
        return False
    r.require(release.get('immutable'), 'Source release must be immutable')
    asset_name = f'{mid}-{version}-arm64.unsigned.zip'
    assets = [a for a in release['assets'] if a['name'] == asset_name]
    r.require(len(assets) == 1 and assets[0]['size'] <= r.MAX_ARCHIVE, 'Missing or oversized build asset')
    asset = assets[0]
    url = f'https://github.com/{repo}/releases/download/{tag}/{asset_name}'
    r.require(asset['browser_download_url'] == url, 'Unexpected build URL')
    with urllib.request.urlopen(url, timeout=90) as response:
        raw = response.read(r.MAX_ARCHIVE+1)
    digest = hashlib.sha256(raw).hexdigest()
    r.require(len(raw) == asset['size'] and asset.get('digest') == 'sha256:'+digest, 'Source download hash mismatch')
    release_tag = f'{mid}-v{version}'
    filename = f'{mid}-{version}-arm64.panasms'
    with tempfile.TemporaryDirectory() as folder:
        archive = Path(folder)/filename
        try:
            published = json.loads(gh('release','view',release_tag,'--repo',REGISTRY,'--json','isDraft'))
        except RuntimeError:
            published = None
        if published and not published['isDraft']:
            gh('release','download',release_tag,'--repo',REGISTRY,'--pattern',filename,'--dir',folder)
            m, _ = r.inspect_archive(archive.read_bytes())
            r.require(m['id']==mid and m['version']==version and m['files']==json.loads(zipfile.ZipFile(io.BytesIO(raw)).read('manifest.json'))['files'], 'Published package differs from source build')
        else:
            archive.write_bytes(signed_archive(raw, mid, version, key))
            if published is None:
                gh('release','create',release_tag,'--repo',REGISTRY,'--draft','--title',f'{mid} {version}',
                   '--notes',f'Automatically built from https://github.com/{repo}/releases/tag/{tag}\nSource payload SHA256: {digest}')
            gh('release','upload',release_tag,str(archive),'--repo',REGISTRY,'--clobber')
            gh('release','edit',release_tag,'--repo',REGISTRY,'--draft=false')
        subprocess.run(['python3',str(r.ROOT/'scripts/registry.py'),'import-release',str(archive)],check=True)
    provenance = r.ROOT/'provenance';provenance.mkdir(exist_ok=True)
    (provenance/f'{mid}-{version}.json').write_bytes(r.canonical({'repository':repo,'tag':tag,'releaseId':release['id'],'assetId':asset['id'],'sha256':digest}))
    return True


def commit_catalog():
    base = api(f'repos/{REGISTRY}/git/ref/heads/main')['object']['sha']
    # Refuse to merge a snapshot prepared against an older registry.
    local = subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()
    r.require(base == local, 'Registry changed during import; rerun to merge new entries')
    paths = ['catalog.json','catalog.sig']
    paths += [p.relative_to(r.ROOT).as_posix() for folder in ('entries','provenance') for p in (r.ROOT/folder).glob('*.json')]
    tree = api(f'repos/{REGISTRY}/git/trees', {'base_tree':api(f'repos/{REGISTRY}/git/commits/{base}')['tree']['sha'],
        'tree':[{'path':p,'mode':'100644','type':'blob','content':(r.ROOT/p).read_text()} for p in paths]})
    commit = api(f'repos/{REGISTRY}/git/commits', {'message':'Publish signed module builds','tree':tree['sha'],'parents':[base]})['sha']
    branch = 'automation/catalog-' + os.environ['GITHUB_RUN_ID'] + '-' + os.environ['GITHUB_RUN_ATTEMPT']
    api(f'repos/{REGISTRY}/git/refs', {'ref':'refs/heads/'+branch,'sha':commit})
    pr = api(f'repos/{REGISTRY}/pulls', {'title':'Publish signed module builds','head':branch,'base':'main','body':'Import immutable official builds, verify payload hashes, sign packages and update the catalog. All public archives and signatures were verified before this commit.'})
    api(f'repos/{REGISTRY}/statuses/{commit}', {'state':'success','context':'validate','description':'Registry tests and public package/signature validation passed','target_url':f'https://github.com/{REGISTRY}/actions/runs/'+os.environ['GITHUB_RUN_ID']})
    result = api(f'repos/{REGISTRY}/pulls/{pr["number"]}/merge', {'merge_method':'squash','sha':commit}, method='PUT')
    r.require(result.get('merged'), 'Catalog PR could not be merged')
    gh('workflow','run','pages.yml','--repo',REGISTRY,'--ref','main')


def main():
    changed = False
    with tempfile.TemporaryDirectory() as folder:
        key = Path(folder)/'signing.pem'
        key.write_text(os.environ.pop('MODULE_SIGNING_KEY'));key.chmod(0o600)
        for mid, repo in REPOSITORIES.items():
            releases = json.loads(gh('api',f'repos/{repo}/releases','--paginate','--slurp'))
            for page in releases:
                for release in reversed(page):
                    changed = publish_build(release,mid,repo,key) or changed
        if not changed:
            print('No new builds');return
        subprocess.run(['python3',str(r.ROOT/'scripts/registry.py'),'sign','--key',str(key),'--signer',SIGNER],check=True)
    subprocess.run(['python3','-m','unittest','discover','-s','tests','-v'],cwd=r.ROOT,check=True)
    subprocess.run(['python3',str(r.ROOT/'scripts/registry.py'),'verify','--online'],check=True)
    commit_catalog()

if __name__ == '__main__':
    main()
