"""Disk-backed story graph and immutable reference assets, independent of transient jobs."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
import uuid

from PIL import Image, ImageDraw, ImageFont, ImageOps


class MemoryError(ValueError):
    def __init__(self, message, status=422):
        super().__init__(message)
        self.status = status


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{32}', value):
        raise MemoryError('Invalid memory identifier')
    return value


def uid():
    return uuid.uuid4().hex


def text(value, limit=4000):
    if not isinstance(value, str) or len(value) > limit:
        raise MemoryError(f'Text must be at most {limit} characters')
    return value.strip()


class StoryMemory:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / 'memory.sqlite3'
        self.lock = threading.RLock()
        with self.connect() as db:
            db.executescript('''
              CREATE TABLE IF NOT EXISTS stories(id TEXT PRIMARY KEY, data TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS characters(id TEXT PRIMARY KEY, story TEXT NOT NULL, data TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS locations(id TEXT PRIMARY KEY, story TEXT NOT NULL, data TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS scenes(id TEXT PRIMARY KEY, story TEXT NOT NULL, data TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS sequences(id TEXT PRIMARY KEY, story TEXT NOT NULL, data TEXT NOT NULL);
              CREATE INDEX IF NOT EXISTS scene_story ON scenes(story);
              CREATE INDEX IF NOT EXISTS character_story ON characters(story);
              CREATE INDEX IF NOT EXISTS location_story ON locations(story);
              CREATE INDEX IF NOT EXISTS sequence_story ON sequences(story);
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.db_path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def _get(self, db, table, key, story=None):
        identifier(key)
        row = db.execute(f'SELECT data FROM {table} WHERE id=?', (key,)).fetchone()
        if row is None:
            raise MemoryError(f'{table[:-1].capitalize()} not found', 404)
        data = json.loads(row['data'])
        if story and data.get('story_id') != story:
            raise MemoryError('This item belongs to another story', 404)
        return data

    def get(self, table, key, story=None):
        if table not in {'stories', 'characters', 'scenes', 'locations', 'sequences'}:
            raise MemoryError('Unknown memory record')
        with self.connect() as db:
            return self._get(db, table, key, story)

    def _put(self, db, table, data):
        if table == 'stories':
            db.execute('INSERT OR REPLACE INTO stories VALUES (?,?)', (data['id'], json.dumps(data)))
        else:
            db.execute(f'INSERT OR REPLACE INTO {table} VALUES (?,?,?)', (data['id'], data['story_id'], json.dumps(data)))

    def list_stories(self):
        with self.connect() as db:
            return sorted((json.loads(row[0]) for row in db.execute('SELECT data FROM stories')), key=lambda r:r['created_at'], reverse=True)

    def create_story(self, name):
        name = text(name, 150)
        if not name:
            raise MemoryError('Give your story a name')
        data = {'id':uid(), 'name':name, 'created_at':time.time(), 'current_scene_id':None}
        with self.lock, self.connect() as db:
            self._put(db, 'stories', data)
        return data

    def snapshot(self, story):
        with self.connect() as db:
            db.execute('BEGIN')
            result = self._get(db,'stories',story)
            for table in ('characters', 'locations', 'scenes', 'sequences'):
                result[table] = sorted((json.loads(r[0]) for r in db.execute(f'SELECT data FROM {table} WHERE story=?', (story,))), key=lambda r:r['created_at'])
        return result

    def path(self, story, relative):
        identifier(story)
        base = (self.root / story).resolve()
        path = (base / relative).resolve()
        if not path.is_relative_to(base) or path == base:
            raise MemoryError('Asset path must stay inside its story')
        return path

    def save_image(self, story, image, folder):
        relative = f'{folder}/{uid()}.png'
        dest = self.path(story, relative)
        dest.parent.mkdir(parents=True, exist_ok=True)
        temporary = dest.with_suffix('.pending')
        clean = ImageOps.exif_transpose(image).convert('RGBA' if 'A' in image.getbands() else 'RGB')
        clean.info.clear()
        try:
            clean.save(temporary, format='PNG')
            os.replace(temporary, dest)
        finally:
            temporary.unlink(missing_ok=True)
        return {'path':relative, 'sha256':hashlib.sha256(dest.read_bytes()).hexdigest()}

    def asset(self, story, asset):
        path = self.path(story, asset['path'])
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != asset['sha256']:
            raise MemoryError('A stored reference is missing or changed. Restore it or assign another reference.', 409)
        return path

    def character(self, story, metadata, key=None):
        self.get('stories', story)
        allowed = {'name','aliases','role','appearance','facial_features','hair','skin_tone','approximate_age',
                   'default_clothing','current_clothing','accessories','relationships','identity_locked'}
        if set(metadata) - allowed:
            raise MemoryError('Unknown character fields')
        with self.lock, self.connect() as db:
            data = self._get(db, 'characters', key, story) if key else {
                'id':uid(), 'story_id':story, 'created_at':time.time(), 'references':[], 'original_reference':None,
                'face_reference':None, 'identity_locked':True, 'aliases':[], 'relationships':[],
                'known_locations':[], 'previous_appearances':[], 'deleted':False}
            if data.get('deleted'):
                raise MemoryError('Character was deleted', 409)
            for field, value in metadata.items():
                if field in {'aliases','relationships'}:
                    if not isinstance(value, list) or len(value)>20:
                        raise MemoryError(f'{field} must be a list of up to 20 descriptions')
                    data[field] = [text(v, 150) for v in value if text(v,150)]
                elif field == 'identity_locked':
                    if not isinstance(value, bool): raise MemoryError('Identity lock must be true or false')
                    data[field] = value
                else:
                    data[field] = text(value, 1000 if field!='name' else 100)
            if not data.get('name'):
                raise MemoryError('Give this character a name')
            self._put(db, 'characters', data)
        return data

    def add_reference(self, story, key, image, box=None, source_scene_id=None):
        # Never replace the original: another upload always creates another immutable asset.
        import identity_assist
        self.get('characters', key, story)
        face = identity_assist.crop_head(image, box) if box is not None else None
        original = self.save_image(story, image, f'characters/{key}/references')
        head = self.save_image(story, face, f'characters/{key}/faces') if face is not None else None
        ref = {'id':uid(), **original, 'face':head, 'head_box':box, 'source_scene_id':source_scene_id, 'created_at':time.time()}
        with self.lock, self.connect() as db:
            character = self._get(db, 'characters', key, story)
            if character.get('deleted'): raise MemoryError('Character was deleted', 409)
            character['references'].append(ref)
            if character['original_reference'] is None:
                character['original_reference'] = original
            if head and character['face_reference'] is None:
                character['face_reference'] = head
            self._put(db, 'characters', character)
        return character

    def reference_board(self, story, references, names):
        """One small CPU contact sheet prevents independent giant head portraits dominating a scene."""
        import io
        board=Image.new('RGB',(512,304),'white'); draw=ImageDraw.Draw(board)
        try: font=ImageFont.truetype('arial.ttf',18)
        except OSError: font=ImageFont.load_default()
        for index,(ref,name) in enumerate(zip(references,names)):
            with Image.open(self.asset(story,ref['asset'])) as image:
                thumbnail=ImageOps.contain(image.convert('RGB'),(240,260))
            left=index*256+(256-thumbnail.width)//2
            board.paste(thumbnail,(left,38+(260-thumbnail.height)//2))
            draw.text((index*256+8,8),f'{index+1}: {name}'[:28],fill='black',font=font)
        buf=io.BytesIO();board.save(buf,'PNG');data=buf.getvalue();digest=hashlib.sha256(data).hexdigest()
        relative=f'memory/reference_boards/{digest}.png';dest=self.path(story,relative)
        with self.lock:
            if dest.exists():
                if hashlib.sha256(dest.read_bytes()).hexdigest()!=digest: raise MemoryError('Reference board changed unexpectedly',409)
            else:
                dest.parent.mkdir(parents=True,exist_ok=True)
                temporary=dest.with_suffix('.pending');temporary.write_bytes(data);os.replace(temporary,dest)
        return {'path':relative,'sha256':digest}

    def location(self, story, name, description='', image=None):
        self.get('stories', story)
        name = text(name,150)
        if not name: raise MemoryError('Give the location a name')
        data = {'id':uid(), 'story_id':story, 'created_at':time.time(), 'name':name,
                'description':text(description), 'reference':None}
        if image is not None: data['reference'] = self.save_image(story,image,f'locations/{data["id"]}')
        with self.lock, self.connect() as db: self._put(db,'locations',data)
        return data

    def delete_character(self, story, key):
        with self.lock, self.connect() as db:
            data = self._get(db,'characters',key,story)
            data['deleted'] = True
            self._put(db,'characters',data)
        return data

    def reserve(self, story, plan, settings):
        with self.lock, self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self._get(db,'stories',story)
            self.guard_sequence(db,story,plan.get('sequence_id'))
            if plan.get('parent_scene_id'):
                parent=self._get(db,'scenes',plan['parent_scene_id'],story)
                if parent['status']!='succeeded' or parent.get('deleted'):
                    raise MemoryError('The selected parent is no longer available',409)
            rows = db.execute('SELECT data FROM scenes WHERE story=?',(story,)).fetchall()
            if any(json.loads(r[0])['status'] in {'queued','running'} for r in rows):
                raise MemoryError('This story already has a scene generating. Wait or cancel it first.',409)
            scene = {'id':uid(), 'story_id':story, 'created_at':time.time(), 'status':'queued',
                     'job_id':None, 'image':None, 'error':None, 'deleted':False, 'settings':settings, **plan}
            self._put(db,'scenes',scene)
        return scene

    def update_scene(self, story, key, **changes):
        with self.lock, self.connect() as db:
            data = self._get(db,'scenes',key,story)
            data.update(changes)
            self._put(db,'scenes',data)
        return data

    def finish(self, story, key, image, metadata, evaluation):
        # Write a unique image first, then atomically commit graph state and head pointer.
        current = self.get('scenes',key,story)
        if current['status']=='succeeded': return current
        asset = self.save_image(story,image,f'scenes/{key}')
        with self.lock, self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            scene = self._get(db,'scenes',key,story)
            if scene['status']=='succeeded': return scene
            if scene['status'] not in {'queued','running'}: raise MemoryError('Scene is no longer active',409)
            scene.update(status='succeeded', image=asset, generation=metadata, evaluation=evaluation, finished_at=time.time())
            story_data = self._get(db,'stories',story)
            if scene.get('new_location'):
                location = dict(scene['new_location'],reference=asset)
                self._put(db,'locations',location)
            for character_id in scene['character_ids']:
                character = self._get(db,'characters',character_id,story)
                if key not in character['previous_appearances']: character['previous_appearances'].append(key)
                if scene.get('location_id') and scene['location_id'] not in character['known_locations']:
                    character['known_locations'].append(scene['location_id'])
                if character_id in scene['clothing']:
                    character['current_clothing'] = scene['clothing'][character_id]
                self._put(db,'characters',character)
            story_data['current_scene_id'] = key
            self._put(db,'scenes',scene)
            self._put(db,'stories',story_data)
        return scene

    def activate(self, story, key):
        with self.lock, self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self.guard_sequence(db,story)
            if any(json.loads(row[0])['status'] in {'queued','running'} for row in db.execute('SELECT data FROM scenes WHERE story=?',(story,))):
                raise MemoryError('Wait for this story generation to finish before changing its continuation point.',409)
            scene = self._get(db,'scenes',key,story)
            if scene['status']!='succeeded' or scene['deleted']: raise MemoryError('Choose a completed scene',409)
            data = self._get(db,'stories',story)
            data['current_scene_id'] = key
            self._put(db,'stories',data)
        return data

    def delete_scene(self, story, key):
        with self.lock, self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self.guard_sequence(db,story)
            scene = self._get(db,'scenes',key,story)
            rows = [json.loads(r[0]) for r in db.execute('SELECT data FROM scenes WHERE story=?',(story,))]
            if scene['status'] in {'queued','running'}: raise MemoryError('Cancel this scene before deleting it',409)
            if any(r.get('parent_scene_id')==key and not r.get('deleted') for r in rows):
                raise MemoryError('This scene has branches. Delete their leaf scenes first.',409)
            scene['deleted'] = True
            data = self._get(db,'stories',story)
            if data['current_scene_id']==key: data['current_scene_id']=scene.get('parent_scene_id')
            self._put(db,'scenes',scene); self._put(db,'stories',data)
        return scene

    def recover(self):
        with self.lock, self.connect() as db:
            for row in db.execute('SELECT data FROM scenes').fetchall():
                scene = json.loads(row[0])
                if scene['status'] in {'queued','running'}:
                    scene.update(status='failed', error='Studio restarted during generation. References and the previous scene are intact; retry this prompt.', finished_at=time.time())
                    self._put(db,'scenes',scene)
            for row in db.execute('SELECT data FROM sequences').fetchall():
                sequence=json.loads(row[0])
                if sequence['status'] in {'queued','running','cancelling'}:
                    sequence.update(status='paused',error='Studio restarted. Saved scenes are intact. Resume explicitly to continue unfinished scenes.',updated_at=time.time())
                    self._put(db,'sequences',sequence)

    def guard_sequence(self, db, story, allowed=None):
        for row in db.execute('SELECT data FROM sequences WHERE story=?',(story,)):
            sequence=json.loads(row[0])
            if sequence['status'] in {'queued','running','cancelling'} and sequence['id']!=allowed:
                raise MemoryError('This story has a sequence running. Stop it or wait before starting another generation or changing its history.',409)
