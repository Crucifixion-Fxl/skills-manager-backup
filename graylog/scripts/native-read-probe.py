"""Pure response projections, no HTTP/credentials/browser access. Official source 7.0.10/7.0.13 only."""
def session_projection(http_status,body,auth_kind,expected_username):
 result={'httpStatus':http_status,'callerBoundBy':'GET /api/system/sessions server getCurrentUser().getName()','identityVerified':False,'allowBusinessReads':False}
 if http_status!=200 or not isinstance(body,dict) or type(body.get('is_valid')) is not bool or auth_kind not in ['session','access-token']:
  return {**result,'status':'SCHEMA_OR_AUTH_UNVERIFIED'}
 username=body.get('username');valid=body['is_valid']
 if not isinstance(username,str) or not username or len(username)>200:return {**result,'status':'UNAUTHENTICATED_OR_UNBOUND','sessionIsValid':valid}
 authenticated=valid or auth_kind=='access-token'
 matched=authenticated and username==expected_username
 return {**result,'status':'SUBJECT_MATCHED' if matched else 'SUBJECT_MISMATCH_OR_SESSION_INVALID','username':username,'sessionIsValid':valid,'identityVerified':matched,'allowBusinessReads':matched,'profileLookupUsed':False}
def system_projection(http_status,body):
 if http_status!=200:return {'httpStatus':http_status,'status':'FORBIDDEN' if http_status==403 else 'READ_FAILED'}
 if not isinstance(body,dict) or not isinstance(body.get('version'),str) or not isinstance(body.get('node_id'),str):return {'httpStatus':http_status,'status':'SCHEMA_UNVERIFIED'}
 return {'httpStatus':http_status,'status':'READ_OK','version':body['version'],'nodeId':body['node_id']}
def streams_projection(http_status,body):
 fail={'httpStatus':http_status,'status':'FORBIDDEN' if http_status==403 else 'SCHEMA_UNVERIFIED'}
 if http_status!=200 or not isinstance(body,dict):return fail
 p=body.get('pagination');rows=body.get('elements')
 if not isinstance(p,dict) or not isinstance(rows,list) or len(rows)>5 or p.get('page')!=1 or p.get('per_page')!=5 or p.get('count')!=len(rows) or type(p.get('total')) is not int or p['total']<len(rows):return fail
 if any(not isinstance(r,dict) or not isinstance(r.get('id'),str) or not isinstance(r.get('title'),str) or type(r.get('disabled')) is not bool for r in rows):return fail
 return {'httpStatus':http_status,'status':'READ_OK','page':1,'perPage':5,'count':len(rows),'authorizedTotal':p['total'],'streams':[{'id':r['id'],'title':r['title'],'disabled':r['disabled']} for r in rows]}


READ_HEADERS = {'Accept': 'application/json',
                'X-Graylog-No-Session-Extension': 'true',
                'X-Requested-By': 'codex-readonly-probe'}


def run_native_read_probe(fetch_json, auth_kind, expected_username):
    """Consume an injected private GET transport; never construct/read credentials.

    fetch_json(path, selected_public_headers) must bind a fixed HTTPS origin and
    previously approved native session/token, refuse redirects, cap response bytes,
    and return (http_status, decoded_body). It must not log credentials/headers or
    raw error bodies. This module performs no HTTP, environment or browser access.
    """
    result = {'identity': {'status': 'NOT_ATTEMPTED'},
              'system': {'status': 'NOT_ATTEMPTED'},
              'streams': {'status': 'NOT_ATTEMPTED'},
              'attemptedPaths': [], 'allReadsSucceeded': False}
    if (auth_kind not in ['session', 'access-token']
            or not isinstance(expected_username, str)
            or not expected_username or len(expected_username) > 200):
        result['identity'] = {'status': 'INVALID_PROBE_ARGUMENTS'}
        return result

    def fetch(path, projector):
        result['attemptedPaths'].append(path)
        try:
            status, body = fetch_json(path, dict(READ_HEADERS))
            return projector(status, body)
        except Exception:
            # No exception text, raw HTTP response, stderr or headers are emitted.
            return {'status': 'TRANSPORT_FAILED'}

    result['identity'] = fetch(
        '/api/system/sessions',
        lambda status, body: session_projection(status, body, auth_kind, expected_username))
    if not result['identity'].get('allowBusinessReads'):
        return result
    result['system'] = fetch('/api/system', system_projection)
    result['streams'] = fetch('/api/streams/paginated?page=1&per_page=5', streams_projection)
    result['allReadsSucceeded'] = (result['system'].get('status') == 'READ_OK'
                                  and result['streams'].get('status') == 'READ_OK')
    return result
