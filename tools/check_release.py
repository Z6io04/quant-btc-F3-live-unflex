#!/usr/bin/env python3
"""Verify a clean source delivery, never connect to an account."""
import hashlib
import json
from pathlib import Path
import sys

root=Path(__file__).resolve().parent.parent
manifest=json.loads((root/'release-manifest.json').read_text())
errors=[]
for name,digest in manifest['files'].items():
    relative=Path(name)
    if relative.is_absolute() or '..' in relative.parts:
        errors.append('不合法的清单路径'); continue
    path=root/relative
    if not path.is_file() or path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest()!=digest:
        errors.append('文件缺失或已修改：'+name)
for p in root.rglob('*'):
    parts=p.relative_to(root).parts
    if p.is_symlink():errors.append('不应有符号链接：'+str(p.relative_to(root)))
    if parts[0] in ('private','var') or p.name=='.env' or p.name.startswith('credentials') or '.sqlite3' in p.name:
        errors.append('交付目录含私有数据：'+str(p.relative_to(root)))
if errors:
    print('\n'.join(errors)); sys.exit(1)
print('原始发布文件校验通过；不代表已经完成真实账户交易验收。')
