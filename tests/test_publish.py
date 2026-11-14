import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import call, patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import publish
import registry

class Publish(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.key=Path(self.temp.name)/'key'
        subprocess.run(['openssl','genpkey','-algorithm','ED25519','-out',str(self.key)],check=True)
        self.root=Path(self.temp.name);(self.root/'keys').mkdir()
        subprocess.run(['openssl','pkey','-in',str(self.key),'-pubout','-out',str(self.root/'keys/panasms-ci.pem')],check=True)
        p=patch.object(registry,'ROOT',self.root);p.start();self.addCleanup(p.stop)
    def payload(self, corrupt=False, extra=False, arch="arm64", license="SUL-1.0"):
        files={'LICENSE':b'license','NOTICE':b'notice','ui/index.js':b'js','bin/server':b'elf'}
        m={'id':'files','version':'0.2.9','architecture':arch,'api':1,'core':'>=0.2.0,<0.3.0','title':'Files','license':license,'service':'bin/server','files':{k:hashlib.sha256(v).hexdigest() for k,v in files.items()}}
        out=io.BytesIO()
        with zipfile.ZipFile(out,'w') as z:
            z.writestr('manifest.json',json.dumps(m))
            for name,value in files.items():z.writestr(name,value+b'x' if corrupt else value)
            if extra:z.writestr('../escape',b'x')
        return out.getvalue()
    def test_sign_and_verify_exact_payload(self):
        raw=publish.signed_archive(self.payload(),'files','0.2.9',self.key)
        m,_=registry.inspect_archive(raw)
        self.assertEqual(m['signer'],'panasms-ci')
        self.assertEqual(m['id'],'files')
    def test_identity_mismatch(self):
        for mid,version in [('terminal','0.2.9'),('files','9.9.9')]:
            with self.assertRaisesRegex(ValueError,'identity'):publish.signed_archive(self.payload(),mid,version,self.key)
    def test_tampered_payload_and_extra_paths(self):
        for raw in [self.payload(corrupt=True),self.payload(extra=True)]:
            with self.assertRaises(ValueError):publish.signed_archive(raw,'files','0.2.9',self.key)
    def test_unpublished_or_prerelease_ignored(self):
        for release in [{'draft':True,'prerelease':False,'tag_name':'v0.2.9'},{'draft':False,'prerelease':True,'tag_name':'v0.2.9'}]:
            self.assertFalse(publish.publish_build(release,'files','PaNasMs/module-files',self.key))
    def test_mutable_release_rejected(self):
        with self.assertRaisesRegex(ValueError,'immutable'):
            publish.publish_build({'draft':False,'prerelease':False,'tag_name':'v0.2.9','immutable':False},'files','PaNasMs/module-files',self.key)

    def test_amd64_signing_rejects_mislabeled_payload(self):
        raw = publish.signed_archive(self.payload(arch='amd64'),'files','0.2.9',self.key,'amd64')
        self.assertEqual(registry.inspect_archive(raw)[0]['architecture'],'amd64')
        with self.assertRaisesRegex(ValueError,'identity'):
            publish.signed_archive(self.payload(),'files','0.2.9',self.key,'amd64')

    def test_multi_architecture_release_finalized_only_after_both_uploads(self):
        payloads={arch:self.payload(arch=arch) for arch in ('amd64','arm64')}
        assets=[{'name':f'files-0.2.9-{arch}.unsigned.zip','size':len(raw),'digest':'sha256:'+hashlib.sha256(raw).hexdigest(),'id':idx,'browser_download_url':f'https://github.com/PaNasMs/module-files/releases/download/v0.2.9/files-0.2.9-{arch}.unsigned.zip'} for idx,(arch,raw) in enumerate(payloads.items())]
        release={'draft':False,'prerelease':False,'tag_name':'v0.2.9','immutable':True,'id':1,'assets':assets}
        calls=[];realrun=subprocess.run
        def fakegh(*args,**kwargs):
            calls.append(args)
            if args[:2]==('release','view'):raise RuntimeError('not found')
            return ''
        def fakerun(args,**kwargs):
            if args[0]=='python3':calls.append(('import',args[-1]));return
            return realrun(args,**kwargs)
        with patch.object(publish,'gh',side_effect=fakegh), patch.object(publish.urllib.request,'urlopen',side_effect=lambda url,**kw:io.BytesIO(payloads['amd64' if '-amd64.' in url else 'arm64'])),patch.object(publish.subprocess,'run',side_effect=fakerun):
            self.assertTrue(publish.publish_build(release,'files','PaNasMs/module-files',self.key))
        upload=next(c for c in calls if c[:2]==('release','upload'))
        self.assertEqual(sum(str(x).endswith('.panasms') for x in upload),2)
        self.assertLess(next(i for i,c in enumerate(calls) if c[:2]==('release','edit')), next(i for i,c in enumerate(calls) if c[0]=='import'))
        self.assertEqual(len(list((self.root/'provenance').glob('*.json'))),2)
        assets[1]['digest']='sha256:'+'0'*64
        with patch.object(publish,'gh') as gh,patch.object(publish.urllib.request,'urlopen',side_effect=lambda url,**kw:io.BytesIO(payloads['amd64' if '-amd64.' in url else 'arm64'])):
            with self.assertRaisesRegex(ValueError,'hash mismatch'):publish.publish_build(release,'files','PaNasMs/module-files',self.key)
            gh.assert_not_called()

    def test_duplicate_architecture_assets_rejected(self):
        asset={'name':'files-0.2.9-amd64.unsigned.zip','size':12}
        with self.assertRaisesRegex(ValueError,'Duplicate'):publish.build_assets({'assets':[asset,asset]},'files','0.2.9')

    def test_current_and_legacy_license_signatures(self):
        for license in ('SUL-1.0', 'LicenseRef-PaNasMs-Source-Available-1.0', 'PolyForm-Noncommercial-1.0.0'):
            with self.subTest(license=license):
                raw = publish.signed_archive(self.payload(license=license), 'files', '0.2.9', self.key)
                manifest, _ = registry.inspect_archive(raw)
                self.assertEqual(manifest['license'], license)
        with self.assertRaisesRegex(ValueError, 'license'):
            publish.signed_archive(self.payload(license='Apache-2.0'), 'files', '0.2.9', self.key)

    def test_catalog_page_preserves_per_release_license(self):
        for name in ('catalog.json', 'catalog.sig', 'LICENSE', 'NOTICE'):
            (self.root / name).write_text('test')
        releases = []
        for license in ('SUL-1.0', 'LicenseRef-PaNasMs-Source-Available-1.0', 'PolyForm-Noncommercial-1.0.0'):
            releases.append({'releases': [{'manifest': {'architecture': 'arm64', 'title': 'Test',
                'version': '0.2.9', 'core': '>=0.2.0', 'license': license}, 'channel': 'stable',
                'url': 'https://example.com/module', 'sha256': '0' * 64}]})
        registry.build_site({'modules': releases})
        page = (self.root / 'site/index.html').read_text()
        self.assertIn('<dd>SUL-1.0</dd>', page)
        self.assertIn('<dd>LicenseRef-PaNasMs-Source-Available-1.0</dd>', page)
        self.assertIn('<dd>PolyForm-Noncommercial-1.0.0</dd>', page)


class CatalogImport(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for directory in ('entries', 'provenance'):
            (self.root / directory).mkdir()
        for name in ('catalog.json', 'catalog.sig', 'entries/current.json'):
            (self.root / name).write_text('{}')
        root = patch.object(registry, 'ROOT', self.root)
        root.start()
        self.addCleanup(root.stop)

    def test_catalog_commit_stops_at_reviewable_pull_request(self):
        base = 'a' * 40
        commit = 'b' * 40
        prefix = 'repos/' + publish.REGISTRY

        def api(path, data=None, method=None):
            self.assertIsNone(method)
            responses = {
                prefix + '/git/ref/heads/main': {'object': {'sha': base}},
                prefix + '/git/commits/' + base: {'tree': {'sha': 'base-tree'}},
                prefix + '/git/trees': {'sha': 'catalog-tree'},
                prefix + '/git/commits': {'sha': commit},
                prefix + '/git/refs': {},
                prefix + '/pulls': {'number': 1, 'html_url': 'https://github.com/' + publish.REGISTRY + '/pull/1'},
                prefix + '/statuses/' + commit: {},
            }
            return responses[path]

        with patch.object(publish, 'api', side_effect=api) as requests, \
                patch.object(publish, 'gh') as gh, \
                patch.object(publish.subprocess, 'check_output', return_value=base + '\n'), \
                patch.dict(publish.os.environ, {'GITHUB_RUN_ID': '123', 'GITHUB_RUN_ATTEMPT': '1'}):
            publish.commit_catalog()
        gh.assert_not_called()
        tree = next(c.args[1] for c in requests.call_args_list if c.args[0] == prefix + '/git/trees')
        self.assertEqual(tree['base_tree'], 'base-tree')
        self.assertEqual({item['path'] for item in tree['tree']}, {'catalog.json', 'catalog.sig', 'entries/current.json'})
        pr = next(c.args[1] for c in requests.call_args_list if c.args[0] == prefix + '/pulls')
        self.assertEqual(pr['base'], 'main')
        self.assertEqual(pr['head'], 'automation/catalog-123-1')
        self.assertEqual(requests.call_args.args[0], prefix + '/statuses/' + commit)

    def test_stale_catalog_cannot_create_a_branch_or_pull_request(self):
        with patch.object(publish, 'api', return_value={'object': {'sha': 'new-base'}}) as api, \
                patch.object(publish.subprocess, 'check_output', return_value='old-base\n'):
            with self.assertRaisesRegex(ValueError, 'Registry changed during import'):
                publish.commit_catalog()
        api.assert_called_once_with('repos/' + publish.REGISTRY + '/git/ref/heads/main')

    def test_pending_catalog_on_later_page_defers_import_until_merged_or_closed(self):
        pending = {'head': {'ref': 'automation/catalog-123-1', 'repo': {'full_name': publish.REGISTRY}},
                   'html_url': 'https://github.com/' + publish.REGISTRY + '/pull/1'}
        unrelated = {'head': {'ref': 'dependency-update', 'repo': {'full_name': publish.REGISTRY}}}
        with patch.object(publish, 'gh', return_value=json.dumps([[unrelated], [pending]])) as gh, \
                patch.object(publish, 'publish_build') as publish_build, \
                patch.object(publish, 'commit_catalog') as commit_catalog, \
                patch.object(publish.subprocess, 'run') as run, \
                patch.dict(publish.os.environ, {}, clear=True):
            publish.main()
        gh.assert_called_once_with('api', 'repos/' + publish.REGISTRY + '/pulls?state=open&base=main&per_page=100', '--paginate', '--slurp')
        publish_build.assert_not_called()
        commit_catalog.assert_not_called()
        run.assert_not_called()

        release = {'tag_name': 'v0.1.7'}
        with patch.object(publish, 'gh', side_effect=[json.dumps([[unrelated]]), json.dumps([[release]])]), \
                patch.object(publish, 'REPOSITORIES', {'backup': 'PaNasMs/module-backup'}), \
                patch.object(publish, 'publish_build', return_value=True) as publish_build, \
                patch.object(publish, 'commit_catalog') as commit_catalog, \
                patch.object(publish.subprocess, 'run') as run, \
                patch.dict(publish.os.environ, {'MODULE_SIGNING_KEY': 'test-key'}, clear=True):
            publish.main()
        publish_build.assert_called_once()
        self.assertEqual(publish_build.call_args.args[:3], (release, 'backup', 'PaNasMs/module-backup'))
        self.assertEqual([c.args[0][2] for c in run.call_args_list], ['sign', 'unittest', 'verify'])
        commit_catalog.assert_called_once_with()

    def test_fork_or_deleted_head_does_not_block_official_imports(self):
        for repo in ({'full_name': 'someone/module-registry'}, None):
            with self.subTest(repo=repo):
                pending = {'head': {'ref': 'automation/catalog-123-1', 'repo': repo}}
                with patch.object(publish, 'gh', side_effect=[json.dumps([[pending]]), '[]']) as gh, \
                        patch.object(publish, 'REPOSITORIES', {'backup': 'PaNasMs/module-backup'}), \
                        patch.object(publish, 'commit_catalog') as commit_catalog, \
                        patch.dict(publish.os.environ, {'MODULE_SIGNING_KEY': 'test-key'}, clear=True):
                    publish.main()
                self.assertEqual(gh.call_args, call('api', 'repos/PaNasMs/module-backup/releases', '--paginate', '--slurp'))
                commit_catalog.assert_not_called()
