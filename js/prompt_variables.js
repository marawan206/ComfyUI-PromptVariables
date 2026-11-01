import { app } from "../../scripts/app.js"
import { api } from "../../scripts/api.js"

const LOADER = "PromptVariablesLoadSpreadsheet"

// Keep VARIABLE and normalize() in sync with prompt_variables.py.
const VARIABLE = /\\([\[\]])|\[([^\[\]\\:|\r\n]+)\]/g
const VALID_NAME = /^[^\[\]\\:|\r\n]+$/

function normalize(name) {
  return name.normalize("NFKC").replace(/_/g, " ").trim().split(/\s+/).join(" ").toLowerCase()
}

function parseTemplate(text) {
  const names = []
  const repeated = []
  for (const m of text.matchAll(VARIABLE)) {
    const name = m[2] ? normalize(m[2]) : ""
    if (!name) continue
    if (!names.includes(name)) names.push(name)
    else if (!repeated.includes(name)) repeated.push(name)
  }
  let open = 0
  for (const ch of text.replace(/\\[\[\]]/g, "")) {
    if (ch === "[") open++
    else if (ch === "]" && open > 0) open--
  }
  return { names, repeated, unclosed: open > 0 }
}

function fill(text, values) {
  return text.replace(VARIABLE, (match, escaped, name) => {
    if (escaped) return escaped
    const key = normalize(name)
    return key ? values[key] ?? "" : match
  })
}

function chain(object, key, fn) {
  if (!object) return
  const original = object[key]
  object[key] = function (...args) {
    const result = original?.apply(this, args)
    fn.apply(this, args)
    return result
  }
}

const getWidget = (node, name) => node.widgets?.find((w) => w.name === name)
const isBox = (w) => w.name.startsWith("[") && w.name.endsWith("]")
const templateText = (node) => `${getWidget(node, "template")?.value ?? ""}`
// The template with every variable name blanked, to tell a rename from a rewrite.
const skeleton = (text) => text.replace(VARIABLE, (match, escaped) => (escaped ? match : "[]"))

function templateLinked(node) {
  const slot = node.inputs?.findIndex((input) => input.widget?.name === "template") ?? -1
  return slot >= 0 && node.isInputConnected(slot)
}

function addButton(node, name, label, onClick) {
  const button = node.addWidget("button", name, "", onClick, { serialize: false })
  button.label = label
  button.serialize = false
}

function addStatus(node) {
  const el = document.createElement("div")
  Object.assign(el.style, { fontSize: "11px", lineHeight: "15px", padding: "2px 6px", color: "var(--descrip-text, #999)" })
  const height = () => el.childElementCount * 15 + 14
  const widget = node.addDOMWidget("pv_status", "pv-status", el, {
    serialize: false,
    hideOnZoom: false,
    getMinHeight: height,
    getMaxHeight: height,
  })
  widget.serialize = false
  node.pvStatus = el
}

function setStatus(node, lines) {
  node.pvStatus.replaceChildren(...lines.map(({ text, warn }) => {
    const line = document.createElement("div")
    Object.assign(line.style, { whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" })
    if (warn) line.style.color = "var(--error-text, #f5a623)"
    line.textContent = text
    line.title = text
    return line
  }))
  const size = node.computeSize()
  if (size[1] > node.size[1]) node.setSize([node.size[0], size[1]])
  node.setDirtyCanvas(true, true)
}

// ---- Prompt Variables ----

function sheetInfo(node) {
  const slot = node.findInputSlot("spreadsheet")
  if (slot < 0 || !node.graph || !node.isInputConnected(slot)) return null
  let source = node.getInputNode(slot)
  while (source?.type === "Reroute") source = source.getInputNode(0)
  if (source?.type !== LOADER) return { unavailable: true }
  return source.pvInfo ?? { pending: true }
}

function addBox(node, name) {
  const widget = node.addWidget("text", `[${name}]`, "", (value) => {
    node.pvValues[name] = value
    updateStatus(node)
  })
  widget.serialize = false
  widget.value = node.pvValues[name] ?? ""
}

function orderBoxes(node, names) {
  const widgets = node.widgets
  const wanted = names.map((name) => `[${name}]`)
  const others = widgets.filter((w) => !isBox(w)).map((w) => w.name)
  const at = others.indexOf("pv_add")
  const desired = [...others.slice(0, at), ...wanted, ...others.slice(at)]
  if (desired.every((name, i) => widgets[i]?.name === name)) return
  for (const name of wanted) {
    const [widget] = widgets.splice(widgets.findIndex((w) => w.name === name), 1)
    widgets.splice(widgets.findIndex((w) => w.name === "pv_add"), 0, widget)
  }
}

// Returns how many boxes were added minus removed.
function syncBoxes(node, typing) {
  const text = templateText(node)
  const { names, unclosed } = parseTemplate(text)
  let change = 0
  // Mid-edit ("[clo") keeps the boxes as they are so they don't flicker. A linked template isn't this text.
  if (!(typing && (unclosed || templateLinked(node)))) {
    const current = node.widgets.filter(isBox).map((w) => w.name.slice(1, -1))
    const added = names.filter((name) => !current.includes(name))
    const removed = current.filter((name) => !names.includes(name))
    const renamed = typing && added.length === 1 && removed.length === 1 && skeleton(text) === skeleton(node.pvSynced ?? "")
    if (renamed && !node.pvValues[added[0]]) node.pvValues[added[0]] = node.pvValues[removed[0]] ?? ""
    node.pvSynced = text
    for (const name of removed) node.removeWidget(getWidget(node, `[${name}]`))
    for (const name of added) addBox(node, name)
    orderBoxes(node, names)
    change = added.length - removed.length
  }
  updateStatus(node)
  return change
}

// Grows or shrinks the node with its boxes so the template keeps its size.
function syncAndResize(node, typing) {
  const height = node.size[1]
  const change = syncBoxes(node, typing)
  if (!change) return
  node.setSize([node.size[0], Math.max(height + change * (LiteGraph.NODE_WIDGET_HEIGHT + 4), node.computeSize()[1])])
  node.setDirtyCanvas(true, true)
}

function updateStatus(node) {
  if (templateLinked(node)) {
    setStatus(node, [{ text: "The template comes from a link, so there is no preview here." }])
    return
  }
  const template = templateText(node)
  const { names, repeated, unclosed } = parseTemplate(template)
  const sheet = sheetInfo(node)
  const lines = []
  const unclosedWarning = { text: "⚠ A [ has no closing ]. Type \\[ for a real bracket.", warn: true }
  // Without the columns there is nothing to check the variables against.
  if (sheet && !sheet.columns) {
    if (sheet.error) lines.push({ text: `⚠ ${sheet.error}`, warn: true })
    else lines.push({ text: sheet.pending ? "Reading spreadsheet…" : "Spreadsheet connected: empty boxes are filled from it when you run." })
    if (unclosed) lines.push(unclosedWarning)
    setStatus(node, lines)
    return
  }

  const columns = Object.create(null)
  const values = Object.create(null)
  values.row = "1"
  if (sheet) {
    const [first, last] = sheet.numbers
    lines.push({ text: `${sheet.count} prompt${sheet.count === 1 ? "" : "s"} · rows ${first === last ? first : `${first}–${last}`}` })
    values.row = String(first)
    sheet.columns.forEach((column, i) => {
      const key = normalize(column)
      if (key in columns) return
      columns[key] = column
      values[key] = sheet.first_row[i]
    })
  }

  let usesSheet = false
  for (const name of names) {
    const typed = `${getWidget(node, `[${name}]`)?.value ?? ""}`
    if (typed.trim()) {
      values[name] = typed
      if (sheet) lines.push({ text: `[${name}] typed: same for every row` })
    } else if (name in columns) {
      usesSheet = true
      lines.push({ text: `[${name}] ← column "${columns[name]}"` })
    } else if (name === "row") {
      usesSheet = true
      if (sheet) lines.push({ text: "[row] ← row number" })
    } else {
      lines.push({ text: `⚠ [${name}] is empty${sheet ? ": no column with that name" : ""}`, warn: true })
    }
  }
  if (sheet?.count > 1 && names.length && !usesSheet) {
    lines.push({ text: "⚠ No variable uses the spreadsheet, so every prompt is the same", warn: true })
  }
  for (const name of repeated) {
    lines.push({ text: `⚠ [${name}] is in the template more than once; each gets the same value`, warn: true })
  }
  if (unclosed) lines.push(unclosedWarning)

  if (!names.length) lines.push({ text: "Put words in [square brackets] to turn them into variables." })
  else lines.push({ text: `${sheet ? `Row ${sheet.numbers[0]}` : "Prompt"}: ${fill(template, values)}` })
  setStatus(node, lines)
}

function schedule(node) {
  clearTimeout(node.pvTimer)
  node.pvTimer = setTimeout(() => {
    if (!node.graph) return
    syncAndResize(node, true)
    autoFill(node)
  }, 300)
}

// Names already in the template are skipped, so the same variable can't be added twice by accident.
function appendVariables(node, names) {
  const used = parseTemplate(templateText(node)).names
  const tokens = []
  const repeated = []
  for (const name of names.map((name) => name.trim())) {
    const key = normalize(name)
    if (!key || !VALID_NAME.test(name)) continue
    if (used.includes(key)) {
      repeated.push(`[${name}]`)
      continue
    }
    used.push(key)
    tokens.push(`[${name}]`)
  }
  if (repeated.length) {
    app.extensionManager.toast.warning(`${repeated.join(" ")} ${repeated.length === 1 ? "is" : "are"} already in the template`)
  }
  if (!tokens.length) return
  const template = getWidget(node, "template")
  const text = templateText(node).trimEnd()
  template.value = text ? `${text} ${tokens.join(", ")}` : tokens.join(", ")
  syncAndResize(node, false)
}

// Connecting a spreadsheet to an empty template fills in all of its columns.
function autoFill(node) {
  if (!node.pvAutoFill) return
  const sheet = sheetInfo(node)
  if (sheet?.pending) return
  node.pvAutoFill = false
  if (sheet?.columns && !templateLinked(node) && !templateText(node).trim()) appendVariables(node, sheet.columns)
}

async function askName(node) {
  const name = await app.extensionManager.dialog.prompt({
    title: "New variable",
    message: "What should the variable be called? For example clothing or town.",
    placeholder: "clothing",
  })
  if (name) appendVariables(node, [name.replace(/[\[\]\\:|\r\n]/g, " ")])
}

function showAddMenu(node, event) {
  const used = parseTemplate(templateText(node)).names
  const columns = (sheetInfo(node)?.columns ?? []).filter((c) => VALID_NAME.test(c) && !used.includes(normalize(c)))
  if (!columns.length) return askName(node)
  // ContextMenu renders content containing tags as HTML.
  const label = (text) => text.replace(/[<>]/g, "")
  const items = columns.map((column) => ({ content: label(`[${column}]`), callback: () => appendVariables(node, [column]) }))
  if (columns.length > 1) items.push({ content: "All columns", callback: () => appendVariables(node, columns) })
  items.push(null, { content: "New variable…", callback: () => askName(node) })
  new LiteGraph.ContextMenu(items, { event, title: "Add variable" })
}

function setupPromptVariables(nodeType) {
  chain(nodeType.prototype, "onNodeCreated", function () {
    this.pvValues = Object.create(null)
    chain(getWidget(this, "template"), "callback", () => schedule(this))
    // Vue nodes call button callbacks without an event; window.event is the click.
    addButton(this, "pv_add", "+ Add variable", (value, canvas, node, pos, e) => showAddMenu(this, e ?? window.event))
    addStatus(this)
    updateStatus(this)
  })

  // Boxes are rebuilt from the template on load, paste and undo, and must exist before
  // the graph finishes configuring. Their values are kept by name, not by widget position.
  chain(nodeType.prototype, "onConfigure", function () {
    this.pvValues = Object.assign(Object.create(null), this.properties?.pv_values)
    syncBoxes(this, false)
  })

  chain(nodeType.prototype, "onSerialize", function (o) {
    o.properties ??= {}
    o.properties.pv_values = Object.fromEntries(this.widgets.filter(isBox).map((w) => [w.name.slice(1, -1), `${w.value ?? ""}`]))
  })

  chain(nodeType.prototype, "onConnectionsChange", function (type, index, connected) {
    if (type !== LiteGraph.INPUT) return
    if (this.inputs?.[index]?.name === "spreadsheet") {
      this.pvAutoFill = connected && !app.configuringGraph && !templateText(this).trim()
    }
    schedule(this)
  })
}

// ---- Load Spreadsheet ----

async function upload(node, file) {
  setStatus(node, [{ text: `Uploading ${file.name}…` }])
  const body = new FormData()
  body.append("image", file)
  const res = await api.fetchApi("/upload/image", { method: "POST", body })
  if (res.status !== 200) {
    setStatus(node, [{ text: `⚠ Upload failed: ${res.status} ${res.statusText}`, warn: true }])
    return
  }
  const { name, subfolder } = await res.json()
  const path = subfolder ? `${subfolder}/${name}` : name
  const widget = getWidget(node, "file")
  if (!widget.options.values.includes(path)) widget.options.values.push(path)
  widget.value = path
  widget.callback?.(path)
}

function chooseFile(node) {
  const input = Object.assign(document.createElement("input"), { type: "file", accept: ".csv,.tsv,.xlsx" })
  input.style.display = "none"
  document.body.append(input)
  input.addEventListener("cancel", () => input.remove())
  input.addEventListener("change", () => {
    input.remove()
    if (input.files?.[0]) upload(node, input.files[0])
  })
  input.click()
}

async function fetchPreview(node) {
  if (!node.graph) return
  const token = (node.pvToken ?? 0) + 1
  node.pvToken = token
  const file = getWidget(node, "file")?.value
  let info = { error: "Upload a spreadsheet, or drop one on this node." }
  if (file) {
    const params = new URLSearchParams({
      file,
      rows: getWidget(node, "rows")?.value ?? "all",
      sheet: getWidget(node, "sheet")?.value ?? "",
    })
    try {
      info = await (await api.fetchApi(`/prompt_variables/preview?${params}`)).json()
    } catch (e) {
      info = { error: `Could not read ${file}: ${e.message ?? e}` }
    }
  }
  if (token !== node.pvToken) return
  node.pvInfo = info

  if (info.error) {
    setStatus(node, [{ text: `⚠ ${info.error}`, warn: true }])
  } else {
    const [first, last] = info.numbers
    const tabs = info.sheets.length > 1 ? ` · tabs: ${info.sheets.join(", ")}` : ""
    const unusable = info.columns.filter((c) => !VALID_NAME.test(c))
    const lines = [
      { text: `${info.count} row${info.count === 1 ? "" : "s"} (${first === last ? first : `${first}–${last}`})${tabs}` },
      { text: `Variables: ${info.columns.filter((c) => VALID_NAME.test(c)).map((c) => `[${c}]`).join(" ")}` },
      { text: `Row ${first}: ${info.first_row.join(" · ")}` },
    ]
    if (unusable.length) lines.push({ text: `⚠ Names with : | [ ] or \\ can't be variables: ${unusable.join(", ")}`, warn: true })
    setStatus(node, lines)
  }
  for (const target of node.graph?.findNodesByType("PromptVariables") ?? []) schedule(target)
}

function scheduleFetch(node) {
  clearTimeout(node.pvFetchTimer)
  node.pvFetchTimer = setTimeout(() => fetchPreview(node), 300)
}

function setupLoader(nodeType) {
  chain(nodeType.prototype, "onNodeCreated", function () {
    for (const name of ["file", "rows", "sheet"]) chain(getWidget(this, name), "callback", () => scheduleFetch(this))
    // The file chooser must open inside the click, before any await.
    addButton(this, "pv_upload", "Upload spreadsheet", () => chooseFile(this))
    addStatus(this)
    scheduleFetch(this)

    // Set per node rather than on the prototype: App mode calls these without `this`.
    this.onDragOver = (e) => [...(e.dataTransfer?.items ?? [])].some((item) => item.kind === "file")
    this.onDragDrop = async (e) => {
      const file = e.dataTransfer?.files?.[0]
      if (!file) return false
      if (/\.(csv|tsv|xlsx)$/i.test(file.name)) {
        await upload(this, file)
        return true
      }
      if (/\.(xls|ods|numbers)$/i.test(file.name)) {
        setStatus(this, [{ text: "⚠ Save it as .xlsx or .csv first, then drop it here.", warn: true }])
        return true
      }
      return false
    }
  })

  chain(nodeType.prototype, "onConfigure", function () {
    scheduleFetch(this)
  })
}

app.registerExtension({
  name: "PromptVariables",
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData.name === "PromptVariables") setupPromptVariables(nodeType)
    else if (nodeData.name === LOADER) setupLoader(nodeType)
  },
})
