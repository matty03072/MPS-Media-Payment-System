import os
from waitress import serve
from mps import create_app
app=create_app()
proxy={'trusted_proxy':'127.0.0.1','trusted_proxy_count':1,'trusted_proxy_headers':{'x-forwarded-for'}} if app.config.get('TRUST_PROXY') else {}
serve(app,host=os.environ.get('BIND_ADDRESS','127.0.0.1'),port=int(os.environ.get('PORT','8099')),threads=8,channel_timeout=600,**proxy)
