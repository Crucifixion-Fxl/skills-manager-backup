"""Real Popen/stdio fake ELF tests. Never invokes a provider or credentials."""
import base64
import ctypes
import hashlib
import http.server
import ssl
import socket
import threading
import importlib.util
import json
import os
from pathlib import Path
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import types
import time
import unittest
import zlib

HERE = Path(__file__).resolve().parent
SOURCE = HERE / 'integration/hostd_l3'
BASE = Path(os.environ.get('HOSTD_REAL_PROVIDER_DOUBLE_SOURCE', str(SOURCE/'acp_double.py')))
ANSWER = 'ACTUAL_INDEPENDENT_FAKE_CHILD_COMPLETION'
# Exact local installed-native initialize capture; no prompt or credentials.
NATIVE_INITIALIZE_RAW = b'{"id":0,"jsonrpc":"2.0","method":"initialize","params":{"clientCapabilities":{"_meta":{"goose":{"customNotifications":true},"terminal-auth":true},"auth":{"terminal":true}},"clientInfo":{"name":"buzz-acp","version":"0.1.0"},"protocolVersion":2}}\n'
NATIVE_INITIALIZE_SHA256 = '1df85efc77261059aa2ae6d4413031954abbd7c9dbb0e1d7324a8b9f8e85bb26'

def digest(raw): return hashlib.sha256(raw).hexdigest()
def save(path, value):
    path.write_bytes(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()); path.chmod(0o600)
    return digest(path.read_bytes())
def png():
    def chunk(kind, raw):
        return len(raw).to_bytes(4,'big') + kind + raw + zlib.crc32(kind+raw).to_bytes(4,'big')
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',b'\0\0\0\1'*2+b'\x08\x02\0\0\0')+chunk(b'IDAT',zlib.compress(b'\0\x11\x22\x33'))+chunk(b'IEND',b'')

# Independent fixture signing; production verifier only verifies signatures.
def sign_event(event, secret=7):
    p=2**256-2**32-977; n=0xfffffffffffffffffffffffffffffffebaaedce6af48a03bbfd25e8cd0364141
    g=(55066263022277343669578718895168534326250603453777594175500187360389116729240,32670510020758816978083085130507043184471273380659243275938904335757337482424)
    def add(a,b):
        if a is None:return b
        if b is None:return a
        if a[0]==b[0] and (a[1]+b[1])%p==0:return None
        slope=((3*a[0]**2)*pow(2*a[1],-1,p) if a==b else (b[1]-a[1])*pow(b[0]-a[0],-1,p))%p
        x=(slope*slope-a[0]-b[0])%p;return x,(slope*(a[0]-x)-a[1])%p
    def mul(k):
        r=None;a=g
        while k:
            if k&1:r=add(r,a)
            a=add(a,a);k>>=1
        return r
    def tag(t,v):
        h=hashlib.sha256(t.encode()).digest();return hashlib.sha256(h+h+v).digest()
    d=secret; q=mul(d)
    if q[1]&1:d=n-d
    event['pubkey']=f'{q[0]:064x}'
    event['id']=digest(json.dumps([0,event['pubkey'],event['created_at'],event['kind'],event['tags'],event['content']],separators=(',',':'),ensure_ascii=False).encode())
    m=bytes.fromhex(event['id']); k=int.from_bytes(tag('BIP0340/nonce',d.to_bytes(32,'big')+q[0].to_bytes(32,'big')+m),'big')%n
    r=mul(k)
    if r[1]&1:k=n-k
    e=int.from_bytes(tag('BIP0340/challenge',r[0].to_bytes(32,'big')+q[0].to_bytes(32,'big')+m),'big')%n
    event['sig']=(r[0].to_bytes(32,'big')+((k+e*d)%n).to_bytes(32,'big')).hex();return event

FAKE_NATIVE_C = r'''
#include <unistd.h>
#include <sys/wait.h>
#include <stdio.h>
int main(int argc,char **argv){
 if(argc<2)return 80;
 pid_t child=fork();if(child<0)return 81;
 if(!child){execv(argv[1],argv+1);_exit(82);}
 int status;if(waitpid(child,&status,0)!=child)return 83;
 FILE *out=fopen("capture.native-wait","w");if(!out)return 84;
 fprintf(out,"%d %d",child,WIFEXITED(status)?WEXITSTATUS(status):-1);fclose(out);
 return WIFEXITED(status)?WEXITSTATUS(status):85;
}
'''

FAKE_C = r'''
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include <stdlib.h>
int main(int argc,char **argv){
 FILE *a=fopen("capture.argv","w"); for(int i=0;i<argc;i++)fprintf(a,"%s\n",argv[i]);fclose(a);
 const char *mode="ok",*image=0;for(int i=1;i<argc;i++){if(!strcmp(argv[i],"--image")&&i+1<argc)image=argv[++i];else if(!strcmp(argv[i],"--fake-mode")&&i+1<argc)mode=argv[++i];}
 if(!strcmp(mode,"delay-image"))usleep(150000);
 FILE *in=fopen(image,"rb"),*out=fopen("capture.image","wb");if(!in||!out)return 8;int c;while((c=fgetc(in))!=EOF)fputc(c,out);fclose(in);fclose(out);
 out=fopen("capture.pid","w");fprintf(out,"%d",getpid());fclose(out);
 out=fopen("capture.prompt","wb");while((c=getchar())!=EOF)fputc(c,out);fclose(out);
 if(!strncmp(mode,"fork-",5)){
  pid_t kid=fork();if(kid<0)return 19;
  if(kid==0){
   if(!strcmp(mode,"fork-escape"))if(setsid()<0)_exit(20);
   close(0);close(1);close(2);
   if(!strcmp(mode,"fork-tree")){
    pid_t grand=fork();if(grand<0)_exit(23);
    if(grand==0){sleep(30);_exit(0);}
    FILE *second=fopen("capture.descendant2","w");fprintf(second,"%d",grand);fclose(second);
   }
   FILE *ready=fopen("capture.child-ready.tmp","w");fprintf(ready,"%d",getpid());fclose(ready);if(rename("capture.child-ready.tmp","capture.child-ready"))_exit(24);
   sleep(30);_exit(0);
  }
  out=fopen("capture.descendant.tmp","w");fprintf(out,"%d",kid);fclose(out);if(rename("capture.descendant.tmp","capture.descendant"))return 25;
  for(int i=0;i<2000&&access("capture.child-ready",F_OK);i++)usleep(1000);
  if(access("capture.child-ready",F_OK))return 21;
  out=fopen("capture.descendant-ready.tmp","w");fprintf(out,"%d",kid);fclose(out);if(rename("capture.descendant-ready.tmp","capture.descendant-ready"))return 26;
  mode=!strcmp(mode,"fork-sleep")?"sleep":!strcmp(mode,"fork-fail")?"fail":!strcmp(mode,"fork-empty")?"empty":"ok";
 }
 puts("{\"type\":\"thread.started\",\"thread_id\":\"fake-backend-session\"}");
 if(!strcmp(mode,"sleep"))sleep(10);
 if(!strcmp(mode,"oversize")){for(int i=0;i<300000;i++)putchar('X');fflush(stdout);return 0;}
 if(strcmp(mode,"empty"))puts("{\"type\":\"item.completed\",\"item\":{\"id\":\"fake-item\",\"type\":\"agent_message\",\"text\":\"ACTUAL_INDEPENDENT_FAKE_CHILD_COMPLETION\"}}");
 if(!strcmp(mode,"fail"))return 7;
 puts("{\"type\":\"turn.completed\",\"usage\":{\"input_tokens\":1,\"output_tokens\":1}}");fflush(stdout);return 0;}
'''


def disappearing_proc_entry(body):
    """Exercise the real open/read exit race, only in the private adapter."""
    hook = r'''
_original_process_table=E.process_table
_proc_exit_scans=2
def _scan_with_disappearing_entry(*args,**kwargs):
    global _proc_exit_scans
    if not _proc_exit_scans:return _original_process_table(*args,**kwargs)
    _proc_exit_scans-=1
    victim=subprocess.Popen(['/usr/bin/sleep','30'],stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
    pidfd=os.pidfd_open(victim.pid);target='/proc/'+str(victim.pid)+'/stat'
    original_read=Path.read_text
    def read_stat(path,*a,**kw):
        if str(path)!=target:return original_read(path,*a,**kw)
        with path.open('r') as opened:
            signal.pidfd_send_signal(pidfd,signal.SIGKILL);victim.wait(timeout=2)
            try:return opened.read()
            except ProcessLookupError as error:
                if error.errno!=3:raise
                fd=os.open('capture.proc-exit',os.O_WRONLY|os.O_CREAT|os.O_APPEND|os.O_NOFOLLOW,0o600)
                try:os.write(fd,b'ESRCH\n')
                finally:os.close(fd)
                raise
    Path.read_text=read_stat
    try:return _original_process_table(*args,**kwargs)
    finally:
        Path.read_text=original_read
        if victim.returncode is None:
            signal.pidfd_send_signal(pidfd,signal.SIGKILL);victim.wait(timeout=2)
        os.close(pidfd)
E.process_table=_scan_with_disappearing_entry
'''
    needle="if __name__=='__main__':sys.exit(main())"
    assert body.count(needle)==1
    return body.replace(needle,hook+'\n'+needle)

class NativeTLSServer:
    """Independent loopback TLS bytes service; never a model/witness oracle."""
    def __init__(self, test):
        self.test=test;self.requests=[];self.mode='ok';self.body=test.image
        self.fault_entered=threading.Event();self.client_closed=threading.Event()
        self.fixture_released=threading.Event();self.fault_connections=[]
        cert=test.root/'ca.pem';key=test.root/'tls.key'
        tls_fixture=os.environ.get('HOSTD_NATIVE_TRANSPORT_TEST_TLS_DIR')
        if tls_fixture:
            directory=Path(tls_fixture);pins=json.loads((directory/'receipt.json').read_text())
            if pins['certificate_sha256']!=digest((directory/'ca.pem').read_bytes()) or pins['synthetic_key_sha256']!=digest((directory/'tls.key').read_bytes()):raise RuntimeError('synthetic TLS fixture pin mismatch')
            shutil.copyfile(directory/'ca.pem',cert);shutil.copyfile(directory/'tls.key',key)
            done=types.SimpleNamespace(returncode=0)
        else:done=subprocess.run(['/usr/bin/openssl','req','-x509','-newkey','rsa:2048','-nodes','-keyout',str(key),'-out',str(cert),'-days','1','-subj','/CN=127.0.0.1','-addext','subjectAltName=IP:127.0.0.1'],capture_output=True,timeout=10)
        if done.returncode:raise RuntimeError('synthetic TLS fixture setup failed')
        cert.chmod(0o600);key.chmod(0o600)
        fixture=self
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                fixture.requests.append({'path':self.path,'host':self.headers.get('Host')})
                auth=self.headers.get('Authorization','')
                try:
                    payload=auth.removeprefix('Nostr ');event=json.loads(base64.urlsafe_b64decode(payload+'='*((-len(payload))%4)))
                    expected=sign_event({k:event[k] for k in ('kind','created_at','content','tags')})
                    assert {k:v for k,v in event.items() if k!='sig'}=={k:v for k,v in expected.items() if k!='sig'}
                    assert test.fixture_signer.schnorr_verify(bytes.fromhex(event['id']),bytes.fromhex(event['pubkey']),bytes.fromhex(event['sig']))
                    assert event['tags']==[['t','get'],['expiration',str(event['created_at']+60)],['server',fixture.origin.removeprefix('https://')],['x',digest(test.image)]]
                    assert self.headers.get('x-auth-tag')==test.transport_env['BUZZ_AUTH_TAG']
                    fixture.requests[-1]['signed_auth_verified']=True
                except Exception:
                    self.send_response(401);self.send_header('Content-Length','0');self.end_headers();return
                if fixture.mode in ('drip-header','stall-body'):
                    # Hold the original response incomplete. Only the client may
                    # end this exchange before test cleanup, never a fixture sleep.
                    fixture.fault_connections.append(self.connection);fixture.fault_entered.set()
                    try:
                        if fixture.mode=='drip-header':
                            self.wfile.write(b'HTTP/1.1 200 OK\r\nX-One: 1\r\n')
                        else:
                            self.send_response(200);self.send_header('Content-Type','image/png')
                            self.send_header('Content-Length',str(len(fixture.body)));self.end_headers()
                        self.wfile.flush()
                    except (BrokenPipeError,ConnectionResetError,ssl.SSLEOFError):
                        if not fixture.fixture_released.is_set():fixture.client_closed.set()
                    else:fixture.await_client_close(self.connection)
                    return
                if fixture.mode=='redirect':
                    self.send_response(302);self.send_header('Location','https://127.0.0.1:1/secret');self.send_header('Content-Length','0');self.end_headers();return
                self.send_response(200)
                self.send_header('Content-Type','image/jpeg' if fixture.mode=='mime' else 'image/png')
                self.send_header('Content-Length',str(24577 if fixture.mode=='oversize' else len(fixture.body)))
                if fixture.mode=='encoded':self.send_header('Content-Encoding','gzip')
                self.end_headers()
                try:self.wfile.write(fixture.body)
                except (BrokenPipeError,ConnectionResetError,ssl.SSLError):pass
        self.server=http.server.HTTPServer(('127.0.0.1',0),Handler)
        context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(cert,key)
        self.server.socket=context.wrap_socket(self.server.socket,server_side=True)
        self.origin='https://127.0.0.1:'+str(self.server.server_port)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.config={'origin':self.origin,'ca_path':str(cert),'ca_sha256':digest(cert.read_bytes()),'certificate_sha256':digest(ssl.PEM_cert_to_DER_cert(cert.read_text())),'signer_path':str(test.root/'nostrkit.py'),'signer_sha256':digest((SOURCE/'nostrkit.py').read_bytes()),'owner_pubkey':'','timeout_seconds':1.0}
    def await_client_close(self,connection):
        try:
            # Longer than the unchanged 4s ACP read guard. This timeout can only
            # fail the test; it cannot manufacture an early transport rejection.
            connection.settimeout(8)
            closed=connection.recv(1)==b''
        except (ConnectionResetError,ssl.SSLEOFError):closed=True
        except (OSError,ssl.SSLError):closed=False
        if closed and not self.fixture_released.is_set():self.client_closed.set()
    def close(self):
        self.fixture_released.set()
        for connection in self.fault_connections:
            try:connection.shutdown(socket.SHUT_RDWR)
            except OSError:pass
        self.server.shutdown();self.server.server_close();self.thread.join(timeout=2)

def native_transport_fixture(test):
    server=NativeTLSServer(test);test.addCleanup(server.close)
    mod=types.ModuleType('fixture_signer');exec(compile((SOURCE/'nostrkit.py').read_bytes(),'fixture_signer','exec'),mod.__dict__)
    test.fixture_signer=mod
    selected=mod.pubkey_xonly((7).to_bytes(32,'big')).hex();owner=mod.pubkey_xonly((11).to_bytes(32,'big')).hex()
    signature=mod.schnorr_sign(hashlib.sha256(('nostr:agent-auth:'+selected+':').encode()).digest(),(11).to_bytes(32,'big'),bytes(32)).hex()
    test.selected=selected;test.config['selected_pubkey']=selected;server.config['owner_pubkey']=owner
    test.transport_env={'BUZZ_PRIVATE_KEY':f'{7:064x}','BUZZ_AUTH_TAG':json.dumps(['auth',owner,'',signature]),'BUZZ_RELAY_URL':server.origin.replace('https://','wss://'),'HTTP_PROXY':'http://127.0.0.1:1','HTTPS_PROXY':'http://127.0.0.1:1'}
    test.native_fixture=True
    test.config['native_image_transport']=server.config
    test.config['native_path']=str(test.native_fake);test.config['native_sha256']=digest(test.native_fake.read_bytes())
    test.event=sign_event({'kind':9,'created_at':1704067200,'content':'Identify the visual object.','tags':[['h',test.channel],['p',selected],['imeta','url '+server.origin+'/media/'+digest(test.image)+'.png','x '+digest(test.image),'m image/png','dim 1x1']]})
    test.config['mirror_pubkey']=test.event['pubkey']
    text=test.prompt[0]['text'];text=text[:text.index('Event ID: ')]+ 'Event ID: '+test.event['id']+'\nChannel: '+test.channel+'\nKind: 9\nFrom: npub1qqqq (hex: '+test.event['pubkey']+')\nTime: 2024-01-01T00:00:00Z\nContent: '+test.event['content']+'\nTags: '+json.dumps(test.event['tags'])+'\n</buzz-event>'
    test.prompt=[{'type':'text','text':text}]
    return server

class TransportDeadlineTests(unittest.TestCase):
    """Exact fetch budget without conflating CPU scheduling with protocol time.

    The real TLS tests separately prove an actual client abort/no child/success.
    Only clock, transport and signing setup are substituted here; the original
    fetch_native_image deadline, Timer callback and PNG/late-success checks run.
    """
    def fixture(self,*,handshake=.05,header_drips=(.06,),body_delays=(.02,.02,.02),
                automatic_timer=True,expire_event=False):
        path=SOURCE/'model_evidence.py';m=types.ModuleType('deadline_evidence');m.__file__=str(path)
        exec(compile(path.read_bytes(),str(path),'exec'),m.__dict__)
        image=png();state=types.SimpleNamespace(now=100.,timers=[],timeouts=[],connect_timeouts=[],
            shutdowns=[],requests=0,header_reads=0,body_reads=0,decoded=0,closed=False)
        def advance(seconds):
            target=state.now+seconds
            for timer in state.timers:
                if automatic_timer and timer.started and not timer.fired and timer.at<=target:
                    state.now=timer.at;timer.fired=True;timer.callback()
            state.now=target
        class Timer:
            def __init__(self,interval,callback):
                self.interval=interval;self.callback=callback;self.at=state.now+interval
                self.started=False;self.fired=False;self.cancelled=False;self.ident=None
                state.timers.append(self)
            def start(self):self.started=True;self.ident=1
            def cancel(self):self.cancelled=True
            def join(self,timeout):self.join_timeout=timeout
        class Socket:
            def settimeout(self,value):state.timeouts.append(value)
            def do_handshake(self):advance(handshake)
            def getpeercert(self,binary_form):return b'test certificate'
            def shutdown(self,how):state.shutdowns.append((self,how))
            def close(self):pass
        sock=Socket()
        def connect(address,timeout):
            self.assertEqual(address,('127.0.0.1',443));state.connect_timeouts.append(timeout)
            advance(.04);return sock
        class Response:
            status=200
            def __init__(self):self.position=0
            def getheaders(self):return [('Content-Type','image/png'),('Content-Length',str(len(image)))]
            def read1(self,limit):
                n=state.body_reads;state.body_reads+=1;advance(body_delays[n])
                if expire_event:state.timers[0].callback()
                size=len(image) if n==len(body_delays)-1 else 10
                value=image[self.position:self.position+min(size,limit)];self.position+=len(value);return value
        class Connection:
            def __init__(self,host,port,timeout,context):
                self.sock=None;self_outer.assertEqual(timeout,.3)
            def request(self,*args,**kwargs):state.requests+=1;advance(.01)
            def getresponse(self):
                for delay in header_drips:
                    state.header_reads+=1;advance(delay)
                    if state.shutdowns:raise OSError('absolute Timer interrupted headers')
                return Response()
            def close(self):state.closed=True
        self_outer=self
        def context(**kwargs):
            advance(.01);return types.SimpleNamespace(wrap_socket=lambda plain,**kw:plain)
        def sign(*args):advance(.02);return bytes(64)
        m.time=types.SimpleNamespace(monotonic=lambda:state.now,monotonic_ns=lambda:int(state.now*1e9),time=lambda:1700000000)
        m.threading=types.SimpleNamespace(Timer=Timer,Event=threading.Event)
        m.socket=types.SimpleNamespace(create_connection=connect,SHUT_RDWR=socket.SHUT_RDWR)
        m.ssl=types.SimpleNamespace(create_default_context=context)
        m.http=types.SimpleNamespace(client=types.SimpleNamespace(HTTPSConnection=Connection,HTTPException=m.http.client.HTTPException))
        m.transport_target=lambda *args:'https://127.0.0.1/media/image.png'
        m.transport_config=lambda *args:(b'ca','ca identity',443)
        m.transport_identity=lambda *args:(types.SimpleNamespace(schnorr_sign=sign),bytes(32),'a'*64,'test auth')
        m.read_private=lambda *args:(b'ca','ca identity')
        decode=m.decode_png
        def observed_decode(*args):state.decoded+=1;return decode(*args)
        m.decode_png=observed_decode
        config={'native_image_transport':{'origin':'https://127.0.0.1','timeout_seconds':.3,
            'certificate_sha256':digest(b'test certificate'),'ca_path':'fixture','ca_sha256':'c'*64}}
        fetch=lambda:m.fetch_native_image(None,{}, {'image_sha256':digest(image)},config)
        return m,state,sock,fetch

    def test_original_deadline_decreases_connect_tls_header_and_body_budgets(self):
        m,state,sock,fetch=self.fixture();result=fetch()
        self.assertEqual(len(state.timers),1);self.assertAlmostEqual(state.timers[0].interval,.27)
        self.assertAlmostEqual(state.connect_timeouts[0],.27)
        self.assertEqual(len(state.timeouts),6)
        for actual,expected in zip(state.timeouts,(.23,.18,.17,.11,.09,.07)):self.assertAlmostEqual(actual,expected)
        self.assertAlmostEqual((result['finished_ns']-result['started_ns'])/1e9,.25)
        self.assertEqual(state.shutdowns,[]);self.assertTrue(state.closed);self.assertTrue(state.timers[0].cancelled)

    def test_handshake_cannot_restart_the_original_budget(self):
        m,state,sock,fetch=self.fixture(handshake=.25,automatic_timer=False)
        with self.assertRaises(m.EvidenceError):fetch()
        self.assertEqual(state.requests,0);self.assertEqual(len(state.timeouts),1)
        self.assertTrue(state.closed);self.assertTrue(state.timers[0].cancelled)

    def test_header_drips_are_interrupted_by_one_absolute_timer(self):
        m,state,sock,fetch=self.fixture(header_drips=(.07,.07,.07,.07))
        with self.assertRaises(m.EvidenceError):fetch()
        self.assertEqual(state.header_reads,3);self.assertEqual(state.body_reads,0)
        self.assertEqual(len(state.timers),1);self.assertAlmostEqual(state.timers[0].at,100.3)
        self.assertEqual(state.shutdowns,[(sock,socket.SHUT_RDWR)]);self.assertTrue(state.closed)

    def test_body_drips_cannot_renew_remaining_budget(self):
        m,state,sock,fetch=self.fixture(body_delays=(.08,.08,.08),automatic_timer=False)
        with self.assertRaises(m.EvidenceError):fetch()
        self.assertEqual(state.body_reads,2);self.assertEqual(state.decoded,0)
        self.assertAlmostEqual(state.timeouts[-1],.03);self.assertTrue(state.closed)

    def test_expired_timer_shuts_down_the_actual_body_socket(self):
        m,state,sock,fetch=self.fixture(body_delays=(.08,.08,.08))
        with self.assertRaises(m.EvidenceError):fetch()
        self.assertEqual(state.body_reads,2);self.assertEqual(state.shutdowns,[(sock,socket.SHUT_RDWR)])
        self.assertTrue(state.timers[0].cancelled);self.assertTrue(state.closed)

    def test_complete_valid_png_after_deadline_never_becomes_success(self):
        m,state,sock,fetch=self.fixture(body_delays=(.12,),automatic_timer=False)
        with self.assertRaises(m.EvidenceError):fetch()
        self.assertEqual(state.decoded,1);self.assertEqual(state.body_reads,1)
        self.assertGreater(state.now,100.3);self.assertTrue(state.closed)

    def test_expired_event_alone_prevents_otherwise_timely_success(self):
        m,state,sock,fetch=self.fixture(body_delays=(.02,),automatic_timer=False,expire_event=True)
        with self.assertRaises(m.EvidenceError):fetch()
        self.assertLess(state.now,100.3);self.assertEqual(state.decoded,1)
        self.assertEqual(state.shutdowns,[(sock,socket.SHUT_RDWR)]);self.assertTrue(state.closed)


class RealProviderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build=tempfile.TemporaryDirectory();cls.fake=Path(cls.build.name)/'fake-backend';src=Path(cls.build.name)/'fake.c';src.write_text(FAKE_C)
        prebuilt=os.environ.get('HOSTD_REAL_PROVIDER_FAKE_ELF')
        if prebuilt:
            binary=Path(prebuilt);receipt=json.loads(binary.with_suffix('.json').read_text())
            if receipt['fake_c_sha256']!=digest(FAKE_C.encode()) or receipt['binary_sha256']!=digest(binary.read_bytes()) or not binary.read_bytes().startswith(b'\x7fELF'):raise RuntimeError('prebuilt fake ELF source/hash mismatch')
            shutil.copyfile(binary,cls.fake)
        else:
            done=subprocess.run(['/usr/bin/cc',str(src),'-O0','-o',str(cls.fake)],capture_output=True,timeout=15)
            if done.returncode:raise RuntimeError('fake ELF environment compilation failed')
        cls.fake.chmod(0o755)
        cls.native_fake=Path(cls.build.name)/'fake-native';src=Path(cls.build.name)/'native.c';src.write_text(FAKE_NATIVE_C)
        native_prebuilt=os.environ.get('HOSTD_REAL_PROVIDER_NATIVE_FAKE_ELF')
        if native_prebuilt:
            binary=Path(native_prebuilt);receipt=json.loads(binary.with_suffix('.json').read_text())
            if receipt['fake_c_sha256']!=digest(FAKE_NATIVE_C.encode()) or receipt['binary_sha256']!=digest(binary.read_bytes()) or not binary.read_bytes().startswith(b'\x7fELF'):raise RuntimeError('prebuilt native fake ELF pin mismatch')
            shutil.copyfile(binary,cls.native_fake)
        else:
            done=subprocess.run(['/usr/bin/cc',str(src),'-O0','-o',str(cls.native_fake)],capture_output=True,timeout=15)
            if done.returncode:raise RuntimeError('native fake ELF environment compilation failed')
        cls.native_fake.chmod(0o755)
    @classmethod
    def tearDownClass(cls):cls.build.cleanup()
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.root.chmod(0o700)
        self.work=self.root/'work';self.work.mkdir(mode=0o700);self.children=[];self.buffers={};self.addCleanup(self.reap)
        self.image=png();self.selected='a'*64;self.channel='12345678-1234-1234-1234-123456789abc';self.runid='1'*32
        self.event=sign_event({'kind':9,'created_at':1704067200,'content':'Identify the visual object.','tags':[['h',self.channel],['p',self.selected],['imeta','x '+digest(self.image),'m image/png','dim 1x1']]})
        self.prompt=[{'type':'text','text':'<buzz-event type="@mention">\nEvent ID: '+self.event['id']+'\nChannel: '+self.channel+'\nKind: 9\nFrom: npub1qqqq (hex: '+self.event['pubkey']+')\nTime: 2024-01-01T00:00:00Z\nContent: '+self.event['content']+'\nTags: '+json.dumps(self.event['tags'])+'\n</buzz-event>'},{'type':'image','mimeType':'image/png','data':base64.b64encode(self.image).decode()}]
        self.manifest=self.root/'manifest.json';self.pin=save(self.manifest,{'version':1,'run_id':self.runid,'work_dir':str(self.work),'receipt_file':str(self.root/'receipt.json'),'reply_text':'FORBIDDEN_CANNED_REPLY','hold_marker':'L3-HOLD-'+self.runid})
        self.witness_config=self.root/'witness-config.json';self.witness_pin=save(self.witness_config,{'version':1,'run_id':self.runid,'work_dir':str(self.work),'witness_file':str(self.root/'input-witness.json')})
        stat=Path('/proc/self/stat').read_text().rsplit(')',1)[1].split()
        self.config={'version':1,'run_id':self.runid,'backend_path':str(self.fake),'backend_sha256':digest(self.fake.read_bytes()),'backend_args':['exec','--json','--ephemeral','--ignore-user-config','--ignore-rules','--image','{image}','--fake-mode','ok','--model','fake-test','-'],'model':'fake-test','timeout_seconds':1.0,'env_allowlist':[],'selected_pubkey':self.selected,'channel_id':self.channel,'mirror_pubkey':self.event['pubkey'],'native_path':os.path.realpath(sys.executable),'native_sha256':digest(Path(sys.executable).read_bytes())}
    def spawn(self,mode='ok'):
        self.config['backend_args'][8]=mode
        config_pin=save(self.root/'backend.json',self.config)
        baseline=os.environ.get('HOSTD_REAL_PROVIDER_BASELINE')=='1'
        if baseline:script=self.root/'acp_double.py';shutil.copyfile(BASE,script)
        else:
            shutil.copyfile(BASE,self.root/'acp_double.py');shutil.copyfile(SOURCE/'model_evidence.py',self.root/'model_evidence.py')
            if 'native_image_transport' in self.config:shutil.copyfile(SOURCE/'nostrkit.py',self.root/'nostrkit.py')
            script=self.root/'acp_real_provider.py'
            body=(SOURCE/'acp_real_provider.py').read_text().replace('__RENDER_BACKEND_CONFIG_SHA256__',config_pin).replace('__RENDER_MODEL_EVIDENCE_SHA256__',digest((self.root/'model_evidence.py').read_bytes()))
            if hasattr(self,'adapter_transform'):body=self.adapter_transform(body)
            script.write_text(body)
        for child in self.root.glob('*.py'):child.chmod(0o600)
        args=[sys.executable,'-I',str(script),'--manifest',str(self.manifest),'--sha256',self.pin,'--witness-manifest',str(self.witness_config),'--witness-sha256',self.witness_pin]
        launch=[str(self.native_fake),*args] if 'native_image_transport' in self.config or getattr(self,'native_fixture',False) else args
        p=subprocess.Popen(launch,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,cwd=self.work,env={'HOME':str(self.root),'PATH':'/usr/bin:/bin','LANG':'C.UTF-8',**getattr(self,'transport_env',{})},start_new_session=True)
        self.children.append(p);self.buffers[p.pid]=b'';self.adapter=script;self.adapter_argv=args;return p
    def reap(self):
        for p in self.children:
            if p.poll() is None:p.stdin.close()
            try:p.wait(timeout=3)
            except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);p.wait(timeout=3)
            for s in (p.stdin,p.stdout,p.stderr):s.close()
    def send(self,p,method,params,rid):
        req={'jsonrpc':'2.0','method':method,'params':params}
        if rid is not None:req['id']=rid
        p.stdin.write((json.dumps(req)+'\n').encode());p.stdin.flush()
    def read(self,p,timeout=4):
        end=time.monotonic()+timeout;buf=self.buffers[p.pid]
        while b'\n' not in buf:
            left=end-time.monotonic();self.assertGreater(left,0,'ACP response timeout')
            self.assertTrue(select.select([p.stdout],[],[],left)[0],'ACP response timeout');piece=os.read(p.stdout.fileno(),65536)
            self.assertTrue(piece,'adapter unexpectedly exited');buf+=piece
        line,self.buffers[p.pid]=buf.split(b'\n',1);return json.loads(line)
    def start(self,p):
        self.send(p,'initialize',{'protocolVersion':1},0);self.assertIn('result',self.read(p))
        self.send(p,'session/new',{'cwd':str(self.work),'mcpServers':[]},1);return self.read(p)['result']['sessionId']
    def submit(self,p,sid,rid=2,prompt=None):self.send(p,'session/prompt',{'sessionId':sid,'prompt':self.prompt if prompt is None else prompt},rid)
    def assert_no_success(self):self.assertFalse((self.root/'model-evidence.json').exists())
    def test_captured_native_protocol2_negotiates_supported_protocol1(self):
        self.assertEqual(digest(NATIVE_INITIALIZE_RAW), NATIVE_INITIALIZE_SHA256)
        original = json.loads(NATIVE_INITIALIZE_RAW)
        self.assertEqual(type(original['params']['protocolVersion']), int)
        self.assertEqual(original['params']['protocolVersion'], 2)
        p = self.spawn()
        p.stdin.write(NATIVE_INITIALIZE_RAW.rstrip(b'\n') + b'\n'); p.stdin.flush()
        response = self.read(p)
        self.assertEqual(response['id'], original['id'])
        self.assertEqual(response['result']['protocolVersion'], 1)
        self.assertEqual(response['result']['agentInfo']['name'], 'hostd-l3-real-exec-adapter')
        self.send(p, 'session/new', {'cwd': str(self.work), 'mcpServers': []}, 100)
        self.assertIn('sessionId', self.read(p)['result'])
        self.assertFalse((self.work/'capture.argv').exists()); self.assert_no_success()
        self.assertEqual(json.loads(NATIVE_INITIALIZE_RAW), original)

    def test_protocol_negotiation_rejects_unsupported_or_malformed_offers(self):
        for version in (3, 0, -1, True, False, '2', 2.0, None):
            with self.subTest(version_type=type(version).__name__, version=version):
                self.reap(); self.tmp.cleanup(); self.setUp()
                p = self.spawn()
                self.send(p, 'initialize', {'protocolVersion': version}, 10)
                self.assertEqual(self.read(p)['error']['code'], -32602)
                # A rejected offer does not initialize the adapter or coerce it.
                self.send(p, 'initialize', {'protocolVersion': 1}, 11)
                self.assertEqual(self.read(p)['result']['protocolVersion'], 1)
                p.stdin.close(); self.assertEqual(p.wait(timeout=3), 0)
                self.assertFalse((self.work/'capture.argv').exists()); self.assert_no_success()

    def test_protocol2_preserves_envelope_id_duplicate_and_initialized_controls(self):
        for changed in ({'jsonrpc': '1.0'}, {'id': True}, {'id': None},
                        {'id': []}, {'params': []}, {'params': '2'}):
            with self.subTest(changed_keys=sorted(changed)):
                self.reap(); self.tmp.cleanup(); self.setUp()
                p = self.spawn()
                req = {'jsonrpc': '2.0', 'id': 10, 'method': 'initialize',
                       'params': {'protocolVersion': 2}}
                req.update(changed)
                p.stdin.write((json.dumps(req)+'\n').encode()); p.stdin.flush()
                self.assertEqual(self.read(p)['error']['code'], -32602)
                p.stdin.close(); self.assertEqual(p.wait(timeout=3), 0)
        self.reap(); self.tmp.cleanup(); self.setUp()
        p = self.spawn()
        self.send(p, 'initialize', {'protocolVersion': 2}, 10)
        self.assertEqual(self.read(p)['result']['protocolVersion'], 1)
        self.send(p, 'initialize', {'protocolVersion': 2}, 10)
        self.assertEqual(self.read(p)['error']['code'], -32600)
        self.send(p, 'initialize', {'protocolVersion': 2}, 11)
        self.assertEqual(self.read(p)['error']['code'], -32000)
        self.assertFalse((self.work/'capture.argv').exists()); self.assert_no_success()

    def test_protocol2_duplicate_json_keys_remain_rejected(self):
        p = self.spawn()
        p.stdin.write(b'{"jsonrpc":"2.0","id":10,"method":"initialize",'
                      b'"params":{"protocolVersion":2,"protocolVersion":1}}\n'); p.stdin.flush()
        self.assertIn('error', self.read(p))
        self.send(p, 'initialize', {'protocolVersion': 1}, 11)
        self.assertEqual(self.read(p)['result']['protocolVersion'], 1)
        self.assertFalse((self.work/'capture.argv').exists()); self.assert_no_success()

    def test_actual_child_completion_and_image_bytes(self):
        p=self.spawn();sid=self.start(p);self.submit(p,sid)
        chunk=self.read(p);self.assertEqual(chunk['params']['update']['content']['text'],ANSWER)
        self.assertEqual(self.read(p)['result']['stopReason'],'end_turn');self.assertEqual((self.work/'capture.image').read_bytes(),self.image)
        doc=json.loads((self.root/'model-evidence.json').read_text());self.assertEqual(doc['records'][0]['completion'],ANSWER);self.assertEqual(doc['records'][0]['completion_id'],'fake-item');self.assertEqual(doc['records'][0]['backend_session_id'],'fake-backend-session');self.assertNotEqual(doc['records'][0]['backend_session_id'],sid)
    def test_native_text_fetch_preserves_witness_and_actual_sealed_child(self):
        server=native_transport_fixture(self);original=json.loads(json.dumps(self.prompt))
        p=self.spawn();sid=self.start(p);self.submit(p,sid)
        response=self.read(p);self.assertIn('params',response)
        self.assertEqual(response['params']['update']['content']['text'],ANSWER);self.assertEqual(self.read(p)['result']['stopReason'],'end_turn')
        record=json.loads((self.root/'model-evidence.json').read_text())['records'][0]
        witness=json.loads((self.root/'input-witness.json').read_text())['records'][0]
        self.assertEqual(record['prompt'],original);self.assertEqual(witness['prompt_sha256'],digest(json.dumps(original,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()))
        self.assertEqual((self.work/'capture.image').read_bytes(),self.image)
        self.assertEqual(record['image']['transport']['source'],'historical-adapter-observed-protected-tls-fetch')
        self.assertEqual(server.requests,[{'path':'/media/'+digest(self.image)+'.png','host':server.origin.removeprefix('https://'),'signed_auth_verified':True}])
        self.assertTrue(record['wait_observed']);p.stdin.close();self.assertEqual(p.wait(timeout=4),0)
        native_wait=(self.work/'capture.native-wait').read_text().split();self.assertEqual(native_wait,[str(witness['provider_pid']),'0'])
        print('NATIVE_TRANSPORT_EVIDENCE '+json.dumps({'native_pid':p.pid,'adapter_pid':witness['provider_pid'],'adapter_exit':p.returncode,'original_prompt_sha256':witness['prompt_sha256'],'original_event':self.event['id'],'image_sha256':digest(self.image),'child':record['child'],'wait_observed':record['wait_observed'],'source':record['image']['transport']['source']},sort_keys=True))

    def test_native_transport_rejects_unbound_or_unsafe_original_metadata(self):
        for kind in ('http','external','query','escape','other-hash','thumbnail','duplicate','dimensions','mime','channel','selected','author','two-event','resource','unsigned-text-only'):
            with self.subTest(kind=kind):
                self.reap();self.tmp.cleanup();self.setUp();server=native_transport_fixture(self)
                if kind in ('channel','selected','author'):
                    self.config[{'channel':'channel_id','selected':'selected_pubkey','author':'mirror_pubkey'}[kind]]='0'*64 if kind!='channel' else '00000000-0000-0000-0000-000000000000'
                elif kind=='resource':self.prompt.append({'type':'resource_link','name':'image','uri':server.origin+'/media/'+digest(self.image)+'.png'})
                elif kind=='two-event':self.prompt.append(dict(self.prompt[0]))
                elif kind=='unsigned-text-only':self.prompt=[{'type':'text','text':'MSG002 simple text'}]
                else:
                    old=server.origin+'/media/'+digest(self.image)+'.png'
                    value={'http':old.replace('https://','http://'),'external':old.replace('127.0.0.1','example.invalid'),'query':old+'?credential=secret','escape':old.replace('/media/','/media/%2e%2e/'),'other-hash':old.replace(digest(self.image),'0'*64),'thumbnail':old.replace('.png','.thumb.jpg')}.get(kind)
                    if value:self.prompt[0]['text']=self.prompt[0]['text'].replace(old,value)
                    elif kind=='duplicate':self.prompt[0]['text']=self.prompt[0]['text'].replace('"dim 1x1"','"dim 1x1", "x '+digest(self.image)+'"')
                    elif kind=='dimensions':self.prompt[0]['text']=self.prompt[0]['text'].replace('dim 1x1','dim 2x1')
                    elif kind=='mime':self.prompt[0]['text']=self.prompt[0]['text'].replace('m image/png','m image/jpeg')
                p=self.spawn();sid=self.start(p);self.submit(p,sid);self.assertIn('error',self.read(p))
                self.assertFalse((self.work/'capture.pid').exists());self.assert_no_success()
                if kind!='dimensions':self.assertEqual(server.requests,[])

    def test_native_transport_tls_response_identity_and_deadline_fail_closed(self):
        for kind in ('redirect','mime','oversize','encoded','hash','crc','truncated','certificate','wrong-signer','invalid-auth','expired-auth','relay','ca-mutated','helper-mutated','drip-header','stall-body','invalid-ca'):
            with self.subTest(kind=kind):
                self.reap();self.tmp.cleanup();self.setUp();server=native_transport_fixture(self)
                server.mode=kind
                if kind in ('drip-header','stall-body'):server.config['timeout_seconds']=.3
                if kind=='hash':server.body=self.image+b'junk'
                if kind=='crc':server.body=self.image[:29]+bytes([self.image[29]^1])+self.image[30:]
                if kind=='truncated':server.body=self.image[:20]
                if kind=='certificate':server.config['certificate_sha256']='0'*64
                if kind=='wrong-signer':self.transport_env['BUZZ_PRIVATE_KEY']=f'{8:064x}'
                if kind=='invalid-auth':self.transport_env['BUZZ_AUTH_TAG']=json.dumps(['auth',server.config['owner_pubkey'],'','0'*128])
                if kind=='expired-auth':
                    # Conditional/expired NIP-OA is outside native launcher's
                    # unconditional credential contract; never silently relax it.
                    self.transport_env['BUZZ_AUTH_TAG']=json.dumps(['auth',server.config['owner_pubkey'],'created_at<1','0'*128])
                if kind=='invalid-ca':
                    (self.root/'ca.pem').write_text('invalid matching pinned PEM');server.config['ca_sha256']=digest((self.root/'ca.pem').read_bytes())
                if kind=='relay':self.transport_env['BUZZ_RELAY_URL']='wss://127.0.0.1:1'
                p=self.spawn();sid=self.start(p)
                if kind=='ca-mutated':(self.root/'ca.pem').write_text('corrupt trust')
                if kind=='helper-mutated':(self.root/'nostrkit.py').write_text('corrupt signer')
                begun=time.monotonic();self.submit(p,sid)
                if kind in ('ca-mutated','helper-mutated'):self.assertNotEqual(p.wait(timeout=4),0)
                else:self.assertIn('error',self.read(p))
                self.assertFalse((self.work/'capture.pid').exists());self.assert_no_success()
                if kind in ('certificate','wrong-signer','invalid-auth','expired-auth','relay','ca-mutated','helper-mutated','invalid-ca'):self.assertEqual(server.requests,[])
                if kind=='redirect':self.assertEqual(len(server.requests),1)
                if kind in ('drip-header','stall-body'):
                    elapsed=time.monotonic()-begun
                    self.assertTrue(server.fault_entered.wait(4),'original TLS fault phase was not reached')
                    self.assertTrue(server.client_closed.wait(4),'client did not close the pending TLS exchange')
                    self.assertFalse(server.fixture_released.is_set())
                    self.assertEqual(len(server.requests),1);self.assertTrue(server.requests[0]['signed_auth_verified'])
                    self.send(p,'session/new',{'cwd':str(self.work),'mcpServers':[]},99);self.assertIn('sessionId',self.read(p)['result'])
                    p.stdin.close();self.assertEqual(p.wait(timeout=4),0);self.assertEqual(p.stderr.read(),b'')
                    print('ABSOLUTE_TRANSPORT_DEADLINE '+json.dumps({'case':kind,'bound_seconds':server.config['timeout_seconds'],'observed_acp_seconds':elapsed,'client_aborted_pending_exchange':True}))

    def test_native_transport_absolute_tls_handshake_deadline(self):
        server=native_transport_fixture(self);old=server.origin
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen(1);listener.settimeout(8)
        accepted=[];client_closed=threading.Event();fixture_released=threading.Event();connections=[]
        def stall():
            try:
                conn,_=listener.accept();accepted.append(True);connections.append(conn)
                with conn:
                    conn.settimeout(8)
                    # Drain the TLS ClientHello without answering it; prove the
                    # client, not a fixture timeout/close, terminates the exchange.
                    while conn.recv(16384):pass
                    if not fixture_released.is_set():client_closed.set()
            except ConnectionResetError:
                if not fixture_released.is_set():client_closed.set()
            except OSError:pass
        thread=threading.Thread(target=stall,daemon=True);thread.start()
        def cleanup():
            fixture_released.set();listener.close()
            for conn in connections:
                try:conn.shutdown(socket.SHUT_RDWR)
                except OSError:pass
            thread.join(2)
        self.addCleanup(cleanup)
        origin='https://127.0.0.1:'+str(listener.getsockname()[1])
        server.config['origin']=origin;server.config['timeout_seconds']=.3
        self.transport_env['BUZZ_RELAY_URL']=origin.replace('https://','wss://');self.prompt[0]['text']=self.prompt[0]['text'].replace(old,origin)
        p=self.spawn();sid=self.start(p);begun=time.monotonic();self.submit(p,sid)
        self.assertIn('error',self.read(p));elapsed=time.monotonic()-begun
        self.assertEqual(accepted,[True]);self.assertTrue(client_closed.wait(4));self.assertFalse(fixture_released.is_set())
        self.assert_no_success();self.assertFalse((self.work/'capture.pid').exists())
        self.send(p,'session/new',{'cwd':str(self.work),'mcpServers':[]},99);self.assertIn('sessionId',self.read(p)['result'])
        p.stdin.close();self.assertEqual(p.wait(timeout=4),0);self.assertEqual(p.stderr.read(),b'')
        print('ABSOLUTE_TRANSPORT_DEADLINE '+json.dumps({'case':'tls-handshake-stall','bound_seconds':.3,'observed_acp_seconds':elapsed,'accepted_original_connection':True,'client_aborted_pending_exchange':True}))

    def test_native_invalid_compressed_png_is_sanitized_denial(self):
        def chunk(kind,raw):return len(raw).to_bytes(4,'big')+kind+raw+zlib.crc32(kind+raw).to_bytes(4,'big')
        self.image=self.image[:33]+chunk(b'IDAT',b'invalid-zlib-stream')+chunk(b'IEND',b'')
        server=native_transport_fixture(self);p=self.spawn();sid=self.start(p);self.submit(p,sid)
        self.assertIn('error',self.read(p));self.assertEqual(len(server.requests),1)
        self.assert_no_success();self.assertFalse((self.work/'capture.pid').exists())
        p.stdin.close();self.assertEqual(p.wait(timeout=4),0);self.assertEqual(p.stderr.read(),b'')

    def test_native_transport_never_passes_native_credentials_to_model_environment(self):
        server=native_transport_fixture(self)
        for name in ('BUZZ_PRIVATE_KEY','BUZZ_AUTH_TAG'):
            self.config['env_allowlist']=[name];p=self.spawn();self.assertNotEqual(p.wait(timeout=4),0)
            self.assert_no_success();self.assertFalse((self.work/'capture.pid').exists());self.assertEqual(server.requests,[])

    def padded_backend(self, size):
        # Sparse actual ELF: filesystem size/hash/exec are observed, never mocked.
        directory=tempfile.TemporaryDirectory(prefix='hostd-backend-size-',dir='/dev/shm')
        self.addCleanup(directory.cleanup)
        path=Path(directory.name)/'fake-backend'
        shutil.copyfile(self.fake,path)
        with path.open('r+b') as out:out.truncate(size)
        path.chmod(0o755)
        self.assertEqual(path.stat().st_size,size)
        self.assertLess(path.stat().st_blocks*512,1024*1024)
        h=hashlib.sha256()
        with path.open('rb') as src:
            for chunk in iter(lambda:src.read(65536),b''):h.update(chunk)
        self.config['backend_path']=str(path)
        self.config['backend_sha256']=h.hexdigest()
        return path
    def test_actual_large_elf_completion_and_sealed_image(self):
        for size in (256*1024*1024+1,289101384):
            with self.subTest(size=size):
                self.reap();self.tmp.cleanup();self.setUp()
                path=self.padded_backend(size)
                p=self.spawn();sid=self.start(p);self.submit(p,sid)
                response=self.read(p,timeout=10)
                if 'error' in response:
                    p.stdin.close();self.assertEqual(p.wait(timeout=4),0)
                    waits=json.loads((self.root/'receipt.json').read_text())['backend_waits']
                    self.assertFalse((self.work/'capture.argv').exists());self.assertEqual(waits,[])
                    print('SIZE_ADMISSION_DENIED '+json.dumps({'size':size,'response':response,'adapter_pid':p.pid,'adapter_exit':p.returncode,'backend_waits':waits,'capture_exists':False},sort_keys=True))
                self.assertIn('params',response)
                self.assertEqual(response['params']['update']['content']['text'],ANSWER)
                self.assertEqual(self.read(p)['result']['stopReason'],'end_turn')
                self.assertEqual((self.work/'capture.image').read_bytes(),self.image)
                record=json.loads((self.root/'model-evidence.json').read_text())['records'][0]
                self.assertEqual(record['backend']['path'],str(path))
                self.assertEqual(record['backend']['sha256'],self.config['backend_sha256'])
                self.assertEqual(record['child']['program']['sha256'],self.config['backend_sha256'])
                self.assertEqual(record['child']['pid'],int((self.work/'capture.pid').read_text()))
                self.assertTrue(record['wait_observed']);self.assertEqual(record['wait_returncode'],0)
                p.stdin.close();self.assertEqual(p.wait(timeout=4),0)
                waits=json.loads((self.root/'receipt.json').read_text())['backend_waits']
                self.assertEqual(len(waits),1);self.assertTrue(waits[0]['wait_observed'])
                print('SIZE_EVIDENCE '+json.dumps({'size':size,'adapter_pid':p.pid,'adapter_exit':p.returncode,'adapter_sha256':digest(self.adapter.read_bytes()),'child':record['child'],'wait':waits[0],'completion':record['completion'],'image_sha256':digest((self.work/'capture.image').read_bytes())},sort_keys=True))
    def test_actual_oversize_elf_denied_before_child(self):
        path=self.padded_backend(512*1024*1024+1)
        p=self.spawn();sid=self.start(p);self.submit(p,sid)
        self.assertIn('error',self.read(p,timeout=10))
        self.assertFalse((self.work/'capture.argv').exists());self.assert_no_success()
        p.stdin.close();self.assertEqual(p.wait(timeout=4),0)
        waits=json.loads((self.root/'receipt.json').read_text())['backend_waits']
        self.assertEqual(waits,[])
        print('SIZE_DENIED '+json.dumps({'size':path.stat().st_size,'sha256':self.config['backend_sha256'],'adapter_pid':p.pid,'adapter_exit':p.returncode,'backend_waits':waits},sort_keys=True))
    def test_mutable_audit_copy_cannot_change_sealed_child_image(self):
        p=self.spawn('delay-image');sid=self.start(p);self.submit(p,sid)
        end=time.monotonic()+1
        while not (self.work/'capture.argv').exists() and time.monotonic()<end:time.sleep(.005)
        self.assertTrue((self.work/'capture.argv').exists())
        (self.root/'model-input.png').write_bytes(b'ordinary mutable audit corruption')
        self.assertEqual(self.read(p)['params']['update']['content']['text'],ANSWER);self.assertEqual(self.read(p)['result']['stopReason'],'end_turn')
        self.assertEqual((self.work/'capture.image').read_bytes(),self.image)
    def test_invalid_image_or_unmatched_prompt_never_spawns(self):
        for kind in ('hash','base64','mime','format','dimensions','two','resource','oversize','selected','mirror','channel'):
            with self.subTest(kind=kind):
                self.reap();self.tmp.cleanup();self.setUp();prompt=json.loads(json.dumps(self.prompt))
                if kind=='hash':prompt[1]['data']=base64.b64encode(png()+b'junk').decode()
                if kind=='base64':prompt[1]['data']='!'
                if kind=='mime':prompt[1]['mimeType']='image/jpeg'
                if kind=='format':prompt[1]['data']=base64.b64encode(b'not-image').decode()
                if kind=='dimensions':prompt[1]['data']=base64.b64encode(png().replace(b'\0\0\0\1',b'\0\0\0\2',1)).decode()
                if kind=='two':prompt.append(prompt[1])
                if kind=='oversize':prompt[1]['data']='A'*32769
                if kind=='selected':self.config['selected_pubkey']='0'*64
                if kind=='mirror':self.config['mirror_pubkey']='0'*64
                if kind=='channel':self.config['channel_id']='00000000-0000-0000-0000-000000000000'
                if kind=='resource':prompt[1]={'type':'resource_link','name':'image','uri':'https://unreachable.invalid/image'}
                p=self.spawn()
                sid=self.start(p);self.submit(p,sid,prompt=prompt);self.assertIn('error',self.read(p))
                self.assertFalse((self.work/'capture.argv').exists());self.assert_no_success()
    def test_child_fail_empty_oversize_timeout_have_no_success(self):
        for mode in ('fail','empty','oversize','sleep'):
            with self.subTest(mode=mode):
                self.reap();self.tmp.cleanup();self.setUp();p=self.spawn(mode);sid=self.start(p);self.submit(p,sid);self.assertIn('error',self.read(p));self.assert_no_success();self.assertFalse(Path('/proc/'+(self.work/'capture.pid').read_text()).exists())
    def test_empty_reply_survives_actual_proc_entry_exit(self):
        self.adapter_transform=disappearing_proc_entry
        p=self.spawn('empty');sid=self.start(p);self.submit(p,sid)
        self.assertIn('error',self.read(p));self.assert_no_success()
        p.stdin.close();self.assertEqual(p.wait(timeout=4),0)
        self.assertEqual((self.work/'capture.proc-exit').read_text().splitlines(),['ESRCH','ESRCH'])
        receipt=json.loads((self.root/'receipt.json').read_text())
        self.assertTrue(receipt['backend_waits'])
        self.assertTrue(all(w['wait_observed'] and w['group_lifecycle']['absence_observed'] for w in receipt['backend_waits']))
    def test_cancel_concurrency_duplicate_and_eof_reap(self):
        p=self.spawn('sleep');sid=self.start(p);self.submit(p,sid)
        self.submit(p,sid,3);self.assertIn('error',self.read(p));self.submit(p,sid,3);self.assertIn('error',self.read(p))
        self.send(p,'session/cancel',{'sessionId':sid},None);self.assertEqual(self.read(p)['result']['stopReason'],'cancelled');self.assert_no_success();self.assertFalse(Path('/proc/'+(self.work/'capture.pid').read_text()).exists())
        self.submit(p,sid,4);p.stdin.close();self.assertEqual(p.wait(timeout=4),0);self.assert_no_success()
        receipt=json.loads((self.root/'receipt.json').read_text());self.assertTrue(receipt['backend_waits']);self.assertTrue(all(w['wait_observed'] for w in receipt['backend_waits']))
    def test_source_and_config_mutations_fail_closed(self):
        p=self.spawn();sid=self.start(p);(self.root/'backend.json').write_text('{}');self.submit(p,sid)
        self.assertNotEqual(p.wait(timeout=3),0);self.assert_no_success()

def _proc(pid):
    raw=Path('/proc/'+str(pid)+'/stat').read_text().rsplit(')',1)[1].split()
    return {'pid':pid,'start_ticks':int(raw[19]),'parent_pid':int(raw[1]),'pgid':int(raw[2]),'sid':int(raw[3]),'state':raw[0],'uid':Path('/proc/'+str(pid)).stat().st_uid,'cgroup_sha256':digest(Path('/proc/'+str(pid)+'/cgroup').read_bytes())}

def _run_fork_case(case):
    # Called only in a private subprocess, never in the shared unittest runner.
    assert ctypes.CDLL(None).prctl(36,1,0,0,0)==0
    RealProviderTests.setUpClass();t=RealProviderTests();t.setUp();kid=None;identity=None
    try:
        mode={'normal':'fork-ok','cancel':'fork-sleep','eof':'fork-sleep','eof-proc-exit':'fork-sleep','timeout':'fork-sleep','error':'fork-fail','empty':'fork-empty','escape':'fork-escape','tree':'fork-tree'}[case]
        if case=='eof-proc-exit':t.adapter_transform=disappearing_proc_entry
        p=t.spawn(mode);sid=t.start(p);t.submit(p,sid)
        end=time.monotonic()+2
        while not (t.work/'capture.descendant-ready').exists() and time.monotonic()<end:time.sleep(.005)
        if not (t.work/'capture.descendant-ready').exists():
            print(json.dumps({'phase':'fixture-not-ready','case':case,'files':[x.name for x in t.work.iterdir()],'adapter_returncode':p.poll(),'receipt':(t.root/'receipt.json').read_text() if (t.root/'receipt.json').exists() else None}),flush=True)
            if p.returncode is not None:print(p.stderr.read().decode(),flush=True)
            raise AssertionError('genuine fork fixture never became ready')
        kid=int((t.work/'capture.descendant').read_text());leader=int((t.work/'capture.pid').read_text())
        try:identity=_proc(kid)
        except FileNotFoundError:identity=None
        print(json.dumps({'phase':'fork-ready','case':case,'adapter':p.pid,'leader':leader,'descendant':identity}),flush=True)
        if case=='cancel':t.send(p,'session/cancel',{'sessionId':sid},None);assert t.read(p)['result']['stopReason']=='cancelled';t.assert_no_success()
        elif case in ('eof','eof-proc-exit'):p.stdin.close();assert p.wait(timeout=4)==0;t.assert_no_success()
        elif case in ('timeout','error','empty','escape'):assert 'error' in t.read(p);t.assert_no_success()
        else:
            assert t.read(p)['params']['update']['content']['text']==ANSWER
            assert t.read(p)['result']['stopReason']=='end_turn'
        # Both running and zombie descendants are incomplete lifecycle evidence.
        assert not Path('/proc/'+str(kid)).exists(),'owned original descendant remains after completion'
        if case=='tree':assert not Path('/proc/'+(t.work/'capture.descendant2').read_text()).exists(),'genuine grandchild remains'
        assert not Path('/proc/'+str(leader)).exists(),'original backend leader not genuinely reaped'
        if not p.stdin.closed:p.stdin.close()
        assert p.wait(timeout=4)==0
        if case=='eof-proc-exit':assert (t.work/'capture.proc-exit').read_text().splitlines()==['ESRCH','ESRCH']
        receipt=json.loads((t.root/'receipt.json').read_text());waits=receipt['backend_waits']
        assert waits and all(w['wait_observed'] and w['group_lifecycle']['absence_observed'] for w in waits)
        lifecycle=waits[0]['group_lifecycle'];assert any(w['pid']==kid and w['wait_observed'] for w in lifecycle['descendant_waits'])
        assert lifecycle['historical'] is True and lifecycle['scope']=='bounded-owned-descendants-and-original-group'
        print(json.dumps({'phase':'fork-cleaned','case':case,'adapter_wait_returncode':p.returncode,'leader_waits':waits}),flush=True)
    finally:
        t.reap()
        # RED cleanup: the private subreaper inherits the genuinely leaked child.
        if kid is not None:
            try:
                now=_proc(kid)
                if identity is None:identity=now
                assert now['start_ticks']==identity['start_ticks'] and now['parent_pid']==os.getpid() and now['uid']==os.geteuid() and now['cgroup_sha256']==identity['cgroup_sha256']
                fd=os.pidfd_open(kid)
                try:
                    assert _proc(kid)['start_ticks']==identity['start_ticks']
                    signal.pidfd_send_signal(fd,signal.SIGKILL)
                    waited,status=os.waitpid(kid,0)
                    print(json.dumps({'phase':'private-red-cleanup','pid':waited,'exit':os.waitstatus_to_exitcode(status),'identity':identity}),flush=True)
                finally:os.close(fd)
            except FileNotFoundError:pass
        t.doCleanups();RealProviderTests.tearDownClass()

def _readiness_probe_source():
    # Gate both publication windows; this proves ordering without scheduler luck.
    lines=[line for line in FAKE_C.splitlines() if line.startswith(('  out=fopen("capture.descendant",', '  out=fopen("capture.descendant.tmp",'))]
    assert len(lines)==1
    line=lines[0]
    opened='FILE *opened=fopen("capture.parent-open.tmp","w");fputs("opened",opened);fclose(opened);rename("capture.parent-open.tmp","capture.parent-open");while(access("capture.finish-parent",F_OK))usleep(1000);'
    gated='  while(access("capture.release-parent",F_OK))usleep(1000);\n'+line.replace('fprintf(out',opened+'fprintf(out',1)
    source=FAKE_C.replace(line,gated,1);needle='   sleep(30);_exit(0);';assert source.count(needle)==1
    observed='   FILE *observed=fopen("capture.child-observed.tmp","w");fprintf(observed,"%d",getpid());fclose(observed);rename("capture.child-observed.tmp","capture.child-observed");\n'
    return source.replace(needle,observed+needle,1)

def _run_readiness_probe(observe=None):
    assert ctypes.CDLL(None).prctl(36,1,0,0,0)==0
    with tempfile.TemporaryDirectory(prefix='fork-ready-') as directory:
        work=Path(directory);source=_readiness_probe_source();binary=work/'ready-probe'
        prebuilt=os.environ.get('HOSTD_REAL_PROVIDER_READINESS_FAKE_ELF')
        if prebuilt:
            original=Path(prebuilt);pins=json.loads(original.with_suffix('.json').read_text())
            assert pins['fake_c_sha256']==digest(source.encode()) and pins['binary_sha256']==digest(original.read_bytes())
            shutil.copyfile(original,binary);binary.chmod(0o755)
        else:
            src=work/'probe.c';src.write_text(source)
            compiled=subprocess.run(['/usr/bin/cc',str(src),'-O0','-o',str(binary)],capture_output=True,timeout=15)
            assert compiled.returncode==0,compiled.stderr.decode()
        # Reuse the frozen adapter's bounded kernel-lineage/pidfd cleanup API.
        shutil.copyfile(BASE,work/'acp_double.py');shutil.copyfile(SOURCE/'model_evidence.py',work/'model_evidence.py')
        for file in ('acp_double.py','model_evidence.py'):(work/file).chmod(0o600)
        adapter=work/'acp_real_provider.py'
        rendered=(SOURCE/'acp_real_provider.py').read_text().replace('__RENDER_BACKEND_CONFIG_SHA256__','0'*64).replace('__RENDER_MODEL_EVIDENCE_SHA256__',digest((work/'model_evidence.py').read_bytes()))
        adapter.write_text(rendered);adapter.chmod(0o600)
        module=types.ModuleType('readiness_cleanup_adapter');module.__file__=str(adapter)
        exec(compile(rendered,str(adapter),'exec'),module.__dict__)
        image=work/'image';image.write_bytes(png())
        proc=subprocess.Popen([str(binary),'--image',str(image),'--fake-mode','fork-sleep'],cwd=work,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
        leader=_proc(proc.pid);child=None;owned=module.OwnedProcess(proc)
        def wait_for(name):
            end=time.monotonic()+2
            while not (work/name).exists() and time.monotonic()<end:time.sleep(.005)
            assert (work/name).exists(),name+' was not published'
        try:
            if observe is not None:observe(proc,owned)
            wait_for('capture.child-observed');child=_proc(int((work/'capture.child-observed').read_text()))
            assert not (work/'capture.descendant-ready').exists(),'child readiness preceded parent PID publication'
            (work/'capture.release-parent').touch();wait_for('capture.parent-open')
            assert not (work/'capture.descendant-ready').exists(),'readiness exposed incomplete parent PID publication'
            (work/'capture.finish-parent').touch();wait_for('capture.descendant-ready')
            assert int((work/'capture.descendant').read_text())==child['pid']
            assert int((work/'capture.pid').read_text())==leader['pid']
            assert _proc(child['pid'])['start_ticks']==child['start_ticks']
            print(json.dumps({'phase':'atomic-fork-ready','leader':leader['pid'],'descendant':child['pid'],'windows_checked':['before-open','before-write']}),flush=True)
        finally:
            code,lifecycle=owned.cleanup(True)
            assert proc._hostd_original_wait_observed is True and lifecycle['absence_observed'] is True
            assert all(row['wait_observed'] for row in lifecycle['descendant_waits'])
            print(json.dumps({'phase':'atomic-probe-cleaned','leader_wait':code,'descendant_waits':lifecycle['descendant_waits'],'absence_observed':lifecycle['absence_observed']}),flush=True)


def _run_readiness_failure_probe():
    from unittest import mock
    original_source=_readiness_probe_source;original_popen=subprocess.Popen;captured={}
    def held_source():
        source=original_source();needle='   FILE *observed=fopen'
        assert source.count(needle)==1
        return source.replace(needle,'   while(access("capture.release-child",F_OK))usleep(1000);FILE *observed=fopen',1)
    def spawn(argv,*args,**kwargs):
        proc=original_popen(argv,*args,**kwargs)
        if Path(argv[0]).name=='ready-probe':captured['leader']=proc
        return proc
    def observe(proc,owned):
        # Observe only after the caller registered the original owned lifecycle.
        captured['original']=_proc(proc.pid)
        end=time.monotonic()+2
        while time.monotonic()<end:
            children=Path('/proc/'+str(proc.pid)+'/task/'+str(proc.pid)+'/children').read_text().split()
            if children:
                child=_proc(int(children[0]));assert child['parent_pid']==proc.pid and child['uid']==captured['original']['uid'] and child['sid']==proc.pid and child['pgid']==proc.pid
                captured['child']=child;break
            time.sleep(.005)
        assert 'child' in captured,'real fixture did not fork'
    try:
        with mock.patch.dict(os.environ,{'HOSTD_REAL_PROVIDER_READINESS_FAKE_ELF':os.environ.get('HOSTD_REAL_PROVIDER_READINESS_FAILURE_FAKE_ELF','')}),mock.patch(__name__+'._readiness_probe_source',side_effect=held_source),mock.patch.object(subprocess,'Popen',side_effect=spawn):
            try:_run_readiness_probe(observe)
            except AssertionError as error:assert str(error)=='capture.child-observed was not published'
            else:raise AssertionError('pre-marker failure was not exercised')
        child=captured['child'];assert captured['leader'].returncode is not None
        assert not Path('/proc/'+str(child['pid'])).exists(),'pre-marker failure leaked original fork descendant'
        print(json.dumps({'phase':'pre-marker-failure-reaped','leader_wait':captured['leader'].returncode,'descendant':child['pid']}),flush=True)
    finally:
        # Genuine RED cleanup retains exact kernel identity of the adopted orphan.
        child=captured.get('child')
        if child is not None:
            try:
                now=_proc(child['pid']);assert now['start_ticks']==child['start_ticks'] and now['parent_pid']==os.getpid() and now['uid']==child['uid'] and now['cgroup_sha256']==child['cgroup_sha256']
                fd=os.pidfd_open(child['pid'])
                try:
                    assert _proc(child['pid'])['start_ticks']==child['start_ticks']
                    signal.pidfd_send_signal(fd,signal.SIGKILL)
                    deadline=time.monotonic()+4
                    while time.monotonic()<deadline:
                        waited,status=os.waitpid(child['pid'],os.WNOHANG)
                        if waited:break
                        time.sleep(.005)
                    assert waited==child['pid'],'diagnostic original descendant wait timed out'
                finally:os.close(fd)
            except FileNotFoundError:pass

class ForkLifecycleTests(unittest.TestCase):
    def run_case(self,case):
        code="import os,sys;sys.path.insert(0,"+repr(str(HERE))+");import test_hostd_l3_acp_real_provider as t;t._run_fork_case("+repr(case)+")"
        p=subprocess.Popen([sys.executable,'-I','-c',code],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,start_new_session=True)
        try:out,_=p.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid,signal.SIGKILL);out,_=p.communicate();self.fail('private fork fixture timeout: '+out.decode())
        print(out.decode(),end='');self.assertEqual(p.returncode,0,out.decode())
    def test_fixture_pre_marker_failure_reaps_original_fork_descendant(self):
        code="import sys;sys.path.insert(0,"+repr(str(HERE))+");import test_hostd_l3_acp_real_provider as t;t._run_readiness_failure_probe()"
        proc=subprocess.Popen([sys.executable,'-I','-c',code],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,start_new_session=True);original=_proc(proc.pid)
        try:output,_=proc.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            current=_proc(proc.pid);self.assertEqual(current['start_ticks'],original['start_ticks']);self.assertEqual(current['uid'],original['uid']);self.assertEqual(current['cgroup_sha256'],original['cgroup_sha256'])
            os.killpg(proc.pid,signal.SIGKILL);output,_=proc.communicate(timeout=4);self.fail('private pre-marker probe timeout: '+output.decode())
        print(output.decode(),end='');self.assertEqual(proc.returncode,0,output.decode())

    def test_fixture_readiness_waits_for_complete_parent_pid_publication(self):
        code="import sys;sys.path.insert(0,"+repr(str(HERE))+");import test_hostd_l3_acp_real_provider as t;t._run_readiness_probe()"
        proc=subprocess.Popen([sys.executable,'-I','-c',code],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,start_new_session=True)
        original=_proc(proc.pid)
        try:output,_=proc.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            current=_proc(proc.pid);self.assertEqual(current['start_ticks'],original['start_ticks']);self.assertEqual(current['uid'],original['uid']);self.assertEqual(current['cgroup_sha256'],original['cgroup_sha256'])
            os.killpg(proc.pid,signal.SIGKILL);output,_=proc.communicate(timeout=4);self.fail('private readiness probe timeout: '+output.decode())
        print(output.decode(),end='');self.assertEqual(proc.returncode,0,output.decode())

    def test_genuine_fork_normal(self):self.run_case('normal')
    def test_genuine_fork_grandchild(self):self.run_case('tree')
    def test_genuine_fork_cancel(self):self.run_case('cancel')
    def test_genuine_fork_eof(self):self.run_case('eof')
    def test_genuine_fork_eof_with_actual_proc_entry_exit(self):self.run_case('eof-proc-exit')
    def test_genuine_fork_timeout(self):self.run_case('timeout')
    def test_genuine_fork_error(self):self.run_case('error')
    def test_genuine_fork_empty(self):self.run_case('empty')
    def test_genuine_fork_changed_session(self):self.run_case('escape')

def _run_identity_guards():
    from unittest import mock
    assert ctypes.CDLL(None).prctl(36,1,0,0,0)==0
    RealProviderTests.setUpClass();t=RealProviderTests();t.setUp()
    try:
        p=t.spawn();sid=t.start(p)
        # Load the rendered/pinned actual adapter API in this private process.
        import types
        m=types.ModuleType('guard_adapter');m.__file__=str(t.adapter)
        exec(compile(t.adapter.read_bytes(),str(t.adapter),'exec'),m.__dict__)
        for mismatch in ('start_ticks','pgid','sid','uid','cgroup_sha256','pid','parent_pid','reaped'):
            proc=subprocess.Popen(['/usr/bin/sleep','30'],start_new_session=True)
            foreign=subprocess.Popen(['/usr/bin/sleep','30'],start_new_session=True)
            owned=m.OwnedProcess(proc);original=dict(owned.original)
            try:
                if mismatch=='reaped':
                    fd=os.pidfd_open(proc.pid);signal.pidfd_send_signal(fd,signal.SIGKILL);os.close(fd);proc.wait(timeout=2)
                elif mismatch=='pid':owned.original['pid']=foreign.pid
                elif mismatch=='uid':owned.original['uid']=[0,0,0,0]
                elif mismatch=='cgroup_sha256':owned.original['cgroup_sha256']='0'*64
                else:owned.original[mismatch]+=1
                with mock.patch.object(m.os,'killpg',wraps=os.killpg) as group_signal,mock.patch.object(m.signal,'pidfd_send_signal',wraps=signal.pidfd_send_signal) as pid_signal:
                    try:owned.cleanup(True)
                    except m.AdapterError:pass
                    else:raise AssertionError('foreign/reused identity accepted: '+mismatch)
                    assert not group_signal.called and not pid_signal.called,'identity failure signaled a process'
                assert foreign.poll() is None,'foreign actual process killed'
                print(json.dumps({'phase':'identity-refused','mismatch':mismatch,'owned_original':original,'foreign':_proc(foreign.pid),'signals':0}),flush=True)
            finally:
                os.close(owned.pidfd)
                for actual in (proc,foreign):
                    if actual.returncode is None:
                        fd=os.pidfd_open(actual.pid);signal.pidfd_send_signal(fd,signal.SIGKILL);os.close(fd)
                    actual.wait(timeout=2)
    finally:t.doCleanups();RealProviderTests.tearDownClass()


def _run_pending_case():
    assert ctypes.CDLL(None).prctl(36,1,0,0,0)==0
    RealProviderTests.setUpClass();t=RealProviderTests();t.setUp();identities=[]
    try:
        def corrupt(body):
            needle="            self.active[sid]=(rid,self.job['deadline']);proc=None;image_fd=None"
            assert body.count(needle)==1
            return body.replace(needle,"            proc._hostd_owned.original['start_ticks']+=1\n"+needle)
        t.adapter_transform=corrupt;p=t.spawn('fork-sleep');sid=t.start(p);t.submit(p,sid)
        end=time.monotonic()+2
        while not (t.work/'capture.descendant-ready').exists() and time.monotonic()<end:time.sleep(.005)
        assert (t.work/'capture.descendant-ready').exists()
        identities=[_proc(int((t.work/name).read_text())) for name in ('capture.pid','capture.descendant')]
        t.send(p,'session/cancel',{'sessionId':sid},None);assert p.wait(timeout=4)==1;t.assert_no_success()
        receipt=json.loads((t.root/'receipt.json').read_text());w=receipt['backend_waits'][0]
        assert w['wait_observed'] is False and w['group_lifecycle']['absence_observed'] is False and w['group_lifecycle']['status']=='pending'
        assert all(_proc(i['pid'])['start_ticks']==i['start_ticks'] for i in identities)
        print(json.dumps({'phase':'unresolved-no-success-no-signal','original_actual':identities,'receipt':receipt}),flush=True)
    finally:
        t.reap()
        # Test deliberately denied ownership; private original diagnostic parent
        # retains exact real identities, then really waits its adopted children.
        for identity in identities:
            try:
                now=_proc(identity['pid']);assert now['start_ticks']==identity['start_ticks'] and now['uid']==identity['uid'] and now['cgroup_sha256']==identity['cgroup_sha256']
                fd=os.pidfd_open(identity['pid'])
                try:signal.pidfd_send_signal(fd,signal.SIGKILL)
                finally:os.close(fd)
            except FileNotFoundError:pass
        for identity in identities:
            try:
                now=_proc(identity['pid']);assert now['start_ticks']==identity['start_ticks'] and now['parent_pid']==os.getpid()
                waited,status=os.waitpid(identity['pid'],0);print(json.dumps({'phase':'pending-private-parent-reap','pid':waited,'exit':os.waitstatus_to_exitcode(status)}),flush=True)
            except FileNotFoundError:pass
        t.doCleanups();RealProviderTests.tearDownClass()

class IdentityGuardTests(unittest.TestCase):
    def test_unconfirmed_original_identity_is_pending_without_signal_or_success(self):
        code="import sys;sys.path.insert(0,"+repr(str(HERE))+");import test_hostd_l3_acp_real_provider as t;t._run_pending_case()"
        result=subprocess.run([sys.executable,'-I','-c',code],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=15)
        print(result.stdout.decode(),end='');self.assertEqual(result.returncode,0,result.stdout.decode())
    def test_actual_foreign_and_changed_or_reaped_identity_never_signaled(self):
        code="import sys;sys.path.insert(0,"+repr(str(HERE))+");import test_hostd_l3_acp_real_provider as t;t._run_identity_guards()"
        result=subprocess.run([sys.executable,'-I','-c',code],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=20)
        print(result.stdout.decode(),end='');self.assertEqual(result.returncode,0,result.stdout.decode())

if __name__=='__main__':unittest.main()
