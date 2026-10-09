#!/usr/bin/env python3
"""Fixed-tenant existing OAuth identity read; private JSON stdin, no cache/login."""
import argparse,json,sys,urllib.request,urllib.error,termios
URL='https://neopace.zendesk.com/api/v2/users/me'
LIMIT=2*1024*1024
class Rejected(Exception):pass
class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*args,**kwargs):raise Rejected('REDIRECT_REJECTED')
def decode(body,expected_email,expected_name):
 if not isinstance(body,dict) or not isinstance(body.get('user'),dict):raise Rejected('INVALID_RESPONSE')
 u=body['user']; email=u.get('email');name=u.get('name');role=u.get('role')
 if not isinstance(email,str) or not isinstance(name,str) or role not in ('end-user','agent','admin'):raise Rejected('INVALID_IDENTITY')
 em=email==expected_email;nm=name==expected_name
 if not em or not nm:raise Rejected('IDENTITY_MISMATCH')
 return {'expectedEmailMatched':em,'expectedNameMatched':nm,'role':role}
def read_identity(token,email,name,opener=None):
 if not isinstance(token,str) or not token or '\r' in token or '\n' in token:raise Rejected('INVALID_CREDENTIAL')
 request=urllib.request.Request(URL,headers={'Authorization':'Bearer '+token,'Accept':'application/json'},method='GET')
 try:
  with (opener or urllib.request.build_opener(NoRedirect())).open(request,timeout=20) as response:
   if response.status!=200:raise Rejected('HTTP_REJECTED')
   raw=response.read(LIMIT+1)
   if len(raw)>LIMIT:raise Rejected('BODY_LIMIT')
   body=json.loads(raw)
 except Rejected:raise
 except urllib.error.HTTPError as e:raise Rejected('HTTP_'+str(e.code)) from None
 except Exception:raise Rejected('REQUEST_OR_DECODE_FAILED') from None
 return decode(body,email,name)
def main():
 parser=argparse.ArgumentParser();parser.add_argument('--expected-email',required=True);parser.add_argument('--expected-name',required=True);args=parser.parse_args()
 old=None
 try:
  if sys.stdin.isatty():
   old=termios.tcgetattr(sys.stdin.fileno());new=termios.tcgetattr(sys.stdin.fileno());new[3]&=~(termios.ECHO|termios.ICANON);termios.tcsetattr(sys.stdin.fileno(),termios.TCSANOW,new)
  print('READY_PRIVATE_STDIN',flush=True);line=sys.stdin.readline(16385)
  if len(line)>16384:raise Rejected('INPUT_LIMIT')
  obj=json.loads(line)
  if not isinstance(obj,dict) or set(obj)!={'oauthAccessToken'}:raise Rejected('INVALID_PRIVATE_INPUT')
  result=read_identity(obj['oauthAccessToken'],args.expected_email,args.expected_name)
  print(json.dumps({'status':'SUCCESS','identity':result},ensure_ascii=False));return 0
 except Rejected as e:print(json.dumps({'status':str(e)}));return 1
 except Exception:print(json.dumps({'status':'INVALID_PRIVATE_INPUT'}));return 1
 finally:
  if old is not None:termios.tcsetattr(sys.stdin.fileno(),termios.TCSANOW,old)
if __name__=='__main__':sys.exit(main())
