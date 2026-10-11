"use strict";

(() => {
  const $ = id => document.getElementById(id);
  const state = {
    task: "generate",
    mode: "balanced",
    source: null,
    sourceUrl: null,
    reference: null,
    pose: null,
    poseUrl: null,
    repairParent: null,
    maskReady: false,
    tool: "paint",
    undo: [],
    drawing: false,
    lastPoint: null,
    result: null,
    jobId: null,
    batchId: null,
    batchData: null,
    selectedBatchJobId: null,
    submitting: false,
    polling: false,
    maskBusy: false,
    enhancing: false,
    prompts: [""],
    promptUndos: [null],
    promptChecks: [],
    activePromptIndex: 0,
    modes: {fast: {resolution: 512, steps: 16}, balanced: {resolution: 768, steps: 28}, ultra: {resolution: 1024, steps: 36}}
  };
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
    try {
      const url = new URL(value, location.href);
      return url.origin === location.origin && /^\/(outputs|assets|uploads|api\/images|api\/files)\//.test(url.pathname) ? url.href : null;
    } catch {
      return null;
    }
  }

  function showError(message, output = false) {
    const box = $(output ? "outputError" : "formError");
    box.textContent = message;
    box.hidden = false;
  }

  function clearErrors() {
    $("formError").hidden = true;
    $("outputError").hidden = true;
  }

  function getActivePromptTextarea() {
    const textareas = document.querySelectorAll(".prompt-textarea");
    if (state.activePromptIndex < textareas.length) {
      return textareas[state.activePromptIndex];
    }
    return $("prompt") || textareas[0];
  }

  function getPromptValues() {
    const textareas = document.querySelectorAll(".prompt-textarea");
    if (!textareas.length) return state.prompts;
    const values = [];
    textareas.forEach(t => values.push(t.value));
    return values;
  }

  function savePreference() {
    try {
      localStorage.setItem("qwen-studio-settings", JSON.stringify({
        mode: state.mode,
        task: state.task,
        aspect_ratio: $("aspectRatio").value,
        seed: $("seed").value,
        num_steps: $("steps").value,
        region: $("editRegion").value,
        preserve: $("preserve").checked,
        is_transparent: $("transparent").checked,
        lora_enabled: $("loraToggle") ? $("loraToggle").checked : true,
        lora_strength: $("loraStrength") ? $("loraStrength").value : "0.80",
        enhancer: state.enhancer || "dolphin",
        enhancement_style: $("enhancementStyle").value
      }));
    } catch {
      /* Private browsing can disable storage. */
    }
  }

  function updateRun() {
    const active = !!state.jobId || !!state.batchId || state.submitting || state.enhancing;
    const promptCount = getPromptValues().map(p => p.trim()).filter(Boolean).length;
    const countLabel = promptCount > 1 ? ` (${promptCount})` : "";
    $("runLabel").textContent = state.submitting
      ? "Submitting…"
      : `${state.task === "edit" ? "Edit image" : "Generate"}${countLabel} · ${formatMode(state.mode)}`;
    $("runButton").disabled = active;
    $("repairResult").disabled = active;
    $("poseEnabled").disabled = active;
    $("poseOrder").disabled = active;
    $("enhanceAllBtn").disabled = active;
    const clearAllBtn = $("clearAllPromptsBtn");
    if (clearAllBtn) clearAllBtn.disabled = active;
    $("addPromptBtn").disabled = state.prompts.length >= 5 || active;
    document.querySelectorAll(".card-enhance-btn, .card-alt-enhance-btn").forEach(btn => { btn.disabled = active; });
    document.querySelectorAll(".card-clear-btn").forEach(btn => { btn.disabled = active; });
    document.querySelectorAll(".card-remove-btn").forEach(btn => { btn.disabled = active; });
    document.querySelectorAll("[data-task]").forEach(button => { button.disabled = state.enhancing; });
  }

  function chooseMode(mode) {
    if (!state.modes[mode]) return;
    state.mode = mode;
    document.querySelectorAll("[data-mode]").forEach(button => {
      const selected = button.dataset.mode === mode;
      button.classList.toggle("selected", selected);
      button.setAttribute("aria-pressed", String(selected));
    });
    $("modeDescription").textContent = modeDescriptions[mode] + (mode === "ultra" && state.task === "edit" ? " Reviewed masks enable a focused crop edit with surrounding context." : "");
    $("steps").placeholder = `${state.modes[mode].steps || state.modes[mode].num_steps || "Mode"} default`;
    updateRun();
    savePreference();
  }

  function chooseTask(task) {
    if (state.task !== task) $("poseReviewed").checked = false;
    state.task = task;
    document.querySelectorAll("[data-task]").forEach(button => {
      const selected = button.dataset.task === task;
      button.classList.toggle("selected", selected);
      button.setAttribute("aria-selected", String(selected));
      button.tabIndex = selected ? 0 : -1;
    });
    document.querySelectorAll(".edit-only").forEach(element => { element.hidden = task !== "edit"; });
    $("styleChips").hidden = task === "edit";
    $("promptLabel").textContent = task === "edit" ? "What would you like to change?" : "Describe your image";
    $("promptHint").textContent = task === "edit" ? "Name the person, action and body part to change. Use whole-image editing for movements; paint a mask for precise preservation." : "Name who acts, who receives the action, the contact body part and any left/right direction. Enhance checks the wording.";
    $("controlBody").setAttribute("aria-labelledby", task === "edit" ? "editTab" : "createTab");
    document.querySelector(".generation-settings").hidden = task === "edit";
    $("modeDescription").textContent = modeDescriptions[state.mode] + (state.mode === "ultra" && task === "edit" ? " Reviewed masks enable a focused crop edit with surrounding context." : "");

    document.querySelectorAll(".prompt-textarea").forEach((textarea, i) => {
      textarea.placeholder = task === "edit"
        ? (i === 0 ? "Change my outfit to a black suit. Keep my face, pose, lighting and background unchanged…" : `Edit variation ${i + 1}…`)
        : (i === 0 ? "A cinematic portrait in warm evening light, rich natural colors and fine detail…" : `Prompt variation ${i + 1}…`);
    });

    updateRun();
    savePreference();
  }

  function invalidateMask() {
    state.maskReady = false;
    $("maskAccepted").hidden = true;
    $("acceptMask").textContent = "Use manual mask";
  }

  function refreshMaskTools() {
    const precise = $("preserve").checked && $("editRegion").value !== "global";
    const manualOpen = !!$("manualMaskDetails")?.open;
    $("maskControls").hidden = !precise;
    $("brushTools").hidden = !state.source;
    overlay.style.pointerEvents = (precise && manualOpen) ? "auto" : "none";
    $("findMask").disabled = !state.source || state.maskBusy || $("editRegion").value === "manual";
    if (!precise) {
      $("maskMessage").textContent = "Whole image editing uses the model's reference conditioning. Identity and background may change.";
    } else if (!state.maskReady) {
      $("maskMessage").textContent = "Auto detects supported clothes/accessory areas. Movement edits use the whole image; exact outside-pixel preservation requires a reviewed mask.";
    }
  }

  function drawOverlay() {
    overlayCtx.clearRect(0, 0, overlay.width, overlay.height);
    overlayCtx.drawImage(mask, 0, 0);
    overlayCtx.globalCompositeOperation = "source-in";
    overlayCtx.fillStyle = "rgba(231, 93, 195, 0.48)";
    overlayCtx.fillRect(0, 0, overlay.width, overlay.height);
    overlayCtx.globalCompositeOperation = "source-over";
  }

  function loadImage(url) {
    return new Promise((resolve, reject) => {
      const img = new Image();
      img.onload = () => resolve(img);
      img.onerror = () => reject(new Error("The image could not be opened. Try a PNG, JPEG or WebP file."));
      img.src = url;
    });
  }

  function validateFile(file) {
    if (!file || !["image/png", "image/jpeg", "image/webp"].includes(file.type)) throw new Error("Choose a PNG, JPEG or WebP image.");
    if (file.size > 25 * 1024 * 1024) throw new Error("This image exceeds 25 MB. Export a smaller PNG, JPEG or WebP and try again.");
  }

  async function setSource(file, normalizedUrl = null) {
    validateFile(file);
    const url = normalizedUrl || URL.createObjectURL(file);
    let img;
    try {
      img = await loadImage(url);
    } catch (error) {
      if (!normalizedUrl) URL.revokeObjectURL(url);
      throw error;
    }
    if (state.sourceUrl?.startsWith("blob:")) URL.revokeObjectURL(state.sourceUrl);
    state.source = file;
    $("poseReviewed").checked = false;
    state.repairParent = null;
    $("repairControls").hidden = true;
    state.sourceUrl = url;
    state.undo = [];
    $("sourcePreview").src = url;
    $("sourceFileLabel").textContent = file.name;
    $("sourceDimensions").textContent = `${img.naturalWidth} × ${img.naturalHeight}`;
    mask.width = overlay.width = img.naturalWidth;
    mask.height = overlay.height = img.naturalHeight;
    $("maskEditor").hidden = false;
    $("removeSource").hidden = false;
    invalidateMask();
    drawOverlay();
    refreshMaskTools();
    $("maskMessage").textContent = "Masking is automatic. Manual mask adjustment is optional.";
  }

  function clearSource() {
    if (state.sourceUrl?.startsWith("blob:")) URL.revokeObjectURL(state.sourceUrl);
    state.source = null;
    state.repairParent = null;
    $("repairControls").hidden = true;
    state.sourceUrl = null;
    state.undo = [];
    $("sourceInput").value = "";
    $("sourceFileLabel").textContent = "Choose an image or drop it here";
    $("maskEditor").hidden = true;
    $("removeSource").hidden = true;
    mask.width = mask.height = overlay.width = overlay.height = 1;
    invalidateMask();
    refreshMaskTools();
  }

  function stashUndo() {
    state.undo.push(mask.toDataURL("image/png"));
    if (state.undo.length > 8) state.undo.shift();
  }

  function pointerPoint(event) {
    const rect = overlay.getBoundingClientRect();
    return {
      x: (event.clientX - rect.left) * mask.width / rect.width,
      y: (event.clientY - rect.top) * mask.height / rect.height
    };
  }

  function paint(point) {
    const size = Math.max(2, Math.min(mask.width, mask.height) * Number($("brushSize").value) / 100);
    maskCtx.globalCompositeOperation = state.tool === "erase" ? "destination-out" : "source-over";
    maskCtx.fillStyle = "white";
    maskCtx.strokeStyle = "white";
    maskCtx.lineWidth = size;
    maskCtx.lineCap = "round";
    maskCtx.lineJoin = "round";
    maskCtx.beginPath();
    maskCtx.moveTo(state.lastPoint?.x ?? point.x, state.lastPoint?.y ?? point.y);
    maskCtx.lineTo(point.x, point.y);
    maskCtx.stroke();
    maskCtx.beginPath();
    maskCtx.arc(point.x, point.y, size / 2, 0, Math.PI * 2);
    maskCtx.fill();
    maskCtx.globalCompositeOperation = "source-over";
    state.lastPoint = point;
    invalidateMask();
    drawOverlay();
  }

  function maskHasContent() {
    const pixels = maskCtx.getImageData(0, 0, mask.width, mask.height).data;
    for (let i = 3; i < pixels.length; i += 4) {
      if (pixels[i] > 20) return true;
    }
    return false;
  }

  async function importMask(url) {
    const img = await loadImage(url);
    if (img.naturalWidth !== mask.width || img.naturalHeight !== mask.height) {
      throw new Error("Mask dimensions must match the original image. Export a white-on-black PNG at the original size.");
    }
    stashUndo();
    maskCtx.clearRect(0, 0, mask.width, mask.height);
    maskCtx.drawImage(img, 0, 0);
    const pixels = maskCtx.getImageData(0, 0, mask.width, mask.height);
    for (let i = 0; i < pixels.data.length; i += 4) {
      const luminance = Math.round((pixels.data[i] + pixels.data[i + 1] + pixels.data[i + 2]) / 3) * pixels.data[i + 3] / 255;
      pixels.data[i] = pixels.data[i + 1] = pixels.data[i + 2] = 255;
      pixels.data[i + 3] = luminance;
    }
    maskCtx.putImageData(pixels, 0, 0);
    invalidateMask();
    drawOverlay();
  }

  function exportMask() {
    const exportCanvas = document.createElement("canvas");
    exportCanvas.width = mask.width;
    exportCanvas.height = mask.height;
    const context = exportCanvas.getContext("2d");
    context.fillStyle = "black";
    context.fillRect(0, 0, mask.width, mask.height);
    context.drawImage(mask, 0, 0);
    return new Promise((resolve, reject) => exportCanvas.toBlob(blob => blob ? resolve(blob) : reject(new Error("The mask could not be exported.")), "image/png"));
  }

  async function findMask() {
    clearErrors();
    if (!state.source) {
      showError("Choose your original image first.");
      return;
    }
    const currentPrompt = (getActivePromptTextarea().value || "").trim();
    if (!currentPrompt) {
      showError("Describe the change before finding its edit area.");
      getActivePromptTextarea().focus();
      return;
    }
    const requestedSource = state.source, requestedPrompt = currentPrompt, requestedRegion = $("editRegion").value;
    const form = new FormData();
    form.append("image", requestedSource);
    form.append("prompt", requestedPrompt);
    form.append("region", requestedRegion);
    state.maskBusy = true;
    $("findMask").textContent = "Finding edit area…";
    refreshMaskTools();
    try {
      const data = await api("/api/mask", {method: "POST", body: form});
      if (state.source !== requestedSource || (getActivePromptTextarea().value || "").trim() !== requestedPrompt || $("editRegion").value !== requestedRegion) {
        throw new Error("Your source or instruction changed during detection. Find the edit area again.");
      }
      const sourceUrl = localImageUrl(data.source_url), maskUrl = localImageUrl(data.mask_url);
      if (!maskUrl) throw new Error("No usable mask was returned. Paint the target area or upload a mask PNG.");
      if (sourceUrl) await setSource(state.source, sourceUrl);
      await importMask(maskUrl);
      if ($("manualMaskDetails") && !$("manualMaskDetails").open) {
        $("manualMaskDetails").open = true;
      }
      refreshMaskTools();
      $("maskMessage").textContent = [data.message || "Edit area highlighted. Correct it with the brush if needed, or choose Use manual mask to override auto-mask.", ...(data.warnings || [])].join(" ");
      $("maskEditor").scrollIntoView({block: "nearest", behavior: "auto"});
    } catch (error) {
      $("maskMessage").textContent = `${error.message} Paint the area manually if you wish to override automatic masking.`;
    } finally {
      state.maskBusy = false;
      $("findMask").textContent = "Find edit area";
      refreshMaskTools();
    }
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
      const actionSupport = data.enhancer?.profile === "qwen21-actions-v2";
      $("actionUnderstandingStatus").textContent = actionSupport
        ? "Action & body-part checks active. Enhance checks wording, not the generated image."
        : "Older Studio server: restart it to activate action & body-part checks.";
      $("actionUnderstandingStatus").title = data.enhancer?.intent_checks || "";
      if (data.enhancer?.models) {
        if ($("enhancerDolphinBtn") && data.enhancer.models.dolphin) {
          $("enhancerDolphinBtn").title = data.enhancer.models.dolphin.installed
            ? "🐬 Dolphin: Creative, detailed rewrite · Ready"
            : "🐬 Dolphin: Model not installed in Ollama";
        }
        if ($("enhancerQwenBtn") && data.enhancer.models.qwen) {
          $("enhancerQwenBtn").title = data.enhancer.models.qwen.installed
            ? "Qwen: Descriptive rewrite of your existing ideas · Ready"
            : "🧠 Qwen: Model not installed in Ollama";
        }
      }
      const profiles = data.mode_info || data.modes || data.profiles;
      if (profiles) {
        const list = Array.isArray(profiles) ? Object.fromEntries(profiles.map(m => [m.id || m.name, m])) : profiles;
        for (const key of ["fast", "balanced", "ultra"]) {
          if (list[key]) state.modes[key] = {...state.modes[key], ...list[key]};
          const m = state.modes[key];
          $(key + "Spec").textContent = `${m.resolution || m.base_resolution || m.size || ""} · ${m.steps || m.num_steps} steps`;
        }
        chooseMode(state.mode);
      }
      const segmentation = data.segmentation || data.segmenter || data.masking;
      if (segmentation?.available === false) {
        $("findMask").title = "Automatic segmentation unavailable; manual masks remain available.";
      }
    } catch (error) {
      $("backendStatus").textContent = "Backend unavailable";
      $("backendStatus").className = "backend-status offline";
      $("backendStatus").title = error.message;
      $("actionUnderstandingStatus").textContent = "Cannot check action support. Start Studio, then select Refresh.";
    } finally {
      $("refreshBackend").disabled = false;
    }
  }

  /* --- Multi-Prompt UI and Management --- */
  function clearAllPrompts() {
    if (state.enhancing || state.jobId || state.batchId || state.submitting) return;
    state.prompts = [""];
    state.promptUndos = [null];
    state.promptChecks = [];
    state.activePromptIndex = 0;
    const enhanceStatus = $("enhanceAllStatus");
    if (enhanceStatus) {
      enhanceStatus.hidden = true;
      enhanceStatus.textContent = "";
    }
    const undoAllBtn = $("undoEnhanceAllBtn");
    if (undoAllBtn) undoAllBtn.hidden = true;
    clearErrors();
    renderPromptCards();
    invalidateMask();
    savePreference();
    updateRun();
    const firstTextarea = getActivePromptTextarea();
    if (firstTextarea) firstTextarea.focus();
  }

  function clearPromptIndex(index) {
    if (state.enhancing || state.jobId || state.batchId || state.submitting) return;
    state.prompts = getPromptValues();
    state.prompts[index] = "";
    state.promptUndos[index] = null;
    state.promptChecks[index] = null;
    renderPromptCards();
    invalidateMask();
    savePreference();
    updateRun();
    const textareas = document.querySelectorAll(".prompt-textarea");
    if (textareas[index]) textareas[index].focus();
  }

  function updateEnhancerSelectorUI() {
    const active = state.enhancer === "qwen" ? "qwen" : "dolphin";
    if ($("enhancerDolphinBtn")) $("enhancerDolphinBtn").classList.toggle("active", active === "dolphin");
    if ($("enhancerQwenBtn")) $("enhancerQwenBtn").classList.toggle("active", active === "qwen");
    const label = active === "qwen" ? "Qwen" : "Dolphin";
    if ($("enhanceAllBtn")) $("enhanceAllBtn").title = `Enhance all entered prompts using ${label}`;
  }

  function setEnhancer(choice) {
    if (state.enhancing) return;
    state.enhancer = choice === "qwen" ? "qwen" : "dolphin";
    updateEnhancerSelectorUI();
    savePreference();
    renderPromptCards();
  }

  function renderPromptCards() {
    const container = $("promptsList");
    if (!container) return;
    container.innerHTML = "";

    if (!state.prompts || state.prompts.length === 0) state.prompts = [""];
    if (state.prompts.length > 5) state.prompts = state.prompts.slice(0, 5);

    while (state.promptUndos.length < state.prompts.length) state.promptUndos.push(null);
    if (state.promptUndos.length > state.prompts.length) state.promptUndos = state.promptUndos.slice(0, state.prompts.length);

    state.prompts.forEach((text, index) => {
      const card = document.createElement("div");
      card.className = "prompt-card";
      card.dataset.index = String(index);
      card.setAttribute("role", "listitem");

      const header = document.createElement("div");
      header.className = "prompt-card-header";

      const label = document.createElement("span");
      label.className = "prompt-card-label";
      label.textContent = `Prompt ${index + 1}`;
      header.appendChild(label);

      const tools = document.createElement("div");
      tools.className = "prompt-card-tools";

      const currentEnhancer = state.enhancer === "qwen" ? "qwen" : "dolphin";
      const altEnhancer = currentEnhancer === "dolphin" ? "qwen" : "dolphin";
      const currentLabel = currentEnhancer === "qwen" ? "Qwen" : "Dolphin";
      const altLabel = altEnhancer === "qwen" ? "Qwen" : "Dolphin";

      const enhanceBtn = document.createElement("button");
      enhanceBtn.type = "button";
      enhanceBtn.className = "text-button card-enhance-btn";
      enhanceBtn.dataset.action = "enhance";
      enhanceBtn.textContent = `Enhance (${currentLabel})`;
      enhanceBtn.title = `Enhance prompt ${index + 1} with ${currentLabel}`;
      enhanceBtn.disabled = !!state.jobId || !!state.batchId || state.submitting || state.enhancing;
      enhanceBtn.addEventListener("click", () => void enhancePromptIndex(index, currentEnhancer));
      tools.appendChild(enhanceBtn);

      const altBtn = document.createElement("button");
      altBtn.type = "button";
      altBtn.className = "text-button card-alt-enhance-btn";
      altBtn.dataset.action = "alt-enhance";
      altBtn.textContent = `or ${altLabel}`;
      altBtn.title = `Enhance prompt ${index + 1} with ${altLabel}`;
      altBtn.disabled = !!state.jobId || !!state.batchId || state.submitting || state.enhancing;
      altBtn.addEventListener("click", () => void enhancePromptIndex(index, altEnhancer));
      tools.appendChild(altBtn);

      const undoBtn = document.createElement("button");
      undoBtn.type = "button";
      undoBtn.className = "text-button card-undo-btn";
      undoBtn.textContent = "Undo";
      undoBtn.hidden = !state.promptUndos[index];
      undoBtn.addEventListener("click", () => undoPromptIndex(index));
      tools.appendChild(undoBtn);

      const clearBtn = document.createElement("button");
      clearBtn.type = "button";
      clearBtn.className = "text-button card-clear-btn";
      clearBtn.textContent = "Clear";
      clearBtn.setAttribute("aria-label", `Clear prompt ${index + 1}`);
      clearBtn.disabled = !!state.jobId || !!state.batchId || state.submitting || state.enhancing;
      clearBtn.addEventListener("click", () => clearPromptIndex(index));
      tools.appendChild(clearBtn);

      if (state.prompts.length > 1) {
        const removeBtn = document.createElement("button");
        removeBtn.type = "button";
        removeBtn.className = "text-button card-remove-btn";
        removeBtn.textContent = "Remove";
        removeBtn.setAttribute("aria-label", `Remove prompt ${index + 1}`);
        removeBtn.disabled = !!state.jobId || !!state.batchId || state.submitting || state.enhancing;
        removeBtn.addEventListener("click", () => removePromptSlot(index));
        tools.appendChild(removeBtn);
      }

      header.appendChild(tools);
      card.appendChild(header);

      const textarea = document.createElement("textarea");
      if (index === 0) textarea.id = "prompt";
      textarea.className = "prompt-textarea";
      textarea.rows = 4;
      textarea.maxLength = 4000;
      textarea.required = index === 0;
      textarea.setAttribute("autocomplete", "off");
      textarea.setAttribute("spellcheck", "false");
      textarea.placeholder = state.task === "edit"
        ? (index === 0 ? "Change my outfit to a black suit. Keep my face, pose, lighting and background unchanged…" : `Edit variation ${index + 1}…`)
        : (index === 0 ? "A cinematic portrait in warm evening light, rich natural colors and fine detail…" : `Prompt variation ${index + 1}…`);
      textarea.value = text;
      textarea.setAttribute("aria-describedby", "promptHint");
      textarea.addEventListener("focus", () => { state.activePromptIndex = index; });
      textarea.addEventListener("input", () => {
        state.prompts[index] = textarea.value;
        state.promptChecks[index] = null;
        const checkStatus = card.querySelector(".card-status");
        if (checkStatus) checkStatus.hidden = true;
        invalidateMask();
        savePreference();
        updateRun();
      });
      card.appendChild(textarea);

      const status = document.createElement("p");
      status.className = "card-status hint";
      status.setAttribute("role", "status");
      status.hidden = !state.promptChecks[index];
      status.textContent = state.promptChecks[index] || "";
      card.appendChild(status);

      container.appendChild(card);
    });

    $("promptCount").textContent = String(state.prompts.length);
    $("addPromptBtn").disabled = state.prompts.length >= 5 || !!state.jobId || !!state.batchId || state.enhancing;
    $("enhanceAllBtn").disabled = !!state.jobId || !!state.batchId || state.submitting || state.enhancing;
    const hasAnyUndo = state.promptUndos.some(u => !!u);
    $("undoEnhanceAllBtn").hidden = !hasAnyUndo;
    updateRun();
  }

  function addPromptSlot() {
    if (state.prompts.length >= 5) return;
    state.prompts = getPromptValues();
    state.prompts.push("");
    state.promptUndos.push(null);
    state.promptChecks.push(null);
    state.activePromptIndex = state.prompts.length - 1;
    renderPromptCards();
    savePreference();
    const textareas = document.querySelectorAll(".prompt-textarea");
    if (textareas[state.activePromptIndex]) {
      textareas[state.activePromptIndex].focus();
    }
  }

  function removePromptSlot(index) {
    if (state.prompts.length <= 1) return;
    state.prompts = getPromptValues();
    state.prompts.splice(index, 1);
    state.promptUndos.splice(index, 1);
    state.promptChecks.splice(index, 1);
    if (state.activePromptIndex >= state.prompts.length) {
      state.activePromptIndex = state.prompts.length - 1;
    }
    renderPromptCards();
    savePreference();
  }

  function describeIntentChecks(data) {
    if (!data.intent) return "Prompt enhanced. Review the wording before generating.";
    const actions = [...new Set(data.intent.action_clauses?.flatMap(c => c.actions || []) || [])];
    const parts = data.intent.body_parts || [];
    const sides = (data.intent.directions || []).map(d => `${d.side} ${d.part}`);
    const summary = [actions.length ? `Actions: ${actions.join(", ")}` : "",
      parts.length ? `Body parts: ${parts.join(", ")}` : "",
      sides.length ? `Sides: ${sides.join(", ")}` : ""].filter(Boolean).join(" · ");
    return (summary ? summary + ". " : "Prompt enhanced. ") + "Text checks only; review the generated image.";
  }

  async function enhancePromptIndex(index, modelOverride) {
    if (state.enhancing || state.jobId || state.batchId || state.submitting) return;
    state.prompts = getPromptValues();
    const current = (state.prompts[index] || "").trim();
    if (!current) {
      showError(`Enter text in Prompt ${index + 1} before enhancing.`);
      const textareas = document.querySelectorAll(".prompt-textarea");
      if (textareas[index]) textareas[index].focus();
      return;
    }
    const chosenModel = modelOverride || state.enhancer || "dolphin";
    const modelLabel = chosenModel === "qwen" || chosenModel === "qwen21-qwen4b" ? "Qwen" : "Dolphin";
    const modelPayload = chosenModel === "qwen" || chosenModel === "qwen21-qwen4b" ? "qwen21-qwen4b" : "qwen21-dolphin";
    const task = state.task, ratio = $("aspectRatio").value;
    const cards = document.querySelectorAll(".prompt-card");
    const card = cards[index];
    const status = card?.querySelector(".card-status");
    const enhanceBtn = card?.querySelector(".card-enhance-btn");
    const textarea = card?.querySelector(".prompt-textarea");

    state.enhancing = true;
    clearErrors();
    updateRun();
    if (enhanceBtn) enhanceBtn.textContent = `Enhancing (${modelLabel})…`;
    if (status) {
      status.hidden = false;
      status.textContent = `The local ${modelLabel} enhancer is rewriting this prompt…`;
    }

    try {
      const data = await api("/api/enhance", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          prompt: current,
          task,
          aspect_ratio: ratio,
          region: $("editRegion").value,
          preserve: $("preserve").checked,
          reference_count: task === "edit" ? Number(Boolean(state.reference)) + Number(Boolean($("poseEnabled")?.checked && state.pose)) : 0,
          is_transparent: $("transparent").checked,
          model: modelPayload,
          enhancement_style: $("enhancementStyle") ? $("enhancementStyle").value : "polish",
          pose_assist: Boolean($("poseEnabled")?.checked && state.pose)
        })
      });
      if (!data.enhanced) throw new Error("The enhancer returned no prompt.");
      if ((textarea?.value || "").trim() !== current) {
        state.prompts[index] = textarea.value;
        state.promptChecks[index] = "Your newer text was kept. Enhance again to check it.";
        return;
      }
      state.promptUndos[index] = { prompt: current, ratio };
      state.prompts[index] = data.enhanced;
      if (textarea) textarea.value = data.enhanced;
      if (task === "generate" && index === 0 && Array.from($("aspectRatio").options).some(o => o.value === data.wh_ratio)) {
        $("aspectRatio").value = data.wh_ratio;
      }
      invalidateMask();
      savePreference();
      state.promptChecks[index] = describeIntentChecks(data);
      if (status) {
        const returnedLabel = (data.model || "").includes("dolphin") ? "Dolphin" : "Qwen";
        status.textContent = task === "edit" ? `Instruction clarified with ${returnedLabel}.` : `Prompt enhanced with ${returnedLabel} for Qwen.`;
      }
    } catch (err) {
      showError(err.message);
      if (status) status.hidden = true;
    } finally {
      state.enhancing = false;
      updateRun();
      renderPromptCards();
    }
  }

  function undoPromptIndex(index) {
    const undo = state.promptUndos[index];
    if (!undo || state.enhancing) return;
    state.prompts[index] = undo.prompt;
    state.promptUndos[index] = null;
    state.promptChecks[index] = null;
    renderPromptCards();
    invalidateMask();
    savePreference();
  }

  async function enhanceAllPrompts() {
    if (state.enhancing || state.jobId || state.batchId || state.submitting) return;
    state.prompts = getPromptValues();
    const filledIndices = [];
    state.prompts.forEach((p, idx) => {
      if (p.trim()) filledIndices.push(idx);
    });
    if (filledIndices.length === 0) {
      showError("Enter at least one prompt before enhancing.");
      return;
    }

    const chosenModel = state.enhancer || "dolphin";
    const modelLabel = chosenModel === "qwen" || chosenModel === "qwen21-qwen4b" ? "Qwen" : "Dolphin";
    const modelPayload = chosenModel === "qwen" || chosenModel === "qwen21-qwen4b" ? "qwen21-qwen4b" : "qwen21-dolphin";
    state.enhancing = true;
    clearErrors();
    updateRun();
    $("enhanceAllBtn").textContent = `Enhancing all (${modelLabel})…`;
    $("enhanceAllStatus").hidden = false;

    try {
      for (let i = 0; i < filledIndices.length; i++) {
        const slotIdx = filledIndices[i];
        $("enhanceAllStatus").textContent = `Enhancing prompt ${i + 1} of ${filledIndices.length} with ${modelLabel}…`;
        const current = state.prompts[slotIdx].trim();
        const data = await api("/api/enhance", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({
            prompt: current,
            task: state.task,
            aspect_ratio: $("aspectRatio").value,
            region: $("editRegion").value,
            preserve: $("preserve").checked,
            reference_count: state.task === "edit" ? Number(Boolean(state.reference)) + Number(Boolean($("poseEnabled")?.checked && state.pose)) : 0,
            is_transparent: $("transparent").checked,
            model: modelPayload,
            enhancement_style: $("enhancementStyle") ? $("enhancementStyle").value : "polish",
            pose_assist: Boolean($("poseEnabled")?.checked && state.pose)
          })
        });
        if (data.enhanced) {
          const textareas = document.querySelectorAll(".prompt-textarea");
          if ((textareas[slotIdx]?.value || "").trim() !== current) {
            state.prompts[slotIdx] = textareas[slotIdx].value;
            state.promptChecks[slotIdx] = "Your newer text was kept. Enhance again to check it.";
            continue;
          }
          state.promptUndos[slotIdx] = { prompt: current, ratio: $("aspectRatio").value };
          state.prompts[slotIdx] = data.enhanced;
          state.promptChecks[slotIdx] = describeIntentChecks(data);
          if (textareas[slotIdx]) textareas[slotIdx].value = data.enhanced;
        }
      }
      $("enhanceAllStatus").textContent = `Enhancement with ${modelLabel} finished. Review each prompt's status before generating.`;
      invalidateMask();
      savePreference();
    } catch (err) {
      showError(err.message);
      $("enhanceAllStatus").textContent = "Enhancement interrupted: " + err.message;
    } finally {
      state.enhancing = false;
      updateRun();
      $("enhanceAllBtn").textContent = "Enhance all";
      renderPromptCards();
    }
  }

  function undoEnhanceAll() {
    if (state.enhancing) return;
    state.prompts.forEach((_, idx) => {
      if (state.promptUndos[idx]) {
        state.prompts[idx] = state.promptUndos[idx].prompt;
        state.promptUndos[idx] = null;
        state.promptChecks[idx] = null;
      }
    });
    $("undoEnhanceAllBtn").hidden = true;
    $("enhanceAllStatus").hidden = true;
    renderPromptCards();
    invalidateMask();
    savePreference();
  }

  /* --- Batch Progress & Gallery UI --- */
  function setStoredJob(id) {
    try {
      if (id) localStorage.setItem("qwen-studio-job", id);
      else localStorage.removeItem("qwen-studio-job");
    } catch { /* Storage is optional. */ }
  }

  function setStoredBatch(id) {
    try {
      if (id) localStorage.setItem("qwen-studio-batch", id);
      else localStorage.removeItem("qwen-studio-batch");
    } catch { /* Storage is optional. */ }
  }

  function startLoadingState(title = "Generating your image…", detail = "Queued for local GPU…") {
    // 1. Immediately remove previously generated image
    $("comparison").hidden = true;
    $("resultTools").hidden = true;
    $("preview").classList.remove("has-image");
    $("emptyState").hidden = true;
    $("outputError").hidden = true;

    // 2. Display the preview loading screen
    $("previewLoading").hidden = false;
    $("loadingTitle").textContent = title;
    $("loadingPhase").textContent = "Qwen Image 2.1 is processing your prompt.";
    $("loadingStepDetail").textContent = detail;
    $("loadingTimer").textContent = "0s";
    $("loadingProgressBar").style.width = "5%";
    $("loadingProgressBar").classList.add("indeterminate");
  }

  function updateLoadingProgress(phase, progress, elapsed) {
    if ($("previewLoading").hidden) return;
    if (phase) {
      $("loadingPhase").textContent = phase;
      $("loadingStepDetail").textContent = phase;
    }
    if (Number.isFinite(progress)) {
      $("loadingProgressBar").classList.remove("indeterminate");
      $("loadingProgressBar").style.width = `${Math.min(100, Math.max(2, Math.round(progress * 100)))}%`;
    }
    if (Number.isFinite(elapsed)) {
      const mins = Math.floor(elapsed / 60);
      const secs = Math.floor(elapsed % 60);
      $("loadingTimer").textContent = mins > 0 ? `${mins}m ${secs}s` : `${secs}s`;
    }
  }

  function finishLoadingState(success = true) {
    $("previewLoading").hidden = true;
    if (!success && !state.result?.image_url) {
      $("emptyState").hidden = false;
    }
  }

  function finishJob() {
    state.jobId = null;
    setStoredJob(null);
    $("jobPanel").hidden = true;
    finishLoadingState(false);
    updateRun();
  }

  function finishBatch() {
    state.batchId = null;
    state.jobId = null;
    setStoredBatch(null);
    setStoredJob(null);
    $("jobPanel").hidden = true;
    finishLoadingState(false);
    updateRun();
  }

  function showJob(data) {
    $("jobPanel").hidden = false;
    $("batchProgressRow").hidden = true;
    const phase = data.phase || (data.status ? formatMode(data.status) : "Running");
    $("jobPhase").textContent = formatMode(phase);
    $("jobDetail").textContent = data.message || (data.status === "queued" ? "Waiting for the local GPU. Queued jobs run one at a time." : "Qwen is processing your request. You can leave this page and reconnect later.");
    if (Number.isFinite(data.progress)) {
      $("jobProgress").max = data.progress > 1 ? 100 : 1;
      $("jobProgress").value = data.progress;
    } else {
      $("jobProgress").removeAttribute("value");
    }
    const elapsed = data.elapsed_sec ?? data.elapsed;
    $("jobElapsed").textContent = Number.isFinite(elapsed)
      ? `${Math.floor(elapsed / 60)}m ${Math.floor(elapsed % 60)}s elapsed · loading and offloading can take time.`
      : "The first run can take longer while models load.";
    updateLoadingProgress(phase, data.progress, elapsed);
  }

  function showBatchJob(batchData) {
    $("jobPanel").hidden = false;
    const total = batchData.total || 1;
    const completed = batchData.completed || 0;
    const activeIdx = batchData.active_index ?? 0;
    const activeJob = batchData.jobs?.[activeIdx];

    $("batchProgressRow").hidden = total <= 1;
    $("batchStepBadge").textContent = `Image ${Math.min(activeIdx + 1, total)} of ${total}`;
    const percent = Math.round((completed / total) * 100);
    $("batchPercentText").textContent = `${percent}%`;

    const phase = activeJob?.phase || (activeJob?.status ? formatMode(activeJob.status) : "Queued");
    const phaseTitle = total > 1 ? `Batch · Image ${activeIdx + 1} of ${total}: ${phase}` : formatMode(phase);
    $("jobPhase").textContent = phaseTitle;
    $("jobDetail").textContent = activeJob?.message || (activeJob?.status === "queued" ? "Waiting for the local GPU. Queued jobs run one at a time." : "Qwen is processing your batch. You can leave this page and reconnect later.");

    if (activeJob && Number.isFinite(activeJob.progress)) {
      $("jobProgress").max = activeJob.progress > 1 ? 100 : 1;
      $("jobProgress").value = activeJob.progress;
    } else {
      $("jobProgress").removeAttribute("value");
    }

    const elapsed = activeJob?.elapsed_sec ?? activeJob?.elapsed;
    $("jobElapsed").textContent = Number.isFinite(elapsed)
      ? `${Math.floor(elapsed / 60)}m ${Math.floor(elapsed % 60)}s elapsed · loading and offloading can take time.`
      : "The first run can take longer while models load.";
    updateLoadingProgress(phaseTitle, activeJob?.progress, elapsed);
  }

  function renderBatchGallery(batchData) {
    if (!batchData || !batchData.jobs || batchData.jobs.length <= 1) {
      $("batchGallerySection").hidden = true;
      return;
    }
    $("batchGallerySection").hidden = false;
    const completedCount = batchData.jobs.filter(j => j.status === "succeeded").length;
    $("batchGalleryCount").textContent = `${completedCount}/${batchData.total}`;

    const container = $("batchGallery");
    container.innerHTML = "";

    batchData.jobs.forEach((job, idx) => {
      const item = document.createElement("button");
      item.type = "button";
      item.className = `batch-item ${state.selectedBatchJobId === job.job_id ? "selected" : ""}`;
      item.setAttribute("role", "tab");
      item.setAttribute("aria-selected", String(state.selectedBatchJobId === job.job_id));

      const thumbWrap = document.createElement("div");
      thumbWrap.className = "batch-thumb-wrap";

      if (job.image_url) {
        const img = document.createElement("img");
        img.src = job.image_url;
        img.alt = `Batch output ${idx + 1}`;
        thumbWrap.appendChild(img);
      } else {
        const placeholder = document.createElement("div");
        placeholder.className = "batch-thumb-placeholder";
        placeholder.textContent = job.status === "running" ? "Generating…" : (job.status === "failed" ? "Failed" : `Queued #${idx + 1}`);
        thumbWrap.appendChild(placeholder);
      }
      item.appendChild(thumbWrap);

      const footer = document.createElement("div");
      footer.className = "batch-item-footer";
      const lbl = document.createElement("span");
      lbl.className = "batch-item-label";
      lbl.textContent = `Image ${idx + 1}`;
      footer.appendChild(lbl);

      const stat = document.createElement("span");
      stat.className = `batch-item-status ${job.status || "queued"}`;
      stat.textContent = job.status === "succeeded" ? "Done" : (job.status === "running" ? "Gen" : (job.status || "Queued"));
      footer.appendChild(stat);

      item.appendChild(footer);

      item.addEventListener("click", () => {
        state.selectedBatchJobId = job.job_id;
        document.querySelectorAll(".batch-item").forEach(b => b.classList.toggle("selected", b === item));
        if (job.image_url) {
          void displayResult(job.result || job, job.job_id);
        }
      });

      container.appendChild(item);
    });
  }

  async function pollJob(id) {
    if (state.polling) return;
    state.polling = true;
    let failures = 0;
    try {
      while (state.jobId === id) {
        try {
          const job = await api(`/api/jobs/${encodeURIComponent(id)}`);
          failures = 0;
          showJob(job);
          if (["succeeded", "completed"].includes(job.status)) {
            await displayResult(job.result || job, id);
            finishJob();
            break;
          }
          if (job.status === "failed") {
            showError(errorText(job), true);
            finishJob();
            break;
          }
          if (job.status === "cancelled") {
            $("outputError").textContent = "The request was cancelled.";
            $("outputError").hidden = false;
            finishJob();
            break;
          }
        } catch (error) {
          failures += 1;
          if (failures >= 5) {
            showError(`Connection lost: ${error.message} Refresh this page to reconnect to the saved job.`, true);
            $("jobPhase").textContent = "Connection interrupted";
            break;
          }
          $("jobDetail").textContent = "Reconnecting to your local job…";
        }
        await sleep(1500);
      }
    } finally {
      state.polling = false;
    }
  }

  async function pollBatch(batchId) {
    if (state.polling) return;
    state.polling = true;
    let failures = 0;
    try {
      while (state.batchId === batchId) {
        try {
          const batch = await api(`/api/batches/${encodeURIComponent(batchId)}`);
          failures = 0;
          state.batchData = batch;
          showBatchJob(batch);
          renderBatchGallery(batch);

          const succeededJobs = (batch.jobs || []).filter(j => j.status === "succeeded");
          if (succeededJobs.length > 0 && !state.selectedBatchJobId) {
            state.selectedBatchJobId = succeededJobs[0].job_id;
            void displayResult(succeededJobs[0].result || succeededJobs[0], succeededJobs[0].job_id);
          } else if (state.selectedBatchJobId) {
            const currentSelected = (batch.jobs || []).find(j => j.job_id === state.selectedBatchJobId);
            if (currentSelected && currentSelected.status === "succeeded" && (!state.result || state.result.job_id !== currentSelected.job_id)) {
              void displayResult(currentSelected.result || currentSelected, currentSelected.job_id);
            }
          }

          if (["succeeded", "completed", "failed", "cancelled"].includes(batch.overall_status)) {
            finishBatch();
            if (succeededJobs.length > 0) {
              const lastDone = succeededJobs[succeededJobs.length - 1];
              state.selectedBatchJobId = lastDone.job_id;
              await displayResult(lastDone.result || lastDone, lastDone.job_id);
              renderBatchGallery(batch);
            }
            if (batch.overall_status === "cancelled") {
              $("outputError").textContent = "The batch request was cancelled.";
              $("outputError").hidden = false;
            } else if (batch.overall_status === "failed") {
              showError("Batch processing completed with errors.", true);
            }
            break;
          }
        } catch (error) {
          failures += 1;
          if (failures >= 5) {
            showError(`Connection lost: ${error.message} Refresh this page to reconnect to the batch.`, true);
            $("jobPhase").textContent = "Connection interrupted";
            break;
          }
          $("jobDetail").textContent = "Reconnecting to your local batch…";
        }
        await sleep(1500);
      }
    } finally {
      state.polling = false;
    }
  }

  async function submitJob(event) {
    event.preventDefault();
    if (state.jobId || state.batchId || state.submitting || state.enhancing) return;
    clearErrors();

    state.prompts = getPromptValues();
    const cleanPrompts = state.prompts.map(p => p.trim()).filter(Boolean);
    if (cleanPrompts.length === 0) {
      showError("Describe the image or change you want first.");
      const textareas = document.querySelectorAll(".prompt-textarea");
      if (textareas[0]) textareas[0].focus();
      return;
    }
    if (state.task === "edit" && !state.source) {
      showError("Choose an original image to edit.");
      $("sourceInput").focus();
      return;
    }
    if (!$("seed").validity.valid || !$("steps").validity.valid) {
      showError("Use a nonnegative whole-number seed and between 1 and 100 steps.");
      return;
    }
    const precise = state.task === "edit" && $("preserve").checked && $("editRegion").value !== "global";
    const usePose = $("poseEnabled").checked;
    if (usePose && (!state.pose || !$("poseReviewed").checked)) {
      showError("Upload a pose reference and approve its preview and subject assignment first.");
      $("poseAssistDetails").open = true;
      return;
    }
    if (state.repairParent && (!precise || !state.maskReady || cleanPrompts.length !== 1)) {
      showError("Repair one selected result with a reviewed custom mask. Include wrist/knee/contact context.");
      return;
    }
    if (usePose && precise && !state.maskReady && $("editRegion").value !== "auto") {
      showError("Review a movement mask or choose Whole image for a pose change.");
      return;
    }
    if (state.maskBusy) {
      showError("Wait for the edit area to finish loading, then review the mask.");
      return;
    }

    state.submitting = true;
    updateRun();

    const form = new FormData();
    form.append("task", state.task);
    form.append("mode", state.mode);
    form.append("aspect_ratio", $("aspectRatio").value);
    form.append("is_transparent", String($("transparent").checked));
    form.append("preserve", String(precise));
    form.append("region", precise ? $("editRegion").value : "global");
    form.append("pose_enabled", String(usePose));
    form.append("pose_reviewed", String($("poseReviewed").checked));
    form.append("pose_order", $("poseOrder").value);
    form.append("mask_reviewed", String(state.maskReady));
    if (usePose) form.append("pose_reference", state.pose);
    if (state.repairParent) {
      form.append("repair_kind", $("repairKind").value);
      form.append("repair_parent_id", state.repairParent);
    }
    if ($("loraToggle")) {
      form.append("lora_enabled", String($("loraToggle").checked));
    }
    if ($("loraStrength")) {
      form.append("lora_strength", $("loraStrength").value);
    }

    const seed = $("seed").value === "" ? Math.floor(Math.random() * 4294967295) : Number($("seed").value);
    form.append("seed", String(seed));
    if ($("steps").value !== "") form.append("num_steps", $("steps").value);

    if (state.task === "edit") {
      form.append("image", state.source);
      if (precise && state.maskReady) {
        form.append("mask", await exportMask(), "reviewed-mask.png");
      }
      if (state.reference) form.append("reference", state.reference);
    }
    savePreference();

    // Immediately remove the previous generated image and display the loading screen
    startLoadingState(
      cleanPrompts.length > 1 ? `Generating batch (1 of ${cleanPrompts.length})…` : "Generating your image…",
      "Submitting request to local Qwen engine…"
    );

    try {
      if (cleanPrompts.length > 1) {
        form.append("prompts", JSON.stringify(cleanPrompts));
        const data = await api("/api/jobs/batch", {method: "POST", body: form});
        const batchId = data.batch_id;
        if (!batchId) throw new Error("The server returned no batch identifier.");
        state.batchId = batchId;
        state.jobId = batchId;
        state.selectedBatchJobId = null;
        setStoredBatch(batchId);
        showBatchJob({total: data.total, completed: 0, active_index: 0, overall_status: "queued", jobs: data.jobs});
        updateRun();
        void pollBatch(batchId);
      } else {
        form.append("prompt", cleanPrompts[0]);
        const data = await api("/api/jobs", {method: "POST", body: form});
        const id = data.job_id || data.id;
        if (!id) throw new Error("The server returned no job identifier.");
        state.jobId = id;
        state.batchId = null;
        setStoredJob(id);
        showJob(data);
        $("batchGallerySection").hidden = true;
        updateRun();
        void pollJob(id);
      }
    } catch (error) {
      finishLoadingState(false);
      showError(error.message);
    } finally {
      state.submitting = false;
      updateRun();
    }
  }

  function updateCompare() {
    const position = Number($("compareRange").value);
    $("beforeImage").style.clipPath = `inset(0 ${100 - position}% 0 0)`;
    $("comparisonLine").style.left = `${position}%`;
  }

  async function displayResult(result, jobId = null) {
    const url = localImageUrl(result.image_url || result.url);
    if (!url) throw new Error("The result does not contain a usable local image URL.");
    const img = await loadImage(url);
    finishLoadingState(true);
    state.result = {...result, image_url: url, job_id: jobId || result.job_id};
    $("repairResult").hidden = !state.result.job_id || !!result.metadata?.repair?.parent_id;
    $("resultImage").src = url;
    $("emptyState").hidden = true;
    $("previewLoading").hidden = true;
    $("comparison").hidden = false;
    $("resultTools").hidden = false;
    $("preview").classList.add("has-image");
    $("workspaceTitle").textContent = "Your studio output";
    $("resultSize").textContent = `${img.naturalWidth} × ${img.naturalHeight}`;
    $("downloadImage").href = url;
    $("downloadImage").download = `qwen-${state.result.job_id || "image"}${new URL(url).pathname.match(/\.(png|jpe?g|webp)$/i)?.[0] || ".png"}`;
    $("downloadImage").textContent = /\.png$/i.test(new URL(url).pathname) ? "Download PNG" : "Download image";

    const metadata = result.metadata || {};
    $("resultMetadata").textContent = JSON.stringify(metadata, null, 2);
    const elapsed = metadata.elapsed_sec ?? metadata.duration_sec;
    const summary = [
      metadata.mode && formatMode(metadata.mode),
      metadata.steps && `${metadata.steps} steps`,
      metadata.seed !== undefined && `Seed ${metadata.seed}`,
      Number.isFinite(elapsed) && `${Math.round(elapsed)}s`,
      metadata.preservation?.protected_pixels_unchanged === true && "Protected pixels preserved"
    ];
    $("resultSummary").textContent = summary.filter(Boolean).join(" · ");
    const warnings = result.warnings || metadata.warnings || [];
    $("resultWarnings").hidden = !warnings.length;
    $("resultWarnings").textContent = Array.isArray(warnings) ? warnings.join(" ") : String(warnings);

    const sourceUrl = localImageUrl(result.source_url || metadata.source_url);
    const hasBefore = !!sourceUrl;
    $("beforeImage").hidden = $("beforeBadge").hidden = $("afterBadge").hidden = $("comparisonLine").hidden = $("comparisonControls").hidden = !hasBefore;
    if (sourceUrl) $("beforeImage").src = sourceUrl;
    $("toggleCompare").setAttribute("aria-pressed", "true");
    $("toggleCompare").textContent = "Hide original";
    $("compareRange").value = 50;
    updateCompare();
  }

  async function imageToSource(url) {
    const safeUrl = localImageUrl(url);
    if (!safeUrl) throw new Error("This image is not available locally.");
    const response = await fetch(safeUrl);
    if (!response.ok) throw new Error("The saved image could not be loaded.");
    const blob = await response.blob();
    const extension = blob.type === "image/jpeg" ? "jpg" : blob.type === "image/webp" ? "webp" : "png";
    await setSource(new File([blob], `studio-source.${extension}`, {type: blob.type || "image/png"}), safeUrl);
    chooseTask("edit");
    const textarea = getActivePromptTextarea();
    textarea.focus();
    $("sourceSection").scrollIntoView({block: "nearest"});
  }

  function bindUpload(input, drop, callback) {
    async function select(file) {
      if (!file) return;
      clearErrors();
      try {
        await callback(file);
      } catch (error) {
        showError(error.message);
      }
    }
    $(input).addEventListener("change", event => void select(event.target.files[0]));
    $(drop).addEventListener("dragover", event => {
      event.preventDefault();
      $(drop).classList.add("dragging");
    });
    $(drop).addEventListener("dragleave", () => $(drop).classList.remove("dragging"));
    $(drop).addEventListener("drop", event => {
      event.preventDefault();
      $(drop).classList.remove("dragging");
      void select(event.dataTransfer.files[0]);
    });
  }

  /* --- Event Listeners --- */
  $("poseEnabled").addEventListener("change", () => {
    $("poseControls").hidden = !$("poseEnabled").checked;
    $("poseReviewed").checked = false;
    invalidateMask();
  });
  $("poseOrder").addEventListener("change", () => { $("poseReviewed").checked = false; invalidateMask(); });
  bindUpload("poseInput", "poseDrop", async file => {
    validateFile(file);
    const url = URL.createObjectURL(file);
    try { await loadImage(url); } catch (error) { URL.revokeObjectURL(url); throw error; }
    if (state.poseUrl) URL.revokeObjectURL(state.poseUrl);
    state.pose = file; state.poseUrl = url;
    $("posePreview").src = url; $("posePreview").hidden = false;
    $("poseOpen").href = url; $("poseOpen").hidden = false;
    $("poseFileLabel").textContent = file.name; $("removePose").hidden = false;
    $("poseReviewed").checked = false;
    invalidateMask();
  });
  $("removePose").addEventListener("click", () => {
    if (state.poseUrl) URL.revokeObjectURL(state.poseUrl);
    state.pose = state.poseUrl = null;
    $("posePreview").hidden = true; $("posePreview").removeAttribute("src");
    $("poseOpen").hidden = true; $("poseOpen").removeAttribute("href");
    $("poseInput").value = ""; $("poseFileLabel").textContent = "Choose pose photograph or skeleton PNG";
    $("removePose").hidden = true; $("poseReviewed").checked = false;
    invalidateMask();
  });
  const repairHints = {
    hand: "Paint the faulty hand, wrist and enough adjoining forearm to reconnect it. Review hidden and overlapping fingers; do not force a visible finger count.",
    leg: "Paint the faulty leg/foot and connected knee or hip context. For movement include both limb positions and exposed background.",
    contact: "Paint both interacting regions and their contact point, including connected limbs and exposed background."
  };
  $("repairKind").addEventListener("change", () => { $("repairHint").textContent = repairHints[$("repairKind").value]; invalidateMask(); });
  $("cancelRepair").addEventListener("click", () => { state.repairParent = null; $("repairControls").hidden = true; });
  $("repairResult").addEventListener("click", async () => {
    if (!state.result?.job_id || state.jobId || state.batchId || state.enhancing || state.submitting) return;
    const selected = {...state.result};
    try {
      await imageToSource(selected.image_url);
      state.repairParent = selected.job_id;
      $("poseEnabled").checked = false; $("poseControls").hidden = true;
      $("preserve").checked = true; $("editRegion").value = "manual";
      $("repairControls").hidden = false; $("manualMaskDetails").open = true;
      $("repairHint").textContent = repairHints[$("repairKind").value];
      state.prompts = [""]; state.promptUndos = [null]; state.promptChecks = [];
      renderPromptCards(); invalidateMask(); refreshMaskTools();
      clearErrors();
      $("maskMessage").textContent = "Describe the correction, paint a mask including connected joint/context, then approve the mask. No repair has been generated yet.";
      getActivePromptTextarea()?.focus();
    } catch (error) { showError(error.message); }
  });
  document.querySelectorAll("[data-task]").forEach(button => {
    button.addEventListener("click", () => chooseTask(button.dataset.task));
    button.addEventListener("keydown", event => {
      if (["ArrowLeft", "ArrowRight"].includes(event.key)) {
        event.preventDefault();
        chooseTask(state.task === "generate" ? "edit" : "generate");
        $(state.task === "edit" ? "editTab" : "createTab").focus();
      }
    });
  });

  document.querySelectorAll("[data-mode]").forEach(button => button.addEventListener("click", () => chooseMode(button.dataset.mode)));

  document.querySelectorAll("[data-style]").forEach(button => button.addEventListener("click", () => {
    const textarea = getActivePromptTextarea();
    textarea.value += (textarea.value.trim() ? ", " : "") + button.dataset.style;
    state.prompts[state.activePromptIndex] = textarea.value;
    savePreference();
    updateRun();
  }));

  document.querySelectorAll("[data-example]").forEach(button => button.addEventListener("click", () => {
    const textarea = getActivePromptTextarea();
    textarea.value = button.dataset.example;
    state.prompts[state.activePromptIndex] = textarea.value;
    invalidateMask();
    savePreference();
    updateRun();
  }));

  $("stickerPreset").addEventListener("click", () => {
    $("transparent").checked = true;
    const textarea = getActivePromptTextarea();
    textarea.value = textarea.value.trim() || "A cute 3D mascot sticker, isolated subject, soft studio lighting";
    state.prompts[state.activePromptIndex] = textarea.value;
    savePreference();
    updateRun();
  });

  $("clearAllPromptsBtn")?.addEventListener("click", clearAllPrompts);
  $("addPromptBtn").addEventListener("click", addPromptSlot);
  $("enhanceAllBtn").addEventListener("click", () => void enhanceAllPrompts());
  $("undoEnhanceAllBtn").addEventListener("click", undoEnhanceAll);

  $("studioForm").addEventListener("submit", event => void submitJob(event).catch(error => {
    state.submitting = false;
    showError(error.message);
    updateRun();
  }));
  $("studioForm").addEventListener("change", savePreference);

  $("loraToggle")?.addEventListener("change", () => {
    if ($("loraStrengthContainer")) {
      $("loraStrengthContainer").classList.toggle("disabled", !$("loraToggle").checked);
    }
    savePreference();
  });
  $("loraStrength")?.addEventListener("input", () => {
    if ($("loraStrengthVal")) {
      $("loraStrengthVal").textContent = Number($("loraStrength").value).toFixed(2);
    }
    savePreference();
  });

  $("editRegion").addEventListener("change", () => {
    invalidateMask();
    if ($("editRegion").value === "global") $("preserve").checked = false;
    refreshMaskTools();
    if ($("editRegion").value === "manual") {
      $("maskMessage").textContent = "Paint the area you want to change, then choose Use manual mask.";
      if ($("manualMaskDetails") && !$("manualMaskDetails").open) $("manualMaskDetails").open = true;
    }
    savePreference();
  });

  $("manualMaskDetails")?.addEventListener("toggle", refreshMaskTools);

  $("preserve").addEventListener("change", () => {
    if ($("preserve").checked && $("editRegion").value === "global") $("editRegion").value = "manual";
    refreshMaskTools();
    savePreference();
  });

  $("refreshBackend").addEventListener("click", () => void fetchCapabilities());
  $("findMask").addEventListener("click", () => void findMask());
  $("removeSource").addEventListener("click", clearSource);

  bindUpload("sourceInput", "sourceDrop", file => setSource(file));
  bindUpload("referenceInput", "referenceDrop", file => {
    validateFile(file);
    state.reference = file;
    $("referenceFileLabel").textContent = file.name;
    $("removeReference").hidden = false;
  });
  $("removeReference").addEventListener("click", () => {
    state.reference = null;
    $("referenceInput").value = "";
    $("referenceFileLabel").textContent = "Choose reference image";
    $("removeReference").hidden = true;
  });

  $("maskInput").addEventListener("change", async event => {
    const file = event.target.files[0];
    if (!file) return;
    const url = URL.createObjectURL(file);
    try {
      if (!state.source) throw new Error("Choose your original image before uploading a mask.");
      await importMask(url);
    } catch (error) {
      showError(error.message);
    } finally {
      URL.revokeObjectURL(url);
      event.target.value = "";
    }
  });

  for (const tool of ["paint", "erase"]) {
    $(tool + "Tool").addEventListener("click", () => {
      state.tool = tool;
      for (const item of ["paint", "erase"]) {
        $(item + "Tool").classList.toggle("selected", item === tool);
        $(item + "Tool").setAttribute("aria-pressed", String(item === tool));
      }
    });
  }

  $("brushSize").addEventListener("input", () => { $("brushValue").value = $("brushSize").value + "%"; });
  $("clearMask").addEventListener("click", () => {
    stashUndo();
    maskCtx.clearRect(0, 0, mask.width, mask.height);
    invalidateMask();
    drawOverlay();
  });

  $("undoMask").addEventListener("click", async () => {
    const previous = state.undo.pop();
    if (!previous) return;
    const img = await loadImage(previous);
    maskCtx.clearRect(0, 0, mask.width, mask.height);
    maskCtx.drawImage(img, 0, 0);
    invalidateMask();
    drawOverlay();
  });

  $("acceptMask").addEventListener("click", () => {
    if (!state.source || !maskHasContent()) {
      showError("Paint or detect an editable area first. An empty mask cannot apply an edit.");
      return;
    }
    state.maskReady = true;
    $("maskAccepted").hidden = false;
    $("acceptMask").textContent = "Manual mask active";
    clearErrors();
  });

  overlay.addEventListener("pointerdown", event => {
    if (!state.source || state.maskBusy) return;
    event.preventDefault();
    overlay.setPointerCapture(event.pointerId);
    stashUndo();
    state.drawing = true;
    state.lastPoint = null;
    paint(pointerPoint(event));
  });
  overlay.addEventListener("pointermove", event => {
    if (state.drawing) paint(pointerPoint(event));
  });
  for (const event of ["pointerup", "pointercancel", "lostpointercapture"]) {
    overlay.addEventListener(event, () => {
      state.drawing = false;
      state.lastPoint = null;
    });
  }

  $("compareRange").addEventListener("input", updateCompare);
  $("toggleCompare").addEventListener("click", () => {
    const hidden = !$("beforeImage").hidden;
    $("beforeImage").hidden = $("comparisonLine").hidden = $("beforeBadge").hidden = $("afterBadge").hidden = hidden;
    $("toggleCompare").setAttribute("aria-pressed", String(!hidden));
    $("toggleCompare").textContent = hidden ? "Show original" : "Hide original";
  });

  $("editResult").addEventListener("click", () => {
    clearErrors();
    if (state.result) void imageToSource(state.result.image_url).catch(error => showError(error.message));
  });

  $("downloadAllBtn").addEventListener("click", () => {
    if (!state.batchData || !state.batchData.jobs) return;
    const completed = state.batchData.jobs.filter(j => j.status === "succeeded" && j.image_url);
    completed.forEach((j, i) => {
      setTimeout(() => {
        const link = document.createElement("a");
        link.href = j.image_url;
        link.download = `qwen-batch-${i + 1}-${j.job_id.slice(0, 8)}.png`;
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
      }, i * 350);
    });
  });

  $("cancelJob").addEventListener("click", async () => {
    if (!state.batchId && !state.jobId) return;
    $("cancelJob").disabled = true;
    try {
      if (state.batchId) {
        await api(`/api/batches/${encodeURIComponent(state.batchId)}/cancel`, {method: "POST"});
      } else if (state.jobId) {
        await api(`/api/jobs/${encodeURIComponent(state.jobId)}/cancel`, {method: "POST"});
      }
      $("jobDetail").textContent = "Cancellation requested. Waiting for the backend to stop safely…";
    } catch (error) {
      showError(error.message, true);
    } finally {
      $("cancelJob").disabled = false;
    }
  });

  /* --- Windows Desktop App Integrations --- */
  async function updateHardwareStats() {
    try {
      const data = await api("/api/windows/system-stats");
      if (data && data.gpu) {
        const gpu = data.gpu;
        const vramUsed = Number(gpu.vram_used_gb).toFixed(1);
        const vramTotal = Number(gpu.vram_total_gb).toFixed(1);
        const gpuShort = gpu.name.replace(/NVIDIA (GeForce )?/i, "").replace(/ Laptop GPU/i, "").trim();
        $("gpuStatText").textContent = `${gpuShort}: ${vramUsed}/${vramTotal} GB`;
        $("gpuStatBadge").title = `${gpu.name}\n${vramUsed} GB of ${vramTotal} GB VRAM used (${gpu.vram_percent}%)\nFree: ${gpu.vram_free_gb} GB`;
        $("gpuStatBadge").hidden = false;
      }
      if (data && data.ram) {
        const ram = data.ram;
        $("ramStatText").textContent = `RAM: ${Number(ram.used_gb).toFixed(1)}/${Number(ram.total_gb).toFixed(0)} GB`;
        $("ramStatBadge").title = `System RAM: ${ram.used_gb} GB of ${ram.total_gb} GB used (${ram.percent}%)\nAvailable: ${ram.free_gb} GB`;
        $("ramStatBadge").hidden = false;
      }
    } catch {
      // Non-critical telemetry
    }
  }

  $("openOutputsFolder").addEventListener("click", async () => {
    try {
      await api("/api/windows/open-folder", {method: "POST"});
    } catch (error) {
      showError(error.message);
    }
  });

  const revealBtn = $("revealInExplorer");
  if (revealBtn) {
    revealBtn.addEventListener("click", async () => {
      if (!state.result?.image_url) return;
      try {
        await api("/api/windows/reveal", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({path: state.result.image_url})
        });
      } catch (error) {
        showError(error.message, true);
      }
    });
  }

  // Window-wide drag and drop for editing images
  window.addEventListener("dragover", event => {
    event.preventDefault();
    if (event.dataTransfer?.types?.includes("Files")) {
      document.body.classList.add("window-dragging");
    }
  });

  window.addEventListener("dragleave", event => {
    if (event.relatedTarget === null) {
      document.body.classList.remove("window-dragging");
    }
  });

  window.addEventListener("drop", event => {
    event.preventDefault();
    document.body.classList.remove("window-dragging");
    const file = event.dataTransfer?.files?.[0];
    if (file && (file.type.startsWith("image/") || /\.(png|jpe?g|webp)$/i.test(file.name))) {
      chooseTask("edit");
      setSource(file);
    }
  });

  document.addEventListener("keydown", event => {
    if ((event.ctrlKey || event.metaKey) && event.key === "Enter" && !state.jobId && !state.batchId) {
      event.preventDefault();
      $("studioForm").requestSubmit();
    } else if ((event.ctrlKey || event.metaKey) && event.shiftKey && (event.key === "O" || event.key === "o")) {
      event.preventDefault();
      $("openOutputsFolder").click();
    } else if ((event.ctrlKey || event.metaKey) && (event.key === "s" || event.key === "S") && state.result?.image_url) {
      event.preventDefault();
      $("downloadImage").click();
    } else if ((event.ctrlKey || event.metaKey) && (event.key === "o" || event.key === "O") && !event.shiftKey) {
      event.preventDefault();
      chooseTask("edit");
      $("sourceInput").click();
    }
  });

  /* --- Initialization --- */
  state.prompts = [""];
  state.promptUndos = [null];
  state.promptChecks = [];
  state.activePromptIndex = 0;
  state.enhancer = "dolphin";

  try {
    const saved = JSON.parse(localStorage.getItem("qwen-studio-settings") || "null");
    if (saved) {
      // Ensure old prompts are never restored on app startup
      if ("prompt" in saved || "prompts" in saved) {
        delete saved.prompt;
        delete saved.prompts;
        try {
          localStorage.setItem("qwen-studio-settings", JSON.stringify(saved));
        } catch { /* Storage is optional */ }
      }
      $("aspectRatio").value = saved.aspect_ratio || "1:1";
      $("seed").value = saved.seed ?? "";
      $("steps").value = saved.num_steps ?? "";
      $("preserve").checked = saved.preserve !== false;
      $("editRegion").value = saved.region || "auto";
      $("transparent").checked = !!saved.is_transparent;
      if ($("loraToggle") && saved.lora_enabled !== undefined) {
        $("loraToggle").checked = !!saved.lora_enabled;
      }
      if ($("loraStrength") && saved.lora_strength !== undefined) {
        $("loraStrength").value = saved.lora_strength;
        if ($("loraStrengthVal")) $("loraStrengthVal").textContent = Number(saved.lora_strength).toFixed(2);
      }
      if ($("loraStrengthContainer") && $("loraToggle")) {
        $("loraStrengthContainer").classList.toggle("disabled", !$("loraToggle").checked);
      }
      if (saved.enhancer) {
        state.enhancer = saved.enhancer === "qwen" ? "qwen" : "dolphin";
      }
      $("enhancementStyle").value = saved.enhancement_style === "polish" ? "polish" : "describe";
      chooseMode(saved.mode || "balanced");
      chooseTask(saved.task || "generate");
    }
  } catch { /* Settings are a convenience. */ }

  renderPromptCards();
  updateEnhancerSelectorUI();
  if ($("enhancerDolphinBtn")) $("enhancerDolphinBtn").addEventListener("click", () => setEnhancer("dolphin"));
  if ($("enhancerQwenBtn")) $("enhancerQwenBtn").addEventListener("click", () => setEnhancer("qwen"));

  try {
    const storedBatch = localStorage.getItem("qwen-studio-batch");
    if (storedBatch) {
      state.batchId = storedBatch;
      state.jobId = storedBatch;
      showBatchJob({total: 1, completed: 0, active_index: 0, overall_status: "running"});
      updateRun();
      void pollBatch(storedBatch);
    } else {
      const storedJob = localStorage.getItem("qwen-studio-job");
      if (storedJob) {
        state.jobId = storedJob;
        showJob({status: "running", phase: "Reconnecting"});
        updateRun();
        void pollJob(storedJob);
      }
    }
  } catch { /* Stored job handling */ }

  refreshMaskTools();
  void fetchCapabilities();
  void updateHardwareStats();
  setInterval(() => void updateHardwareStats(), 8000);
})();

