"""Release allowlist: excludes workspace state, customer data and test evidence."""
import hashlib, json, tarfile, zipfile
from pathlib import Path
root=Path(__file__).resolve().parents[1]
out=root.parent/'members-installer-dist';out.mkdir(exist_ok=True)
allowed_dirs={'mps','templates','static','installer'}
allowed_files={'app.py','serve.py','requirements.txt','README.md','TEST-REPORT.md','install-linux.sh','install-windows.ps1','uninstall-linux.sh','uninstall-windows.ps1'}
files=[]
for path in root.rglob('*'):
 rel=path.relative_to(root)
 if not path.is_file() or '__pycache__' in rel.parts or path.suffix=='.pyc':continue
 if rel.parts[0] not in allowed_dirs and str(rel) not in allowed_files:continue
 if path.suffix in ('.db','.sqlite3','.key','.pem','.log') or path.name in ('config.json','setup-code.txt'):raise RuntimeError('Private artifact in release allowlist')
 files.append(path)
manifest={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(files)}
manifest_bytes=(json.dumps(manifest,indent=2)+'\n').encode()
with zipfile.ZipFile(out/'members-portal-0.1.0-windows.zip','w',zipfile.ZIP_DEFLATED) as z:
 for p in files:z.write(p,p.relative_to(root))
 z.writestr('MANIFEST.sha256.json',manifest_bytes)
import io
with tarfile.open(out/'members-portal-0.1.0-linux.tar.gz','w:gz') as t:
 for p in files:t.add(p,arcname=str(p.relative_to(root)),recursive=False)
 info=tarfile.TarInfo('MANIFEST.sha256.json');info.size=len(manifest_bytes);info.mode=0o644;t.addfile(info,io.BytesIO(manifest_bytes))
checks=[]
for p in sorted(out.iterdir()):
 if p.suffix in ('.zip','.gz'):checks.append(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.name)
(out/'SHA256SUMS.txt').write_text('\n'.join(checks)+'\n')
print('PACKAGED_FILES='+str(len(files)))
print('\n'.join(checks))
