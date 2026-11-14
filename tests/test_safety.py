import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest

ROOT=pathlib.Path(__file__).resolve().parents[1]
NAME='k8s-transport-lb-lab'

class SafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=pathlib.Path(self.tmp.name)
        (self.root/'scripts').mkdir(); (self.root/'bin').mkdir()
        shutil.copy(ROOT/'scripts/lab.sh',self.root/'scripts/lab.sh')
        shutil.copy(ROOT/'versions.env',self.root/'versions.env')
        self.log=self.root/'calls.log'
        kind=self.root/'bin/kind'
        kind.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> "$KIND_CALL_LOG"\nif [[ "$1 $2" == "get clusters" ]]; then echo k8s-transport-lb-lab; else exit 99; fi\n')
        kind.chmod(0o755)
        self.env=dict(os.environ, PATH=str(self.root/'bin')+':'+os.environ['PATH'],KIND_CALL_LOG=str(self.log),CLUSTER_NAME=NAME)
    def run_clean(self):
        return subprocess.run(['bash',str(self.root/'scripts/lab.sh'),'clean'],env=self.env,universal_newlines=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=10)
    def test_refuses_non_lab_name(self):
        self.env['CLUSTER_NAME']='production'
        result=self.run_clean()
        self.assertNotEqual(result.returncode,0)
        self.assertIn('Unsafe cluster name',result.stderr)
        self.assertFalse(self.log.exists())
    def test_refuses_existing_unowned_cluster(self):
        result=self.run_clean()
        self.assertNotEqual(result.returncode,0)
        self.assertIn('No ownership marker',result.stderr)
        self.assertNotIn('delete',self.log.read_text())
    def test_refuses_owner_mismatch(self):
        state=self.root/'.lab'/NAME; state.mkdir(parents=True)
        (state/'owner').write_text('our-token'); (state/'kubeconfig').write_text('unit-test')
        kubectl=self.root/'bin/kubectl'; kubectl.write_text('#!/usr/bin/env bash\necho foreign-token\n'); kubectl.chmod(0o755)
        result=self.run_clean()
        self.assertNotEqual(result.returncode,0)
        self.assertIn('Ownership mismatch',result.stderr)
        self.assertNotIn('delete',self.log.read_text())

if __name__=='__main__': unittest.main()
