"""Database-backed site identity shared by web requests and mail workers."""
import base64
import io
import re
from flask import Blueprint, abort, send_file, request
from PIL import Image, UnidentifiedImageError
from .core import setting, put

PROJECT_URL = 'https://github.com/matty03072/MPS-Media-Payment-System'
DEFAULT_NAME = 'MPS — Media Payment System'
bp = Blueprint('branding', __name__)

def identity():
    name = setting('site_name', DEFAULT_NAME) or DEFAULT_NAME
    name = ''.join(c for c in name if ord(c) >= 32 and ord(c) != 127)[:80] or DEFAULT_NAME
    return {'site_name': name,
            'brand_logo_url': '/branding/logo' if setting('brand_logo') else '/static/mps-logo.svg?v=1',
            'custom_logo': bool(setting('brand_logo')), 'project_url': PROJECT_URL}

def logo_bytes():
    value = setting('brand_logo')
    if value:
        return base64.b64decode(value)
    from flask import current_app
    from pathlib import Path
    return (Path(current_app.static_folder) / 'mps-icon.png').read_bytes()

@bp.get('/branding/logo')
def logo():
    return send_file(io.BytesIO(logo_bytes()), mimetype='image/png', download_name='site-logo.png')

def save_branding():
    name = request.form.get('site_name', '').strip()
    if not name or len(name) > 80 or any(ord(c) < 32 or ord(c) == 127 for c in name):
        abort(400, 'Use a site name of 1–80 characters without control characters.')
    upload = request.files.get('logo')
    encoded = None
    if upload and upload.filename:
        raw = upload.read(512001)
        if len(raw) > 512000:
            abort(400, 'Logo must be 500 KB or smaller.')
        try:
            with Image.open(io.BytesIO(raw)) as image:
                if image.format not in ('PNG', 'JPEG', 'WEBP') or image.width > 4096 or image.height > 4096:
                    raise ValueError()
                image.load()
                image.thumbnail((800, 400))
                out = io.BytesIO()
                image.convert('RGBA').save(out, format='PNG')
                encoded = base64.b64encode(out.getvalue()).decode('ascii')
        except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
            abort(400, 'Upload a valid PNG, JPEG or WebP logo, at most 4096 × 4096 pixels.')
    put('site_name', name)
    if encoded is not None:
        put('brand_logo', encoded)
    elif request.form.get('remove_logo') == 'yes':
        put('brand_logo', '')

def brand_text(value):
    """Brand authored copy without rewriting action links or token values."""
    if value is None:
        return None
    name = identity()['site_name']
    parts = re.split(r'(https?://\S+)', value)
    return ''.join(part if i % 2 else re.sub(r'MPS(?: — Media Payment System)?', lambda match: name, part)
                   for i, part in enumerate(parts))
