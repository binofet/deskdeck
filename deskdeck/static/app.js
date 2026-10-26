"use strict";

(() => {
  const devices = {
    main: { name: "Main", label: "Main laptop" },
    mac: { name: "Mac", label: "Mac" },
    linux: { name: "Linux", label: "Linux" },
    windows: { name: "Windows", label: "Windows laptop" },
  };

  const fallbackProfiles = [
    { id: "shared", name: "Shared desk", description: "Main, Linux & Mac together", mode: "shared", focus: null, regions: [{ device: "main", position: "left" }, { device: "linux", position: "upper-right" }, { device: "mac", position: "lower-right" }], left: "main", right: "windows" },
    ...["main", "mac", "linux", "windows"].map(device => ({ id: `focus-${device}`, name: `${devices[device].name} focus`, description: `${devices[device].label} on the full display`, mode: "focus", focus: device, regions: [{ device, position: "fullscreen" }], left: "main", right: "windows" })),
  ];

  const glyphPaths = {
    shared: '<rect x="2" y="3" width="16" height="14" rx="2"/><path d="M10 3v14m0-7h8"/>',
    main: '<rect x="3" y="3" width="14" height="10" rx="1.5"/><path d="M1 16h18M7 13l-1 3m7-3 1 3"/>',
    mac: '<path d="M7 7V4.5a2.5 2.5 0 1 0-2.5 2.5h11A2.5 2.5 0 1 0 13 4.5v11a2.5 2.5 0 1 0 2.5-2.5h-11A2.5 2.5 0 1 0 7 15.5V7Z"/>',
    linux: '<rect x="2" y="3" width="16" height="14" rx="2"/><path d="m5 7 3 3-3 3m6 0h4"/>',
    windows: '<rect x="2" y="2" width="6" height="6" rx=".6"/><rect x="12" y="2" width="6" height="6" rx=".6"/><rect x="2" y="12" width="6" height="6" rx=".6"/><rect x="12" y="12" width="6" height="6" rx=".6"/>',
  };

  const $ = id => document.getElementById(id);
  const state = { profiles: fallbackProfiles, selectedId: "shared", status: null, token: null, backendReady: false, loading: true, reviewing: false, applying: false, plan: null };
  const elements = Object.fromEntries(["profile-list", "preview-title", "layout-badge", "desk-visual", "monitor-screen", "display-description", "pointer-description", "selection-name", "review-button", "apply-button", "apply-hint", "mode-badge", "mode-label", "connection-title", "connection-message", "hardware-indicator", "deskflow-indicator", "retry-button", "current-profile", "plan-panel", "plan-title", "plan-status", "plan-intro", "plan-steps", "plan-blockers", "blocker-list", "feedback"].map(id => [id, $(id)]));

  const make = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };

  function glyph(device) {
    const span = make("span", "device-glyph");
    span.setAttribute("aria-hidden", "true");
    span.innerHTML = `<svg viewBox="0 0 20 20" fill="none">${glyphPaths[device] || glyphPaths.shared}</svg>`;
    return span;
  }

  function selectedProfile() { return state.profiles.find(profile => profile.id === state.selectedId) || state.profiles[0]; }

  function humanize(value) {
    return String(value || "").replace(/[_-]+/g, " ").replace(/^\w/, letter => letter.toUpperCase());
  }

  function readableDetails(value) {
    if (value === null || value === undefined) return "";
    if (typeof value === "string") return value;
    if (Array.isArray(value)) return value.map(readableDetails).filter(Boolean).join(" · ");
    if (typeof value === "object") return Object.entries(value).map(([key, item]) => `${humanize(key)}: ${readableDetails(item)}`).join(" · ");
    return String(value);
  }

  async function api(path, options = {}) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 12000);
    try {
      const headers = { Accept: "application/json", ...options.headers };
      if (options.body) headers["Content-Type"] = "application/json";
      if (options.method && options.method !== "GET" && state.token) headers["X-DeskDeck-Token"] = state.token;
      const response = await fetch(path, { ...options, headers, credentials: "same-origin", cache: "no-store", signal: controller.signal });
      let result;
      try { result = await response.json(); } catch (_) { throw new Error("The local service returned an unreadable response."); }
      if (!response.ok) throw new Error(readableDetails(result.error || result.message) || `The request could not be completed (${response.status}).`);
      return result;
    } catch (error) {
      if (error.name === "AbortError") throw new Error("The local service did not respond in time.");
      throw error;
    } finally { clearTimeout(timeout); }
  }

  function renderProfiles() {
    const legend = make("legend", "visually-hidden", "Desk profile");
    const children = [legend];
    for (const [index, profile] of state.profiles.entries()) {
      const label = make("label", "profile-option");
      const input = make("input");
      input.type = "radio";
      input.name = "desk-profile";
      input.value = profile.id;
      input.id = `profile-${index}`;
      input.checked = profile.id === state.selectedId;
      input.addEventListener("change", () => selectProfile(profile.id));
      const card = make("span", "profile-card");
      const device = profile.mode === "shared" ? "shared" : profile.focus;
      const icon = make("span", `profile-icon icon-${device}`);
      icon.append(glyph(device).firstElementChild);
      icon.setAttribute("aria-hidden", "true");
      const copy = make("span", "profile-copy");
      copy.append(make("span", "profile-name", profile.name), make("span", "profile-description", profile.description || "Complete desk profile"));
      const check = make("span", "profile-check");
      check.setAttribute("aria-hidden", "true");
      check.innerHTML = '<svg viewBox="0 0 16 16"><path d="m3 8 3 3 7-7"/></svg>';
      card.append(icon, copy, check);
      label.append(input, card);
      children.push(label);
    }
    elements["profile-list"].replaceChildren(...children);
  }

  function selectProfile(id) {
    if (state.applying || id === state.selectedId) return;
    state.selectedId = id;
    state.plan = null;
    elements["plan-panel"].hidden = true;
    elements.feedback.hidden = true;
    document.querySelectorAll('input[name="desk-profile"]').forEach(input => { input.checked = input.value === id; });
    renderPreview();
    renderStatus();
  }

  function renderPreview() {
    const profile = selectedProfile();
    if (!profile) return;
    const shared = profile.mode === "shared";
    const focus = devices[profile.focus] ? profile.focus : "main";
    elements["preview-title"].textContent = profile.name;
    elements["selection-name"].textContent = profile.name;
    elements["layout-badge"].textContent = shared ? "Three views. One display." : `${devices[focus].name}, room to focus.`;
    elements["monitor-screen"].className = `monitor-screen ${shared ? "shared-layout" : "focus-layout"}`;
    const regions = shared ? ["main", "linux", "mac"] : [focus];
    const positionNames = shared ? ["Left · full height", "Upper right", "Lower right"] : ["Full display"];
    elements["monitor-screen"].replaceChildren(...regions.map((device, index) => {
      const region = make("div", `screen-region device-${device} region-${device}`);
      region.append(glyph(device), make("span", "region-name", devices[device].name), make("span", "region-position", positionNames[index]));
      return region;
    }));
    elements["display-description"].textContent = shared ? "Main left · Linux upper right · Mac lower right" : `${devices[focus].label} · full display`;
    elements["pointer-description"].textContent = "Main laptop left · Windows laptop right";
    elements["desk-visual"].setAttribute("aria-label", `${profile.name}: Main laptop at far left. ${shared ? "Dell display has Main on the left at full height, Linux at upper right, and Mac at lower right." : `Dell display shows ${devices[focus].label} fullscreen.`} Windows laptop at far right.`);
  }

  function canApply() {
    return state.backendReady && state.token && state.status?.mode === "live" && state.status.hardware_ready === true && state.status.deskflow_ready === true && state.plan?.ready === true && state.plan.profile?.id === state.selectedId && !state.reviewing && !state.applying;
  }

  function renderStatus() {
    const status = state.status;
    const preview = status?.mode !== "live";
    const online = state.backendReady;
    elements["mode-label"].textContent = state.loading ? "Connecting" : !online ? "Service offline" : preview ? "Preview mode" : "Live mode";
    elements["mode-badge"].classList.toggle("is-offline", !state.loading && !online);
    elements["mode-badge"].classList.toggle("is-live", online && !preview);
    elements["connection-title"].textContent = state.loading ? "Connecting to your desk" : !online ? "Your desk is in preview" : preview ? "Explore your desk in preview" : status.hardware_ready && status.deskflow_ready ? "Your desk is connected" : "A little setup, then you're ready";
    elements["connection-message"].textContent = state.loading ? "Checking display controls and Deskflow." : !online ? "The local DeskDeck service is unavailable. These are layout previews; your desk has not changed." : status.message || (preview ? "Review any profile. Connect display controls and Deskflow to enable switching." : "Review a profile before applying it to your desk.");
    for (const [id, ready, name] of [["hardware-indicator", online && status?.hardware_ready, "Display controls"], ["deskflow-indicator", online && status?.deskflow_ready, "Deskflow"]]) {
      elements[id].classList.toggle("is-ready", ready === true);
      elements[id].classList.toggle("is-pending", ready !== true);
      elements[id].title = `${name}: ${ready ? "connected" : "setup pending"}`;
      elements[id].setAttribute("aria-label", `${name}: ${ready ? "connected" : "setup pending"}`);
    }
    elements["retry-button"].hidden = state.loading || online;
    elements["review-button"].disabled = !online || !state.token || state.reviewing || state.applying;
    elements["review-button"].firstChild.textContent = state.reviewing ? "Preparing plan… " : "Review profile ";
    elements["apply-button"].disabled = !canApply();
    elements["apply-button"].firstChild.textContent = state.applying ? "Applying… " : "Apply profile ";
    document.querySelectorAll('input[name="desk-profile"]').forEach(input => { input.disabled = state.applying; });
    let hint;
    if (state.loading) hint = "Connecting to the local DeskDeck service…";
    else if (!online) hint = "Start the local service to review and apply profiles.";
    else if (!state.token) hint = "A local session is needed. Retry the connection to continue.";
    else if (preview) hint = "Preview mode · Your desk will stay as it is.";
    else if (!status.hardware_ready || !status.deskflow_ready) hint = "Connect display controls and Deskflow to enable Apply.";
    else if (!state.plan) hint = "Review this profile to enable Apply.";
    else if (!state.plan.ready) hint = "Finish the setup shown in the profile plan to enable Apply.";
    else hint = "Ready to apply the complete desk profile.";
    elements["apply-hint"].textContent = hint;
    const current = state.profiles.find(profile => profile.id === status?.current_profile);
    elements["current-profile"].textContent = current ? `Current profile: ${current.name}` : "No profile applied in this session";
  }

  function showFeedback(message, isError = false) {
    elements.feedback.textContent = message;
    elements.feedback.classList.toggle("is-error", isError);
    elements.feedback.hidden = false;
  }

  function renderPlan() {
    const plan = state.plan;
    if (!plan) return;
    const profile = selectedProfile();
    const preview = state.status?.mode !== "live";
    elements["plan-title"].textContent = `${profile.name} plan`;
    elements["plan-status"].textContent = preview ? "Preview only" : plan.ready ? "Ready to apply" : "Setup needed";
    elements["plan-intro"].textContent = preview ? "Here's what this complete profile will change when your desk is connected. Reviewing this plan leaves your desk as it is." : "This plan covers the display layout and the pointer path. Apply when you're ready to switch the whole desk.";
    const steps = Array.isArray(plan.steps) ? plan.steps : [];
    elements["plan-steps"].replaceChildren(...steps.map(step => {
      const item = make("li", "plan-step");
      const copy = make("div");
      copy.append(make("span", "step-phase", humanize(step.phase || "Desk profile")), make("h3", "step-action", humanize(step.action || "Update desk layout")));
      const details = readableDetails(step.details);
      if (details) copy.append(make("p", "step-details", details));
      item.append(copy);
      return item;
    }));
    const blockers = Array.isArray(plan.blockers) ? plan.blockers : [];
    elements["plan-blockers"].hidden = blockers.length === 0;
    elements["blocker-list"].replaceChildren(...blockers.map(blocker => make("li", "", readableDetails(blocker))));
    elements["plan-panel"].hidden = false;
  }

  async function initialize() {
    if (state.loading && state.initialized) return;
    state.initialized = true;
    state.loading = true;
    state.backendReady = false;
    state.plan = null;
    state.token = null;
    elements["plan-panel"].hidden = true;
    elements.feedback.hidden = true;
    renderStatus();
    const [profilesResult, statusResult, sessionResult] = await Promise.allSettled([api("/api/profiles"), api("/api/status"), api("/api/session")]);
    let validProfiles = false;
    if (profilesResult.status === "fulfilled") {
      const profiles = profilesResult.value.profiles;
      if (Array.isArray(profiles) && profiles.length > 0 && profiles.every(profile => typeof profile.id === "string" && typeof profile.name === "string" && ["shared", "focus"].includes(profile.mode) && (profile.mode === "shared" || devices[profile.focus]))) {
        const oldSelection = selectedProfile();
        state.profiles = profiles;
        state.selectedId = (profiles.find(profile => profile.id === state.selectedId) || profiles.find(profile => profile.mode === oldSelection.mode && profile.focus === oldSelection.focus) || profiles[0]).id;
        validProfiles = true;
      }
    }
    state.status = statusResult.status === "fulfilled" ? statusResult.value : null;
    state.token = sessionResult.status === "fulfilled" && typeof sessionResult.value.token === "string" ? sessionResult.value.token : null;
    state.backendReady = validProfiles && statusResult.status === "fulfilled";
    state.loading = false;
    renderProfiles();
    renderPreview();
    renderStatus();
    if (state.backendReady && !state.token) {
      elements["retry-button"].hidden = false;
      showFeedback("The local session could not be started. Retry the connection to review a profile.", true);
    }
  }

  async function reviewProfile() {
    if (!state.backendReady || !state.token || state.reviewing || state.applying) return;
    const profileId = state.selectedId;
    state.reviewing = true;
    elements.feedback.hidden = true;
    renderStatus();
    try {
      const plan = await api("/api/plan", { method: "POST", body: JSON.stringify({ profile_id: profileId }) });
      if (state.selectedId !== profileId) return;
      if (!plan.profile || plan.profile.id !== profileId || !Array.isArray(plan.steps)) throw new Error("The service returned a plan for a different or incomplete profile. Please review again.");
      state.plan = plan;
      renderPlan();
      elements["plan-panel"].scrollIntoView({ behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth", block: "nearest" });
    } catch (error) { showFeedback(`${error.message} Your desk has not changed.`, true); }
    finally { state.reviewing = false; renderStatus(); }
  }

  async function applyProfile() {
    if (!canApply()) return;
    const profileId = state.selectedId;
    state.applying = true;
    elements.feedback.hidden = true;
    renderStatus();
    try {
      const result = await api("/api/apply", { method: "POST", body: JSON.stringify({ profile_id: profileId }) });
      state.status = await api("/api/status");
      if (state.status.current_profile === profileId) showFeedback(`${selectedProfile().name} is the current profile reported by DeskDeck.`);
      else showFeedback(result.message || "The apply request completed. The service has not confirmed the current profile; check your desk before continuing.");
      state.plan = null;
      elements["plan-panel"].hidden = true;
    } catch (error) {
      showFeedback(`${error.message} The switch could not be confirmed. Review the desk before trying again.`, true);
      try { state.status = await api("/api/status"); } catch (_) { state.backendReady = false; }
    } finally { state.applying = false; renderStatus(); }
  }

  document.querySelectorAll("[data-glyph]").forEach(node => { node.replaceChildren(glyph(node.dataset.glyph).firstElementChild); });
  elements["review-button"].addEventListener("click", reviewProfile);
  elements["apply-button"].addEventListener("click", applyProfile);
  elements["retry-button"].addEventListener("click", initialize);
  renderProfiles();
  renderPreview();
  initialize();
})();
