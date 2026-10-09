"""Real OS/fake-ELF admission controls; no provider, API or credentials."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import unittest

PROBE=Path(__file__).resolve().parent/'integration/hostd_l3/native_ancestry_probe.py'
LAUNCHER_C=r'''
#include <unistd.h>
#include <sys/wait.h>
int main(int argc,char **argv){
 if(argc<2)return 80;
 pid_t child=fork();if(child<0)return 81;
 if(!child){execv(argv[1],argv+1);_exit(82);}
 int status;if(waitpid(child,&status,0)!=child)return 83;
 return WIFEXITED(status)?WEXITSTATUS(status):85;
}
'''


class NativeAncestry(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory(prefix='hostd-native-ancestry-');cls.addClassCleanup(cls.tmp.cleanup)
        cls.work=Path(cls.tmp.name);cls.work.chmod(0o700);cls.native=cls.work/'native'
        prebuilt=os.environ.get('HOSTD_NATIVE_ANCESTRY_FAKE_ELF')
        if prebuilt:
            binary=Path(prebuilt)
            receipt=json.loads(binary.with_suffix('.json').read_text())
            raw=binary.read_bytes()
            if (not binary.is_absolute() or receipt['source_sha256']!=hashlib.sha256(LAUNCHER_C.encode()).hexdigest()
                    or receipt['binary_sha256']!=hashlib.sha256(raw).hexdigest() or not raw.startswith(b'\x7fELF')):
                raise RuntimeError('synthetic native ELF source/binary pin mismatch')
            cls.native.write_bytes(raw)
        else:
            src=cls.work/'launcher.c';src.write_text(LAUNCHER_C)
            done=subprocess.run(['/usr/bin/cc',str(src),'-O0','-o',str(cls.native)],capture_output=True,timeout=15)
            if done.returncode:raise RuntimeError('synthetic ELF compilation failed')
        cls.large=cls.work/'unrelated-large';shutil.copyfile(cls.native,cls.large)
        with cls.large.open('ab') as stream:stream.truncate(16*1024*1024)
        cls.native.chmod(0o755);cls.large.chmod(0o755)

    def probe(self,mode='ok',double=False):
        argv=[str(self.large),str(self.native)]
        if double:argv.append(str(self.native))
        argv.extend([sys.executable,'-I',str(PROBE),str(self.native),mode])
        env={'HOME':str(self.work),'PATH':'/usr/bin:/bin','LANG':'C.UTF-8','TMPDIR':str(self.work)}
        child=subprocess.Popen(argv,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True,env=env)
        try:
            stat=Path('/proc/'+str(child.pid)+'/stat').read_text().rsplit(')',1)[1].split();start=int(stat[19])
            out,err=child.communicate(timeout=15)
        finally:
            if child.returncode is None:
                # communicate has not reaped the retained original group leader.
                os.killpg(child.pid,signal.SIGKILL);child.communicate(timeout=5)
        print('ORIGINAL_FIXTURE_WAIT '+json.dumps({'pid':child.pid,'start_ticks':start,'returncode':child.returncode,'wait_observed':True,'timeout_seconds':15}),flush=True)
        self.assertEqual(child.returncode,0,err.decode());self.assertEqual(err,b'')
        row=json.loads(out);print('ANCESTRY_HASH_CALLS '+json.dumps(row,sort_keys=True),flush=True)
        return row

    def test_unrelated_large_executable_is_not_hashed(self):
        row=self.probe();self.assertTrue(row['admitted'])
        self.assertFalse([call for call in row['hash_calls'] if call['path']==str(self.large)])
        self.assertEqual({call['pid'] for call in row['hash_calls']},set(row['matching_pids']))
        self.assertEqual(row['returned_ancestry'],row['ancestors'])

    def test_all_matching_ancestors_are_fully_hashed(self):
        row=self.probe(double=True);self.assertTrue(row['admitted']);self.assertEqual(len(row['matching_pids']),2)
        self.assertEqual({call['pid'] for call in row['hash_calls']},set(row['matching_pids']))
        self.assertEqual(row['returned_ancestry'],row['ancestors'])

    def test_missing_native_denied(self):self.assertFalse(self.probe('missing-native')['admitted'])
    def test_matching_wrong_hash_denied(self):self.assertFalse(self.probe('wrong-hash')['admitted'])
    def test_matching_start_identity_drift_denied(self):self.assertFalse(self.probe('start-drift')['admitted'])
    def test_matching_path_drift_before_program_denied(self):self.assertFalse(self.probe('early-path-drift')['admitted'])
    def test_matching_path_drift_after_hash_denied(self):self.assertFalse(self.probe('late-path-drift')['admitted'])


if __name__=='__main__':unittest.main(verbosity=2)
