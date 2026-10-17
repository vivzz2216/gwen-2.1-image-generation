"""Deterministic scene retrieval and native Qwen reference/prompt planning."""
import re
import time

from visual_memory import MemoryError, uid


def contains(prompt, word):
    return bool(word and re.search(r'(?<!\w)'+re.escape(word)+r'(?!\w)',prompt,re.I))


PLACES = r'(?:hotel room|living room|dining room|car interior|car|bus|train|office|gym|shop|hospital|school|restaurant|apartment|beach|park|forest|street|bedroom|kitchen|caf[eé]|room|garden|balcony)'
GARMENT = r'(?:t-shirt|shirt|blouse|top|sweater|cardigan|hoodie|jacket|coat|suit|dress|jeans|trousers|pants|shorts|skirt|shoes|boots|sneakers|hat|scarf)'
COLORS = r'(?:(?:(?:dark|light|pale|bright)\s+)?(?:red|black|blue|white|green|yellow|pink|purple|gr[ae]y|brown|beige|cream|navy|orange|silver|gold)|dark|light)'


def explicit_location(prompt):
    match = re.search(r'\b(?:inside|in|into|to|at|outside)\s+(?:of\s+)?(?:an?\s+|the\s+)?((?:(?:modern|parked|small|large)\s+)?'+PLACES+r')\b', prompt, re.I)
    return match.group(1).lower() if match else ''


def garment_parts(value):
    """Only record clothing actually described; never invent an outfit."""
    pattern = r'\b((?:'+COLORS+r'\s+)?(?:(?:denim|leather|cotton|wool|knit|striped|long-sleeved|short-sleeved)\s+)?'+GARMENT+r')\b'
    result = {}
    for match in re.finditer(pattern, value, re.I):
        phrase = match.group(1).lower()
        kind = re.search(GARMENT+r'$', phrase, re.I).group(0).lower()
        kind = {'t-shirt':'shirt', 'pants':'trousers'}.get(kind, kind)
        result[kind] = phrase
    return result


def character_clause(prompt, character, characters):
    """Attribute descriptions to the named subject, stopping at the next person."""
    names = [character['name']]+[a for a in character.get('aliases',[]) if a.lower() not in {'he','his','him','she','her'}]
    starts = [m for name in names for m in re.finditer(r'(?<!\w)'+re.escape(name)+r'(?!\w)', prompt, re.I)]
    if not starts:
        return ''
    start = min(starts, key=lambda m:m.start()).end()
    ends = [m.start() for other in characters if other['id']!=character['id']
            for name in [other['name']]+[a for a in other.get('aliases',[]) if a.lower() not in {'he','his','him','she','her'}]
            for m in re.finditer(r'(?<!\w)'+re.escape(name)+r'(?!\w)', prompt, re.I) if m.start()>=start]
    return prompt[start:min(ends) if ends else len(prompt)]


def discover_characters(memory, story, prompt):
    existing = [c for c in memory.snapshot(story)['characters'] if not c.get('deleted')]
    # Named cast must be recorded before generic roles (including "boyfriend named Roy").
    named = []
    for pattern in [r'\b(?P<role>woman|girl|man|guy|boyfriend|girlfriend)\s+(?:named|called)\s+(?P<name>[A-Za-z][\w-]*)',
                    r'\b(?P<name>[A-Z][\w-]*),\s*(?:an?\s+)?(?:adult\s+)?(?P<role>woman|girl|man|guy)\b']:
        named.extend(re.finditer(pattern, prompt))
    for match in named:
        name = match.group('name')
        role = 'woman' if match.group('role') in {'woman','girl','girlfriend'} else 'man'
        if any(contains(name,c['name']) or name.lower() in [a.lower() for a in c.get('aliases',[])] for c in existing):
            continue
        generic = next((c for c in existing if c.get('role')==role and re.fullmatch(r'(?:Woman|Man) \d+',c['name'])), None)
        metadata = {'name':name,'role':role,'aliases':['woman','girl'] if role=='woman' else ['man','guy']}
        c = memory.character(story,metadata,generic['id'] if generic else None)
        existing = [old for old in existing if old['id']!=c['id']]+[c]
    if named:
        return [c['id'] for c in existing if any(c['name']==m.group('name') for m in named)]
    fresh = bool(re.search(r'\b(?:different|new|another)\s+(?:woman|girl|man|guy|person|character)\b',prompt,re.I))
    if existing and not fresh: return []
    created = []
    for role, words in [('woman',['woman','girl']),('man',['man','guy'])]:
        match = re.search(r'\b(?:'+ '|'.join(words) +r')\b',prompt,re.I)
        if not match: continue
        tail = re.split(r'\b(?:woman|girl|man|guy)\b',prompt[match.end():],flags=re.I)[0][:100]
        garment = re.search(r'\b((?:red|black|blue|white|green|yellow|pink|purple|gray|grey|brown)\s+(?:dress|shirt|jacket|suit|top|coat))\b',tail,re.I)
        clothing = garment.group(1) if garment else ''
        created.append(memory.character(story,{'name':f'{role.capitalize()} {1+sum(c.get("role")==role for c in existing)}',
            'role':role, 'aliases':words, 'appearance':'', 'default_clothing':clothing,'current_clothing':clothing}))
    return [c['id'] for c in created]


def interpretation(prompt):
    lower = prompt.lower()
    environment = bool(explicit_location(prompt) or re.search(r'\b(outside|outdoors|beach|rain|forest|street|park|different location|new location|new scene)\b',lower)
                       or re.search(r'\b(?:move|take|bring|go|goes|travel)\b.{0,50}\b(?:to|into)\b',lower))
    new_people = bool(re.search(r'\b(?:different|new|another)\s+(?:woman|girl|man|guy|person|character)\b',lower))
    clothes = bool(re.search(r'\b(chang\w*|replac\w*|wear\w*|remov\w*|tak\w*\s+off|pull\w*\s+off|put\w*\s+on|switch\w*)\b.{0,100}\b('+GARMENT+r'|clothes|clothing|outfit|'+COLORS+r')\b',lower))
    hair = bool(re.search(r'\b(hair|hairstyle|haircut|bald)\b',lower))
    lighting = bool(re.search(r'\b(lighting|sunset|sunrise|night|daylight|dawn|dusk)\b',lower))
    style = bool(re.search(r'\b(watercolou?r|anime|oil painting|cartoon|illustration|sketch|photorealistic|visual style)\b',lower))
    camera = bool(re.search(r'\b(camera|close.up|wide shot|side view|overhead|bird.s.eye|rear view|top view|low angle|high angle|perspective)\b',lower))
    objects = bool(re.search(r'\b(add|adds|adding|place|places|placing|put|puts|putting|insert|introduce)\b',lower)
                   or any(contains(prompt,word) for word in ['cup','glass','phone','smartphone','tablet','bicycle','book','necklace','ball','table','chair','box','bottle','mug']))
    interaction=re.search(r'\b(woman|girl|man|guy)\b.{0,24}?\b(slaps?|push(?:es)?|kicks?|punch(?:es)?|hugs?)\s+(?:the\s+)?(woman|girl|man|guy)\b',lower)
    target=re.search(r'\b(chest|neck|stomach|abdomen|cheek|face|shoulder|back|knee)\b',lower)
    movement=bool(re.search(r'\b(run\w*|walk\w*|slap\w*|kick\w*|push\w*|punch\w*|hug\w*|step\w*|turn\w*|rais\w*|wav\w*|sit\w*|stand\w*|sleep\w*|jump\w*|rid\w*|cycl\w*|lie|lies|lying|mov\w*|hold\w*|held|reach\w*|check\w*|drink\w*|read\w*|argu\w*|kiss\w*|pull\w*|tak\w*)\b',lower))
    expression=bool(interaction or re.search(r'\b(smil\w*|laugh\w*|angry|shout\w*|cry\w*|cries|tear\w*|sob\w*|shocked|surprised|expression|frown\w*|argu\w*)\b',lower))
    return {'action':prompt,'actor':interaction.group(1) if interaction else None,
        'recipient':interaction.group(3) if interaction else None,'action_type':interaction.group(2) if interaction else None,
        'target_region':target.group(1) if target else 'cheek' if interaction and interaction.group(2).startswith('slap') else None,
        'environment_change':environment,'new_characters':new_people,
        'change':{'pose':movement,'facial_expression':expression,'body_position':movement,'clothing':clothes,'hair':hair,
                  'lighting':lighting,'visual_style':style,'camera':camera,'objects':objects}}


def build_plan(memory, story_id, prompt, parent_id=None, character_ids=None, location_id=None,
               preserve=None, auto_references=True, reference_ids=None, evaluate=True):
    snapshot = memory.snapshot(story_id)
    parent_id = snapshot['current_scene_id'] if parent_id is None else parent_id or None
    parent = memory.get('scenes',parent_id,story_id) if parent_id else None
    if parent and (parent['status']!='succeeded' or parent.get('deleted')):
        raise MemoryError('Continue from a completed scene',409)
    info = interpretation(prompt)
    place_name = explicit_location(prompt)
    previous_location = memory.get('locations',parent['location_id'],story_id) if parent and parent.get('location_id') else None
    if place_name and parent:
        old_place = (previous_location or {}).get('name','').lower()
        info['environment_change'] = place_name != old_place
    characters = [c for c in snapshot['characters'] if not c.get('deleted')]
    by_id = {c['id']:c for c in characters}
    mentioned = []
    aliases = {}
    for c in characters:
        for word in [c['name']]+c.get('aliases',[]):
            if contains(prompt,word): aliases.setdefault(word.lower(),[]).append(c['id'])
    active = [key for key in parent.get('character_ids',[]) if key in by_id] if parent else []
    for alias, matches in aliases.items():
        preferred = [key for key in matches if key in active]
        if info['new_characters']:
            preferred = matches[-1:]
        candidates = preferred or matches
        if len(candidates)>1 and character_ids is None:
            raise MemoryError(f'“{alias}” matches several characters. Select the intended characters explicitly.',409)
        mentioned.extend(candidates)
    if character_ids is not None:
        if len(character_ids)!=len(set(character_ids)) or any(key not in by_id for key in character_ids):
            raise MemoryError('Choose existing, distinct characters from this story')
        selected = character_ids
    elif info['new_characters']:
        selected = list(dict.fromkeys(mentioned))
    elif (info['environment_change'] or re.search(r'\b(?:alone|by herself|by himself)\b',prompt,re.I)) and mentioned:
        selected = list(dict.fromkeys(mentioned))
    else:
        selected = list(dict.fromkeys(active + mentioned)) or [c['id'] for c in characters]
    # Gendered pronouns resolve only within the active cast; never merge identities by description.
    for pattern, roles in [(r'\b(she|her)\b',{'woman','girl'}),(r'\b(he|him|his)\b',{'man','guy'})]:
        if re.search(pattern,prompt,re.I) and not mentioned and character_ids is None:
            candidates = [key for key in selected if by_id[key].get('role','').lower() in roles]
            if len(candidates)>1: raise MemoryError('This pronoun is ambiguous. Select the intended character.',409)
            mentioned.extend(candidates)
    if len(selected)>12: raise MemoryError('Use at most 12 characters in one scene; only two independent identity references fit this GPU profile')
    preservation = {'faces':True,'clothing':True,'environment':True,'style':True}
    preservation.update(preserve or {})
    previous_state = parent.get('clothing',{}) if parent else {}
    clothing = {key:previous_state.get(key,by_id[key].get('current_clothing') or by_id[key].get('default_clothing','')) for key in selected}
    wardrobe = {key:dict((parent or {}).get('wardrobe',{}).get(key) or garment_parts(clothing[key])) for key in selected}
    # Opening descriptions establish each person's outfit independently.
    if not parent:
        for key in selected:
            described = garment_parts(character_clause(prompt,by_id[key],characters))
            if described:
                wardrobe[key].update(described)
                clothing[key] = ', '.join(wardrobe[key].values())
    change_targets = mentioned or selected
    if parent and info['change']['clothing']:
        for pronoun,role in [('her','woman'),('his','man')]:
            if re.search(r'\b'+pronoun+r'\s+(?:'+GARMENT+r'|clothes|outfit)\b',prompt,re.I):
                owners=[key for key in selected if by_id[key].get('role')==role]
                if len(owners)==1: change_targets=owners
        owners=[]
        for key in selected:
            if any(re.search(r'\b'+re.escape(word)+r"(?:['’]s)?\s+(?:"+GARMENT+r"|clothes|outfit)\b",prompt,re.I) for word in [by_id[key]['name']]+by_id[key].get('aliases',[])):
                owners.append(key)
        if owners: change_targets=owners
        # Preserve the rest of the outfit when a single garment is changed or removed.
        for key in change_targets:
            clause = character_clause(prompt,by_id[key],characters) if len(change_targets)>1 else prompt
            described = garment_parts(clause)
            for match in re.finditer(r'\b('+GARMENT+r')\s+(?:to\s+)?('+COLORS+r')\b',clause,re.I):
                described.update(garment_parts(f'{match.group(2)} {match.group(1)}'))
            removed = re.search(r'\b(?:remov\w*|tak\w*\s+off|pull\w*\s+off)\b',clause,re.I)
            for kind, phrase in described.items():
                wardrobe[key][kind] = f'no {kind} (removed)' if removed else phrase
            if described:
                clothing[key] = ', '.join(wardrobe[key].values())
    new_location = None
    if location_id and parent and location_id!=parent.get('location_id'):
        info['environment_change']=True
    location = memory.get('locations',location_id,story_id) if location_id else None
    explicit_place=re.search(r'\b(?:inside|in|into|to|at)\s+(?:an?\s+|the\s+)?((?:modern\s+|parked\s+)?'+PLACES+r')\b',prompt,re.I)
    if info['environment_change'] and location and explicit_place and explicit_place.group(1).lower()!=location['name'].lower():
        location=None; location_id=None
    if location is None and parent and not info['environment_change']:
        location_id = parent.get('location_id')
        if location_id: location=memory.get('locations',location_id,story_id)
    if location is None:
        place = explicit_place
        description = 'Outside in the rain' if re.search(r'\boutside\b.*\brain\b',prompt,re.I) else place.group(1) if place else ''
        if description:
            location_id=uid()
            new_location={'id':location_id,'story_id':story_id,'name':description,'description':description,'created_at':time.time(),'reference':None}
            location = new_location
        elif info['environment_change']:
            location_id=None
    references, warnings = [], []
    # Major body movement needs a new composition, rather than another refinement
    # of a stationary source pose. Only release the source when every person has
    # an independently confirmed face, retaining identity and outfit guidance.
    major_motion = bool(re.search(r'\b(?:push\w*|kick\w*|run\w*|jump\w*|cycl\w*|riding|ride|sleep\w*|lying|lies|sits?|sitting|check\w*|drink\w*|read\w*|hold\w*|held)\b',prompt,re.I))
    # A tight portrait cannot show a new hand-held interaction without reframing.
    major_motion = major_motion or bool(info['change']['pose'] and info['change']['objects'])
    confirmed_faces = bool(selected) and all(by_id[key].get('face_reference') for key in selected)
    known_outfits = bool(selected) and all(clothing.get(key) for key in selected)
    recompose = bool(parent and auto_references and major_motion and confirmed_faces and known_outfits)
    if parent and major_motion and confirmed_faces and not known_outfits:
        warnings.append('The current outfit is not recorded for every character. The previous image is retained; record current clothes in Character manager for a new pose composition with explicit outfit guidance.')
    source_asset = parent['image'] if parent and not info['new_characters'] and not info['environment_change'] and not recompose else None
    if source_asset:
        memory.asset(story_id,source_asset)
        references.append({'role':'previous_scene','asset':source_asset,'scene_id':parent_id})
    candidates = []
    ordered = list(dict.fromkeys(mentioned+selected))
    for key in ordered:
        if key not in selected: continue
        character = by_id[key]
        if preservation['faces'] and character.get('identity_locked',True):
            asset = character.get('face_reference') or character.get('original_reference')
            if asset:
                candidates.append({'role':'character_identity','character_id':key,'asset':asset})
            else:
                warnings.append(f'{character["name"]} has no confirmed reference yet. Assign a face from a scene in the character manager.')
    if len(candidates)==2 and auto_references and not reference_ids:
        ids=[ref['character_id'] for ref in candidates]
        # A full original scene would reintroduce stale poses, clothes and background.
        # Use confirmed head crops even when both people came from that same scene.
        shared=False
        asset=memory.reference_board(story_id,candidates,[by_id[key]['name'] for key in ids])
        candidates=[{'role':'character_group','character_ids':ids,'asset':asset,
                     'sources':[ref['asset'] for ref in candidates],'shared_original_scene':bool(shared)}]
    if location and location.get('reference') and preservation['environment'] and not info['environment_change']:
        candidates.append({'role':'location','location_id':location_id,'asset':location['reference']})
    alternate = []
    for c in characters:
        for ref in c.get('references',[]):
            alternate.append({'role':'character_identity','character_id':c['id'],'reference_id':ref['id'],'asset':ref.get('face') or {'path':ref['path'],'sha256':ref['sha256']}})
    if reference_ids:
        selected_refs=[r for key in reference_ids for r in alternate if r['reference_id']==key]
        if len(selected_refs)!=len(reference_ids): raise MemoryError('An additional reference does not belong to this story')
        candidates=selected_refs+(candidates if auto_references else [])
    elif not auto_references:
        candidates=[]
    if parent and not source_asset and selected and not candidates and not info['new_characters'] and auto_references:
        # A location change still needs an appearance anchor when no faces were assigned.
        candidates.append({'role':'cast_appearance','character_ids':selected,'asset':parent['image']})
        warnings.append('No confirmed face reference is available; the previous scene supplies appearance only for the new setting.')
    seen={r['asset']['sha256'] for r in references}
    for ref in candidates:
        if ref['asset']['sha256'] in seen:
            if references and ref.get('character_ids'): references[0]['identity_character_ids']=ref['character_ids']
            continue
        if len(references)>=3:
            warnings.append('Reference limit reached: the previous scene and two priority references are used; remaining references are stored but omitted from this generation.')
            break
        memory.asset(story_id,ref['asset'])
        references.append(ref); seen.add(ref['asset']['sha256'])
    roles=[]
    for index,ref in enumerate(references,1):
        tag=f'<image{index}>'
        if ref['role']=='previous_scene':
            roles.append(f'Edit {tag} for the next moment of the same story: {prompt}')
        elif ref['role']=='character_identity':
            c=by_id[ref['character_id']]
            roles.append(f'{tag} shows {c["name"]}\'s facial identity only; use the requested pose, expression and current outfit.')
        elif ref['role']=='character_group':
            people=', '.join(by_id[key]['name'] for key in ref['character_ids'])
            roles.append(f'{tag} identifies {people}, left to right. Use these faces and the current outfits below in one scene.')
        elif ref['role']=='cast_appearance':
            roles.append(f'{tag} supplies the people and outfits only. Recreate them in the requested new setting and action; replace the reference background and pose.')
        else:
            roles.append(f'{tag} supplies the location and its unrequested environment details.')
    appearance_changes = dict(parent.get('appearance_changes',{})) if parent else {}
    if info['change']['hair']:
        for key in change_targets: appearance_changes[key]=(appearance_changes.get(key,'')+'; '+prompt).strip('; ')[-1000:]
    facts=[]
    for key in selected:
        c=by_id[key]
        facts.append(f'{c["name"]} ({", ".join(c.get("aliases",[]))}): '+ '; '.join(filter(None,[c.get('appearance','')[:220],c.get('facial_features','')[:120],
            'skin tone: '+c.get('skin_tone','') if c.get('skin_tone') else '',
            'approximate age: '+c.get('approximate_age','') if c.get('approximate_age') else '',
            'hair: '+c.get('hair','') if c.get('hair') and not info['change']['hair'] else '',
            'clothing: '+clothing[key] if clothing[key] and preservation['clothing'] else '',
            'accessories: '+c.get('accessories','') if c.get('accessories') else '',
            'Current appearance change (takes priority over older reference hair): '+appearance_changes.get(key,'') if key in appearance_changes else ''])))
    rules=[]
    if parent and selected and preservation['faces']: rules.append('Keep facial identities, hair and body proportions unless explicitly changed.')
    if parent and selected and preservation['clothing']: rules.append('Keep current outfits and accessories except explicitly requested clothing changes.')
    if parent and preservation['environment'] and not info['environment_change']: rules.append('Keep unrelated setting details except requested location or background changes.')
    if info['environment_change']: rules.append('Change the environment as requested. Do not force the previous location or background into the new scene. Show only the selected characters in the new location.')
    if parent and preservation['style'] and not info['change']['visual_style']: rules.append('Keep the visual style unless this scene requests a different style.')
    if parent and not info['change']['lighting'] and not info['environment_change']: rules.append('Keep lighting and exposure consistent except requested changes.')
    # An incomplete verb detector must never freeze hands, pose or facial expression.
    # Explicit user preservation remains in the verbatim instruction above.
    if info['new_characters']: rules.append('Use new characters as requested; do not reuse a replaced character identity.')
    interaction_detail=''
    if info.get('action_type','') and info['action_type'].startswith('slap'):
        interaction_detail=(f'Action contact: the {info["actor"]} is the actor and the {info["recipient"]} receives the slap. '
            f'The actor\'s open palm contacts the recipient\'s {info["target_region"]}, with a natural reacting head angle. '
            'The contact is palm-to-cheek, not palm-to-palm or a high-five. Keep each arm attached to its own person.')
    elif info.get('actor') and info.get('target_region'):
        interaction_detail=f'Actor: {info["actor"]}. Recipient: {info["recipient"]}. Requested contact region on the recipient: {info["target_region"]}.'
    closing = 'Change pose, hands, expression and framing as needed for the requested scene.' if parent and selected else ''
    sections = [] if references and references[0]['role']=='previous_scene' else [prompt]
    if interaction_detail: sections.append(interaction_detail)
    sections.extend(roles)
    if facts:
        sections.append('Selected cast:')
        sections.extend(facts)
    if location and location.get('description'):
        sections.append('Current setting (unless changed in this instruction): '+location['description'])
    sections.extend(rules)
    if closing: sections.append(closing)
    compiled=' '.join(sections)
    return {'parent_scene_id':parent_id,'previous_scene_id':snapshot['current_scene_id'],'user_prompt':prompt,
        'interpretation':info,'character_ids':selected,'location_id':location_id,'new_location':new_location,'clothing':clothing,'wardrobe':wardrobe,
        'appearance_changes':appearance_changes,
        'props':list(dict.fromkeys((parent.get('props',[]) if parent else [])+[word for word in ['cup','glass','phone','bicycle','book','necklace'] if contains(prompt,word)])),
        'lighting':prompt if info['change']['lighting'] else parent.get('lighting','unspecified') if parent else 'unspecified',
        'visual_style':prompt if info['change']['visual_style'] else parent.get('visual_style','unspecified') if parent else 'unspecified',
        'camera':prompt if info['change']['camera'] else parent.get('camera','unspecified') if parent else 'unspecified',
        'environment':location['description'] if location else '', 'references':references,'preserve':preservation,
        'transition_route':'reference_recompose' if recompose or (parent and info['environment_change']) else 'edit_previous' if source_asset else 'new_scene',
        'compiled_prompt':compiled,'warnings':warnings,'evaluate':evaluate}
