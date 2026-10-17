"use strict";
(() => {
  const $ = id => document.getElementById(id);
  const state = {story:null, data:null, selected:null, character:null, mode:"balanced", pending:null, polling:false, assigning:null, faces:[], sequence:null, sequencePolling:false, submitting:false, sequenceToken:null, hydratedSequence:null, sequenceLastScene:null, enhancing:false, storyEnhancerReady:false, enhancementUndo:null};
  const sleep = ms => new Promise(resolve => setTimeout(resolve,ms));
  const base = () => { if(!state.story) throw new Error("Create or select a story first."); return `/api/stories/${state.story}`; };
  const fileUrl = asset => `${base()}/files/${asset.path.split("/").map(encodeURIComponent).join("/")}`;
  const node = (tag, content, className) => { const el=document.createElement(tag); if(content!==undefined) el.textContent=content; if(className) el.className=className; return el; };
  const fail = error => { $("storyError").textContent=error.message||String(error); $("storyError").hidden=false; };
  const action = fn => async event => { event?.preventDefault(); $("storyError").hidden=true; try { await fn(event); } catch(error) { fail(error); } };
  async function api(url, options={}) {
    const response=await fetch(url,options); const data=await response.json().catch(()=>({}));
    if(!response.ok) throw new Error(typeof data.detail==="string" ? data.detail : Array.isArray(data.detail) ? data.detail.map(v=>v.msg).join("; ") : "The local request failed. Try refreshing the workspace.");
    return data;
  }
  const json = value => ({method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(value)});
  const scenes = () => (state.data?.scenes||[]).filter(s=>!s.deleted);
  const completed = () => scenes().filter(s=>s.status==="succeeded");
  const characters = () => (state.data?.characters||[]).filter(c=>!c.deleted);
  const sceneName = id => {const index=scenes().findIndex(s=>s.id===id); return index>=0 ? `Scene ${index+1}` : "Opening";};
  function options(id, rows, first, selected) {
    const el=$(id); el.replaceChildren(); if(first!==null) el.add(new Option(first,""));
    rows.forEach(([value,label])=>el.add(new Option(label,value)));
    if(selected && [...el.options].some(o=>o.value===selected)) el.value=selected;
  }
  function updateBusy() {
    const busy=!!state.pending || sequenceActive() || state.submitting || state.enhancing;
    $("generateScene").disabled=!state.story || busy;
    $("cancelScene").hidden=!state.pending || sequenceActive();
    $("previewContext").disabled=!state.story || busy;
    $("storySelect").disabled=busy;
    $("newStoryForm").querySelector("button").disabled=busy;
    for(const id of ["continueScene","branchScene","retryScene","deleteScene","saveSceneReview"]) $(id).disabled=busy;
    for(const form of [$("nextForm"),$("characterForm"),$("locationForm")]) {
      for(const el of form.querySelectorAll("input,textarea,select,button")) {
        if(["generateScene","cancelScene","previewContext"].includes(el.id))continue;
        el.disabled=busy;
      }
    }
    for(const id of ["referencePhoto","detectSceneFaces","assignmentFace","assignCharacter"]) $(id).disabled=busy;
    $("resumeSequence").disabled=busy;
    $("enhanceStory").disabled=!state.story || busy || !state.storyEnhancerReady;
    $("undoStoryEnhance").hidden=!state.enhancementUndo;
    $("undoStoryEnhance").disabled=busy;
  }
  const sequenceActive=()=>!!state.sequence && ["queued","running","cancelling"].includes(state.sequence.status);
  function workflow() {
    const multi=$("storyWorkflow").value==="sequence";
    $("singleSceneInput").hidden=multi;$("sequenceInputs").hidden=!multi;$("nextPrompt").required=!multi;
    $("generateScene").textContent=multi ? "Generate sequence" : "Generate next scene";
  }
  function selectMode(mode) {
    state.mode=mode;
    document.querySelectorAll("[data-story-mode]").forEach(button=>{const selected=button.dataset.storyMode===mode;button.classList.toggle("selected",selected);button.setAttribute("aria-pressed",String(selected));});
  }
  function renderSequence() {
    const seq=state.sequence;$("sequenceStatus").hidden=!seq;if(!seq)return;
    const done=seq.items.filter(item=>item.status==="succeeded").length;
    const current=seq.items.find(item=>item.status==="running");
    $("sequenceProgressText").textContent=`${done} of ${seq.items.length} scenes generated · ${seq.status}${current ? ` · Scene ${current.index+1}` : ""}`;
    $("sequenceProgress").max=seq.items.length;$("sequenceProgress").value=done;
    $("sequenceItems").replaceChildren();
    for(const item of seq.items) {
      const li=node("li"),button=node("button");button.type="button";
      const imageScene=scenes().find(s=>s.id===item.scene_id&&s.image);
      const review=imageScene?.visual_review?.status;
      button.disabled=!imageScene;button.title=item.request.prompt;
      button.append(node("strong",`Scene ${item.index+1}`),node("span",item.status==="succeeded" ? `Generated · ${review&&review!=="not_reviewed" ? `review ${review}` : "review needed"}` : item.status==="waiting" ? "Not started" : item.status));
      button.addEventListener("click",()=>{state.selected=item.scene_id;renderScene();renderHistory();});li.append(button);$("sequenceItems").append(li);
    }
    $("sequenceError").hidden=!seq.error;$("sequenceError").textContent=seq.error||"";
    $("stopSequence").hidden=!sequenceActive();$("stopSequence").disabled=seq.status==="cancelling";
    $("resumeSequence").hidden=!["failed","paused","cancelled"].includes(seq.status);
    if(state.hydratedSequence!==seq.id) {
      state.hydratedSequence=seq.id;
      $("storyWorkflow").value="sequence";workflow();
      for(let i=0;i<5;i++)$("sequencePrompt"+(i+1)).value=seq.items[i]?.request.prompt||"";
      const req=seq.items[0].request;selectMode(req.mode);
      $("storyAspect").value=req.aspect_ratio;$("storySeed").value=String(req.seed);$("storySteps").value=req.num_steps||"";
      $("storyLora").checked=req.lora_enabled;$("autoReferences").checked=req.auto_references;$("evaluateScene").checked=req.evaluate;
      for(const [field,key] of [["keepFaces","faces"],["keepClothes","clothing"],["keepEnvironment","environment"],["keepStyle","style"]])$(field).checked=req.preserve[key];
      for(const option of $("sceneCast").options)option.selected=(req.character_ids||[]).includes(option.value);
      for(const option of $("extraReferences").options)option.selected=req.reference_ids.includes(option.value);
      $("sceneLocation").value=req.location_id||"";
      $("newCamera").value=req.camera_change;$("newLighting").value=req.lighting_change;$("newLocation").value=req.location_change;
    }
  }
  async function loadStories(selectId) {
    const data=await api("/api/stories");
    options("storySelect",data.stories.map(s=>[s.id,s.name]),"Choose or create a story",selectId||state.story);
  }
  async function loadStory(id, preferScene=null) {
    if(state.story!==id){state.enhancementUndo=null;$("storyEnhanceStatus").hidden=true;}
    state.story=id; state.data=await api(`/api/stories/${id}`);
    try{sessionStorage.setItem("qwen-active-story",id);localStorage.removeItem("qwen-active-story");}catch{}
    if(preferScene) state.selected=preferScene;
    state.pending=scenes().find(s=>["queued","running"].includes(s.status))?.id||null;
    const sequences=state.data.sequences||[];
    state.sequence=sequences.find(s=>["queued","running","cancelling"].includes(s.status))||sequences.at(-1)||null;
    if(sequenceActive()) {
      const newest=state.sequence.items.filter(item=>item.status==="succeeded").at(-1)?.scene_id;
      if(newest&&newest!==state.sequenceLastScene){state.selected=newest;state.sequenceLastScene=newest;}
    }
    $("storyTitle").textContent=state.data.name;
    const parent=$("parentScene").value;
    options("parentScene",completed().map(s=>[s.id,`${sceneName(s.id)} · ${s.user_prompt.slice(0,65)}`]),"Start a new scene",preferScene||parent||state.data.current_scene_id);
    const cast=[...$("sceneCast").selectedOptions].map(o=>o.value),refs=[...$("extraReferences").selectedOptions].map(o=>o.value),location=$("sceneLocation").value;
    options("sceneCast",characters().map(c=>[c.id,c.name]),null);
    options("sceneLocation",state.data.locations.map(l=>[l.id,l.name]),"Automatic from scene and prompt",location);
    options("extraReferences",characters().flatMap(c=>(c.references||[]).map((r,i)=>[r.id,`${c.name} · view ${i+1}`])),null);
    for(const option of $("sceneCast").options)option.selected=cast.includes(option.value);
    for(const option of $("extraReferences").options)option.selected=refs.includes(option.value);
    options("characterSelect",characters().map(c=>[c.id,c.name]),"Add a new character",state.character);
    renderCharacters(); renderScene(); renderHistory(); renderSequence(); updateBranch(); updateBusy();
    $("locationList").textContent=state.data.locations.map(l=>l.name).join(" · ")||"Locations will be remembered from your scene instructions.";
    $("characterCount").textContent=String(characters().length);
    if(sequenceActive() && !state.sequencePolling) void watchSequence(state.story,state.sequence.id);
    else if(state.pending && !sequenceActive() && !state.polling) void watchScene(state.pending);
  }
  function renderHistory() {
    $("sceneTimeline").replaceChildren(); $("historyEmpty").hidden=scenes().length>0;
    for(const scene of scenes()) {
      const button=node("button",undefined,"story-scene"); button.type="button"; button.setAttribute("aria-pressed",String(scene.id===state.selected));
      button.setAttribute("aria-label",`${sceneName(scene.id)}: ${scene.user_prompt}`);
      if(scene.image) {const img=node("img"); img.src=fileUrl(scene.image); img.alt=sceneName(scene.id); img.loading="lazy"; button.append(img);}
      const status=scene.status==="succeeded" ? `generated · review ${scene.visual_review?.status||"not reviewed"}` : scene.status;
      button.append(node("strong",sceneName(scene.id)),node("span",scene.user_prompt.slice(0,100)),node("span",`${scene.parent_scene_id ? "From "+sceneName(scene.parent_scene_id) : "Opening scene"} · ${status}`));
      button.addEventListener("click",()=>{state.selected=scene.id;renderScene();renderHistory();}); $("sceneTimeline").append(button);
    }
  }
  function renderScene() {
    let scene=scenes().find(s=>s.id===state.selected);
    if(!scene) {scene=scenes().find(s=>s.id===state.data?.current_scene_id)||completed().at(-1); state.selected=scene?.id||null;}
    const hasImage=!!scene?.image;
    $("sceneFigure").hidden=$("sceneActions").hidden=!hasImage; $("sceneEmpty").hidden=hasImage;
    $("sceneLabel").textContent=scene ? `${sceneName(scene.id)} · review ${scene.visual_review?.status||"not reviewed"}` : "";
    if(!hasImage) {if(scene?.error) fail(new Error(scene.error)); return;}
    $("selectedSceneImage").src=fileUrl(scene.image); $("sceneCaption").textContent=scene.user_prompt;
    $("downloadScene").href=fileUrl(scene.image); $("downloadScene").download=`${sceneName(scene.id).replaceAll(" ","-")}.png`;
    $("sceneMetadata").textContent=JSON.stringify(scene,null,2);
    $("sceneReviewStatus").value=scene.visual_review?.status||"not_reviewed";
    $("sceneReviewNotes").value=scene.visual_review?.notes||"";
    const identity=(scene.evaluation?.identity||[]).map(item=>`${item.name}: ${item.status==="measured" ? item.faces.map(f=>`face ${f.face_index+1} cosine ${f.cosine_similarity.toFixed(3)}`).join(", ") : "not measured"}`).join("; ");
    $("sceneEvaluation").textContent=[...(scene.warnings||[]),...(scene.memory_budget?.warnings||[]),scene.evaluation?.presence_warning,scene.evaluation?.note||"Review this scene's action, faces, clothing and environment.",identity].filter(Boolean).join(" ");
    options("compareScene",completed().filter(s=>s.id!==scene.id).map(s=>[s.id,sceneName(s.id)]),"No comparison");
    compare();
    const index=completed().findIndex(s=>s.id===scene.id);
    $("previousScene").disabled=index<=0; $("nextScene").disabled=index<0||index>=completed().length-1;
  }
  function compare() {
    const scene=completed().find(s=>s.id===$("compareScene").value);
    $("compareSceneImage").hidden=!scene; $("sceneImages").classList.toggle("comparing",!!scene);
    if(scene) {$("compareSceneImage").src=fileUrl(scene.image); $("compareSceneImage").alt=`Comparison: ${sceneName(scene.id)}`;}
    else $("compareSceneImage").removeAttribute("src");
  }
  function updateBranch() {
    const parent=$("parentScene").value;
    $("branchStatus").textContent=!state.story ? "Choose a story to begin." : parent ? `Next image continues from ${sceneName(parent)}${parent!==state.data.current_scene_id ? " as an alternative branch" : ""}.` : "This creates a fresh scene within this story. Select specific characters if needed.";
    $("nextPrompt").placeholder=parent ? "The woman apologizes to the man." : "A woman in a red dress talks to a man in a black shirt in a modern apartment.";
  }
  function request(prompt=$("nextPrompt").value) {
    if(!state.story) throw new Error("Create or select a story first.");
    if(!prompt.trim()) throw new Error("Describe what happens next.");
    for(const id of ["storySeed","storySteps"]) if(!$(id).validity.valid) throw new Error("Use a nonnegative whole-number seed and 1–100 steps.");
    const cast=[...$("sceneCast").selectedOptions].map(o=>o.value);
    return {prompt:prompt.trim(),parent_scene_id:$("parentScene").value,
      character_ids:cast.length ? cast : null, location_id:$("sceneLocation").value||null,
      preserve:{faces:$("keepFaces").checked,clothing:$("keepClothes").checked,environment:$("keepEnvironment").checked,style:$("keepStyle").checked},
      auto_references:$("autoReferences").checked,reference_ids:[...$("extraReferences").selectedOptions].map(o=>o.value),evaluate:$("evaluateScene").checked,
      camera_change:$("newCamera").value,lighting_change:$("newLighting").value,location_change:$("newLocation").value,
      mode:state.mode,aspect_ratio:$("storyAspect").value,num_steps:$("storySteps").value ? Number($("storySteps").value) : null,
      seed:$("storySeed").value ? Number($("storySeed").value) : Math.floor(Math.random()*4294967295),lora_enabled:$("storyLora").checked};
  }
  function storyPrompts() {
    const prompts=Array.from({length:5},(_,i)=>$("sequencePrompt"+(i+1)).value.trim());
    const last=prompts.findLastIndex(Boolean);
    if(last<0)throw new Error("Enter Scene 1 before generating a sequence.");
    for(let i=0;i<=last;i++)if(!prompts[i])throw new Error(`Scene ${i+1} is empty. Fill scenes in order; leave only the unused final boxes blank.`);
    return prompts.slice(0,last+1);
  }
  function sequenceRequests() {
    const prompts=storyPrompts();
    const userSpecifiedSeed=$("storySeed").value ? Number($("storySeed").value) : null;
    const shared=request(prompts[0]);
    return prompts.map((prompt,index)=>({
      ...shared,
      prompt,
      parent_scene_id:index===0?shared.parent_scene_id:null,
      seed:userSpecifiedSeed!==null ? (userSpecifiedSeed+index)%4294967295 : Math.floor(Math.random()*4294967295)
    }));
  }
  async function watchSequence(story,id) {
    if(state.sequencePolling)return;state.sequencePolling=true;let errors=0;
    try {
      while(state.story===story) {
        await sleep(1800);
        try {
          await loadStory(story);
          errors=0;
          if(state.sequence?.id!==id || !sequenceActive()) {
            const newest=state.sequence?.items.filter(item=>item.status==="succeeded").at(-1)?.scene_id;
            if(newest){state.selected=newest;renderScene();renderHistory();}
            $("storyProgressText").textContent=state.sequence?.status==="succeeded" ? "Sequence generated. Review each scene's action and continuity." : "Sequence stopped. Saved scenes are retained.";
            break;
          }
          if(state.pending) {
            const scene=scenes().find(s=>s.id===state.pending);
            if(scene?.job_id){const job=await api(`/api/jobs/${scene.job_id}`);$("storyProgressText").textContent=job.phase;}
          }
        } catch(error) {
          if(++errors>=5){fail(error);$("storyProgressText").textContent="Connection interrupted. Refresh to reconnect; the server queue is not resubmitted.";break;}
        }
      }
    } finally {state.sequencePolling=false;updateBusy();}
  }
  async function watchScene(id) {
    if(state.polling) return; state.polling=true; let errors=0;
    try {
      while(state.pending===id) {
        try {
          const data=await api(base()); const scene=data.scenes.find(s=>s.id===id); errors=0;
          if(!scene) throw new Error("Generating scene is unavailable. Reopen the story.");
          if(!["queued","running"].includes(scene.status)) {
            state.pending=null; $("storyProgress").hidden=true; $("storyProgressText").textContent=scene.status==="succeeded" ? "Scene saved to this story." : "Previous scene and references retained.";
            await loadStory(state.story,scene.status==="succeeded" ? id : null);
            if(scene.status!=="succeeded") fail(new Error(scene.error||scene.status));
            break;
          }
          if(scene.job_id) {
            const job=await api(`/api/jobs/${scene.job_id}`);
            $("storyProgressText").textContent=`${job.phase} · ${Math.round(job.elapsed_sec||0)}s`;
            $("storyProgress").hidden=false;
            if(job.progress===null||job.progress===undefined) $("storyProgress").removeAttribute("value");
            else {$("storyProgress").max=1;$("storyProgress").value=job.progress;}
          }
        } catch(error) {if(++errors>=5) {fail(error); $("storyProgressText").textContent="Connection interrupted. Refresh to reconnect to this story."; break;}}
        await sleep(1800);
      }
    } finally {state.polling=false;updateBusy();}
  }
  const characterFields={characterName:"name",characterRole:"role",characterAliases:"aliases",characterAppearance:"appearance",characterFace:"facial_features",characterHair:"hair",characterSkin:"skin_tone",characterAge:"approximate_age",characterDefaultClothes:"default_clothing",characterClothes:"current_clothing",characterAccessories:"accessories",characterRelationships:"relationships"};
  function selectCharacter(id) {
    state.character=id||null; const c=characters().find(c=>c.id===id)||{}; $("characterSelect").value=id||"";
    Object.entries(characterFields).forEach(([field,key])=>{$(field).value=Array.isArray(c[key]) ? c[key].join(key==="aliases" ? ", " : "\n") : c[key]||"";});
    $("characterLock").checked=c.identity_locked!==false; $("deleteCharacter").hidden=!id;
    state.assigning=null; state.faces=[]; $("assignmentFaces").replaceChildren(); options("assignmentFace",[],"Choose a face / use the entire uploaded photo");
    $("referencePhoto").value=""; $("referenceStatus").textContent=id ? `References for ${c.name}. Original views are retained.` : "Save a character before adding references.";
  }
  function renderCharacters() {
    $("characterList").replaceChildren();
    for(const c of characters()) {
      const button=node("button",undefined,"story-character"); button.type="button";
      const ref=c.face_reference||c.original_reference;
      if(ref) {const image=node("img");image.src=fileUrl(ref);image.alt=`${c.name} reference`;image.loading="lazy";button.append(image);}
      const caption=node("span",c.name);caption.append(node("small",`${c.references.length} reference${c.references.length===1 ? "" : "s"} · ${c.identity_locked ? "identity lock on" : "identity lock off"}`));button.append(caption);
      button.addEventListener("click",()=>selectCharacter(c.id));$("characterList").append(button);
    }
  }
  function showFaces(candidates) {
    state.faces=candidates;$("assignmentFaces").replaceChildren();
    options("assignmentFace",candidates.map((face,index)=>[String(index),`Face ${index+1}`]),"Choose the intended face");
    candidates.forEach((face,index)=>{if(face.preview) {const image=node("img");image.src=face.preview;image.alt=`Face ${index+1}`;$("assignmentFaces").append(image);}});
    if(candidates.length===1) $("assignmentFace").value="0";
  }
  async function normalize(file) {
    if(!file||file.size>25*1024*1024) throw new Error("Choose a still photo smaller than 25 MB.");
    const url=URL.createObjectURL(file);
    try {
      const image=await new Promise((resolve,reject)=>{const img=new Image();img.onload=()=>resolve(img);img.onerror=()=>reject(new Error("This photo could not be opened. Try a PNG or JPEG copy."));img.src=url;});
      if(image.width*image.height>24000000||Math.max(image.width,image.height)>8192) throw new Error("Choose a photo up to 24 megapixels and 8192 pixels per side.");
      const canvas=document.createElement("canvas");canvas.width=image.width;canvas.height=image.height;canvas.getContext("2d").drawImage(image,0,0);
      const blob=await new Promise(resolve=>canvas.toBlob(resolve,"image/png"));
      if(!blob||blob.size>25*1024*1024) throw new Error("The PNG copy exceeds the upload limit. Export a smaller photo.");
      return new File([blob],"reference.png",{type:"image/png"});
    } finally {URL.revokeObjectURL(url);}
  }
  $("newStoryForm").addEventListener("submit",action(async()=>{
    const story=await api("/api/stories",json({name:$("storyName").value})); $("storyName").value="";state.selected=null;state.character=null;
    await loadStories(story.id);await loadStory(story.id);selectCharacter("");
  }));
  $("storySelect").addEventListener("change",action(async()=>{if(!$("storySelect").value)return;state.selected=null;state.character=null;$("parentScene").value="";await loadStory($("storySelect").value);selectCharacter("");}));
  $("parentScene").addEventListener("change",updateBranch);
  $("nextForm").addEventListener("submit",action(async()=>{
    if(state.pending||sequenceActive()||state.submitting||state.enhancing)return;
    const multi=$("storyWorkflow").value==="sequence";
    const req=multi?sequenceRequests():request();state.submitting=true;updateBusy();
    try {
      if(multi){state.sequenceToken||=crypto.randomUUID();await api(`${base()}/sequences`,json({scenes:req,idempotency_key:state.sequenceToken}));state.sequenceToken=null;}
      else {const scene=await api(`${base()}/scenes`,json(req));state.pending=scene.id;}
      await loadStory(state.story);
    } finally {state.submitting=false;updateBusy();}
  }));
  $("storyWorkflow").addEventListener("change",workflow);
  $("enhanceStory").addEventListener("click",action(async()=>{
    if(state.enhancing||state.pending||sequenceActive()||state.submitting)return;
    const prompts=storyPrompts(),story=state.story,parent=$("parentScene").value;
    const original=Array.from({length:5},(_,i)=>$("sequencePrompt"+(i+1)).value);
    const cast=[...$("sceneCast").selectedOptions].map(o=>o.value);
    state.enhancing=true;updateBusy();$("enhanceStory").textContent="Enhancing story…";
    $("storyEnhanceStatus").hidden=false;$("storyEnhanceStatus").textContent="Qwen is reading the scenes together and clarifying the story…";
    try {
      const result=await api(`${base()}/enhance`,json({scenes:prompts,parent_scene_id:parent,character_ids:cast.length?cast:null}));
      if(!Array.isArray(result.scenes)||result.scenes.length!==prompts.length||result.scenes.some(p=>typeof p!=="string"||!p.trim()||p.length>4000))throw new Error("The enhancer returned an incomplete story. Your prompts were kept.");
      if(state.story!==story||$("parentScene").value!==parent||original.some((p,i)=>$("sequencePrompt"+(i+1)).value!==p))throw new Error("Your newer edits were kept. Enhance again for the updated story.");
      const applied=original.map((p,i)=>result.scenes[i]??p);
      applied.forEach((p,i)=>{$("sequencePrompt"+(i+1)).value=p;});
      state.enhancementUndo={story,original,applied};state.sequenceToken=null;
      $("storyEnhanceStatus").textContent=`Enhanced ${result.scenes.length} scene${result.scenes.length===1?"":"s"} together with local Qwen (${result.elapsed_sec}s). Review the wording, then Generate sequence.`;
    } catch(error) {$("storyEnhanceStatus").textContent="Enhancement could not finish. Your prompts were kept.";throw error;}
    finally {state.enhancing=false;$("enhanceStory").textContent="Enhance story (Qwen)";updateBusy();}
  }));
  $("undoStoryEnhance").addEventListener("click",()=>{
    const undo=state.enhancementUndo;if(!undo||undo.story!==state.story)return;
    let kept=false;undo.original.forEach((p,i)=>{const field=$("sequencePrompt"+(i+1));if(field.value===undo.applied[i])field.value=p;else kept=true;});
    state.enhancementUndo=null;state.sequenceToken=null;updateBusy();
    $("storyEnhanceStatus").textContent=kept?"Enhancement undone where unchanged; your newer edits were kept.":"Original scene prompts restored.";
  });
  $("nextForm").addEventListener("input",()=>{state.sequenceToken=null;});
  $("previewContext").addEventListener("click",action(async()=>{const req=$("storyWorkflow").value==="sequence"?sequenceRequests()[0]:request();const plan=await api(`${base()}/plan`,json(req));$("contextPreview").textContent=JSON.stringify(plan,null,2);$("contextPreview").hidden=false;}));
  $("stopSequence").addEventListener("click",action(async()=>{if(state.sequence){await api(`${base()}/sequences/${state.sequence.id}/cancel`,json({}));await loadStory(state.story);}}));
  $("resumeSequence").addEventListener("click",action(async()=>{if(state.sequence){state.submitting=true;updateBusy();try{await api(`${base()}/sequences/${state.sequence.id}/resume`,json({}));await loadStory(state.story);}finally{state.submitting=false;updateBusy();}}}));
  $("cancelScene").addEventListener("click",action(async()=>{if(state.pending)await api(`${base()}/scenes/${state.pending}/cancel`,json({}));}));
  document.querySelectorAll("[data-story-mode]").forEach(button=>button.addEventListener("click",()=>selectMode(button.dataset.storyMode)));
  $("compareScene").addEventListener("change",compare);
  for(const [id,direction] of [["previousScene",-1],["nextScene",1]]) $(id).addEventListener("click",()=>{const rows=completed(),index=rows.findIndex(s=>s.id===state.selected);state.selected=rows[index+direction]?.id||state.selected;renderScene();renderHistory();});
  $("continueScene").addEventListener("click",action(async()=>{await api(`${base()}/scenes/${state.selected}/continue`,json({}));await loadStory(state.story,state.selected);$("nextPrompt").focus();}));
  $("branchScene").addEventListener("click",()=>{$("parentScene").value=state.selected;updateBranch();$("nextPrompt").focus();});
  $("retryScene").addEventListener("click",()=>{
    const scene=scenes().find(s=>s.id===state.selected);if(!scene)return;
    $("storyWorkflow").value="single";workflow();
    $("parentScene").value=scene.parent_scene_id||"";$("nextPrompt").value=scene.user_prompt;
    $("storySeed").value=String(scene.settings.seed);$("storySteps").value=scene.settings.num_steps||"";
    updateBranch();$("branchStatus").textContent=`Retrying ${sceneName(scene.id)} from its original parent. Adjust the prompt or generation settings; the existing result will be kept.`;$("nextPrompt").focus();
  });
  $("saveSceneReview").addEventListener("click",action(async()=>{await api(`${base()}/scenes/${state.selected}/review`,json({status:$("sceneReviewStatus").value,notes:$("sceneReviewNotes").value}));await loadStory(state.story);}));
  $("deleteScene").addEventListener("click",action(async()=>{if(!confirm(`Delete ${sceneName(state.selected)}? Its image remains recoverable on disk. Scenes with branches must have their leaf scenes removed first.`))return;await api(`${base()}/scenes/${state.selected}/delete`,json({confirmed:true}));state.selected=null;await loadStory(state.story);}));
  $("characterSelect").addEventListener("change",()=>selectCharacter($("characterSelect").value));
  $("characterForm").addEventListener("submit",action(async()=>{
    const metadata={identity_locked:$("characterLock").checked};Object.entries(characterFields).forEach(([field,key])=>{metadata[key]=["aliases","relationships"].includes(key)?$(field).value.split(key==="aliases" ? "," : "\n").map(v=>v.trim()).filter(Boolean):$(field).value;});
    const opts=json(metadata);if(state.character)opts.method="PATCH";
    const c=await api(`${base()}/characters${state.character?"/"+state.character:""}`,opts);await loadStory(state.story);selectCharacter(c.id);
  }));
  $("deleteCharacter").addEventListener("click",action(async()=>{if(!state.character||!confirm("Delete this character from the active cast? Previous scenes and original reference files are retained."))return;await api(`${base()}/characters/${state.character}/delete`,json({confirmed:true}));state.character=null;await loadStory(state.story);selectCharacter("");}));
  $("detectSceneFaces").addEventListener("click",action(async()=>{
    if(!state.character||!state.selected)throw new Error("Select a saved character and display a completed scene first.");
    const data=await api(`${base()}/scenes/${state.selected}/faces`);state.assigning={scene:state.selected};showFaces(data.candidates);
    $("referenceStatus").textContent=data.candidates.length ? "Choose the face belonging to this character, then save the reference." : "No clear face found. Upload a closer reference photo.";
  }));
  $("referencePhoto").addEventListener("change",action(async()=>{
    if(!state.character)throw new Error("Save or select a character first.");const file=await normalize($("referencePhoto").files[0]);state.assigning={file};
    const form=new FormData();form.append("image",file);const data=await api("/api/identity/analyze",{method:"POST",body:form});
    // Candidate boxes are shown by the corresponding crop previews before association.
    const url=URL.createObjectURL(file);
    try {const image=await new Promise(resolve=>{const img=new Image();img.onload=()=>resolve(img);img.src=url;});
      data.candidates.forEach(face=>{const [x,y,w,h]=face.head_box,canvas=document.createElement("canvas");canvas.width=Math.max(1,Math.round(w*image.width));canvas.height=Math.max(1,Math.round(h*image.height));canvas.getContext("2d").drawImage(image,x*image.width,y*image.height,w*image.width,h*image.height,0,0,canvas.width,canvas.height);face.preview=canvas.toDataURL("image/png");});
    } finally {URL.revokeObjectURL(url);}
    showFaces(data.candidates);$("referenceStatus").textContent=data.candidates.length?"Choose the intended face and save this view. Other references remain stored.":"No face detected. Saving will associate the whole photo without a facial crop.";
  }));
  $("assignCharacter").addEventListener("click",action(async()=>{
    if(!state.character||!state.assigning)throw new Error("Select a character and choose a scene face or upload a reference photo.");
    const selected=$("assignmentFace").value;const box=selected!==""?state.faces[Number(selected)]?.head_box:null;
    if(state.faces.length && !box)throw new Error("Choose which face belongs to the selected character.");
    if(state.assigning.scene) {if(!box)throw new Error("Choose a visible face from this scene.");await api(`${base()}/characters/${state.character}/assign`,json({scene_id:state.assigning.scene,head_box:box}));}
    else {const form=new FormData();form.append("image",state.assigning.file);if(box)form.append("head_box",JSON.stringify(box));await api(`${base()}/characters/${state.character}/references`,{method:"POST",body:form});}
    const key=state.character;await loadStory(state.story);selectCharacter(key);$("referenceStatus").textContent="Reference saved. The original identity reference is unchanged.";
  }));
  $("locationForm").addEventListener("submit",action(async()=>{if(!state.story)throw new Error("Choose a story first.");const form=new FormData();form.append("name",$("locationName").value);form.append("description",$("locationDescription").value);const photo=$("locationPhoto").files[0];if(photo)form.append("image",photo);await api(`${base()}/locations`,{method:"POST",body:form});$("locationForm").reset();await loadStory(state.story);}));
  async function hardware() {try {const stats=await api("/api/windows/system-stats");$("storyHardware").textContent=stats.gpu?`${stats.gpu.name.replace("NVIDIA GeForce ","")} · ${stats.gpu.vram_used_gb}/${stats.gpu.vram_total_gb} GB` : "Local ComfyUI";}catch{$("storyHardware").textContent="Local ComfyUI";}}
  const session=crypto.randomUUID().replaceAll("-","");const heartbeat=()=>fetch("/api/session/ping",json({session_id:session,hidden:document.hidden})).catch(()=>{});

  function cleanStorySession() {
    try {
      localStorage.removeItem("qwen-active-story");
      sessionStorage.clear();
    } catch {}
    if ($("scenePrompt")) $("scenePrompt").value = "";
    if ($("nextPrompt")) $("nextPrompt").value = "";
    if ($("characterPrompt")) $("characterPrompt").value = "";
    ["sequencePrompt1", "sequencePrompt2", "sequencePrompt3", "sequencePrompt4", "sequencePrompt5"].forEach(id => {
      if ($(id)) $(id).value = "";
    });
    if ($("sceneFigure")) $("sceneFigure").hidden = true;
    if ($("sceneActions")) $("sceneActions").hidden = true;
    if ($("sceneEmpty")) $("sceneEmpty").hidden = false;
    if ($("sceneTimeline")) $("sceneTimeline").replaceChildren();
    if ($("historyEmpty")) $("historyEmpty").hidden = false;
    try {
      navigator.sendBeacon("/api/session/close",new Blob([JSON.stringify({session_id:session})],{type:"application/json"}));
    } catch {}
  }
  $("refreshStory")?.addEventListener("click", () => {
    cleanStorySession();
    window.location.reload();
  });
  window.addEventListener("pagehide", cleanStorySession);
  window.addEventListener("beforeunload", cleanStorySession);
  window.addEventListener("pageshow", cleanStorySession);

  void heartbeat();setInterval(heartbeat,20000);void hardware();setInterval(hardware,10000);
  void action(async()=>{
    const config=await api("/api/stories/config");
    state.storyEnhancerReady=config.story_enhancer?.profile==="qwen21-story-v1";
    if(!state.storyEnhancerReady){$("storyEnhanceHint").textContent="Restart the Studio backend to enable the Qwen story enhancer.";$("enhanceStory").title="Restart Studio to enable story enhancement";}
    const modeButton=document.querySelector(`[data-story-mode="${config.default_mode}"]`);
    if(modeButton)modeButton.click();
    if([...$("storyAspect").options].some(o=>o.value===config.default_aspect_ratio))$("storyAspect").value=config.default_aspect_ratio;
    const sequenceOption=$("storyWorkflow").querySelector('option[value="sequence"]');sequenceOption.disabled=config.sequence_max_scenes!==5;
    if(sequenceOption.disabled)sequenceOption.textContent="5-scene sequence · restart Studio to enable";
    try {
      localStorage.removeItem("qwen-active-story");
      sessionStorage.removeItem("qwen-active-story");
    } catch {}
    await loadStories();
    workflow();
    updateBusy();
  })();
})();
