"use strict";

(() => {
  const $ = id => document.getElementById(id);
  const state = {task: "generate", mode: "balanced", source: null, sourceUrl: null, reference: null, maskReady: false, tool: "paint", undo: [], drawing: false, lastPoint: null, result: null, jobId: null, submitting: false, polling: false, maskBusy: false, modes: {fast: {resolution: 512, steps: 16}, balanced: {resolution: 768, steps: 28}, ultra: {resolution: 1024, steps: 36}}};
  const mask = document.createElement("canvas");
  const maskCtx = mask.getContext("2d", {willReadFrequently: true});
  const overlay = $("maskOverlay");
  const overlayCtx = overlay.getContext("2d");
  const modeDescriptions = {
    fast: "A smaller, lighter pass for quick drafts and testing an idea.",
    balanced: "A higher resolution pass for a strong balance of detail and resource use.",
    ultra: "The largest mode with more sampling for fine detail. Uses more time and memory."
  };
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  const formatMode = value => value ? value[0].toUpperCase() + value.slice(1) : "";
  function errorText(data) {
    const detail = data?.detail ?? data?.error ?? data?.message;
    if (Array.isArray(detail)) return detail.map(item => item.msg || String(item)).join("; ");
    return typeof detail === "string" ? detail : detail ? JSON.stringify(detail) : "The request could not be completed.";
  }
  async function api(url, options) {
    const response = await fetch(url, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(errorText(data));
    return data;
  }
  function localImageUrl(value) {
    if (!value) return null;
    try { const url = new URL(value, location.href); return url.origin === location.origin && /^\/(outputs|assets|uploads|api\/images|api\/files)\//.test(url.pathname) ? url.href : null; } catch { return null; }
  }
  function showError(message, output = false) {
    const box = $(output ? "outputError" : "formError"); box.textContent = message; box.hidden = false;
  }
  function clearErrors() { $("formError").hidden = true; $("outputError").hidden = true; }
  function savePreference() {
    try { localStorage.setItem("qwen-studio-settings", JSON.stringify({mode: state.mode, task: state.task, aspect_ratio: $("aspectRatio").value, prompt: $("prompt").value, seed: $("seed").value, num_steps: $("steps").value, region: $("editRegion").value, preserve: $("preserve").checked, is_transparent: $("transparent").checked})); } catch { /* Private browsing can disable storage. */ }
  }
  function updateRun() { $("runLabel").textContent = state.submitting ? "Submitting…" : `${state.task === "edit" ? "Edit image" : "Generate"} · ${formatMode(state.mode)}`; $("runButton").disabled = !!state.jobId || state.submitting; }
  function chooseMode(mode) {
    if (!state.modes[mode]) return;
    state.mode = mode;
    document.querySelectorAll("[data-mode]").forEach(button => { const selected = button.dataset.mode === mode; button.classList.toggle("selected", selected); button.setAttribute("aria-pressed", String(selected)); });
    $("modeDescription").textContent = modeDescriptions[mode] + (mode === "ultra" && state.task === "edit" ? " Reviewed masks enable a focused crop edit with surrounding context." : "");
    $("steps").placeholder = `${state.modes[mode].steps || state.modes[mode].num_steps || "Mode"} default`;
    updateRun(); savePreference();
  }
  function chooseTask(task) {
    state.task = task;
    document.querySelectorAll("[data-task]").forEach(button => { const selected = button.dataset.task === task; button.classList.toggle("selected", selected); button.setAttribute("aria-selected", String(selected)); button.tabIndex = selected ? 0 : -1; });
    document.querySelectorAll(".edit-only").forEach(element => { element.hidden = task !== "edit"; });
    $("styleChips").hidden = task === "edit";
    $("promptLabel").textContent = task === "edit" ? "What would you like to change?" : "Describe your image";
    $("prompt").placeholder = task === "edit" ? "Change my outfit to a black suit. Keep my face, pose, lighting and background unchanged…" : "A cinematic portrait in warm evening light, rich natural colors and fine detail…";
    $("promptHint").textContent = task === "edit" ? "Describe what to change and what should stay. Paint the area for precise preservation." : "Be specific about the subject, scene, lighting and style.";
    $("controlBody").setAttribute("aria-labelledby", task === "edit" ? "editTab" : "createTab");
    $("enhancePrompt").hidden = task === "edit";
    document.querySelector(".generation-settings").hidden = task === "edit";
    $("modeDescription").textContent = modeDescriptions[state.mode] + (state.mode === "ultra" && task === "edit" ? " Reviewed masks enable a focused crop edit with surrounding context." : "");
    updateRun(); savePreference();
  }
  function invalidateMask() { state.maskReady = false; $("maskAccepted").hidden = true; $("acceptMask").textContent = "Use reviewed mask"; }
  function refreshMaskTools() {
    const precise = $("preserve").checked && $("editRegion").value !== "global";
    $("maskControls").hidden = !precise;
    $("brushTools").hidden = !state.source;
    overlay.style.pointerEvents = precise ? "auto" : "none";
    $("findMask").disabled = !state.source || state.maskBusy || $("editRegion").value === "manual";
    if (!precise) $("maskMessage").textContent = "Whole image editing uses the model's reference conditioning. Identity and background may change.";
  }
  function drawOverlay() {
    overlayCtx.clearRect(0, 0, overlay.width, overlay.height);
    overlayCtx.drawImage(mask, 0, 0);
    overlayCtx.globalCompositeOperation = "source-in";
    overlayCtx.fillStyle = "rgba(231, 93, 195, 0.48)";
    overlayCtx.fillRect(0, 0, overlay.width, overlay.height);
    overlayCtx.globalCompositeOperation = "source-over";
  }
  function loadImage(url) { return new Promise((resolve, reject) => { const img = new Image(); img.onload = () => resolve(img); img.onerror = () => reject(new Error("The image could not be opened. Try a PNG, JPEG or WebP file.")); img.src = url; }); }
  function validateFile(file) {
    if (!file || !["image/png", "image/jpeg", "image/webp"].includes(file.type)) throw new Error("Choose a PNG, JPEG or WebP image.");
    if (file.size > 25 * 1024 * 1024) throw new Error("This image exceeds 25 MB. Export a smaller PNG, JPEG or WebP and try again.");
  }
  async function setSource(file, normalizedUrl = null) {
    validateFile(file);
    const url = normalizedUrl || URL.createObjectURL(file);
    let img;
    try { img = await loadImage(url); } catch (error) { if (!normalizedUrl) URL.revokeObjectURL(url); throw error; }
    if (state.sourceUrl?.startsWith("blob:")) URL.revokeObjectURL(state.sourceUrl);
    state.source = file; state.sourceUrl = url; state.undo = [];
    $("sourcePreview").src = url;
    $("sourceFileLabel").textContent = file.name;
    $("sourceDimensions").textContent = `${img.naturalWidth} × ${img.naturalHeight}`;
    mask.width = overlay.width = img.naturalWidth; mask.height = overlay.height = img.naturalHeight;
    $("maskEditor").hidden = false; $("removeSource").hidden = false;
    invalidateMask(); drawOverlay(); refreshMaskTools();
    $("maskMessage").textContent = "Find the edit area automatically or paint it directly. Review and accept the mask before editing.";
  }
  function clearSource() {
    if (state.sourceUrl?.startsWith("blob:")) URL.revokeObjectURL(state.sourceUrl);
    state.source = null; state.sourceUrl = null; state.undo = [];
    $("sourceInput").value = ""; $("sourceFileLabel").textContent = "Choose an image or drop it here";
    $("maskEditor").hidden = true; $("removeSource").hidden = true;
    mask.width = mask.height = overlay.width = overlay.height = 1;
    invalidateMask(); refreshMaskTools();
  }
  function stashUndo() { state.undo.push(mask.toDataURL("image/png")); if (state.undo.length > 8) state.undo.shift(); }
  function pointerPoint(event) { const rect = overlay.getBoundingClientRect(); return {x: (event.clientX - rect.left) * mask.width / rect.width, y: (event.clientY - rect.top) * mask.height / rect.height}; }
  function paint(point) {
    const size = Math.max(2, Math.min(mask.width, mask.height) * Number($("brushSize").value) / 100);
    maskCtx.globalCompositeOperation = state.tool === "erase" ? "destination-out" : "source-over";
    maskCtx.fillStyle = "white"; maskCtx.strokeStyle = "white"; maskCtx.lineWidth = size; maskCtx.lineCap = "round"; maskCtx.lineJoin = "round";
    maskCtx.beginPath(); maskCtx.moveTo(state.lastPoint?.x ?? point.x, state.lastPoint?.y ?? point.y); maskCtx.lineTo(point.x, point.y); maskCtx.stroke();
    maskCtx.beginPath(); maskCtx.arc(point.x, point.y, size / 2, 0, Math.PI * 2); maskCtx.fill();
    maskCtx.globalCompositeOperation = "source-over"; state.lastPoint = point; invalidateMask(); drawOverlay();
  }
  function maskHasContent() { const pixels = maskCtx.getImageData(0, 0, mask.width, mask.height).data; for (let i = 3; i < pixels.length; i += 4) if (pixels[i] > 20) return true; return false; }
  async function importMask(url) {
    const img = await loadImage(url);
    if (img.naturalWidth !== mask.width || img.naturalHeight !== mask.height) throw new Error("Mask dimensions must match the original image. Export a white-on-black PNG at the original size.");
    stashUndo(); maskCtx.clearRect(0, 0, mask.width, mask.height); maskCtx.drawImage(img, 0, 0);
    const pixels = maskCtx.getImageData(0, 0, mask.width, mask.height);
    for (let i = 0; i < pixels.data.length; i += 4) { const luminance = Math.round((pixels.data[i] + pixels.data[i + 1] + pixels.data[i + 2]) / 3) * pixels.data[i + 3] / 255; pixels.data[i] = pixels.data[i + 1] = pixels.data[i + 2] = 255; pixels.data[i + 3] = luminance; }
    maskCtx.putImageData(pixels, 0, 0); invalidateMask(); drawOverlay();
  }
  function exportMask() {
    const exportCanvas = document.createElement("canvas"); exportCanvas.width = mask.width; exportCanvas.height = mask.height;
    const context = exportCanvas.getContext("2d"); context.fillStyle = "black"; context.fillRect(0, 0, mask.width, mask.height); context.drawImage(mask, 0, 0);
    return new Promise((resolve, reject) => exportCanvas.toBlob(blob => blob ? resolve(blob) : reject(new Error("The mask could not be exported.")), "image/png"));
  }
  async function findMask() {
    clearErrors();
    if (!state.source) { showError("Choose your original image first."); return; }
    if (!$("prompt").value.trim()) { showError("Describe the change before finding its edit area."); $("prompt").focus(); return; }
    const requestedSource = state.source, requestedPrompt = $("prompt").value.trim(), requestedRegion = $("editRegion").value;
    const form = new FormData(); form.append("image", requestedSource); form.append("prompt", requestedPrompt); form.append("region", requestedRegion);
    state.maskBusy = true; $("findMask").textContent = "Finding edit area…"; refreshMaskTools();
    try {
      const data = await api("/api/mask", {method: "POST", body: form});
      if (state.source !== requestedSource || $("prompt").value.trim() !== requestedPrompt || $("editRegion").value !== requestedRegion) throw new Error("Your source or instruction changed during detection. Find the edit area again.");
      const sourceUrl = localImageUrl(data.source_url), maskUrl = localImageUrl(data.mask_url);
      if (!maskUrl) throw new Error("No usable mask was returned. Paint the target area or upload a mask PNG.");
      if (sourceUrl) await setSource(state.source, sourceUrl);
      await importMask(maskUrl);
      $("maskMessage").textContent = [data.message || "Review the highlighted area, correct it with the brush, then choose Use reviewed mask.", ...(data.warnings || [])].join(" ");
      $("maskEditor").scrollIntoView({block: "nearest", behavior: "auto"});
    } catch (error) { $("maskMessage").textContent = `${error.message} Paint the area manually and review it before editing.`; }
    finally { state.maskBusy = false; $("findMask").textContent = "Find edit area"; refreshMaskTools(); }
  }
  async function fetchCapabilities() {
    $("refreshBackend").disabled = true;
    try {
      const data = await api("/api/capabilities");
      const backend = data.backend || data.comfyui || {};
      const ready = data.ready ?? data.backend_ready ?? backend.ready ?? backend.available ?? data.available ?? (backend.status === "ready");
      $("backendStatus").textContent = ready ? "Local Qwen ready" : "ComfyUI offline";
      $("backendStatus").className = `backend-status ${ready ? "ready" : "offline"}`;
      $("backendStatus").title = typeof backend === "string" ? backend : backend.error || data.message || "";
      const profiles = data.mode_info || data.modes || data.profiles;
      if (profiles) {
        const list = Array.isArray(profiles) ? Object.fromEntries(profiles.map(mode => [mode.id || mode.name, mode])) : profiles;
        for (const key of ["fast", "balanced", "ultra"]) {
          if (list[key]) state.modes[key] = {...state.modes[key], ...list[key]};
          const mode = state.modes[key]; $(key + "Spec").textContent = `${mode.resolution || mode.base_resolution || mode.size || ""} · ${mode.steps || mode.num_steps} steps`;
        }
        chooseMode(state.mode);
      }
      const segmentation = data.segmentation || data.segmenter || data.masking;
      if (segmentation?.available === false) $("findMask").title = "Automatic segmentation unavailable; manual masks remain available.";
    } catch (error) { $("backendStatus").textContent = "Backend unavailable"; $("backendStatus").className = "backend-status offline"; $("backendStatus").title = error.message; }
    finally { $("refreshBackend").disabled = false; }
  }
  async function enhancePrompt() {
    if (!$("prompt").value.trim()) { showError("Enter a prompt before enhancing it."); $("prompt").focus(); return; }
    $("enhancePrompt").disabled = true; $("enhancePrompt").textContent = "Enhancing…";
    try { const data = await api("/api/enhance", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({prompt: $("prompt").value.trim()})}); if (!data.enhanced) throw new Error("The enhancer returned no prompt."); $("prompt").value = data.enhanced; savePreference(); }
    catch (error) { showError(error.message); }
    finally { $("enhancePrompt").disabled = false; $("enhancePrompt").textContent = "Enhance prompt"; }
  }
  function setStoredJob(id) { try { if (id) localStorage.setItem("qwen-studio-job", id); else localStorage.removeItem("qwen-studio-job"); } catch { /* Storage is optional. */ } }
  function finishJob() { state.jobId = null; setStoredJob(null); $("jobPanel").hidden = true; updateRun(); }
  function showJob(data) {
    $("jobPanel").hidden = false;
    $("jobPhase").textContent = formatMode(data.phase || data.status || "Running");
    $("jobDetail").textContent = data.message || (data.status === "queued" ? "Waiting for the local GPU. Queued jobs run one at a time." : "Qwen is processing your request. You can leave this page and reconnect later.");
    if (Number.isFinite(data.progress)) { $("jobProgress").max = data.progress > 1 ? 100 : 1; $("jobProgress").value = data.progress; } else $("jobProgress").removeAttribute("value");
    const elapsed = data.elapsed_sec ?? data.elapsed;
    $("jobElapsed").textContent = Number.isFinite(elapsed) ? `${Math.floor(elapsed / 60)}m ${Math.floor(elapsed % 60)}s elapsed · loading and offloading can take time.` : "The first run can take longer while models load.";
  }
  async function pollJob(id) {
    if (state.polling) return;
    state.polling = true;
    let failures = 0;
    try {
      while (state.jobId === id) {
        try {
          const job = await api(`/api/jobs/${encodeURIComponent(id)}`); failures = 0; showJob(job);
          if (["succeeded", "completed"].includes(job.status)) { await displayResult(job.result || job, id); finishJob(); break; }
          if (job.status === "failed") { showError(errorText(job), true); finishJob(); break; }
          if (job.status === "cancelled") { $("outputError").textContent = "The request was cancelled."; $("outputError").hidden = false; finishJob(); break; }
        } catch (error) {
          failures += 1;
          if (failures >= 5) { showError(`Connection lost: ${error.message} Refresh this page to reconnect to the saved job.`, true); $("jobPhase").textContent = "Connection interrupted"; break; }
          $("jobDetail").textContent = "Reconnecting to your local job…";
        }
        await sleep(1500);
      }
    } finally { state.polling = false; }
  }
  async function submitJob(event) {
    event.preventDefault(); if (state.jobId || state.submitting) return; clearErrors();
    const prompt = $("prompt").value.trim();
    if (!prompt) { showError("Describe the image or change you want first."); $("prompt").focus(); return; }
    if (state.task === "edit" && !state.source) { showError("Choose an original image to edit."); $("sourceInput").focus(); return; }
    if (!$("seed").validity.valid || !$("steps").validity.valid) { showError("Use a nonnegative whole-number seed and between 1 and 100 steps."); return; }
    const precise = state.task === "edit" && $("preserve").checked && $("editRegion").value !== "global";
    if (precise && !state.maskReady) { showError("Find or paint an edit area, review the pink highlight, then choose Use reviewed mask. Select Whole image for a broader edit."); $("findMask").focus(); return; }
    if (state.maskBusy) { showError("Wait for the edit area to finish loading, then review the mask."); return; }
    state.submitting = true; updateRun();
    const form = new FormData(); form.append("task", state.task); form.append("prompt", prompt); form.append("mode", state.mode); form.append("aspect_ratio", $("aspectRatio").value); form.append("is_transparent", String($("transparent").checked)); form.append("preserve", String(precise)); form.append("region", precise ? $("editRegion").value : "global");
    const seed = $("seed").value === "" ? Math.floor(Math.random() * 4294967295) : Number($("seed").value); form.append("seed", String(seed));
    if ($("steps").value !== "") form.append("num_steps", $("steps").value);
    if (state.task === "edit") { form.append("image", state.source); if (precise) form.append("mask", await exportMask(), "reviewed-mask.png"); if (state.reference) form.append("reference", state.reference); }
    savePreference();
    try { const data = await api("/api/jobs", {method: "POST", body: form}); const id = data.job_id || data.id; if (!id) throw new Error("The server returned no job identifier."); state.jobId = id; setStoredJob(id); showJob(data); updateRun(); void pollJob(id); }
    catch (error) { showError(error.message); }
    finally { state.submitting = false; updateRun(); }
  }
  function updateCompare() { const position = Number($("compareRange").value); $("beforeImage").style.clipPath = `inset(0 ${100 - position}% 0 0)`; $("comparisonLine").style.left = `${position}%`; }
  async function displayResult(result, jobId = null) {
    const url = localImageUrl(result.image_url || result.url);
    if (!url) throw new Error("The result does not contain a usable local image URL.");
    const img = await loadImage(url);
    state.result = {...result, image_url: url, job_id: jobId || result.job_id};
    $("resultImage").src = url; $("emptyState").hidden = true; $("comparison").hidden = false; $("resultTools").hidden = false; $("preview").classList.add("has-image");
    $("workspaceTitle").textContent = "Your studio output"; $("resultSize").textContent = `${img.naturalWidth} × ${img.naturalHeight}`;
    $("downloadImage").href = url;
    $("downloadImage").download = `qwen-${state.result.job_id || "image"}${new URL(url).pathname.match(/\.(png|jpe?g|webp)$/i)?.[0] || ".png"}`;
    $("downloadImage").textContent = /\.png$/i.test(new URL(url).pathname) ? "Download PNG" : "Download image";
    if ($("downloadWorkflow")) $("downloadWorkflow").hidden = true;
    const metadata = result.metadata || {};
    $("resultMetadata").textContent = JSON.stringify(metadata, null, 2);
    const elapsed = metadata.elapsed_sec ?? metadata.duration_sec;
    const summary = [metadata.mode && formatMode(metadata.mode), metadata.steps && `${metadata.steps} steps`, metadata.seed !== undefined && `Seed ${metadata.seed}`, Number.isFinite(elapsed) && `${Math.round(elapsed)}s`, metadata.preservation?.protected_pixels_unchanged === true && "Protected pixels preserved"];
    $("resultSummary").textContent = summary.filter(Boolean).join(" · ");
    const warnings = result.warnings || metadata.warnings || [];
    $("resultWarnings").hidden = !warnings.length; $("resultWarnings").textContent = Array.isArray(warnings) ? warnings.join(" ") : String(warnings);
    const sourceUrl = localImageUrl(result.source_url || metadata.source_url);
    const hasBefore = !!sourceUrl;
    $("beforeImage").hidden = $("beforeBadge").hidden = $("afterBadge").hidden = $("comparisonLine").hidden = $("comparisonControls").hidden = !hasBefore;
    if (sourceUrl) $("beforeImage").src = sourceUrl;
    $("toggleCompare").setAttribute("aria-pressed", "true"); $("toggleCompare").textContent = "Hide original"; $("compareRange").value = 50; updateCompare();
  }
  async function imageToSource(url) {
    const safeUrl = localImageUrl(url); if (!safeUrl) throw new Error("This image is not available locally.");
    const response = await fetch(safeUrl); if (!response.ok) throw new Error("The saved image could not be loaded.");
    const blob = await response.blob(); const extension = blob.type === "image/jpeg" ? "jpg" : blob.type === "image/webp" ? "webp" : "png";
    await setSource(new File([blob], `studio-source.${extension}`, {type: blob.type || "image/png"}), safeUrl); chooseTask("edit");
    $("prompt").focus(); $("sourceSection").scrollIntoView({block: "nearest"});
  }
  function bindUpload(input, drop, callback) {
    async function select(file) { if (!file) return; clearErrors(); try { await callback(file); } catch (error) { showError(error.message); } }
    $(input).addEventListener("change", event => void select(event.target.files[0]));
    $(drop).addEventListener("dragover", event => { event.preventDefault(); $(drop).classList.add("dragging"); });
    $(drop).addEventListener("dragleave", () => $(drop).classList.remove("dragging"));
    $(drop).addEventListener("drop", event => { event.preventDefault(); $(drop).classList.remove("dragging"); void select(event.dataTransfer.files[0]); });
  }
  document.querySelectorAll("[data-task]").forEach(button => { button.addEventListener("click", () => chooseTask(button.dataset.task)); button.addEventListener("keydown", event => { if (["ArrowLeft", "ArrowRight"].includes(event.key)) { event.preventDefault(); chooseTask(state.task === "generate" ? "edit" : "generate"); $(state.task === "edit" ? "editTab" : "createTab").focus(); } }); });
  document.querySelectorAll("[data-mode]").forEach(button => button.addEventListener("click", () => chooseMode(button.dataset.mode)));
  document.querySelectorAll("[data-style]").forEach(button => button.addEventListener("click", () => { $("prompt").value += ($("prompt").value.trim() ? ", " : "") + button.dataset.style; savePreference(); }));
  document.querySelectorAll("[data-example]").forEach(button => button.addEventListener("click", () => { $("prompt").value = button.dataset.example; invalidateMask(); savePreference(); }));
  $("stickerPreset").addEventListener("click", () => { $("transparent").checked = true; $("prompt").value = $("prompt").value.trim() || "A cute 3D mascot sticker, isolated subject, soft studio lighting"; savePreference(); });
  $("studioForm").addEventListener("submit", event => void submitJob(event).catch(error => { state.submitting = false; showError(error.message); updateRun(); }));
  $("studioForm").addEventListener("change", savePreference);
  $("prompt").addEventListener("input", () => { invalidateMask(); savePreference(); });
  $("editRegion").addEventListener("change", () => { invalidateMask(); if ($("editRegion").value === "global") $("preserve").checked = false; refreshMaskTools(); if ($("editRegion").value === "manual") $("maskMessage").textContent = "Paint the area you want to change, then choose Use reviewed mask."; savePreference(); });
  $("preserve").addEventListener("change", () => { if ($("preserve").checked && $("editRegion").value === "global") $("editRegion").value = "manual"; refreshMaskTools(); savePreference(); });
  $("enhancePrompt").addEventListener("click", () => void enhancePrompt());
  $("refreshBackend").addEventListener("click", () => void fetchCapabilities());
  $("findMask").addEventListener("click", () => void findMask());
  $("removeSource").addEventListener("click", clearSource);
  bindUpload("sourceInput", "sourceDrop", file => setSource(file));
  bindUpload("referenceInput", "referenceDrop", file => { validateFile(file); state.reference = file; $("referenceFileLabel").textContent = file.name; $("removeReference").hidden = false; });
  $("removeReference").addEventListener("click", () => { state.reference = null; $("referenceInput").value = ""; $("referenceFileLabel").textContent = "Choose reference image"; $("removeReference").hidden = true; });
  $("maskInput").addEventListener("change", async event => { const file = event.target.files[0]; if (!file) return; const url = URL.createObjectURL(file); try { if (!state.source) throw new Error("Choose your original image before uploading a mask."); await importMask(url); } catch (error) { showError(error.message); } finally { URL.revokeObjectURL(url); event.target.value = ""; } });
  for (const tool of ["paint", "erase"]) $(tool + "Tool").addEventListener("click", () => { state.tool = tool; for (const item of ["paint", "erase"]) { $(item + "Tool").classList.toggle("selected", item === tool); $(item + "Tool").setAttribute("aria-pressed", String(item === tool)); } });
  $("brushSize").addEventListener("input", () => { $("brushValue").value = $("brushSize").value + "%"; });
  $("clearMask").addEventListener("click", () => { stashUndo(); maskCtx.clearRect(0, 0, mask.width, mask.height); invalidateMask(); drawOverlay(); });
  $("undoMask").addEventListener("click", async () => { const previous = state.undo.pop(); if (!previous) return; const img = await loadImage(previous); maskCtx.clearRect(0, 0, mask.width, mask.height); maskCtx.drawImage(img, 0, 0); invalidateMask(); drawOverlay(); });
  $("acceptMask").addEventListener("click", () => { if (!state.source || !maskHasContent()) { showError("Paint or detect an editable area first. An empty mask cannot apply an edit."); return; } state.maskReady = true; $("maskAccepted").hidden = false; $("acceptMask").textContent = "Mask accepted"; clearErrors(); });
  overlay.addEventListener("pointerdown", event => { if (!state.source || state.maskBusy) return; event.preventDefault(); overlay.setPointerCapture(event.pointerId); stashUndo(); state.drawing = true; state.lastPoint = null; paint(pointerPoint(event)); });
  overlay.addEventListener("pointermove", event => { if (state.drawing) paint(pointerPoint(event)); });
  for (const event of ["pointerup", "pointercancel", "lostpointercapture"]) overlay.addEventListener(event, () => { state.drawing = false; state.lastPoint = null; });
  $("compareRange").addEventListener("input", updateCompare);
  $("toggleCompare").addEventListener("click", () => { const hidden = !$("beforeImage").hidden; $("beforeImage").hidden = $("comparisonLine").hidden = $("beforeBadge").hidden = $("afterBadge").hidden = hidden; $("toggleCompare").setAttribute("aria-pressed", String(!hidden)); $("toggleCompare").textContent = hidden ? "Show original" : "Hide original"; });
  $("editResult").addEventListener("click", () => { clearErrors(); if (state.result) void imageToSource(state.result.image_url).catch(error => showError(error.message)); });
  $("cancelJob").addEventListener("click", async () => { if (!state.jobId) return; $("cancelJob").disabled = true; try { await api(`/api/jobs/${encodeURIComponent(state.jobId)}/cancel`, {method: "POST"}); $("jobDetail").textContent = "Cancellation requested. Waiting for the backend to stop safely…"; } catch (error) { showError(error.message, true); } finally { $("cancelJob").disabled = false; } });
  document.addEventListener("keydown", event => { if ((event.ctrlKey || event.metaKey) && event.key === "Enter" && !state.jobId) { event.preventDefault(); $("studioForm").requestSubmit(); } });
  try {
    const saved = JSON.parse(localStorage.getItem("qwen-studio-settings") || "null");
    if (saved) { $("prompt").value = saved.prompt || ""; $("aspectRatio").value = saved.aspect_ratio || "1:1"; $("seed").value = saved.seed ?? ""; $("steps").value = saved.num_steps ?? ""; $("preserve").checked = saved.preserve !== false; $("editRegion").value = saved.region || "auto"; $("transparent").checked = !!saved.is_transparent; chooseMode(saved.mode || "balanced"); chooseTask(saved.task || "generate"); }
    const storedJob = localStorage.getItem("qwen-studio-job"); if (storedJob) { state.jobId = storedJob; showJob({status: "running", phase: "Reconnecting"}); updateRun(); void pollJob(storedJob); }
  } catch { /* Settings are a convenience, not a requirement. */ }
  refreshMaskTools(); void fetchCapabilities();
})();
