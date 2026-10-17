"""Recoverable snapshot of Identity Assist code; no models, photos or Git resets."""
import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
SCOPE = ['studio_service.py', 'qwen_engine.py', 'web/index.html', 'web/studio.js', 'web/layout.css',
         'identity_assist.py', 'test_identity_assist.py', 'test_qwen_core_ui.py', 'IDENTITY_ASSIST.md',
         'tools/test_identity_live.py', 'requirements_identity.txt', 'dolphin_prompting.py', 'pose_assist.py',
         'visual_memory.py', 'continuity_engine.py', 'story_api.py', 'test_visual_memory.py',
         'web/story.html', 'web/story.js', 'web/story.css', 'config/visual_memory.json', 'VISUAL_MEMORY.md', 'README.md',
         'story_sequences.py', 'test_story_sequences.py', 'desktop_app.py', 'test_desktop_app.py']

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['create', 'extend', 'seal', 'restore'])
    parser.add_argument('snapshot', nargs='?')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    base = ROOT / 'outputs' / '.rollback'
    if args.action == 'create':
        target = base / (datetime.now().strftime('%Y%m%d_%H%M%S_%f') + '_identity')
        target.mkdir(parents=True)
        data = {'files': []}
        for name in SCOPE:
            src = ROOT / name
            if src.exists():
                copy = target / 'before' / name
                copy.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, copy)
            data['files'].append({'path': name, 'before': sha(src)})
    else:
        target = Path(args.snapshot).resolve()
        if not target.is_relative_to(base.resolve()):
            raise ValueError('Snapshot must be inside outputs/.rollback')
        data = json.loads((target / 'snapshot.json').read_text())
        for row in data['files']:
            if row['path'] not in SCOPE or not (ROOT / row['path']).resolve().is_relative_to(ROOT):
                raise ValueError('Unexpected snapshot target')
        if args.action == 'extend':
            names = {row['path'] for row in data['files']}
            for name in SCOPE:
                if name in names:
                    continue
                src = ROOT / name
                if src.exists():
                    copy = target / 'before' / name
                    copy.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, copy)
                data['files'].append({'path': name, 'before': sha(src)})
        elif args.action == 'seal':
            for row in data['files']:
                row['after'] = sha(ROOT / row['path'])
        else:
            for row in data['files']:
                if 'after' not in row or sha(ROOT / row['path']) != row['after']:
                    raise ValueError('Later changes found; preserve and review manually: ' + row['path'])
                if row['before'] and sha(target / 'before' / row['path']) != row['before']:
                    raise ValueError('Snapshot checksum mismatch')
            print('Restore scope: ' + ', '.join(row['path'] for row in data['files']))
            if not args.apply:
                print('Preview only. Use --apply only when rollback is requested.')
                return
            rescue = target / ('recovered_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
            for row in data['files']:
                dest = ROOT / row['path']
                if dest.exists():
                    copy = rescue / row['path']
                    copy.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(dest), str(copy))
                if row['before']:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(target / 'before' / row['path'], dest)
            return
    (target / 'snapshot.json').write_text(json.dumps(data, indent=2), encoding='utf-8')
    print(target)

if __name__ == '__main__':
    main()
