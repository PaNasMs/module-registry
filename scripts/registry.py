#!/usr/bin/env python3
"""Build and verify the offline-signed PaNasMs module registry."""
import argparse
import base64
import hashlib
import html
import io
import json
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
MAX_ARCHIVE = 128 * 1024 * 1024
VERSION = r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False) + '\n').encode()


def verify_signature(raw, signature, signer):
    require(re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', signer), 'Invalid signer')
    key = ROOT / 'keys' / (signer + '.pem')
    require(key.is_file(), 'Unknown signing key')
    require(len(signature) == 64, 'Invalid signature size')
    with tempfile.TemporaryDirectory() as folder:
        p = Path(folder)
        (p / 'data').write_bytes(raw)
        (p / 'sig').write_bytes(signature)
        result = subprocess.run(['openssl', 'pkeyutl', '-verify', '-pubin', '-inkey', str(key),
                                 '-rawin', '-in', str(p / 'data'), '-sigfile', str(p / 'sig')],
                                capture_output=True)
        require(result.returncode == 0, 'Invalid Ed25519 signature')


def validate_manifest(m):
    require(isinstance(m, dict), 'Invalid manifest')
    require(isinstance(m.get('id'), str) and re.fullmatch(r'[a-z][a-z0-9-]{1,39}', m['id']), 'Invalid module ID')
    require(isinstance(m.get('version'), str) and re.fullmatch(VERSION, m['version']), 'Invalid module version')
    require(type(m.get('api')) is int and m['api'] == 1, 'Unsupported module API')
    require(m.get('architecture') in ('arm64', 'amd64', 'all'), 'Invalid architecture')
    require(isinstance(m.get('core'), str) and all(re.fullmatch(r'\s*(>=|<=|>|<|=)?' + VERSION + r'\s*', p) for p in m['core'].split(',')), 'Invalid core compatibility')
    require(isinstance(m.get('title'), str) and 0 < len(m['title']) <= 120, 'Invalid title')
    require(m.get('license') == 'PolyForm-Noncommercial-1.0.0', 'Official module license missing')
    require(isinstance(m.get('files'), dict) and 0 < len(m['files']) <= 2000, 'Missing file hashes')
    require({'LICENSE', 'NOTICE', 'ui/index.js'} <= m['files'].keys(), 'Required distribution files missing')
    require(not m.get('service') or m['service'] in m['files'], 'Missing module server')
    for name, digest in m['files'].items():
        require(name in ('LICENSE', 'NOTICE') or (
            re.fullmatch(r'(ui|bin|backend)/[a-zA-Z0-9_./-]+', name) and
            all(p not in ('', '.', '..') for p in name.split('/'))), 'Invalid payload path')
        require(isinstance(digest, str) and re.fullmatch(r'[0-9a-f]{64}', digest), 'Invalid file hash')


def inspect_archive(raw):
    require(0 < len(raw) <= MAX_ARCHIVE, 'Archive exceeds size limit')
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        infos = archive.infolist()
        names = [i.filename for i in infos]
        require(len(names) == len(set(names)) and len(names) <= 6000, 'Duplicate or excessive archive members')
        require(sum(i.file_size for i in infos) <= 256 * 1024 * 1024, 'Expanded archive exceeds limit')
        for i in infos:
            require(not i.is_dir() and not stat.S_ISLNK(i.external_attr >> 16) and not i.flag_bits & 1,
                    'Unsupported archive member')
            require(not i.filename.startswith('/') and all(p not in ('', '.', '..') for p in i.filename.split('/')), 'Unsafe archive path')
        require(archive.getinfo('bundle.json').file_size <= 4096, 'Oversized bundle descriptor')
        mid = json.loads(archive.read('bundle.json'))['root']
        require(isinstance(mid, str) and re.fullmatch(r'[a-z][a-z0-9-]{1,39}', mid), 'Invalid bundle root')
        prefix = 'modules/' + mid + '/'
        require(archive.getinfo(prefix + 'manifest.json').file_size <= 256 * 1024, 'Oversized manifest')
        manifest_bytes = archive.read(prefix + 'manifest.json')
        manifest = json.loads(manifest_bytes)
        validate_manifest(manifest)
        require(manifest['id'] == mid, 'Module identity mismatch')
        require(manifest_bytes == json.dumps(manifest, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode(), 'Manifest is not canonical')
        signature = archive.read(prefix + 'signature')
        verify_signature(manifest_bytes, signature, manifest['signer'])
        expected = {'bundle.json', prefix + 'manifest.json', prefix + 'signature'}
        expected.update(prefix + name for name in manifest['files'])
        require(set(names) == expected, 'Unexpected or missing archive files')
        for name, digest in manifest['files'].items():
            require(hashlib.sha256(archive.read(prefix + name)).hexdigest() == digest, 'Corrupted payload: ' + name)
        return manifest, base64.b64encode(signature).decode()


def validate_entry(entry):
    require(set(entry) == {'manifest', 'manifestSignature', 'url', 'sha256', 'size', 'channel'}, 'Unexpected entry fields')
    m = entry['manifest']
    validate_manifest(m)
    require(entry['channel'] in ('stable', 'testing'), 'Invalid channel')
    require(type(entry['size']) is int and 0 < entry['size'] <= MAX_ARCHIVE, 'Invalid archive size')
    require(re.fullmatch(r'[0-9a-f]{64}', entry['sha256']), 'Invalid archive digest')
    filename = f"{m['id']}-{m['version']}-{m['architecture']}.panasms"
    pattern = r'https://github.com/PaNasMs/[a-z][a-z0-9-]*/releases/download/' + re.escape(m['id'] + '-v' + m['version']) + '/' + re.escape(filename)
    require(re.fullmatch(pattern, entry['url']), 'URL must reference the exact official GitHub release asset')
    raw = json.dumps(m, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
    verify_signature(raw, base64.b64decode(entry['manifestSignature'], validate=True), m['signer'])


def catalog():
    releases = {}
    identities = set()
    for p in sorted((ROOT / 'entries').glob('*.json')):
        entry = json.loads(p.read_text())
        validate_entry(entry)
        m = entry['manifest']
        identity = (m['id'], m['version'], m['architecture'])
        require(identity not in identities, 'Duplicate release identity')
        identities.add(identity)
        releases.setdefault(m['id'], []).append(entry)
    require(releases, 'Empty registry')
    modules = []
    for mid, versions in sorted(releases.items()):
        versions.sort(key=lambda v: (tuple(map(int, v['manifest']['version'].split('.'))), v['manifest']['architecture']), reverse=True)
        modules.append({'id': mid, 'releases': versions})
    return {'schemaVersion': 1, 'id': 'panasms-official', 'name': 'PaNasMs modules', 'modules': modules}


def verify_catalog():
    expected = canonical(catalog())
    require((ROOT / 'catalog.json').read_bytes() == expected, 'Rebuild and sign the catalog')
    envelope = json.loads((ROOT / 'catalog.sig').read_text())
    require(envelope.get('algorithm') == 'Ed25519', 'Unsupported signature algorithm')
    verify_signature(expected, base64.b64decode(envelope['signature'], validate=True), envelope['signer'])
    return json.loads(expected)


def verify_online(data):
    for module in data['modules']:
        for entry in module['releases']:
            request = urllib.request.Request(entry['url'], headers={'User-Agent': 'PaNasMs-registry-validator/1'})
            with urllib.request.urlopen(request, timeout=60) as response:
                raw = response.read(MAX_ARCHIVE + 1)
            require(len(raw) == entry['size'], 'Downloaded size mismatch')
            require(hashlib.sha256(raw).hexdigest() == entry['sha256'], 'Downloaded digest mismatch')
            manifest, signature = inspect_archive(raw)
            require(manifest == entry['manifest'] and signature == entry['manifestSignature'], 'Release does not match catalog')
            print('Verified public asset:', entry['url'])


def build_site(data):
    site = ROOT / 'site'
    site.mkdir(exist_ok=True)
    for name in ('catalog.json', 'catalog.sig', 'LICENSE', 'NOTICE'):
        shutil.copyfile(ROOT / name, site / name)
    shutil.copytree(ROOT / 'keys', site / 'keys', dirs_exist_ok=True)
    cards = []
    for module in data['modules']:
        release = module['releases'][0]
        m = release['manifest']
        e = html.escape
        cards.append(f'''<article><span class="tag">{e(m['architecture'])} · {e(release['channel'])}</span>
<h2>{e(m['title'])}</h2><p>{e(m.get('description', ''))}</p>
<dl><dt>Version</dt><dd>{e(m['version'])}</dd><dt>Core</dt><dd>{e(m['core'])}</dd><dt>License</dt><dd>PolyForm Noncommercial 1.0.0</dd></dl>
<a class="download" href="{e(release['url'])}">Download module <span>↓</span></a>
<details><summary>SHA256</summary><code>{e(release['sha256'])}</code></details></article>''')
    document = '''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>PaNasMs · Modules</title><meta name="description" content="Official signed modules for your PaNasMs home server.">
<style>body{margin:0;background:#10151d;color:#edf2fa;font:16px/1.6 system-ui,sans-serif}main{max-width:1000px;margin:auto;padding:64px 24px}.brand{font-weight:750;letter-spacing:-1px;font-size:24px;color:#a6d9c5}h1{font-size:clamp(38px,7vw,64px);letter-spacing:-2px;line-height:1.1;margin:32px 0 18px}.lead{color:#a9b4c4;max-width:640px}section{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:20px;margin:40px 0}article{background:#19212d;border:1px solid #303d4d;border-radius:18px;padding:26px}.tag{font-size:12px;text-transform:uppercase;letter-spacing:1px;color:#a6d9c5}h2{font-size:26px;margin:12px 0}article p{color:#a9b4c4;min-height:76px}dl{display:grid;grid-template-columns:75px 1fr;gap:8px;font-size:14px}dt{color:#8f9eaf}dd{margin:0}a{color:#a6d9c5}.download{display:flex;justify-content:space-between;border:1px solid #617f76;border-radius:9px;padding:10px 14px;text-decoration:none;margin:24px 0 14px}.download:hover{background:#263a37}summary{cursor:pointer;color:#a9b4c4;font-size:13px}code{overflow-wrap:anywhere;font-size:12px}footer{border-top:1px solid #303d4d;padding-top:24px;font-size:14px;color:#a9b4c4}nav{display:flex;gap:22px;flex-wrap:wrap}</style>
<main><div class="brand">PaNasMs</div><h1>A home for your modules.</h1><p class="lead">Official extensions for your home server. Versioned packages, signed with Ed25519 and checked before publication.</p><section>'''
    document += ''.join(cards) + '''</section><footer><p>Install or update with one click from the Modules page in PaNasMs core 0.2.1 or later. You can also download an archive and upload it in the panel.</p><nav><a href="catalog.json">JSON catalog</a><a href="catalog.sig">Catalog signature</a><a href="https://github.com/PaNasMs/module-registry">GitHub</a><a href="LICENSE">License</a></nav><p>Original software: PolyForm Noncommercial 1.0.0. Third-party components retain their own licenses.</p></footer></main></html>'''
    (site / 'index.html').write_text(document)
    (site / '.nojekyll').touch()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    imp = sub.add_parser('import-release')
    imp.add_argument('archive', type=Path)
    imp.add_argument('--repository', default='PaNasMs/module-registry')
    imp.add_argument('--channel', choices=['stable', 'testing'], default='stable')
    sign = sub.add_parser('sign')
    sign.add_argument('--key', required=True, type=Path)
    sign.add_argument('--signer', default='panasms-local')
    verify = sub.add_parser('verify')
    verify.add_argument('--online', action='store_true')
    build = sub.add_parser('build')
    build.add_argument('--online', action='store_true')
    args = parser.parse_args()
    if args.action == 'import-release':
        raw = args.archive.read_bytes()
        m, signature = inspect_archive(raw)
        entry = {'manifest': m, 'manifestSignature': signature, 'channel': args.channel,
                 'url': f"https://github.com/{args.repository}/releases/download/{m['id']}-v{m['version']}/{m['id']}-{m['version']}-{m['architecture']}.panasms",
                 'sha256': hashlib.sha256(raw).hexdigest(), 'size': len(raw)}
        validate_entry(entry)
        dest = ROOT / 'entries' / f"{m['id']}-{m['version']}-{m['architecture']}.json"
        require(not dest.exists(), 'Release entry already exists; publish a new version')
        dest.write_bytes(canonical(entry))
    elif args.action == 'sign':
        raw = canonical(catalog())
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder)
            (p / 'catalog').write_bytes(raw)
            subprocess.run(['openssl', 'pkeyutl', '-sign', '-inkey', str(args.key), '-rawin', '-in', str(p / 'catalog'), '-out', str(p / 'sig')], check=True)
            signature = (p / 'sig').read_bytes()
            verify_signature(raw, signature, args.signer)
        (ROOT / 'catalog.json').write_bytes(raw)
        (ROOT / 'catalog.sig').write_bytes(canonical({'algorithm': 'Ed25519', 'signer': args.signer, 'signature': base64.b64encode(signature).decode()}))
    else:
        data = verify_catalog()
        if args.online:
            verify_online(data)
        if args.action == 'build':
            build_site(data)
        print('Verified signed catalog:', len(data['modules']), 'modules')


if __name__ == '__main__':
    main()
