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
from unittest.mock import patch
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
    def payload(self, corrupt=False, extra=False, arch="arm64"):
        files={'LICENSE':b'license','NOTICE':b'notice','ui/index.js':b'js','bin/server':b'elf'}
        m={'id':'files','version':'0.2.9','architecture':arch,'api':1,'core':'>=0.2.0,<0.3.0','title':'Files','license':'PolyForm-Noncommercial-1.0.0','service':'bin/server','files':{k:hashlib.sha256(v).hexdigest() for k,v in files.items()}}
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
