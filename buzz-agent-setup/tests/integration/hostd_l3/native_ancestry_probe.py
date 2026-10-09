"""Synthetic native-admission probe. Importing this file performs no work."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import types


def main():
    native=Path(sys.argv[1]);mode=sys.argv[2]
    source=Path(__file__).resolve().parent
    # Render the actual adjacent adapter into an isolated protected deployment,
    # exactly as its existing fake-ELF fixtures do. No source is edited in place.
    with tempfile.TemporaryDirectory(prefix='hostd-native-probe-') as directory:
        package=Path(directory);package.chmod(0o700)
        for name in ('acp_real_provider.py','acp_double.py','model_evidence.py'):
            shutil.copyfile(source/name,package/name);(package/name).chmod(0o600)
        path=package/'acp_real_provider.py'
        body=path.read_text().replace('__RENDER_MODEL_EVIDENCE_SHA256__',hashlib.sha256((package/'model_evidence.py').read_bytes()).hexdigest())
        module=types.ModuleType('private_native_admission');module.__file__=str(path)
        exec(compile(body,str(path),'exec'),module.__dict__)
        d,e=module.D,module.E
        config=module.Config.__new__(module.Config)
        config.doc={'native_path':str(native),'native_sha256':hashlib.sha256(native.read_bytes()).hexdigest()}
        if mode=='wrong-hash':config.doc['native_sha256']='0'*64
        if mode=='missing-native':config.doc['native_path']=str(native)+'.absent'
        own,chain=d.process_ancestry(os.getpid());matching=[]
        for pid,start in chain:
            try:
                if os.readlink('/proc/'+str(pid)+'/exe')==str(native):matching.append(pid)
            except OSError:pass
        original_program=e.program;calls=[]
        def program(pid,start,metadata):
            result=original_program(pid,start,metadata)
            calls.append({'pid':pid,'path':result['path'],'bytes':Path('/proc/'+str(pid)+'/exe').stat().st_size})
            return result
        e.program=program  # Observe the real digest result; never substitute it.
        if mode=='start-drift':
            original_metadata=d.process_metadata
            def metadata(pid):
                result=original_metadata(pid)
                # Inject lowest-level identity drift only after actual ancestry.
                if pid in matching and sys._getframe(1).f_code.co_name in ('native','program'):
                    result=dict(result,start_ticks=result['start_ticks']+1)
                return result
            d.process_metadata=metadata
        if mode in ('early-path-drift','late-path-drift'):
            original_readlink=os.readlink;reads={}
            def readlink(path,*args,**kwargs):
                value=original_readlink(path,*args,**kwargs)
                if str(path).startswith('/proc/') and str(path).endswith('/exe') and value==str(native):
                    reads[str(path)]=reads.get(str(path),0)+1
                    if reads[str(path)] >= (2 if mode=='early-path-drift' else 3):return value+'.drift'
                return value
            os.readlink=readlink
        try:
            result=config.native();admitted=True
        except module.AdapterError:
            result=None;admitted=False
        print(json.dumps({'mode':mode,'admitted':admitted,'own':own,'ancestors':chain,'matching_pids':matching,'hash_calls':calls,'returned_ancestry':None if result is None else result[1]},sort_keys=True),flush=True)


if __name__=='__main__':main()
