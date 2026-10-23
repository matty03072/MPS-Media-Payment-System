from pathlib import Path
from flask import Blueprint,abort,render_template,request,send_from_directory
from .core import login_required,admin_required
from .guide_content import MEMBER,ADMIN
bp=Blueprint('guides',__name__)
ROOT=Path(__file__).resolve().parents[1]/'guide_images'

def listing(items,audience):
    query=request.args.get('q','').strip()[:100]
    visible=[g for g in items if not query or query.casefold() in (g['title']+' '+g['category']+' '+g['intro']).casefold()]
    return render_template('guides.html',guides=visible,audience=audience,query=query)

def detail(items,audience,slug):
    item=next((g for g in items if g['slug']==slug),None)
    if not item:abort(404)
    return render_template('guide.html',guide=item,audience=audience,guide_image_available=(ROOT/audience/(item['image']+'.png')).is_file(),related=[g for g in items if g['category']==item['category'] and g!=item])

def picture(items,audience,name):
    if name not in {g['image']+'.png' for g in items}:abort(404)
    return send_from_directory(ROOT/audience,name)

@bp.get('/guides')
@login_required
def members():return listing(MEMBER,'member')

@bp.get('/guides/<slug>')
@login_required
def member_guide(slug):return detail(MEMBER,'member',slug)

@bp.get('/guides/images/<name>')
@login_required
def member_image(name):return picture(MEMBER,'member',name)

@bp.get('/admin/guides')
@admin_required
def admins():return listing(ADMIN,'admin')

@bp.get('/admin/guides/<slug>')
@admin_required
def admin_guide(slug):return detail(ADMIN,'admin',slug)

@bp.get('/admin/guides/images/<name>')
@admin_required
def admin_image(name):return picture(ADMIN,'admin',name)
