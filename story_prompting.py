"""Grounded, ordered story rewriting with the installed local Qwen 4B enhancer."""
import json
import re
import time

import dolphin_prompting as dolphin
import prompt_intent
import prompt_polish

PROFILE = 'qwen21-story-v1'


def validate_inputs(scenes):
    if not isinstance(scenes,list) or not 1 <= len(scenes) <= 5:
        raise dolphin.EnhancementError('Enter one to five story scenes.',422)
    if any(not isinstance(s,str) or not s.strip() or len(s)>4000 for s in scenes):
        raise dolphin.EnhancementError('Every entered scene needs a prompt of up to 4000 characters.',422)
    if sum(len(s) for s in scenes)>10000:
        raise dolphin.EnhancementError('Enhance up to 10,000 characters together on this local profile. Shorten the scenes; your prompts are unchanged.',422)
    return [s.strip() for s in scenes]


def validate_reply(content, originals, context):
    try:
        result=json.loads(content)
    except (ValueError,TypeError) as exc:
        raise dolphin.EnhancementError('Qwen returned invalid story JSON. Original scenes are unchanged.') from exc
    if not isinstance(result,dict) or set(result)!={'scenes'} or not isinstance(result['scenes'],list) or len(result['scenes'])!=len(originals):
        raise dolphin.EnhancementError('Qwen must return exactly one prompt per scene in the original order.')
    output=[]
    grounding=json.dumps(context,ensure_ascii=False)
    for index,(original,row) in enumerate(zip(originals,result['scenes']),1):
        if not isinstance(row,dict) or set(row)!={'scene','prompt'} or type(row['scene']) is not int or row['scene']!=index or not isinstance(row['prompt'],str):
            raise dolphin.EnhancementError('Scene numbers must remain in order, with one prompt for each original scene.')
        fragments=re.split(r'("[^"\n]*")',row['prompt'])
        rewritten=''.join(part if i%2 else re.sub(r'\s+',' ',part) for i,part in enumerate(fragments)).strip()
        rewritten=prompt_polish.neutralize_default_stance(original+' '+grounding,rewritten)
        if not rewritten or len(rewritten)>4000 or not dolphin.english_script(rewritten):
            raise dolphin.EnhancementError(f'Scene {index} must contain a valid English prompt of up to 4000 characters.')
        if re.match(r'(?i)^(?:sure\b|here (?:is|are)\b|as an ai\b|enhanced prompt\s*:)',rewritten):
            raise dolphin.EnhancementError(f'Scene {index} contains commentary instead of an image prompt.')
        for literal in re.findall(r'["“]([^"“”]+)["”]',original):
            if dolphin.english_script(literal) and literal not in rewritten:
                raise dolphin.EnhancementError(f'Scene {index} changed exact quoted text.')
        errors=prompt_intent.rewrite_errors(original,rewritten)
        local_errors=prompt_polish.rewrite_errors(original,rewritten)
        contextual_errors=prompt_polish.rewrite_errors(original+' '+grounding,rewritten)
        # Existing outfit/style/posture facts may carry forward; future scenes
        # are deliberately excluded from the grounding for validation.
        errors += [e for e in local_errors if not e.startswith(('Do not introduce','Do not invent')) or e in contextual_errors]
        errors += prompt_polish.description_errors(original+' '+grounding,rewritten)
        if re.search(r'\b(?:cry|cries|crying|sobs?|sobbing)\b',original,re.I) and not re.search(r'\b(?:tears?|tearful|wet eyes|wet cheeks)\b',rewritten,re.I):
            errors.append('Make the requested crying visible with tears or wet tear trails; grammatical rewriting alone is not enough.')
        if re.search(r'\bargu\w*\b',original,re.I) and not re.search(r'\b(?:gesture\w*|brows?|mouth\w*|tense|angry|speaking|frown\w*)\b',rewritten,re.I):
            errors.append('Clarify the requested argument with readable speaking expressions or gestures, without inventing dialogue.')
        if errors:
            raise dolphin.EnhancementError(f'Scene {index}: '+ ' '.join(dict.fromkeys(errors)))
        output.append(rewritten)
        grounding += '\n'+original
    return output


def enhance(scenes, context, *, keep_alive=0, model=None):
    originals=validate_inputs(scenes)
    selected=model or dolphin.QWEN_MODEL
    if selected!=dolphin.QWEN_MODEL:
        raise dolphin.EnhancementError('Story enhancement uses the installed Qwen 4B profile.',422)
    system=(dolphin.ROOT/'prompts/qwen_story.txt').read_text(encoding='utf-8')
    schema={'type':'object','properties':{'scenes':{'type':'array','minItems':len(originals),'maxItems':len(originals),
        'items':{'type':'object','properties':{'scene':{'type':'integer','minimum':1,'maximum':len(originals)},'prompt':{'type':'string'}},
                 'required':['scene','prompt'],'additionalProperties':False}}},'required':['scenes'],'additionalProperties':False}
    brief={'task':'polish an ordered continuing story for Qwen Image 2.1','established_context':context,
           'scenes':[{'scene':i,'prompt':p} for i,p in enumerate(originals,1)]}
    payload={'model':selected,'messages':[{'role':'system','content':system},{'role':'user','content':json.dumps(brief,ensure_ascii=False)}],
             'stream':False,'think':False,'format':schema,'keep_alive':keep_alive,
             'options':{'temperature':.15,'top_p':.8,'top_k':20,'min_p':0,'repeat_penalty':1.0,'seed':42,'num_ctx':8192,'num_predict':2000}}
    started=time.monotonic()
    for attempt in range(2):
        reply=dolphin.chat(payload)
        try:
            enhanced=validate_reply(reply,originals,context)
            break
        except dolphin.EnhancementError as exc:
            if attempt:raise
            brief['validation_feedback']=str(exc)
            payload['messages'][-1]['content']=json.dumps(brief,ensure_ascii=False)
    return {'originals':originals,'scenes':enhanced,'model':selected,'profile':PROFILE,'story_based':True,
            'elapsed_sec':round(time.monotonic()-started,2),'model_calls':attempt+1,'image_understanding':False,
            'scope':'Ordered story text with established character/outfit context; review the wording and generated images.'}
