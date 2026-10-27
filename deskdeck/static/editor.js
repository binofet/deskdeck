"use strict";

(() => {
  const key = "deskdeck.setup-draft.v1";
  const list = document.getElementById("computer-editor-list");
  const owner = document.getElementById("editor-input-owner");
  const status = document.getElementById("editor-status");
  let draft = { schema_version: 1, computers: [], input_owner: null };
  let revision = 0;
  let recoveryBlocked = false;
  const node = (tag, text) => {
    const item = document.createElement(tag);
    if (text !== undefined) item.textContent = text;
    return item;
  };
  const tell = text => { status.textContent = text; };
  function remember() {
    revision += 1;
    if (recoveryBlocked) return;
    try { localStorage.setItem(key, JSON.stringify(draft)); }
    catch (_) { tell("Browser storage is unavailable. Export a copy before leaving."); }
    renderMap();
  }
  function renderMap() {
    const map = document.getElementById("editor-anchor-map");
    map.replaceChildren();
    for (const [anchor, label] of [["above", "Above Dell"], ["left", "Left of Dell"], ["dell", "Dell panes"], ["right", "Right of Dell"], ["below", "Below Dell"], ["unplaced", "Unplaced screens"]]) {
      const cell = node("div"); cell.className = "anchor-cell anchor-" + anchor;
      cell.append(node("strong", label));
      for (const computer of draft.computers) for (const display of computer.displays) {
        if (display.anchor === anchor) cell.append(node("span", `${computer.name || "Unnamed computer"} · ${display.name || "Unnamed screen"}`));
      }
      map.append(cell);
    }
    const headless = draft.computers.filter(computer => !computer.displays.length).map(computer => computer.name || "Unnamed computer");
    if (headless.length) {
      const note = node("p", "No screen anchor: " + headless.join(", ")); note.className = "anchor-headless"; map.append(note);
    }
  }
  function field(label, input) {
    const wrapper = node("label", label);
    wrapper.className = "editor-field";
    wrapper.append(input);
    return wrapper;
  }
  function select(values, value, change) {
    const input = node("select");
    for (const [id, text] of values) {
      const option = node("option", text);
      option.value = id;
      input.append(option);
    }
    input.value = value || "";
    input.addEventListener("change", () => { change(input.value); remember(); });
    return input;
  }
  function textInput(value, change) {
    const input = node("input");
    input.type = "text";
    input.maxLength = 64;
    input.value = value;
    input.addEventListener("input", () => { change(input.value); remember(); });
    input.addEventListener("change", renderOwner);
    return input;
  }
  function button(label, action) {
    const item = node("button", label);
    item.type = "button";
    item.className = "button button-secondary";
    item.addEventListener("click", action);
    return item;
  }
  function renderOwner() {
    owner.replaceChildren();
    const empty = node("option", "Choose later"); empty.value = ""; owner.append(empty);
    for (const computer of draft.computers) {
      const option = node("option", computer.name || "Unnamed computer");
      option.value = computer.id; owner.append(option);
    }
    owner.value = draft.input_owner || "";
  }
  function render() {
    list.replaceChildren();
    if (!draft.computers.length) list.append(node("p", "Start with the computer you are using, then add its neighbors."));
    for (const computer of draft.computers) {
      const card = node("section"); card.className = "computer-editor-card";
      card.append(field("Computer name", textInput(computer.name, value => { computer.name = value; })));
      card.append(field("Dell video input", select([["", "Not connected to Dell"], ...["Thunderbolt", "DP1", "DP2", "HDMI1", "HDMI2"].map(x => [x, x])], computer.dell_input, value => { computer.dell_input = value || null; })));
      card.append(field("USB upstream", select([["", "Not assigned"], ...["Thunderbolt", "USB2", "USB3", "USB4"].map(x => [x, x])], computer.usb_upstream, value => { computer.usb_upstream = value || null; })));
      const screens = node("div"); screens.className = "editor-screens";
      if (!computer.displays.length) screens.append(node("p", "No screens described. This computer has no pointer-edge anchor."));
      for (const display of computer.displays) {
        const row = node("div"); row.className = "editor-display";
        row.append(field("Screen name", textInput(display.name, value => { display.name = value; })));
        row.append(field("Screen type", select([["built-in", "Built-in screen"], ["external", "External screen"]], display.kind, value => { display.kind = value; })));
        row.append(field("Screen anchor", select([["unplaced", "Choose later"], ["left", "Left of Dell"], ["right", "Right of Dell"], ["above", "Above Dell"], ["below", "Below Dell"], ["dell", "A pane on Dell"]], display.anchor, value => { display.anchor = value; display.placement = null; })));
        row.append(button("Remove screen", () => {
          computer.displays = computer.displays.filter(item => item.id !== display.id);
          remember(); render();
        }));
        screens.append(row);
      }
      card.append(screens);
      card.append(button("Add screen +", () => {
        if (draft.computers.reduce((sum, item) => sum + item.displays.length, 0) >= 16) return tell("This setup supports up to 16 screens.");
        computer.displays.push({ id: "screen-" + crypto.randomUUID().replaceAll("-", "").slice(0, 20), name: "New screen", kind: "external", anchor: "unplaced" });
        remember(); render();
      }));
      card.append(button("Remove computer", () => {
        if (computer.displays.length || draft.input_owner === computer.id) return tell("Remove this computer’s screens and choose another keyboard owner first. Nothing was removed.");
        draft.computers = draft.computers.filter(item => item.id !== computer.id);
        remember(); render(); tell("Computer removed from this draft only.");
      }));
      list.append(card);
    }
    renderOwner();
    renderMap();
  }
  async function validate(payload) {
    const session = await fetch("/api/session", { cache: "no-store", signal: AbortSignal.timeout(12000) });
    if (!session.ok) throw new Error("The local setup service is unavailable.");
    const { token } = await session.json();
    const response = await fetch("/api/desk-model", {
      method: "POST", headers: { "Content-Type": "application/json", "X-DeskDeck-Token": token },
      body: JSON.stringify(payload), cache: "no-store", credentials: "same-origin", signal: AbortSignal.timeout(12000),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Check the screen anchors and input assignments.");
    return result.model;
  }
  document.getElementById("add-computer").addEventListener("click", () => {
    if (draft.computers.length >= 16) return tell("This setup supports up to 16 computers.");
    draft.computers.push({ id: "computer-" + crypto.randomUUID().replaceAll("-", "").slice(0, 20), name: "New computer", displays: [], dell_input: null, usb_upstream: null });
    remember(); render(); tell("Computer added to your draft. Add any screens it uses.");
  });
  owner.addEventListener("change", () => { draft.input_owner = owner.value || null; remember(); });
  document.getElementById("export-desk-model").addEventListener("click", async () => {
    try {
      const snapshot = JSON.parse(JSON.stringify(draft));
      const model = await validate({ model: snapshot });
      const url = URL.createObjectURL(new Blob([JSON.stringify(model, null, 2) + "\n"], { type: "application/json" }));
      const link = node("a"); link.href = url; link.download = "deskdeck-setup.json";
      document.body.append(link); link.click(); link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      tell("Setup validated and exported. Your displays, USB and Deskflow remain unchanged.");
    } catch (error) { tell(error.message); }
  });
  document.getElementById("import-desk-model").addEventListener("change", async event => {
    const file = event.target.files[0];
    try {
      if (!file || file.size > 65536) throw new Error("Choose a setup JSON file smaller than 64 KiB.");
      const version = revision;
      const model = await validate({ document: await file.text() });
      if (revision !== version) throw new Error("Your draft changed during import. Try importing again when ready.");
      recoveryBlocked = false;
      document.getElementById("add-computer").disabled = false;
      document.getElementById("export-desk-model").disabled = false;
      owner.disabled = false;
      draft = model; remember(); render(); tell("Setup imported as a draft. Nothing was applied.");
    } catch (error) { tell(error.message); }
    finally { event.target.value = ""; }
  });
  // A draft can contain unfinished names or temporary wiring conflicts. Full
  // validation belongs to import/export, not recovery of an editing session.
  function recoverDraft(value) {
    const keys = (item, required, optional = []) => item && typeof item === "object" && !Array.isArray(item) && required.every(key => Object.hasOwn(item, key)) && Object.keys(item).every(key => required.includes(key) || optional.includes(key));
    const id = value => typeof value === "string" && /^[a-z][a-z0-9_-]{0,31}$/.test(value);
    const name = value => typeof value === "string" && [...value].length <= 64;
    const port = (value, allowed) => value === undefined || value === null || allowed.includes(value);
    const ids = new Set(), screens = new Set();
    if (!keys(value, ["schema_version", "computers", "input_owner"]) || value.schema_version !== 1 || !Array.isArray(value.computers) || value.computers.length > 16) throw new Error("Invalid draft");
    for (const computer of value.computers) {
      if (!keys(computer, ["id", "name", "displays"], ["dell_input", "usb_upstream"]) || !id(computer.id) || ids.has(computer.id) || !name(computer.name) || !Array.isArray(computer.displays)) throw new Error("Invalid computer draft");
      if (!port(computer.dell_input, ["Thunderbolt", "USB-C", "DP1", "DP2", "HDMI1", "HDMI2"]) || !port(computer.usb_upstream, ["Thunderbolt", "USB2", "USB3", "USB4"])) throw new Error("Invalid wiring draft");
      ids.add(computer.id);
      for (const display of computer.displays) {
        if (!keys(display, ["id", "name", "kind", "anchor"], ["placement"]) || !id(display.id) || screens.has(display.id) || screens.size >= 16 || !name(display.name) || !["built-in", "external"].includes(display.kind) || !["left", "right", "above", "below", "dell", "unplaced"].includes(display.anchor)) throw new Error("Invalid screen draft");
        screens.add(display.id);
        const placement = display.placement;
        if (placement !== undefined && placement !== null && (!keys(placement, ["x", "y", "width", "height"]) || !Object.values(placement).every(number => typeof number === "number" && Number.isFinite(number) && number >= 0 && number <= 1) || placement.width <= 0 || placement.height <= 0 || placement.x + placement.width > 1 || placement.y + placement.height > 1)) throw new Error("Invalid placement draft");
      }
    }
    if (value.input_owner !== null && !ids.has(value.input_owner)) throw new Error("Invalid input owner");
    return value;
  }
  function restore() {
    let saved;
    try { saved = localStorage.getItem(key); }
    catch (_) {
      tell("Browser storage is unavailable. Export a copy before leaving.");
      render(); return;
    }
    try {
      if (saved) {
        if (saved.length > 65536) throw new Error("Draft too large");
        draft = recoverDraft(JSON.parse(saved));
      }
    } catch (_) {
      recoveryBlocked = true;
      document.getElementById("add-computer").disabled = true;
      document.getElementById("export-desk-model").disabled = true;
      owner.disabled = true;
      tell("The saved draft could not be opened. Its data is untouched. Import a valid setup to replace it.");
    }
    render();
  }
  restore();
})();
