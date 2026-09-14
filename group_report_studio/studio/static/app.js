(() => {
  "use strict";

  const state = { config: null, templates: [], reports: [], report: null, selectedSectionId: null, references: [], pollTimer: null, activeJob: null, loadToken: 0 };
  const $ = (id) => document.getElementById(id);
  const el = (tag, options = {}) => {
    const node = document.createElement(tag);
    if (options.className) node.className = options.className;
    if (options.text !== undefined) node.textContent = String(options.text);
    if (options.attrs) Object.entries(options.attrs).forEach(([key, value]) => node.setAttribute(key, String(value)));
    return node;
  };

  async function api(path, options = {}) {
    const response = await fetch(`/api${path}`, { headers: { "Content-Type": "application/json", ...(options.headers || {}) }, ...options });
    if (!response.ok) {
      let detail = `요청을 처리하지 못했습니다 (${response.status})`;
      try { const body = await response.json(); if (body.detail) detail = body.detail; } catch (_) { /* response may not be JSON */ }
      throw new Error(detail);
    }
    if (response.status === 204) return null;
    return response;
  }

  async function apiJson(path, options) { return (await api(path, options)).json(); }
  function showToast(message, type = "info") {
    const toast = el("div", { className: `toast ${type === "error" ? "error" : ""}`, text: message, attrs: { role: "status" } });
    $("toastRegion").append(toast); window.setTimeout(() => toast.remove(), 4200);
  }
  function setBusy(button, busy, busyText) {
    if (!button.dataset.label) button.dataset.label = button.textContent;
    button.disabled = busy; button.textContent = busy ? busyText : button.dataset.label;
  }
  function dateText(value) { if (!value) return ""; const date = new Date(value); return Number.isNaN(date.valueOf()) ? value : date.toLocaleString("ko-KR", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }); }
  function statusText(status) { return ({ draft: "초안", generating: "작성 중", editing: "수정 중", finalized: "확정", queued: "대기 중", running: "진행 중", succeeded: "완료", failed: "실패", cancelled: "취소됨" })[status] || status || "초안"; }

  async function initialize() {
    bindEvents(); setDefaultWeek();
    const results = await Promise.allSettled([apiJson("/config"), apiJson("/templates"), apiJson("/reports")]);
    if (results[0].status === "fulfilled") { state.config = results[0].value; renderConfig(); } else { renderConnectionFailure(results[0].reason); }
    if (results[1].status === "fulfilled") { state.templates = results[1].value || []; renderTemplateOptions(); }
    if (results[2].status === "fulfilled") { state.reports = results[2].value || []; renderReportList(); }
  }

  function bindEvents() {
    $("newReportButton").addEventListener("click", openCreateDialog); $("welcomeNewButton").addEventListener("click", openCreateDialog);
    $("closeDialogButton").addEventListener("click", () => $("createDialog").close()); $("cancelCreateButton").addEventListener("click", () => $("createDialog").close());
    $("refreshButton").addEventListener("click", refreshReports); $("homeButton").addEventListener("click", showWelcome);
    $("addSectionButton").addEventListener("click", () => { $("outlineSettings").open = true; addSectionRow(); $("sectionEditor").lastElementChild.querySelector('input[aria-label="항목 제목"]').focus(); });
    $("templateSelect").addEventListener("change", applySelectedTemplate);
    $("saveTemplateInput").addEventListener("change", (event) => { $("templateNameField").hidden = !event.target.checked; });
    $("referenceFiles").addEventListener("change", parseReferenceFiles); $("addTextReferenceButton").addEventListener("click", addTextReference);
    $("weekInput").addEventListener("change", () => { if ($("titleInput").dataset.autoTitle === "true") setDefaultTitle(); });
    $("titleInput").addEventListener("input", () => { $("titleInput").dataset.autoTitle = "false"; });
    $("createForm").addEventListener("submit", createReport); $("generateButton").addEventListener("click", generateDraft); $("finalizeButton").addEventListener("click", finalizeReport);
    $("downloadButton").addEventListener("click", downloadReport); $("chatForm").addEventListener("submit", editReport);
    $("saveCurrentTemplateButton").addEventListener("click", saveCurrentTemplate);
    $("clearSelectionButton").addEventListener("click", () => selectSection(null));
    $("evidenceToggle").addEventListener("click", toggleEvidence);
    $("cancelJobButton").addEventListener("click", cancelJob); $("retryJobButton").addEventListener("click", retryJob);
  }

  function renderConnectionFailure(error) { $("connectionState").textContent = "서버 연결 필요"; $("connectionState").classList.add("problem"); $("configWarning").hidden = false; $("configWarning").textContent = error.message; }
  function renderConfig() {
    const ready = Boolean(state.config && state.config.ready); $("connectionState").textContent = ready ? "연결 설정됨" : "설정 필요"; $("connectionState").classList.toggle("problem", !ready);
    const missing = state.config?.missing || []; $("configWarning").hidden = ready || missing.length === 0;
    if (!ready && missing.length) $("configWarning").textContent = `연결 설정이 필요합니다: ${missing.join(", ")}. 설정 이름만 표시하며 실제 자료는 생성하지 않습니다.`;
  }
  function setDefaultWeek() { const d = new Date(); const day = (d.getDay() + 6) % 7; d.setDate(d.getDate() - day + 3); const firstThursday = new Date(d.getFullYear(), 0, 4); const week = 1 + Math.round(((d - firstThursday) / 86400000 - 3 + ((firstThursday.getDay() + 6) % 7)) / 7); $("weekInput").value = `${d.getFullYear()}-W${String(week).padStart(2, "0")}`; }
  function normalizeWeek(value) { return String(value || "").replace("-W", "-"); }
  function setDefaultTitle() { const week = normalizeWeek($("weekInput").value); const [year, number] = week.split("-"); $("titleInput").value = year && number ? `${year}년 ${number}주 그룹 주보` : "그룹 주보"; $("titleInput").dataset.autoTitle = "true"; }
  function isoWeekMonday(value) { const match = /^(\d{4})-W?(\d{2})$/.exec(value); if (!match) return null; const year = Number(match[1]); const week = Number(match[2]); const jan4 = new Date(Date.UTC(year, 0, 4)); const monday = new Date(jan4); monday.setUTCDate(jan4.getUTCDate() - ((jan4.getUTCDay() + 6) % 7) + (week - 1) * 7); return monday; }
  function isPriorWeek(referenceWeek, targetWeek) { const reference = isoWeekMonday(referenceWeek); const target = isoWeekMonday(targetWeek); if (!reference || !target) return false; const weeks = (target - reference) / 604800000; return weeks === 1 || weeks === 2; }
  function defaultTemplate() { return state.config?.default_template || { name: "기본 양식", sections: [] }; }
  function openCreateDialog() { resetCreateForm(); $("createDialog").showModal(); }
  function resetCreateForm() { $("outlineSettings").open = false; $("templateSelect").value = ""; state.references = []; setDefaultTitle(); $("teamsInput").value = ""; $("saveTemplateInput").checked = false; $("templateNameField").hidden = true; renderSectionEditor(defaultTemplate().sections || []); renderReferences(); }
  function renderTemplateOptions() { const select = $("templateSelect"); select.replaceChildren(el("option", { text: "기본 양식", attrs: { value: "" } })); state.templates.forEach((template) => select.append(el("option", { text: template.name, attrs: { value: template.id } }))); }
  function applySelectedTemplate() { const template = state.templates.find((item) => String(item.id) === $("templateSelect").value) || defaultTemplate(); renderSectionEditor(template.sections || []); }
  function renderSectionEditor(sections) { const editor = $("sectionEditor"); editor.replaceChildren(); sections.forEach(addSectionRow); if (!sections.length) addSectionRow(); }
  function addSectionRow(section = {}) {
    const row = el("div", { className: "section-row", attrs: { draggable: "true" } }); row.dataset.id = section.id || crypto.randomUUID();
    const handle = el("span", { className: "drag-handle", text: "⠿", attrs: { title: "끌거나 방향키로 순서 변경", role: "button", tabindex: "0", "aria-label": "항목 순서 변경" } });
    const group = el("input", { attrs: { type: "text", placeholder: "대주제", "aria-label": "대주제" } }); group.value = section.group || "";
    const title = el("input", { attrs: { type: "text", placeholder: "항목 제목", "aria-label": "항목 제목", required: "" } }); title.value = section.title || "";
    const instructions = el("input", { attrs: { type: "text", placeholder: "작성 지침", "aria-label": "작성 지침" } }); instructions.value = section.instructions || "";
    const remove = el("button", { className: "delete-section", text: "×", attrs: { type: "button", "aria-label": "항목 삭제" } }); remove.addEventListener("click", () => row.remove());
    handle.addEventListener("keydown", (event) => { if (event.key === "ArrowUp" && row.previousElementSibling) { event.preventDefault(); row.parentNode.insertBefore(row, row.previousElementSibling); } if (event.key === "ArrowDown" && row.nextElementSibling) { event.preventDefault(); row.parentNode.insertBefore(row.nextElementSibling, row); } });
    row.append(handle, group, title, instructions, remove); addDragBehavior(row); $("sectionEditor").append(row);
  }
  function addDragBehavior(row) { row.addEventListener("dragstart", () => row.classList.add("dragging")); row.addEventListener("dragend", () => row.classList.remove("dragging")); row.addEventListener("dragover", (event) => { event.preventDefault(); const dragging = $("sectionEditor").querySelector(".dragging"); if (dragging && dragging !== row) { const box = row.getBoundingClientRect(); row.parentNode.insertBefore(dragging, event.clientY < box.top + box.height / 2 ? row : row.nextSibling); } }); }
  function collectSections() { return [...$("sectionEditor").querySelectorAll(".section-row")].map((row) => ({ id: row.dataset.id, group: row.children[1].value.trim(), title: row.children[2].value.trim(), instructions: row.children[3].value.trim() })).filter((section) => section.title); }

  async function parseReferenceFiles(event) {
    for (const file of event.target.files) {
      if (state.references.length >= 2) { showToast("이전 주보는 최대 2개까지 추가할 수 있습니다.", "error"); break; }
      try { const content_base64 = await fileToBase64(file); const parsed = await apiJson("/references/parse", { method: "POST", body: JSON.stringify({ name: file.name, content_base64 }) }); state.references.push({ name: parsed.name, text: parsed.text, week: "" }); renderReferences(); }
      catch (error) { showToast(`${file.name}: ${error.message}`, "error"); }
    } event.target.value = "";
  }
  function fileToBase64(file) { return new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result).split(",")[1]); reader.onerror = () => reject(new Error("파일을 읽지 못했습니다.")); reader.readAsDataURL(file); }); }
  function addTextReference() { if (state.references.length >= 2) { showToast("이전 주보는 최대 2개까지 추가할 수 있습니다.", "error"); return; } state.references.push({ name: "붙여 넣은 이전 주보", text: "", week: "" }); renderReferences(); }
  function renderReferences() {
    const list = $("referenceList"); list.replaceChildren(); const atLimit = state.references.length >= 2; $("referenceFiles").disabled = atLimit; $("addTextReferenceButton").disabled = atLimit; state.references.forEach((reference, index) => {
      const item = el("div", { className: "reference-item" }); const content = el("textarea", { attrs: { rows: "2", "aria-label": `${reference.name} 본문`, placeholder: "이전 주보 본문" } }); content.value = reference.text; content.addEventListener("input", () => { state.references[index].text = content.value; });
      const week = el("input", { attrs: { type: "week", "aria-label": `${reference.name} 기준 주` } }); week.value = reference.week || ""; week.addEventListener("input", () => { state.references[index].week = week.value; });
      const remove = el("button", { className: "delete-section", text: "×", attrs: { type: "button", "aria-label": `${reference.name} 삭제` } }); remove.addEventListener("click", () => { state.references.splice(index, 1); renderReferences(); });
      const name = el("strong", { text: reference.name }); const wrap = el("div"); wrap.append(name, content); item.append(wrap, week, remove); list.append(item);
    });
  }
  function splitTeams(text) { return text.split(/[\n,]/).map((item) => item.trim()).filter(Boolean); }
  async function createReport(event) {
    event.preventDefault(); const sections = collectSections(); if (!sections.length) { showToast("목차 항목을 하나 이상 입력하세요.", "error"); return; }
    const button = $("createSubmitButton"); setBusy(button, true, "만드는 중…");
    try {
      const template = { name: $("templateNameInput").value.trim() || defaultTemplate().name || "기본 양식", sections };
      if ($("saveTemplateInput").checked) { if (!$("templateNameInput").value.trim()) throw new Error("저장할 양식 이름을 입력하세요."); await apiJson("/templates", { method: "POST", body: JSON.stringify(template) }); }
      const targetWeekInput = $("weekInput").value; const references = state.references.filter((ref) => ref.text.trim());
      if (references.some((ref) => !ref.week)) throw new Error("각 이전 주보의 기준 주를 선택하세요.");
      if (references.some((ref) => !isPriorWeek(ref.week, targetWeekInput))) throw new Error("이전 주보는 기준 주보다 1주 또는 2주 전 자료만 추가할 수 있습니다.");
      const report = await apiJson("/reports", { method: "POST", body: JSON.stringify({ week: normalizeWeek(targetWeekInput), title: $("titleInput").value.trim() || "그룹 주보", template, references: references.map((ref) => ({ ...ref, week: normalizeWeek(ref.week) })), expected_teams: splitTeams($("teamsInput").value) }) });
      $("createDialog").close(); await refreshReports(); await openReport(report.id); await startJob("generate", { base_version: report.version });
    } catch (error) { showToast(error.message, "error"); } finally { setBusy(button, false); }
  }

  async function refreshReports() { try { state.reports = await apiJson("/reports"); renderReportList(); } catch (error) { showToast(error.message, "error"); } }
  function renderReportList() {
    const list = $("reportList"); list.replaceChildren(); $("reportListEmpty").hidden = state.reports.length > 0;
    state.reports.forEach((report) => { const li = el("li"); const button = el("button", { className: `report-button ${state.report?.id === report.id ? "selected" : ""}`, attrs: { type: "button" } }); button.append(el("strong", { text: report.title }), el("span", { text: `${report.week} · v${report.version} · ${statusText(report.status)}` })); button.addEventListener("click", () => openReport(report.id)); li.append(button); list.append(li); });
  }
  async function openReport(id) { const loadToken = ++state.loadToken; window.clearTimeout(state.pollTimer); state.pollTimer = null; state.activeJob = null; renderJob(); try { const report = await apiJson(`/reports/${encodeURIComponent(id)}`); if (loadToken !== state.loadToken) return; state.report = report; state.selectedSectionId = null; state.activeJob = state.report.active_job || null; renderReport(); renderJob(); if (state.activeJob && ["queued", "running"].includes(state.activeJob.status)) state.pollTimer = window.setTimeout(pollJob, 900); } catch (error) { if (loadToken === state.loadToken) showToast(error.message, "error"); } }
  function showWelcome() { $("saveCurrentTemplateButton").hidden = true; state.loadToken += 1; window.clearTimeout(state.pollTimer); state.pollTimer = null; state.activeJob = null; renderJob(); state.report = null; state.selectedSectionId = null; $("paper").hidden = true; $("welcome").hidden = false; $("generateButton").hidden = true; $("finalizeButton").disabled = true; $("downloadButton").disabled = true; $("chatInput").disabled = true; $("sendButton").disabled = true; $("clearSelectionButton").disabled = true; $("selectionLabel").textContent = "전체 문서"; $("outline").replaceChildren(el("p", { className: "empty-note", text: "주보를 열면 목차가 표시됩니다." })); $("history").replaceChildren(el("p", { className: "empty-note", text: "변경 기록이 여기에 쌓입니다." })); renderReportList(); }
  function renderReport() {
    const report = state.report; if (!report) return; $("welcome").hidden = true; $("paper").hidden = false; $("paperWeek").textContent = report.week || ""; $("paperTitle").textContent = report.title || "제목 없는 주보";
    $("saveCurrentTemplateButton").hidden = false;
    $("reportStatus").textContent = `${statusText(report.status)} · v${report.version}`; $("reportStatus").className = `status-badge ${report.status || ""}`; renderWarnings(); renderCoverage(); renderSections(); renderOutline(); renderHistory(); renderMessages(); renderEvidence(); renderReportList();
    const busy = Boolean(state.activeJob && ["queued", "running"].includes(state.activeJob.status)); const hasSections = (report.sections || []).length > 0; const ready = Boolean(state.config?.ready); $("generateButton").hidden = hasSections || report.status === "finalized"; $("generateButton").disabled = busy || !ready; $("generateButton").title = ready ? "저장된 자료를 바탕으로 빈 초안을 작성합니다." : "자료 연결 설정을 마치면 생성할 수 있습니다."; $("chatInput").disabled = busy || !hasSections || !ready; $("sendButton").disabled = busy || !hasSections || !ready; $("clearSelectionButton").disabled = busy || !hasSections; $("finalizeButton").disabled = busy || report.status === "finalized" || !hasSections; $("downloadButton").disabled = report.status !== "finalized";
  }
  function renderWarnings() { const warnings = state.report.warnings || []; const box = $("reportWarnings"); box.hidden = !warnings.length; box.replaceChildren(...warnings.map((warning) => el("div", { text: warning }))); }
  function renderCoverage() { const coverage = state.report.coverage; const box = $("coverage"); if (!coverage || !Object.keys(coverage).length) { box.hidden = true; return; } box.hidden = false; const entries = Object.entries(coverage).map(([key, value]) => `${key}: ${Array.isArray(value) ? value.join(", ") : String(value)}`); box.textContent = entries.join("  ·  "); }
  function renderSections() {
    const container = $("sections"); container.replaceChildren(); const sections = state.report.sections || [];
    if (!sections.length) { container.append(el("p", { className: "empty-note", text: "아직 작성된 본문이 없습니다. 생성을 시작하면 항목별 초안이 여기에 표시됩니다." })); return; }
    sections.forEach((section) => { const node = el("section", { className: `report-section ${state.selectedSectionId === section.id ? "selected" : ""}`, attrs: { id: `section-${section.id}`, tabindex: "0", "aria-label": `${section.title} 선택` } }); const heading = el("h2", { text: section.title }); if (section.group) heading.append(el("span", { className: "section-group", text: section.group })); node.append(heading); (section.blocks || []).forEach((block) => node.append(renderBlock(block))); (section.warnings || []).forEach((warning) => node.append(el("div", { className: "section-warning", text: warning }))); node.addEventListener("click", () => selectSection(section.id)); node.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); selectSection(section.id); } }); container.append(node); });
  }
  function renderBlock(block) {
    if (block.kind === "table") { const table = el("table", { className: "report-table" }); if ((block.headers || []).length) { const tr = el("tr"); block.headers.forEach((value) => tr.append(el("th", { text: value }))); const head = el("thead"); head.append(tr); table.append(head); } const body = el("tbody"); (block.rows || []).forEach((row) => { const tr = el("tr"); row.forEach((value) => tr.append(el("td", { text: value }))); body.append(tr); }); table.append(body); return table; }
    if (block.kind === "bullet") { const ul = el("ul", { className: "block-bullet" }); String(block.text || "").split("\n").filter(Boolean).forEach((line) => ul.append(el("li", { text: line.replace(/^[-•]\s*/, "") }))); return ul; }
    return el("p", { className: "block-paragraph", text: block.text || "" });
  }
  function renderOutline() { const nav = $("outline"); nav.replaceChildren(); (state.report.sections || []).forEach((section) => { const button = el("button", { className: `outline-link ${state.selectedSectionId === section.id ? "selected" : ""}`, text: section.title, attrs: { type: "button" } }); button.addEventListener("click", () => { selectSection(section.id); $(`section-${section.id}`)?.scrollIntoView({ behavior: "smooth", block: "start" }); }); nav.append(button); }); if (!nav.children.length) nav.append(el("p", { className: "empty-note", text: "본문 생성 후 목차를 선택할 수 있습니다." })); }
  function selectSection(id) { state.selectedSectionId = id; const section = (state.report?.sections || []).find((item) => item.id === id); $("selectionLabel").textContent = section ? `선택: ${section.title}` : "전체 문서"; renderSections(); renderOutline(); }
  function renderHistory() { const wrap = $("history"); wrap.replaceChildren(); [...(state.report.versions || [])].reverse().forEach((version) => { const item = el("div", { className: "history-item" }); item.append(el("strong", { text: `v${version.version}` }), el("small", { text: `${version.reason || "변경"} · ${dateText(version.created_at)}` })); if (version.version !== state.report.version) { const restore = el("button", { className: "text-button", text: "복원", attrs: { type: "button" } }); restore.addEventListener("click", () => restoreVersion(version.version)); item.append(restore); } wrap.append(item); }); if (!wrap.children.length) wrap.append(el("p", { className: "empty-note", text: "아직 변경 기록이 없습니다." })); }
  function renderMessages() { const wrap = $("messages"); wrap.replaceChildren(); const messages = state.report.messages || []; if (!messages.length) wrap.append(el("div", { className: "guide-message", text: "수정할 내용을 알려주세요. 본문에서 항목을 선택하면 그 부분만 다듬습니다." })); messages.forEach((message) => wrap.append(el("div", { className: `message ${message.role === "user" ? "user" : "assistant"}`, text: message.content }))); wrap.scrollTop = wrap.scrollHeight; }
  function renderEvidence() { const facts = state.report.facts || []; const sources = state.report.sources || []; $("evidencePanel").hidden = !facts.length && !sources.length; $("evidenceCount").textContent = `${facts.length}개 사실 · ${sources.length}개 원문`; const content = $("evidenceContent"); content.replaceChildren(); facts.forEach((fact) => { const item = el("article", { className: "evidence-item" }); item.append(el("strong", { text: fact.text }), el("p", { text: fact.quote || "인용문 없음" }), el("div", { className: "evidence-meta", text: `근거 ${fact.source_id || "미지정"}` })); content.append(item); }); sources.forEach((source) => { const item = el("article", { className: "evidence-item" }); item.append(el("strong", { text: source.team || "출처" }), el("p", { text: source.text || "" }), el("div", { className: "evidence-meta", text: [source.week, source.mail_id, source.part_index != null ? `부분 ${source.part_index}` : ""].filter(Boolean).join(" · ") })); content.append(item); }); }
  function toggleEvidence() { const content = $("evidenceContent"); content.hidden = !content.hidden; $("evidenceToggle").setAttribute("aria-expanded", String(!content.hidden)); }

  async function startJob(action, body) { try { const job = await apiJson(`/reports/${encodeURIComponent(state.report.id)}/${action}`, { method: "POST", body: JSON.stringify(body) }); watchJob(job); return true; } catch (error) { showToast(error.message, "error"); return false; } }
  function watchJob(job) { window.clearTimeout(state.pollTimer); state.activeJob = job; renderJob(); renderReport(); if (["queued", "running"].includes(job.status)) state.pollTimer = window.setTimeout(pollJob, 900); }
  async function pollJob() { if (!state.activeJob || !state.report) return; const expectedJobId = state.activeJob.id; const expectedReportId = state.report.id; try { const job = await apiJson(`/jobs/${encodeURIComponent(expectedJobId)}`); if (state.activeJob?.id !== expectedJobId || state.report?.id !== expectedReportId) return; state.activeJob = job; renderJob(); if (["queued", "running"].includes(job.status)) state.pollTimer = window.setTimeout(pollJob, 900); else { await openReport(expectedReportId); if (state.report?.id === expectedReportId && job.status === "succeeded") showToast("문서가 업데이트되었습니다."); } } catch (error) { if (state.activeJob?.id !== expectedJobId || state.report?.id !== expectedReportId) return; showToast(error.message, "error"); state.pollTimer = window.setTimeout(pollJob, 2500); } }
  function renderJob() { const job = state.activeJob; $("jobCard").hidden = !job; if (!job) return; const progress = Math.max(0, Math.min(100, Number(job.progress) || 0)); $("jobMessage").textContent = job.message || statusText(job.status); $("jobProgress").textContent = `${progress}%`; $("jobProgressBar").style.width = `${progress}%`; $("jobError").hidden = !job.error; $("jobError").textContent = job.error || ""; $("cancelJobButton").hidden = !["queued", "running"].includes(job.status); $("retryJobButton").hidden = !["failed", "cancelled"].includes(job.status); }
  async function cancelJob() { if (!state.activeJob) return; try { state.activeJob = await apiJson(`/jobs/${encodeURIComponent(state.activeJob.id)}/cancel`, { method: "POST", body: "{}" }); renderJob(); await openReport(state.activeJob.report_id); } catch (error) { showToast(error.message, "error"); } }
  async function retryJob() { if (!state.activeJob) return; try { const job = await apiJson(`/jobs/${encodeURIComponent(state.activeJob.id)}/retry`, { method: "POST", body: "{}" }); watchJob(job); } catch (error) { showToast(error.message, "error"); } }
  async function generateDraft() { if (!state.report || (state.report.sections || []).length) return; await startJob("generate", { base_version: state.report.version }); }
  async function editReport(event) { event.preventDefault(); const message = $("chatInput").value.trim(); if (!state.report || !message) return; const accepted = await startJob("edit", { base_version: state.report.version, message, section_id: state.selectedSectionId }); if (accepted) $("chatInput").value = ""; }
  async function restoreVersion(targetVersion) { try { state.report = await apiJson(`/reports/${encodeURIComponent(state.report.id)}/restore`, { method: "POST", body: JSON.stringify({ base_version: state.report.version, target_version: targetVersion }) }); renderReport(); showToast(`버전 ${targetVersion}의 내용을 새 버전으로 복원했습니다.`); } catch (error) { showToast(error.message, "error"); } }
  async function finalizeReport() { if (!state.report) return; const button = $("finalizeButton"); setBusy(button, true, "확정 중…"); try { state.report = await apiJson(`/reports/${encodeURIComponent(state.report.id)}/finalize`, { method: "POST", body: JSON.stringify({ base_version: state.report.version }) }); renderReport(); showToast("주보를 확정했습니다. 이 버전으로 Word 파일을 받을 수 있습니다."); } catch (error) { showToast(error.message, "error"); } finally { setBusy(button, false); } }
  function downloadReport() { if (!state.report || state.report.status !== "finalized") return; const version = encodeURIComponent(state.report.version); window.location.assign(`/api/reports/${encodeURIComponent(state.report.id)}/export?version=${version}`); }

  async function saveCurrentTemplate() {
    if (!state.report) return;
    const button = $("saveCurrentTemplateButton"); setBusy(button, true, "저장 중…");
    try {
      const template = { ...state.report.template, name: `${state.report.title} 양식`.slice(0, 100) };
      await apiJson("/templates", { method: "POST", body: JSON.stringify(template) });
      state.templates = await apiJson("/templates"); renderTemplateOptions();
      showToast("현재 목차를 저장했습니다. 새 주보에서 이 양식을 선택할 수 있습니다.");
    } catch (error) { showToast(error.message, "error"); }
    finally { setBusy(button, false); }
  }

  initialize();
})();
