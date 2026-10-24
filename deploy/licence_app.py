"""Owner-site entry point: keep the original Members app, add only the licence shop."""
from streaminghome import create_app as create_members_app
from licence_store.web import install


def create_app(config=None):
    app=create_members_app(config)
    install(app,'streaminghome')
    return app
