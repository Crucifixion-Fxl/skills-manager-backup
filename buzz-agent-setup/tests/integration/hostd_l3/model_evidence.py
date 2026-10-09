"""Independent sealed component evidence observer; never image understanding PASS.

Runtime acceptance roots must come from original launcher and signed readback.
Child metadata is historical adapter observation after a genuine Popen.wait.
"""
import ast
import base64
import fcntl
import hashlib
import http.client
import ssl
import socket
import threading
import json
import os
from pathlib import Path
import re
import stat
import types
import time
import zlib

DOUBLE_SHA256 = '4a5fda0bdca07d626f003a15f28ec9a5ed56133617df88ecb2668934797642a3'
LIMIT = 256 * 1024

class EvidenceError(Exception): pass
class ProcessChanged(EvidenceError): pass

def sha(raw): return hashlib.sha256(raw).hexdigest()
def open_safe(path, directory=False):
    value=str(path); p=Path(value)
    if not p.is_absolute() or str(p)!=value or '..' in p.parts:raise EvidenceError()
    fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY|os.O_CLOEXEC)
    try:
        for i,name in enumerate(p.parts[1:]):
            flags=os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC
            if i<len(p.parts)-2 or directory:flags|=os.O_DIRECTORY
            child=os.open(name,flags,dir_fd=fd);os.close(fd);fd=child
        return fd
    except BaseException:os.close(fd);raise

def private(meta,directory=False):
    if (meta.st_uid!=os.geteuid() or stat.S_IMODE(meta.st_mode)!=(0o700 if directory else 0o600)
        or not (stat.S_ISDIR(meta.st_mode) if directory else stat.S_ISREG(meta.st_mode))
        or not directory and meta.st_nlink!=1):raise EvidenceError()

def read_private(path,pin=None,limit=LIMIT):
    fd=open_safe(path)
    try:
        meta=os.fstat(fd);private(meta)
        if meta.st_size>limit:raise EvidenceError()
        raw=os.read(fd,limit+1)
        if len(raw)!=meta.st_size or pin is not None and sha(raw)!=pin:raise EvidenceError()
        return raw,(meta.st_dev,meta.st_ino)
    finally:os.close(fd)

def load_pinned(path,pin):
    raw,identity=read_private(path,pin)
    module=types.ModuleType('hostd_pinned_'+sha(raw));module.__file__=str(path)
    exec(compile(raw,str(path),'exec'),module.__dict__)
    if read_private(path,pin)[1]!=identity:raise EvidenceError()
    return module

def double():return load_pinned(Path(__file__).parent/'acp_double.py',DOUBLE_SHA256)


# Bounded kernel directory observation, not an unbounded descendant oracle.
PROC_LIMIT = 65536

def process_table(deadline=None):
    rows={}
    for name in os.listdir('/proc'):
        if not name.isdecimal():continue
        if len(rows)>=PROC_LIMIT or deadline is not None and time.monotonic()>=deadline:raise EvidenceError()
        pid=int(name)
        try:
            values=Path('/proc/'+name+'/stat').read_text().rsplit(')',1)[1].split()
            rows[pid]={'pid':pid,'start_ticks':int(values[19]),'parent_pid':int(values[1]),'pgid':int(values[2]),'sid':int(values[3]),'state':values[0]}
        # A process can disappear after stat was opened: Linux then returns
        # ESRCH from read, rather than ENOENT from open. Neither is a live row.
        except (FileNotFoundError,ProcessLookupError):continue
    return rows

def kernel_identity(pid):
    # Open the actual proc directory; compare start/parent/group/session twice.
    directory=os.open('/proc/'+str(pid),os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        def read(name):
            fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=directory)
            try:
                raw=os.read(fd,65537)
                if len(raw)>65536:raise EvidenceError()
                return raw
            finally:os.close(fd)
        first=read('stat').decode().rsplit(')',1)[1].split()
        uid=[line.split()[1:] for line in read('status').decode().splitlines() if line.startswith('Uid:')]
        group=read('cgroup');last=read('stat').decode().rsplit(')',1)[1].split()
        if first[19]!=last[19]:raise EvidenceError()
        if any(first[i]!=last[i] for i in (1,2,3)):raise ProcessChanged()
        if len(uid)!=1 or len(uid[0])!=4 or len(group.splitlines())!=1 or not group.startswith(b'0::/'):raise EvidenceError()
        return {'pid':pid,'start_ticks':int(first[19]),'parent_pid':int(first[1]),'pgid':int(first[2]),'sid':int(first[3]),'state':last[0],'uid':list(map(int,uid[0])),'cgroup_sha256':sha(group)}
    finally:os.close(directory)

def require_children_absent(pid,start):
    before=kernel_identity(pid)
    if before['start_ticks']!=start:raise EvidenceError()
    tasks=Path('/proc/'+str(pid)+'/task');names=os.listdir(tasks)
    if not names or len(names)>128:raise EvidenceError()
    for name in names:
        if not name.isdecimal():raise EvidenceError()
        fd=os.open(tasks/name/'children',os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
        try:raw=os.read(fd,65537)
        finally:os.close(fd)
        # Nonempty kernel children means a currently active or adopted lineage,
        # including groups/sessions changed by backend descendants. Conservatively
        # reject another active request rather than infer which old job owns it.
        if len(raw)>65536 or raw.strip():raise EvidenceError()
    after=kernel_identity(pid)
    if any(after[k]!=before[k] for k in before if k!='state'):raise EvidenceError()

def require_group_absent(pgid):
    if type(pgid) is not int or pgid<=0 or any(v['pgid']==pgid for v in process_table(time.monotonic()+.5).values()):raise EvidenceError()

def decode_image(blocks,event):
    """Strict single PNG, CRC, full bounded zlib stream and scanline shape.

Other MIME types require a reviewed decoder contract; never claim image pass.
"""
    images=[b for b in blocks if b.get('type')=='image']
    if len(images)!=1:raise EvidenceError()
    b=images[0]
    if set(b)!={'type','mimeType','data'} or b['mimeType']!='image/png' or not isinstance(b['data'],str) or len(b['data'])>32768:raise EvidenceError()
    raw=base64.b64decode(b['data'],validate=True)
    return decode_png(raw,event)

def decode_png(raw,event):
    # Retrieved bytes use this decoder directly, never invented native ACP blocks.
    if len(raw)>24576 or not raw.startswith(b'\x89PNG\r\n\x1a\n') or sha(raw)!=event['image_sha256']:raise EvidenceError()
    pos=8; chunks=[]; data=bytearray(); dimensions=None; ended=False
    while pos<len(raw):
        if pos+12>len(raw):raise EvidenceError()
        length=int.from_bytes(raw[pos:pos+4],'big');kind=raw[pos+4:pos+8];end=pos+12+length
        if end>len(raw) or zlib.crc32(raw[pos+4:end-4])!=int.from_bytes(raw[end-4:end],'big'):raise EvidenceError()
        value=raw[pos+8:end-4];chunks.append(kind)
        if kind==b'IHDR':
            if len(chunks)!=1 or length!=13:raise EvidenceError()
            width=int.from_bytes(value[:4],'big');height=int.from_bytes(value[4:8],'big')
            # Conservative 8-bit grayscale/RGB/RGBA noninterlaced PNG contract.
            channels={0:1,2:3,4:2,6:4}.get(value[9])
            if not channels or value[8]!=8 or value[10:]!=b'\0\0\0' or not 1<=width<=4096 or not 1<=height<=4096 or width*height>4_194_304:raise EvidenceError()
            dimensions=(width,height,channels)
        elif kind==b'IDAT':
            if dimensions is None or b'IEND' in chunks:raise EvidenceError()
            if b'IDAT' in chunks[:-1] and chunks[-2]!=b'IDAT':raise EvidenceError()
            data.extend(value)
        elif kind==b'IEND':
            if length or end!=len(raw):raise EvidenceError()
            ended=True
        elif kind not in (b'tEXt',b'pHYs',b'sRGB',b'gAMA',b'cHRM'):
            raise EvidenceError()
        pos=end
    if not ended or dimensions is None or not data:raise EvidenceError()
    width,height,channels=dimensions;stride=1+width*channels;size=height*stride
    # Bound expanded data too, independently of compressed input.
    if size>16*1024*1024:raise EvidenceError()
    dec=zlib.decompressobj()
    try:pixels=dec.decompress(bytes(data),size+1)
    except zlib.error:raise EvidenceError() from None
    if len(pixels)!=size or not dec.eof or dec.unused_data or dec.unconsumed_tail or any(pixels[i]>4 for i in range(0,size,stride)):raise EvidenceError()
    return raw,{'sha256':sha(raw),'size':len(raw),'mime':'image/png','width':width,'height':height}

SIGNER_SHA256 = 'c7b78be78d074c430cbcb4ef7ff44c7bb49968dad1be13f5ddd28d464ddc1ebc'

def transport_config(c):
    if not isinstance(c,dict) or set(c)!={'origin','ca_path','ca_sha256','certificate_sha256','signer_path','signer_sha256','owner_pubkey','timeout_seconds'}:raise EvidenceError()
    if not isinstance(c['origin'],str) or re.fullmatch(r'https://127\.0\.0\.1:([1-9][0-9]{0,4})',c['origin']) is None:raise EvidenceError()
    port=int(c['origin'].rsplit(':',1)[1])
    if port>65535 or type(c['timeout_seconds']) not in (int,float) or not .1<=c['timeout_seconds']<=5:raise EvidenceError()
    for key in ('ca_sha256','certificate_sha256','owner_pubkey'):
        if not isinstance(c[key],str) or not re.fullmatch('[0-9a-f]{64}',c[key]):raise EvidenceError()
    if c['signer_sha256']!=SIGNER_SHA256:raise EvidenceError()
    ca,identity=read_private(c['ca_path'],c['ca_sha256'],16384)
    # Only the one reviewed byte-exact signer next to this pinned verifier.
    if c['signer_path']!=str(Path(__file__).absolute().parent/'nostrkit.py'):raise EvidenceError()
    read_private(c['signer_path'],SIGNER_SHA256)
    return ca,identity,port

def transport_target(fields,event,c):
    transport_config(c)
    # Exact native original URL: no query, fragment, aliases, escape or thumbnail.
    url=c['origin']+'/media/'+event['image_sha256']+'.png'
    if fields.get('url')!=url or fields.get('x')!=event['image_sha256'] or fields.get('m')!='image/png' or re.fullmatch('[1-9][0-9]{0,3}x[1-9][0-9]{0,3}',fields.get('dim','')) is None:raise EvidenceError()
    return url

def transport_identity(d,config,c):
    nk=load_pinned(c['signer_path'],SIGNER_SHA256)
    value=os.environ.get('BUZZ_PRIVATE_KEY','')
    try:
        secret=bytes.fromhex(value) if re.fullmatch('[0-9a-f]{64}',value) else nk.bech32_decode(value,'nsec')
        pub=nk.pubkey_xonly(secret).hex()
    except Exception:raise EvidenceError() from None
    if pub!=config['selected_pubkey']:raise EvidenceError()
    # Native launcher requires an unconditional verified owner attestation.
    raw=os.environ.get('BUZZ_AUTH_TAG','')
    if len(raw.encode())>1024:raise EvidenceError()
    auth=d._json(raw)
    if not isinstance(auth,list) or len(auth)!=4 or auth[:3]!=['auth',c['owner_pubkey'],''] or c['owner_pubkey']==pub or not isinstance(auth[3],str) or not re.fullmatch('[0-9a-f]{128}',auth[3]):raise EvidenceError()
    verify_schnorr(c['owner_pubkey'],hashlib.sha256(('nostr:agent-auth:'+pub+':').encode()).digest(),auth[3])
    if os.environ.get('BUZZ_RELAY_URL')!=c['origin'].replace('https://','wss://'):raise EvidenceError()
    return nk,secret,pub,raw

def fetch_native_image(d,fields,event,config):
    c=config['native_image_transport'];url=transport_target(fields,event,c)
    ca,ca_identity,port=transport_config(c);nk,secret,pub,auth=transport_identity(d,config,c)
    started=time.monotonic_ns();deadline=time.monotonic()+c['timeout_seconds']
    now=int(time.time());tags=[['t','get'],['expiration',str(now+60)],['server',c['origin'].removeprefix('https://')],['x',event['image_sha256']]]
    # Official buzz-media auth.rs accepts GET scoped by x and/or server.
    signed={'pubkey':pub,'created_at':now,'kind':24242,'tags':tags,'content':'Get media'}
    signed['id']=sha(json.dumps([0,pub,now,24242,tags,signed['content']],separators=(',',':'),ensure_ascii=False).encode())
    signed['sig']=nk.schnorr_sign(bytes.fromhex(signed['id']),secret).hex();secret=None
    header='Nostr '+base64.urlsafe_b64encode(json.dumps(signed,separators=(',',':')).encode()).decode().rstrip('=')
    try:context=ssl.create_default_context(cadata=ca.decode('ascii'))
    except (OSError,UnicodeError):raise EvidenceError() from None
    # HTTPSConnection with literal endpoint bypasses all proxy env and DNS.
    conn=http.client.HTTPSConnection('127.0.0.1',port,timeout=c['timeout_seconds'],context=context)
    tls_socket=None;expired=threading.Event()
    def abort_transport():
        expired.set()
        if tls_socket is not None:
            try:tls_socket.shutdown(socket.SHUT_RDWR)
            except OSError:pass
    timer=threading.Timer(max(0,deadline-time.monotonic()),abort_transport);timer.daemon=True
    def remaining():
        left=deadline-time.monotonic()
        if left<=0 or expired.is_set():raise EvidenceError()
        tls_socket.settimeout(left)
    try:
        timer.start()
        plain=socket.create_connection(('127.0.0.1',port),max(.001,deadline-time.monotonic()));tls_socket=plain
        try:tls_socket=context.wrap_socket(plain,server_hostname='127.0.0.1',do_handshake_on_connect=False)
        except BaseException:plain.close();raise
        conn.sock=tls_socket;remaining();tls_socket.do_handshake();remaining()
        if sha(conn.sock.getpeercert(binary_form=True))!=c['certificate_sha256']:raise EvidenceError()
        conn.request('GET',url.removeprefix(c['origin']),headers={'Authorization':header,'x-auth-tag':auth,'Accept':'image/png','Accept-Encoding':'identity','Connection':'close'})
        remaining();response=conn.getresponse()
        headers=response.getheaders()
        def one(name):
            values=[v for k,v in headers if k.lower()==name]
            if len(values)!=1:raise EvidenceError()
            return values[0]
        length=one('content-length')
        if response.status!=200 or one('content-type')!='image/png' or not re.fullmatch('[1-9][0-9]{0,4}',length) or int(length)>24576 or any(k.lower() in ('location','transfer-encoding','content-encoding','content-range') for k,v in headers):raise EvidenceError()
        # read1 avoids blocking across multiple underlying socket reads without
        # resetting the retained overall deadline. Never follow a redirect.
        chunks=[];size=0
        while size<int(length):
            remaining();piece=response.read1(min(4096,int(length)-size))
            if not piece:raise EvidenceError()
            chunks.append(piece);size+=len(piece)
        raw=b''.join(chunks);decode_png(raw,event)
        if time.monotonic()>=deadline or expired.is_set():raise EvidenceError()
        if read_private(c['ca_path'],c['ca_sha256'],16384)!=(ca,ca_identity):raise EvidenceError()
        transport_config(c)
        return {'source':'historical-adapter-observed-protected-tls-fetch','url':url,'origin':c['origin'],'certificate_sha256':c['certificate_sha256'],'ca_sha256':c['ca_sha256'],'signer_sha256':SIGNER_SHA256,'status':200,'content_type':'image/png','content_length':len(raw),'started_ns':started,'finished_ns':time.monotonic_ns(),'data_base64':base64.b64encode(raw).decode()}
    except (OSError,http.client.HTTPException,ValueError,UnicodeError):raise EvidenceError() from None
    finally:
        timer.cancel()
        if timer.ident is not None:timer.join(timeout=.1)
        conn.close()

def retrieved_image(fields,event,config,record):
    c=config['native_image_transport'];url=transport_target(fields,event,c)
    keys={'source','url','origin','certificate_sha256','ca_sha256','signer_sha256','status','content_type','content_length','started_ns','finished_ns','data_base64'}
    if not isinstance(record,dict) or set(record)!=keys:raise EvidenceError()
    expected={'source':'historical-adapter-observed-protected-tls-fetch','url':url,'origin':c['origin'],'certificate_sha256':c['certificate_sha256'],'ca_sha256':c['ca_sha256'],'signer_sha256':SIGNER_SHA256,'status':200,'content_type':'image/png'}
    if any(record[k]!=v for k,v in expected.items()) or type(record['status']) is not int:raise EvidenceError()
    if type(record['started_ns']) is not int or type(record['finished_ns']) is not int or not 0<record['started_ns']<record['finished_ns'] or record['finished_ns']-record['started_ns']>c['timeout_seconds']*1e9:raise EvidenceError()
    data=record['data_base64']
    if not isinstance(data,str) or len(data)>32768:raise EvidenceError()
    raw=base64.b64decode(data,validate=True)
    if type(record['content_length']) is not int or record['content_length']!=len(raw):raise EvidenceError()
    raw,image=decode_png(raw,event);image['transport']=record
    return raw,image

def semantic_input(d,blocks,config,retrieved=None):
    event=d.parse_prompt(blocks)
    if event is None or event['channel_id']!=config['channel_id'] or event['author_pubkey']!=config['mirror_pubkey']:raise EvidenceError()
    framed=[b['text'] for b in blocks if b.get('type')=='text' and b.get('text','').startswith('<buzz-event type="@mention">\n')]
    tags=d._json(framed[0].split('\nTags: ',1)[1].split('\nParsed: ',1)[0].removesuffix('\n</buzz-event>'))
    if len([t for t in tags if t[:2]==['p',config['selected_pubkey']]])!=1:raise EvidenceError()
    fields=dict(v.split(' ',1) for t in tags if t[:1]==['imeta'] for v in t[1:])
    if any(b.get('type')=='image' for b in blocks):
        if retrieved is not None:raise EvidenceError()
        raw,image=decode_image(blocks,event)
    else:
        if 'native_image_transport' not in config or any(set(b)!={'type','text'} or b['type']!='text' for b in blocks):raise EvidenceError()
        if retrieved is None:retrieved=fetch_native_image(d,fields,event,config)
        raw,image=retrieved_image(fields,event,config,retrieved)
    if fields.get('m',image['mime'])!=image['mime'] or fields.get('dim',f"{image['width']}x{image['height']}")!=f"{image['width']}x{image['height']}":raise EvidenceError()
    return event,raw,image

def verify_signed_event(event):
    """BIP340 verification, no secret key/dependency/module import."""
    if not isinstance(event,dict) or set(event)!={'id','pubkey','created_at','kind','tags','content','sig'}:raise EvidenceError()
    if type(event['created_at']) is not int or type(event['kind']) is not int or event['kind']!=9:raise EvidenceError()
    if any(not isinstance(event[k],str) or not re.fullmatch('[0-9a-f]{'+str(n)+'}',event[k]) for k,n in [('id',64),('pubkey',64),('sig',128)]):raise EvidenceError()
    raw=json.dumps([0,event['pubkey'],event['created_at'],event['kind'],event['tags'],event['content']],separators=(',',':'),ensure_ascii=False).encode()
    if len(raw)>65536 or sha(raw)!=event['id']:raise EvidenceError()
    verify_schnorr(event['pubkey'],bytes.fromhex(event['id']),event['sig'])

def verify_schnorr(pubkey,message,signature):
    if not isinstance(message,bytes) or len(message)!=32 or not isinstance(pubkey,str) or not re.fullmatch('[0-9a-f]{64}',pubkey) or not isinstance(signature,str) or not re.fullmatch('[0-9a-f]{128}',signature):raise EvidenceError()
    p=2**256-2**32-977;n=0xfffffffffffffffffffffffffffffffebaaedce6af48a03bbfd25e8cd0364141
    g=(55066263022277343669578718895168534326250603453777594175500187360389116729240,32670510020758816978083085130507043184471273380659243275938904335757337482424)
    def add(a,b):
        if a is None:return b
        if b is None:return a
        if a[0]==b[0] and (a[1]+b[1])%p==0:return None
        v=((3*a[0]**2)*pow(2*a[1],-1,p) if a==b else (b[1]-a[1])*pow(b[0]-a[0],-1,p))%p
        x=(v*v-a[0]-b[0])%p;return x,(v*(a[0]-x)-a[1])%p
    def mul(k,a):
        r=None
        while k:
            if k&1:r=add(r,a)
            a=add(a,a);k>>=1
        return r
    x=int(pubkey,16)
    if x>=p:raise EvidenceError()
    y=pow((x*x*x+7)%p,(p+1)//4,p)
    if y*y%p!=(x*x*x+7)%p:raise EvidenceError()
    if y&1:y=p-y
    sig=bytes.fromhex(signature);r=int.from_bytes(sig[:32],'big');s=int.from_bytes(sig[32:],'big')
    if r>=p or s>=n:raise EvidenceError()
    h=hashlib.sha256(b'BIP0340/challenge').digest();e=int.from_bytes(hashlib.sha256(h+h+sig[:32]+bytes.fromhex(pubkey)+message).digest(),'big')%n
    R=add(mul(s,g),mul(n-e,(x,y)))
    if R is None or R[1]&1 or R[0]!=r:raise EvidenceError()

def program(pid,start,d):
    before=d.process_metadata(pid)
    if before['start_ticks']!=start:raise EvidenceError()
    path=Path('/proc')/str(pid);exe=os.readlink(path/'exe')
    fd=os.open(path/'exe',os.O_RDONLY|os.O_CLOEXEC)
    try:
        h=hashlib.sha256()
        while True:
            part=os.read(fd,65536)
            if not part:break
            h.update(part)
    finally:os.close(fd)
    fd=os.open(path/'cmdline',os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:raw=os.read(fd,65537)
    finally:os.close(fd)
    if len(raw)>65536 or not raw.endswith(b'\0') or d.process_metadata(pid)!=before:raise EvidenceError()
    return {'path':exe,'sha256':h.hexdigest(),'argv':[v.decode() for v in raw[:-1].split(b'\0')]}

class ModelLedger:
    def __init__(self,manifest,config_check,d):
        self.manifest=manifest;self.check=config_check;self.d=d;self.path=manifest.root/'model-evidence.json';self.fd=None;self.sealed_fd=None;self.identity=None;self.records=[]
        if os.path.lexists(self.path):raise EvidenceError()
    def record(self,row):
        self.check();raw=json.dumps({'version':1,'run_id':self.manifest.doc['run_id'],'records':[*self.records,row]},sort_keys=True,separators=(',',':')).encode()+b'\n'
        if len(raw)>LIMIT:raise EvidenceError()
        sealed=os.memfd_create('hostd-acp-model-'+self.manifest.doc['run_id'],os.MFD_CLOEXEC|os.MFD_ALLOW_SEALING);os.fchmod(sealed,0o600)
        directory=None
        try:
            write_all(sealed,raw);os.fsync(sealed)
            mask=fcntl.F_SEAL_WRITE|fcntl.F_SEAL_GROW|fcntl.F_SEAL_SHRINK|fcntl.F_SEAL_SEAL;fcntl.fcntl(sealed,fcntl.F_ADD_SEALS,mask)
            directory=open_safe(self.manifest.root,True);private(os.fstat(directory),True)
            if (os.fstat(directory).st_dev,os.fstat(directory).st_ino)!=self.manifest.root_identity:raise EvidenceError()
            if self.fd is None:
                self.fd=os.open(self.path.name,os.O_RDWR|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600,dir_fd=directory);self.identity=(os.fstat(self.fd).st_dev,os.fstat(self.fd).st_ino)
            meta=os.stat(self.path.name,dir_fd=directory,follow_symlinks=False);private(meta)
            if (meta.st_dev,meta.st_ino)!=self.identity:raise EvidenceError()
            os.lseek(self.fd,0,0);os.ftruncate(self.fd,0);write_all(self.fd,raw);os.fsync(self.fd);self.check()
            if read_private(self.path)[1]!=self.identity:raise EvidenceError()
            self.records.append(row)
            if self.sealed_fd is not None:os.close(self.sealed_fd)
            self.sealed_fd=sealed;sealed=None
        finally:
            if directory is not None:os.close(directory)
            if sealed is not None:os.close(sealed)
    def close(self):
        for key in ('fd','sealed_fd'):
            fd=getattr(self,key)
            if fd is not None:os.close(fd);setattr(self,key,None)

def write_all(fd,raw):
    offset=0
    while offset<len(raw):
        n=os.write(fd,raw[offset:])
        if n<=0:raise EvidenceError()
        offset+=n

def model_snapshot(pid,run_id,identity):
    directory=open_safe('/proc/'+str(pid)+'/fd',True);sealed=[];regular=0
    try:
        if os.fstat(directory).st_uid!=os.geteuid():raise EvidenceError()
        names=os.listdir(directory)
        if len(names)>512:raise EvidenceError()
        for name in names:
            if not re.fullmatch('[0-9]{1,9}',name):raise EvidenceError()
            try:target=os.readlink(name,dir_fd=directory);meta=os.stat(name,dir_fd=directory)
            except FileNotFoundError:continue
            if (meta.st_dev,meta.st_ino)==identity:
                private(meta);info=Path('/proc')/str(pid)/'fdinfo'/name;fd=open_safe(info)
                try:raw=os.read(fd,4097)
                finally:os.close(fd)
                flags=[l.split(':',1)[1].strip() for l in raw.decode().splitlines() if l.startswith('flags:')]
                if len(raw)>4096 or len(flags)!=1 or int(flags[0],8)&os.O_ACCMODE!=os.O_RDWR:raise EvidenceError()
                regular+=1
            if target.startswith('/memfd:hostd-acp-model-'):
                expected='/memfd:hostd-acp-model-'+run_id+' (deleted)'
                if target!=expected:raise EvidenceError()
                fd=os.open('/proc/'+str(pid)+'/fd/'+name,os.O_RDONLY|os.O_CLOEXEC)
                try:
                    m=os.fstat(fd);mask=fcntl.F_SEAL_WRITE|fcntl.F_SEAL_GROW|fcntl.F_SEAL_SHRINK|fcntl.F_SEAL_SEAL
                    if (m.st_uid!=os.geteuid() or stat.S_IMODE(m.st_mode)!=0o600 or not stat.S_ISREG(m.st_mode) or m.st_nlink!=0 or m.st_size>LIMIT or fcntl.fcntl(fd,fcntl.F_GET_SEALS)&mask!=mask or os.readlink(name,dir_fd=directory)!=expected or (m.st_dev,m.st_ino)!=(meta.st_dev,meta.st_ino)):raise EvidenceError()
                    raw=os.read(fd,LIMIT+1)
                    if len(raw)!=m.st_size:raise EvidenceError()
                    sealed.append(raw)
                finally:os.close(fd)
        if regular!=1 or len(sealed)!=1:raise EvidenceError()
        return sealed[0]
    finally:os.close(directory)

def observe(root,run_id,input_witness_path,expected_event,initial_unit,initial_pid,initial_start_ticks,selected_pubkey,adapter_path,adapter_sha256,python_path,python_sha256,adapter_argv,backend_config_sha256):
    """All roots are independent caller acceptance inputs. Fail closed/pending."""
    try:
        root=Path(root);d=load_pinned(root/'acp_double.py',DOUBLE_SHA256)
        if not re.fullmatch('[0-9a-f]{32}',run_id) or not isinstance(initial_unit,str) or not initial_unit or Path(adapter_path).parent!=root or Path(input_witness_path).parent!=root:raise EvidenceError()
        fd=open_safe(root,True)
        try:private(os.fstat(fd),True);root_identity=(os.fstat(fd).st_dev,os.fstat(fd).st_ino)
        finally:os.close(fd)
        adapter_raw,adapter_inode=read_private(adapter_path,adapter_sha256)
        config_raw,config_inode=read_private(root/'backend.json',backend_config_sha256);config=d._json(config_raw)
        if config['run_id']!=run_id or config['selected_pubkey']!=selected_pubkey:raise EvidenceError()
        # Source closure: caller pins adapter plus config; adapter embeds verifier pin.
        me=read_private(root/'model_evidence.py')[0]
        constants={n.targets[0].id:n.value.value for n in ast.parse(adapter_raw).body if isinstance(n,ast.Assign) and len(n.targets)==1 and isinstance(n.targets[0],ast.Name) and isinstance(n.value,ast.Constant)}
        if constants.get('DOUBLE_SHA256')!=DOUBLE_SHA256 or constants.get('MODEL_EVIDENCE_SHA256')!=sha(me) or constants.get('BACKEND_CONFIG_SHA256')!=backend_config_sha256:raise EvidenceError()
        verify_signed_event(expected_event)
        if expected_event['pubkey']!=config['mirror_pubkey'] or ['h',config['channel_id']] not in expected_event['tags'] or len([t for t in expected_event['tags'] if t[:2]==['p',selected_pubkey]])!=1:raise EvidenceError()
        model_raw,identity=read_private(root/'model-evidence.json');doc=d._json(model_raw)
        if set(doc)!={'version','run_id','records'} or type(doc['version']) is not int or doc['version']!=1 or doc['run_id']!=run_id or not isinstance(doc['records'],list) or not 1<=len(doc['records'])<=64:raise EvidenceError()
        input_raw,input_identity=read_private(input_witness_path);input_doc=d._json(input_raw)
        if set(input_doc)!={'version','run_id','records'} or type(input_doc['version']) is not int or input_doc['version']!=1 or input_doc['run_id']!=run_id:raise EvidenceError()
        rows=[r for r in doc['records'] if r.get('input',{}).get('event',{}).get('event_id')==expected_event['id']]
        if len(rows)!=1:raise EvidenceError()
        row=rows[0];keys={'input','prompt','image','selected_pubkey','adapter','backend','child','completion','completion_id','backend_session_id','completion_sha256','stream_sha256','started_ns','finished_ns','wait_returncode','wait_observed','backend_config_sha256','group_lifecycle'}
        if set(row)!=keys or row['selected_pubkey']!=selected_pubkey or row['backend_config_sha256']!=backend_config_sha256 or type(row['wait_returncode']) is not int or row['wait_returncode']!=0 or row['wait_observed'] is not True or not isinstance(row['completion'],str) or not row['completion'].strip() or sha(row['completion'].encode())!=row['completion_sha256'] or not re.fullmatch('[0-9a-f]{64}',row['stream_sha256']):raise EvidenceError()
        if any(v is not None and (not isinstance(v,str) or len(v)>128) for v in (row['completion_id'],row['backend_session_id'])):raise EvidenceError()
        inp=row['input'];pid=inp['provider_pid'];start=inp['provider_start_ticks'];own,ancestors=d.process_ancestry(pid);native=d.process_metadata(initial_pid)
        if own['start_ticks']!=start or native['start_ticks']!=initial_start_ticks or [initial_pid,initial_start_ticks] not in ancestors or ancestors!=inp['ancestors'] or own['cgroup_sha256']!=native['cgroup_sha256'] or own['cgroup_sha256']!=inp['cgroup_sha256']:raise EvidenceError()
        native_program=program(initial_pid,initial_start_ticks,d)
        if native_program['path']!=config['native_path'] or native_program['sha256']!=config['native_sha256']:raise EvidenceError()
        p=program(pid,start,d)
        if p!={'path':str(python_path),'sha256':python_sha256,'argv':list(adapter_argv)}:raise EvidenceError()
        if row['adapter']!={'path':str(adapter_path),'sha256':adapter_sha256} or inp not in input_doc['records'] or input_doc['records'].count(inp)!=1:raise EvidenceError()
        if not re.fullmatch('l3-'+run_id+'-(?:[1-9]|1[0-6])',inp['session_id']) or type(inp['prompt_sequence']) is not int or not 1<=inp['prompt_sequence']<=64 or d._hash_json(row['prompt'])!=inp['prompt_sha256'] or not re.fullmatch('[0-9a-f]{64}',inp['request_id_sha256']):raise EvidenceError()
        ev,raw,image=semantic_input(d,row['prompt'],config,row['image'].get('transport'))
        expected={'event_id':expected_event['id'],'channel_id':config['channel_id'],'kind':9,'author_pubkey':expected_event['pubkey'],'created_at':expected_event['created_at'],'body_sha256':sha(expected_event['content'].encode()),'tags_sha256':d._hash_json(expected_event['tags']),'image_sha256':image['sha256']}
        if ev!=expected or inp['event']!=expected or row['image']!=image:raise EvidenceError()
        if 'transport' in image and image['transport']['finished_ns']>row['started_ns']:raise EvidenceError()
        backend=row['backend'];child=row['child']
        expected_args=[config['backend_path'],*['/proc/self/fd/'+str(child['image_fd']) if x=='{image}' else x for x in config['backend_args']]]
        if type(child['image_fd']) is not int or not 3<=child['image_fd']<512:raise EvidenceError()
        if backend!={'path':config['backend_path'],'sha256':config['backend_sha256'],'argv':expected_args,'model':config['model']} or child['program']!={'path':config['backend_path'],'sha256':config['backend_sha256'],'argv':expected_args} or child['parent_pid']!=pid or child['cgroup_sha256']!=own['cgroup_sha256'] or type(child['pid']) is not int or child['pid']<=0 or type(child['start_ticks']) is not int or child['start_ticks']<=0:raise EvidenceError()
        life=row['group_lifecycle']
        if set(life)!={'historical','scope','original','started_ns','finished_ns','absence_observed','descendant_waits','escaped_observed'} or life['historical'] is not True or life['scope']!='bounded-owned-descendants-and-original-group' or life['absence_observed'] is not True or life['escaped_observed'] is not False:raise EvidenceError()
        original=life['original']
        if set(original)!={'pid','start_ticks','parent_pid','pgid','sid','uid','cgroup_sha256'} or original['pid']!=child['pid'] or original['start_ticks']!=child['start_ticks'] or original['parent_pid']!=pid or original['pgid']!=child['pid'] or original['sid']!=child['pid'] or original['cgroup_sha256']!=own['cgroup_sha256'] or original['uid']!=kernel_identity(pid)['uid']:raise EvidenceError()
        if type(life['started_ns']) is not int or type(life['finished_ns']) is not int or not row['started_ns']<=life['started_ns']<life['finished_ns']<=row['finished_ns'] or life['finished_ns']-life['started_ns']>2e9 or not isinstance(life['descendant_waits'],list) or len(life['descendant_waits'])>256:raise EvidenceError()
        seen=set()
        for waited in life['descendant_waits']:
            if set(waited)!={'pid','start_ticks','identity','ancestry','returncode','wait_observed'} or waited['wait_observed'] is not True or type(waited['returncode']) is not int or waited['pid'] in seen or waited['pid']==child['pid']:raise EvidenceError()
            ident=waited['identity'];lineage=waited['ancestry']
            if ident['pid']!=waited['pid'] or ident['start_ticks']!=waited['start_ticks'] or ident['start_ticks']<original['start_ticks'] or ident['pgid']!=original['pgid'] or ident['sid']!=original['sid'] or ident['uid']!=original['uid'] or ident['cgroup_sha256']!=original['cgroup_sha256'] or not isinstance(lineage,list) or not lineage or lineage[0]!=[ident['pid'],ident['start_ticks']] or lineage[-1] not in ([pid,start],[child['pid'],child['start_ticks']]):raise EvidenceError()
            seen.add(waited['pid'])
        # Independent current kernel observation; historical claimed absence alone is insufficient.
        require_group_absent(original['pgid'])
        require_children_absent(pid,start)
        if type(row['started_ns']) is not int or type(row['finished_ns']) is not int or not 0<row['started_ns']<row['finished_ns'] or row['finished_ns']-row['started_ns']>(config['timeout_seconds']+2)*1e9:raise EvidenceError()
        if d.provider_snapshot(pid,run_id,input_identity)!=input_raw or model_snapshot(pid,run_id,identity)!=model_raw:raise EvidenceError()
        if d.process_ancestry(pid)!=(own,ancestors) or d.process_metadata(initial_pid)!=native:raise EvidenceError()
        if read_private(root/'model-evidence.json')!=(model_raw,identity) or read_private(input_witness_path)!=(input_raw,input_identity) or read_private(adapter_path,adapter_sha256)[1]!=adapter_inode or read_private(root/'backend.json',backend_config_sha256)[1]!=config_inode:raise EvidenceError()
        fd=open_safe(root,True)
        try:
            if (os.fstat(fd).st_dev,os.fstat(fd).st_ino)!=root_identity:raise EvidenceError()
        finally:os.close(fd)
        require_group_absent(original['pgid'])
        require_children_absent(pid,start)
        return {'status':'component-observed','live_verified':False,'model_evidence_sha256':sha(model_raw),'initial_unit':initial_unit,'backend_child_metadata':'historical-adapter-observed'}
    except Exception:
        return {'status':'pending','live_verified':False,'reason':'model_evidence_pending'}
