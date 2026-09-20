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
    def payload(self, corrupt=False, extra=False):
        files={'LICENSE':b'license','NOTICE':b'notice','ui/index.js':b'js','bin/server':b'elf'}
        m={'id':'files','version':'0.2.9','architecture':'arm64','api':1,'core':'>=0.2.0,<0.3.0','title':'Files','license':'PolyForm-Noncommercial-1.0.0','service':'bin/server','files':{k:hashlib.sha256(v).hexdigest() for k,v in files.items()}}
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
