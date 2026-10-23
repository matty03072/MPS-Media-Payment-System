from urllib.parse import quote,urlsplit
import requests
from .emby import APIError,request
from .core import setting

def stripe_key_mode(key):
    for prefix, mode in (('sk_live_', 'live'), ('rk_live_', 'live'), ('sk_test_', 'test'), ('rk_test_', 'test')):
        if key.startswith(prefix) and len(key) > len(prefix):
            return mode
    return None

class StripeAPI:
    version='2025-02-24.acacia'
    def call(self,method,path,data=None,key=None):
        token=setting('stripe_secret',secret=True)
        if not token:raise APIError('Stripe is not configured.')
        headers={'Stripe-Version':self.version}
        if key:headers['Idempotency-Key']=key
        return request(method,'https://api.stripe.com/v1'+path,auth=(token,''),headers=headers,data=data)
    def get(self,path):return self.call('GET',path)
    def post(self,path,data,key):return self.call('POST',path,data,key)
    def delete(self,path,data,key):return self.call('DELETE',path,data,key)

class OmbiAPI:
    def __init__(self,token=''):
        self.base=setting('ombi_url').rstrip('/')
        parsed=urlsplit(self.base)
        if not self.base or parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.query:
            raise APIError('Ombi has not been configured with a valid server URL.')
        self.token=token
    def call(self,method,path,body=None,admin=False):
        # Member calls never use the global administrator API key.
        headers={'ApiKey':setting('ombi_key',secret=True)} if admin else {'Authorization':'Bearer '+self.token}
        return request(method,self.base+'/api/v1'+path,headers=headers,json=body)
    def authenticate(self,username,password):
        result=request('POST',self.base+'/api/v1/Token',json={'username':username,'password':password,'rememberMe':True})
        token=result.get('access_token')
        if not token:raise APIError('Ombi sign-in was not confirmed.')
        self.token=token
        identity=self.call('GET','/Identity')
        if not identity.get('id') or not identity.get('userName'):raise APIError('Ombi did not return a usable member identity.')
        return token,identity
    def users(self):
        result=self.call('GET','/Identity/Users',admin=True)
        if not isinstance(result,list):raise APIError('Ombi did not return a usable identity list.')
        return result
    def create(self,username,email,password):
        result=self.call('POST','/Identity',{'userName':username,'emailAddress':email,'password':password,'streamingCountry':'GB',
          'claims':[{'value':'RequestMovie','enabled':True},{'value':'RequestTv','enabled':True}]},admin=True)
        if not result.get('successful'):raise APIError('Ombi account creation needs administrator review. The email or username may already exist.')
    def password(self,user_id,username,email,password):
        matches=[u for u in self.users() if str(u.get('id'))==user_id]
        if len(matches)!=1:raise APIError('Linked Ombi identity is unavailable.')
        user=matches[0]
        if ((user.get('userName') or '').casefold()!=username.casefold() or
            (user.get('emailAddress') or '').casefold()!=email.casefold() or
            any(c.get('enabled') and c.get('value','').lower() in ('admin','poweruser') for c in (user.get('claims') or []))):
            raise APIError('Linked Ombi identity needs administrator review.')
        # PUT replaces preferences and claims too: preserve the full provider record.
        body=dict(user);body['password']=password
        result=self.call('PUT','/Identity',body,admin=True)
        if not result.get('successful'):raise APIError('Ombi did not confirm the password update.')
        token,identity=self.authenticate(username,password)
        if str(identity.get('id'))!=user_id:raise APIError('Ombi password verification returned a different identity.')
        return token
    def delete(self,user_id):
        result=self.call('DELETE','/Identity/'+quote(user_id,safe=''),admin=True)
        if not result.get('successful'):raise APIError('Ombi did not confirm account deletion.')
    def request_history(self,user_id):
        # Ombi can expose other users' requests depending on its visibility setting.
        # Always filter exact requester IDs, including each TV child request.
        if not self.token or not user_id:raise APIError('Your request account is not linked.')
        entries=[]
        def add(row,title,kind,detail=''):
            if str(row.get('requestedUserId') or '')!=str(user_id):return
            state=('Available' if row.get('available') is True else 'Declined' if row.get('denied') is True else
                   'Approved — queued / processing' if row.get('approved') is True else 'Pending approval')
            entries.append({'key':('movie' if kind=='Movie' else 'tv')+':'+str(row.get('id','')), 'title':title or 'Untitled request','kind':kind,'detail':detail,'status':state,'date':str(row.get('requestedDate') or '')[:10]})
        for kind in ('movie','tv'):
            rows=self.call('GET','/Request/'+kind)
            if not isinstance(rows,list):raise APIError('Request history is temporarily unavailable.')
            for row in rows:
                if not isinstance(row,dict):raise APIError('Request history is temporarily unavailable.')
                if kind=='movie':add(row,row.get('title'),'Movie')
                else:
                    for child in row.get('childRequests') or []:
                        seasons=child.get('seasonRequests') or []
                        numbers=[str(s['seasonNumber']) for s in seasons if s.get('seasonNumber') is not None]
                        add(child,row.get('title'),'TV','Seasons '+', '.join(numbers) if numbers else '')
        return sorted(entries,key=lambda r:r['date'],reverse=True)
    def search(self,kind,query):
        if kind not in ('movie','tv'):raise APIError('Unsupported media type.')
        result=self.call('GET','/Search/'+kind+'/'+quote(query,safe=''))
        if not isinstance(result,list):raise APIError('Ombi returned an unexpected search response.')
        return result[:30]
    def make_request(self,kind,media_id):
        body={'theMovieDbId':media_id} if kind=='movie' else {'tvDbId':media_id,'requestAll':True,'seasons':[]}
        result=self.call('POST','/Request/'+kind,body)
        if not result.get('result'):raise APIError('Ombi did not accept the request. It may already exist or exceed your request limit.')
        return result
