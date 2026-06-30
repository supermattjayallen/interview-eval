const form = document.getElementById("analysis-form");
const submitBtn = document.getElementById("submit-btn");
const statusPanel = document.getElementById("status-panel");
const statusLabel = document.getElementById("status-label");
const statusMessage = document.getElementById("status-message");
const progressBar = document.getElementById("progress-bar");
const errorPanel = document.getElementById("error-panel");
const resultsSection = document.getElementById("results");
const qaList = document.getElementById("qa-list");

let currentAnalysisResult = null;

const STATUS_PROGRESS = {
  pending: 10,
  downloading: 30,
  transcribing: 60,
  analyzing: 85,
  cached: 100,
  completed: 100,
  failed: 100,
};

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  clearError();
  hideResults();
  hideBatchResults();

  const formData = new FormData(form);
  const batchUrls = parseBatchUrls(formData.get("recording_urls_batch"));
  const singleUrl = (formData.get("recording_url") || "").trim();

  if (batchUrls.length === 0 && !singleUrl) {
    showError("Provide one recording link or paste multiple links (one per line).");
    return;
  }

  setLoading(true);

  try {
    if (batchUrls.length > 0) {
      await runBatchAnalysis(formData, batchUrls);
    } else {
      await runSingleAnalysis(formData, singleUrl);
    }
  } catch (error) {
    showError(error.message || "Something went wrong.");
    hideStatus();
  } finally {
    setLoading(false);
  }
});

async function runSingleAnalysis(formData, singleUrl) {
  showStatus("pending", "Starting analysis...");
  const payload = buildPayload(formData, singleUrl);

  const startResponse = await fetch("/analyze", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  if (!startResponse.ok) {
    throw new Error(await readError(startResponse));
  }

  const { job_id: jobId } = await startResponse.json();
  const result = await pollJob(jobId);
  renderResults(result);
  showStatus("completed", "Analysis complete");
}

async function runBatchAnalysis(formData, batchUrls) {
  showStatus("pending", `Starting batch of ${batchUrls.length} recordings...`);
  const payload = buildBatchPayload(formData, batchUrls);

  const startResponse = await fetch("/analyze/batch", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  if (!startResponse.ok) {
    throw new Error(await readError(startResponse));
  }

  const { batch_id: batchId } = await startResponse.json();
  const batch = await pollBatch(batchId);
  renderBatchResults(batch);
  showStatus(batch.status, batch.message || "Batch complete");
}

function parseBatchUrls(raw) {
  return String(raw || "")
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
}

function buildPayload(formData, recordingUrl) {
  const criteriaRaw = formData.get("evaluation_criteria");
  const criteria = criteriaRaw
    ? criteriaRaw
        .split(",")
        .map((item) => item.trim())
        .filter(Boolean)
    : null;

  const payload = {
    recording_url: recordingUrl,
    role_title: formData.get("role_title") || null,
    role_description: formData.get("role_description") || null,
    interview_step: formData.get("interview_step") || null,
    evaluation_criteria: criteria,
    first_speaker: formData.get("first_speaker") || "interviewer",
    force_refresh: formData.get("force_refresh") === "on",
  };

  return Object.fromEntries(
    Object.entries(payload).filter(([, value]) => value !== null && value !== "")
  );
}

function buildBatchPayload(formData, batchUrls) {
  const criteriaRaw = formData.get("evaluation_criteria");
  const criteria = criteriaRaw
    ? criteriaRaw
        .split(",")
        .map((item) => item.trim())
        .filter(Boolean)
    : null;

  const payload = {
    recording_urls: batchUrls,
    role_title: formData.get("role_title") || null,
    role_description: formData.get("role_description") || null,
    interview_step: formData.get("interview_step") || null,
    evaluation_criteria: criteria,
    first_speaker: formData.get("first_speaker") || "interviewer",
    force_refresh: formData.get("force_refresh") === "on",
    skip_evaluation: formData.get("skip_evaluation") === "on",
  };

  return Object.fromEntries(
    Object.entries(payload).filter(
      ([key, value]) => key === "skip_evaluation" || (value !== null && value !== "")
    )
  );
}

async function pollBatch(batchId) {
  while (true) {
    const response = await fetch(`/analyze/batch/${batchId}`);
    if (!response.ok) {
      throw new Error(await readError(response));
    }

    const batch = await response.json();
    showStatus(
      batch.status,
      batch.message || `Processing ${batch.current_index + 1} of ${batch.total_count}...`
    );

    if (batch.status === "completed" || batch.status === "failed") {
      return batch;
    }

    await sleep(3000);
  }
}

function renderBatchResults(batch) {
  document.getElementById("batch-summary").textContent =
    batch.message ||
    `${batch.completed_count} completed, ${batch.failed_count} failed, ${batch.cached_count} cached`;

  const container = document.getElementById("batch-items");
  container.innerHTML = "";

  batch.items.forEach((item, index) => {
    const article = document.createElement("article");
    article.className = "batch-item";
    const questionCount = item.result ? item.result.total_questions : "-";
    article.innerHTML = `
      <div class="qa-top">
        <h3 class="qa-question">${index + 1}. ${escapeHtml(item.recording_url)}</h3>
        <span class="score-pill">${escapeHtml(item.status)}</span>
      </div>
      <p class="qa-meta">${escapeHtml(item.message || "")}${item.result ? ` · ${questionCount} questions` : ""}</p>
    `;
    container.appendChild(article);
  });

  document.getElementById("batch-results").classList.remove("hidden");
  document.getElementById("batch-results").scrollIntoView({ behavior: "smooth", block: "start" });
}

function hideBatchResults() {
  document.getElementById("batch-results").classList.add("hidden");
}

async function pollJob(jobId) {
  while (true) {
    const response = await fetch(`/analyze/${jobId}`);
    if (response.status === 404) {
      throw new Error(
        "Analysis job was lost, usually because the server restarted during processing. Please run the analysis again."
      );
    }
    if (!response.ok) {
      throw new Error(await readError(response));
    }

    const job = await response.json();
    showStatus(job.status, job.message || "");

    if (job.status === "completed" || job.status === "cached") {
      return job.result;
    }

    if (job.status === "failed") {
      throw new Error(job.message || "Analysis failed.");
    }

    await sleep(2500);
  }
}

function renderResults(result) {
  currentAnalysisResult = result;
  document.getElementById("average-score").textContent = result.average_score.toFixed(1);
  document.getElementById("total-questions").textContent = String(result.total_questions);
  document.getElementById("recommendation").textContent = result.feedback.overall_recommendation;
  document.getElementById("transcript-summary").textContent = buildSummaryLine(result);

  renderChips("topics-covered", result.topics_covered);
  renderChips("highlights", result.highlights, "chips-positive");
  renderChips("red-flags", result.red_flags, "chips-warning");
  renderQaList(result.qa_pairs, result.evaluation_skipped);
  renderFeedback("candidate-feedback", result.feedback.candidate_feedback);

  resultsSection.classList.remove("hidden");
  resultsSection.scrollIntoView({ behavior: "smooth", block: "start" });
}

function buildSummaryLine(result) {
  const parts = [result.transcript_summary];
  if (result.interview_step) {
    const inferred = result.interview_step_inferred ? " (auto-detected)" : "";
    parts.push(`Interview step: ${result.interview_step.replaceAll("_", " ")}${inferred}.`);
  }
  if (result.evaluation_skipped) {
    parts.push("Answer evaluation was skipped (question bank mode).");
  }
  if (result.reevaluated_with_new_context) {
    parts.push("Scores and feedback were refreshed using the updated evaluation criteria.");
  }
  if (result.from_saved_data && !result.reevaluated_with_new_context) {
    const savedAt = result.saved_at ? ` (${result.saved_at})` : "";
    const source = result.storage_source || "saved storage";
    parts.push(`Loaded from ${source}${savedAt}.`);
  }
  return parts.join(" ");
}

function renderChips(containerId, items, extraClass = "") {
  const container = document.getElementById(containerId);
  container.className = `chips ${extraClass}`.trim();
  container.innerHTML = "";

  if (!items || items.length === 0) {
    container.innerHTML = '<span class="chip">None noted</span>';
    return;
  }

  for (const item of items) {
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.textContent = item;
    container.appendChild(chip);
  }
}

function idealAnswerButtonLabel(qa) {
  if (!hasGeneratedIdeal(qa)) {
    return "Generate better answer";
  }
  if (qa.ideal_answer_source === "polished_extracted") {
    return "Generate better answer";
  }
  return "Regenerate";
}

function idealAnswerSourceLabel(qa) {
  if (!hasGeneratedIdeal(qa)) {
    return "";
  }
  if (qa.ideal_answer_source === "polished_extracted") {
    return "Saved from your recording";
  }
  return "AI-generated";
}

function hasGeneratedIdeal(qa) {
  return Boolean(qa?.ideal_answer?.trim()) && Boolean(qa?.ideal_answer_generated);
}

function renderIdealAnswerActions(qa, index) {
  const hasIdeal = hasGeneratedIdeal(qa);
  const hasExtractedAnswer = Boolean(qa?.answer?.trim());
  const usingExtracted = qa?.ideal_answer_source === "polished_extracted";

  if (!hasIdeal) {
    return `<button type="button" class="qa-regenerate-btn" data-index="${index}">Generate better answer</button>`;
  }

  const buttons = [
    `<button type="button" class="qa-regenerate-btn" data-index="${index}">${idealAnswerButtonLabel(qa)}</button>`,
  ];

  if (hasExtractedAnswer && !usingExtracted) {
    buttons.push(
      `<button type="button" class="qa-polish-btn" data-index="${index}">Use my extracted answer</button>`,
    );
  }

  return buttons.join("");
}

function renderIdealAnswerBlock(qa, index) {
  const hasIdeal = hasGeneratedIdeal(qa);
  const hasExtractedAnswer = Boolean(qa?.answer?.trim());
  const usingExtracted = qa?.ideal_answer_source === "polished_extracted";
  const sourceLabel = idealAnswerSourceLabel(qa);
  const showCompareHint = hasIdeal && hasExtractedAnswer && !usingExtracted;
  return `
    <div class="qa-block qa-block-ideal">
      <div class="qa-block-header">
        <h3>Better answer</h3>
        <div class="qa-ideal-actions">
          ${renderIdealAnswerActions(qa, index)}
        </div>
      </div>
      ${sourceLabel ? `<p class="qa-ideal-source">${escapeHtml(sourceLabel)}</p>` : ""}
      ${showCompareHint ? `<p class="qa-ideal-hint">Compare with your answer above. Prefer what you said on the recording?</p>` : ""}
      <p class="qa-ideal-answer ${hasIdeal ? "" : "qa-ideal-placeholder"}">${
        hasIdeal
          ? escapeHtml(qa.ideal_answer)
          : "Generate a suggested answer first, then choose whether to keep it or use your recording answer."
      }</p>
      ${
        hasIdeal
          ? `<div class="qa-ideal-points-block">
        <h3>Key points to include</h3>
        <ul class="qa-ideal-points">${renderListItems(qa.ideal_answer_points)}</ul>
      </div>`
          : ""
      }
    </div>
  `;
}

function renderQaList(qaPairs, evaluationSkipped = false) {
  const container = qaList;
  container.innerHTML = "";

  qaPairs.forEach((qa, index) => {
    const scoreLabel = evaluationSkipped ? "—" : `${qa.score}/10`;
    const item = document.createElement("article");
    item.className = "qa-item";
    item.dataset.qaIndex = String(index);
    item.innerHTML = `
      <div class="qa-top">
        <h3 class="qa-question">Q${index + 1}. ${escapeHtml(qa.question)}</h3>
        <span class="score-pill ${qa.quality}">${scoreLabel}</span>
      </div>
      <p class="qa-meta">
        ${formatTimestamp(qa.question_timestamp, qa.answer_timestamp)}
        ${evaluationSkipped ? "" : `Quality: ${qa.quality.replaceAll("_", " ")}`}
      </p>
      <div class="qa-block">
        <h3>Candidate answer</h3>
        <p class="qa-answer">${escapeHtml(qa.answer || "No answer captured.")}</p>
      </div>
      ${
        evaluationSkipped
          ? ""
          : `<div class="qa-columns">
        <div>
          <h3>Strengths</h3>
          <ul>${renderListItems(qa.strengths)}</ul>
        </div>
        <div>
          <h3>Gaps</h3>
          <ul>${renderListItems(qa.gaps)}</ul>
        </div>
      </div>`
      }
      ${renderIdealAnswerBlock(qa, index)}
    `;
    container.appendChild(item);
  });
}

qaList.addEventListener("click", async (event) => {
  const polishButton = event.target.closest(".qa-polish-btn");
  const regenerateButton = event.target.closest(".qa-regenerate-btn");
  const button = polishButton || regenerateButton;
  if (!button || !currentAnalysisResult) {
    return;
  }

  event.preventDefault();

  const index = Number(button.dataset.index);
  if (Number.isNaN(index)) {
    return;
  }

  const endpoint = polishButton
    ? "/analyze/polish-extracted-answer"
    : "/analyze/regenerate-ideal-answer";
  const loadingText = polishButton ? "Saving..." : "Generating...";

  clearError();
  const originalText = button.textContent;
  button.disabled = true;
  button.textContent = loadingText;
  button
    .closest(".qa-ideal-actions")
    ?.querySelectorAll("button")
    .forEach((peer) => {
      peer.disabled = true;
    });

  let succeeded = false;
  try {
    const response = await fetch(endpoint, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        recording_url: currentAnalysisResult.recording_url,
        question_index: index,
      }),
    });

    if (!response.ok) {
      throw new Error(await readError(response));
    }

    const data = await response.json();
    if (!data.qa_pair?.ideal_answer?.trim()) {
      throw new Error("The server returned an empty better answer. Try again.");
    }
    if (!data.qa_pair.ideal_answer_generated) {
      data.qa_pair.ideal_answer_generated = true;
    }

    currentAnalysisResult.qa_pairs[index] = data.qa_pair;
    updateQaIdealAnswer(index, data.qa_pair);
    succeeded = true;
  } catch (error) {
    showError(error.message || "Could not update the better answer.");
    errorPanel?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  } finally {
    if (!succeeded && button.isConnected) {
      const actions = button.closest(".qa-ideal-actions");
      actions?.querySelectorAll("button").forEach((peer) => {
        peer.disabled = false;
      });
      button.textContent = originalText;
      const qa = currentAnalysisResult.qa_pairs[index];
      const polishBtn = actions?.querySelector(".qa-polish-btn");
      if (polishBtn && !qa?.answer?.trim()) {
        polishBtn.disabled = true;
      }
    }
  }
});

function updateQaIdealAnswer(index, qa) {
  const item = document.querySelector(`.qa-item[data-qa-index="${index}"]`);
  if (!item) {
    return;
  }

  const idealBlock = item.querySelector(".qa-block-ideal");
  if (idealBlock) {
    idealBlock.outerHTML = renderIdealAnswerBlock(qa, index);
    return;
  }

  const ideal = item.querySelector(".qa-ideal-answer");
  const pointsBlock = item.querySelector(".qa-ideal-points-block");
  const button = item.querySelector(".qa-regenerate-btn");
  if (button) {
    button.textContent = idealAnswerButtonLabel(qa);
  }
  if (ideal) {
    const hasIdeal = hasGeneratedIdeal(qa);
    ideal.classList.toggle("qa-ideal-placeholder", !hasIdeal);
    ideal.textContent = hasIdeal
      ? qa.ideal_answer
      : "Generate a suggested answer first, then choose whether to keep it or use your recording answer.";
  }
  if (pointsBlock && hasGeneratedIdeal(qa)) {
    pointsBlock.outerHTML = `<div class="qa-ideal-points-block">
        <h3>Key points to include</h3>
        <ul class="qa-ideal-points">${renderListItems(qa.ideal_answer_points)}</ul>
      </div>`;
  }
}

function renderFeedback(containerId, items) {
  const container = document.getElementById(containerId);
  container.innerHTML = renderListItems(items);
}

function renderListItems(items) {
  if (!items || items.length === 0) {
    return "<li>None noted</li>";
  }

  return items.map((item) => `<li>${escapeHtml(item)}</li>`).join("");
}

function formatTimestamp(questionTs, answerTs) {
  const parts = [];
  if (questionTs) parts.push(`Question at ${questionTs}`);
  if (answerTs) parts.push(`Answer at ${answerTs}`);
  return parts.join(" · ");
}

function showStatus(status, message) {
  statusPanel.classList.remove("hidden");
  statusLabel.textContent = status;
  statusMessage.textContent = message;
  progressBar.style.width = `${STATUS_PROGRESS[status] || 8}%`;
}

function hideStatus() {
  statusPanel.classList.add("hidden");
}

function showError(message) {
  errorPanel.textContent = message;
  errorPanel.classList.remove("hidden");
}

function clearError() {
  errorPanel.textContent = "";
  errorPanel.classList.add("hidden");
}

function hideResults() {
  currentAnalysisResult = null;
  resultsSection.classList.add("hidden");
  hideBatchResults();
}

function setLoading(isLoading) {
  submitBtn.disabled = isLoading;
  submitBtn.textContent = isLoading ? "Analyzing..." : "Analyze interview";
}

async function readError(response) {
  try {
    const data = await response.json();
    if (typeof data.detail === "string") {
      return data.detail;
    }
    if (Array.isArray(data.detail)) {
      return data.detail.map((item) => item.msg).join(", ");
    }
  } catch {
    // Fall through to generic message.
  }
  return `Request failed (${response.status})`;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

const tabs = document.querySelectorAll(".tab");
const analyzePanel = document.getElementById("analyze-panel");
const preparePanel = document.getElementById("prepare-panel");
const prepForm = document.getElementById("prep-form");
const prepSubmitBtn = document.getElementById("prep-submit-btn");
const prepErrorPanel = document.getElementById("prep-error-panel");
const prepResultsSection = document.getElementById("prep-results");

const prepCategoryLabels = {};
const prepCategoryValues = [];

const DEFAULT_UNCHECKED_PREP_CATEGORIES = new Set(["logistics", "other"]);

const PREP_CATEGORY_GROUPS = [
  { label: "Skills & depth", values: ["technical", "coding", "system_design"] },
  { label: "You & experience", values: ["behavioral", "experience", "leadership", "role_specific"] },
  { label: "Fit & logistics", values: ["culture", "logistics", "other"] },
];

const PREP_CATEGORY_PRESETS = {
  all: () => [...prepCategoryValues],
  technical: ["technical", "coding", "system_design", "experience", "role_specific"],
  behavioral: ["behavioral", "culture", "leadership", "experience"],
  recruiter: ["behavioral", "experience", "logistics", "culture", "role_specific"],
  none: () => [],
};

const STEP_CATEGORY_SUGGESTIONS = {
  recruiter_screen: ["behavioral", "experience", "logistics", "culture", "role_specific"],
  hiring_manager: ["behavioral", "experience", "role_specific", "leadership", "culture"],
  technical: ["technical", "coding", "system_design", "experience", "role_specific"],
  coding: ["coding", "technical", "experience"],
  system_design: ["system_design", "technical", "experience"],
  behavioral: ["behavioral", "culture", "leadership", "experience"],
  culture_fit: ["culture", "behavioral", "logistics"],
  panel: ["technical", "behavioral", "system_design", "leadership", "experience"],
  final: ["behavioral", "logistics", "culture", "experience", "role_specific"],
};

let prepCategoriesCustomized = false;

tabs.forEach((tab) => {
  tab.addEventListener("click", () => {
    tabs.forEach((item) => item.classList.remove("active"));
    tab.classList.add("active");
    const isAnalyze = tab.dataset.tab === "analyze";
    analyzePanel.classList.toggle("active", isAnalyze);
    preparePanel.classList.toggle("active", !isAnalyze);
  });
});

loadInterviewSteps();
loadPrepCategories();
wirePrepCategoryControls();

function wirePrepCategoryControls() {
  document.querySelectorAll(".prep-preset-card").forEach((button) => {
    button.addEventListener("click", () => {
      applyPrepCategoryPreset(button.dataset.preset);
    });
  });

  const slider = document.getElementById("prep-question-count-slider");
  const display = document.getElementById("prep-question-count-display");
  const hiddenCount = document.getElementById("prep-question-count");
  slider.addEventListener("input", () => {
    display.textContent = slider.value;
    hiddenCount.value = slider.value;
  });

  document.getElementById("prep-apply-step-suggestion").addEventListener("click", () => {
    const step = document.getElementById("prep-interview-step").value;
    const suggested = STEP_CATEGORY_SUGGESTIONS[step];
    if (!suggested) {
      return;
    }
    setSelectedPrepCategories(suggested);
    prepCategoriesCustomized = true;
    updatePrepPresetButtons();
    hideStepCategorySuggestion();
  });

  document.getElementById("prep-interview-step").addEventListener("change", () => {
    updateStepCategorySuggestion();
    if (!prepCategoriesCustomized) {
      const step = document.getElementById("prep-interview-step").value;
      const suggested = STEP_CATEGORY_SUGGESTIONS[step];
      if (suggested) {
        setSelectedPrepCategories(suggested);
        updatePrepPresetButtons();
      }
    }
  });
}

async function loadPrepCategories() {
  try {
    const response = await fetch("/prep/categories");
    if (!response.ok) {
      return;
    }

    const categories = await response.json();
    const byValue = new Map(categories.map((category) => [category.value, category]));
    const container = document.getElementById("prep-category-list");
    container.innerHTML = "";
    prepCategoryValues.length = 0;

    for (const category of categories) {
      prepCategoryLabels[category.value] = category.label;
      prepCategoryValues.push(category.value);
    }

    for (const group of PREP_CATEGORY_GROUPS) {
      const groupValues = group.values.filter((value) => byValue.has(value));
      if (groupValues.length === 0) {
        continue;
      }

      const groupEl = document.createElement("div");
      groupEl.className = "category-group";
      groupEl.innerHTML = `<p class="category-group-label">${escapeHtml(group.label)}</p>`;

      const row = document.createElement("div");
      row.className = "category-chip-row";

      for (const value of groupValues) {
        const category = byValue.get(value);
        const checked = !DEFAULT_UNCHECKED_PREP_CATEGORIES.has(value);
        const label = document.createElement("label");
        label.className = checked ? "category-chip is-selected" : "category-chip";
        label.dataset.category = value;
        label.innerHTML = `
          <input
            type="checkbox"
            name="question_categories"
            value="${escapeHtml(value)}"
            ${checked ? "checked" : ""}
          >
          <span>${escapeHtml(category.label)}</span>
        `;

        const checkbox = label.querySelector("input");
        checkbox.addEventListener("change", () => {
          label.classList.toggle("is-selected", checkbox.checked);
          prepCategoriesCustomized = true;
          updatePrepCategoryCount();
          updatePrepPresetButtons();
          updateStepCategorySuggestion();
        });

        row.appendChild(label);
      }

      groupEl.appendChild(row);
      container.appendChild(groupEl);
    }

    updatePrepCategoryCount();
    updatePrepPresetButtons();
    updateStepCategorySuggestion();
  } catch {
    // Ignore category list failures in the UI.
  }
}

function getPrepCategoryCheckboxes() {
  return Array.from(document.querySelectorAll('#prep-category-list input[name="question_categories"]'));
}

function setSelectedPrepCategories(values) {
  const allowed = new Set(values);
  getPrepCategoryCheckboxes().forEach((checkbox) => {
    checkbox.checked = allowed.has(checkbox.value);
    checkbox.closest(".category-chip")?.classList.toggle("is-selected", checkbox.checked);
  });
  updatePrepCategoryCount();
}

function applyPrepCategoryPreset(presetKey) {
  const preset = PREP_CATEGORY_PRESETS[presetKey];
  if (!preset) {
    return;
  }

  const values = typeof preset === "function" ? preset() : preset;
  setSelectedPrepCategories(values);
  prepCategoriesCustomized = presetKey !== "all";
  updatePrepPresetButtons();
  updateStepCategorySuggestion();
}

function updatePrepCategoryCount() {
  const selected = getPrepCategoryCheckboxes().filter((checkbox) => checkbox.checked).length;
  const total = prepCategoryValues.length;
  const counter = document.getElementById("prep-category-count");
  counter.textContent = `${selected} of ${total} selected`;
  counter.classList.toggle("is-empty", selected === 0);
}

function updatePrepPresetButtons() {
  const selected = new Set(
    getPrepCategoryCheckboxes()
      .filter((checkbox) => checkbox.checked)
      .map((checkbox) => checkbox.value)
  );

  document.querySelectorAll(".prep-preset-card").forEach((button) => {
    const presetKey = button.dataset.preset;
    const preset = PREP_CATEGORY_PRESETS[presetKey];
    const values = typeof preset === "function" ? preset() : preset || [];
    const matches =
      values.length === selected.size && values.every((value) => selected.has(value));
    button.classList.toggle("is-active", matches);
  });
}

function updateStepCategorySuggestion() {
  const step = document.getElementById("prep-interview-step").value;
  const suggestion = document.getElementById("prep-category-suggestion");
  const suggested = STEP_CATEGORY_SUGGESTIONS[step];
  if (!step || !suggested) {
    suggestion.classList.add("hidden");
    return;
  }

  const selected = new Set(
    getPrepCategoryCheckboxes()
      .filter((checkbox) => checkbox.checked)
      .map((checkbox) => checkbox.value)
  );
  const alreadyMatches =
    suggested.length === selected.size && suggested.every((value) => selected.has(value));

  suggestion.classList.toggle("hidden", alreadyMatches);
}

function hideStepCategorySuggestion() {
  document.getElementById("prep-category-suggestion").classList.add("hidden");
}

function formatPrepCategory(category) {
  return prepCategoryLabels[category] || String(category).replaceAll("_", " ");
}

async function loadInterviewSteps() {
  try {
    const response = await fetch("/interview-steps");
    if (!response.ok) {
      return;
    }

    const steps = await response.json();
    populateStepSelect("interview-step", steps, false);
    populateStepSelect("prep-interview-step", steps, true);
  } catch {
    // Ignore step list failures in the UI.
  }
}

function populateStepSelect(selectId, steps, required) {
  const select = document.getElementById(selectId);
  select.innerHTML = required
    ? '<option value="">Select interview step</option>'
    : '<option value="">Auto-detect from questions</option>';

  for (const step of steps) {
    const option = document.createElement("option");
    option.value = step.value;
    option.textContent = step.label;
    select.appendChild(option);
  }
}

prepForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  clearPrepError();
  hidePrepResults();
  setPrepLoading(true);

  const formData = new FormData(prepForm);
  const selectedCategories = formData.getAll("question_categories");
  if (selectedCategories.length === 0) {
    showPrepError("Select at least one question type.");
    setPrepLoading(false);
    return;
  }

  const payload = {
    role_title: formData.get("role_title"),
    role_description: formData.get("role_description"),
    interview_step: formData.get("interview_step"),
    company: formData.get("company") || null,
    question_count: Number(formData.get("question_count") || 10),
    question_categories: selectedCategories,
  };

  try {
    const response = await fetch("/prepare", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (!response.ok) {
      throw new Error(await readError(response));
    }

    const result = await response.json();
    renderPrepResults(result);
  } catch (error) {
    showPrepError(error.message || "Something went wrong.");
  } finally {
    setPrepLoading(false);
  }
});

function renderPrepResults(result) {
  document.getElementById("prep-total-questions").textContent = String(result.predicted_questions.length);
  document.getElementById("prep-matching-interviews").textContent = String(
    result.matching_step_interviews_used ?? 0
  );
  const bankTotal = result.past_questions_reviewed ?? 0;
  const bankSample = result.unique_past_questions_used ?? 0;
  document.getElementById("prep-bank-sample").textContent =
    bankTotal > 0 ? `${bankSample} / ${bankTotal}` : String(bankSample);
  document.getElementById("prep-summary").textContent = result.prep_summary;
  const requested = result.requested_question_count ?? result.predicted_questions.length;
  const returned = result.predicted_questions.length;
  const available = result.available_bank_questions ?? 0;
  const shortfall = document.getElementById("prep-shortfall-notice");
  if (returned < requested && available < requested) {
    shortfall.textContent =
      `Showing ${returned} of ${requested} requested questions — your bank has ${available} unique question(s) for this round. Analyze more recordings to add questions.`;
    shortfall.classList.remove("hidden");
  } else {
    shortfall.textContent = "";
    shortfall.classList.add("hidden");
  }
  document.getElementById("prep-step-label").textContent =
    `Preparing for · ${formatStepLabel(result.interview_step)}`;
  renderChips("prep-focus-areas", result.focus_areas);

  const container = document.getElementById("prep-question-list");
  container.innerHTML = "";

  result.predicted_questions.forEach((item, index) => {
    const article = document.createElement("article");
    article.className = "prep-question-card";
    article.dataset.category = item.category || "other";
    article.innerHTML = `
      <header class="prep-question-head">
        <span class="prep-question-index">${String(index + 1).padStart(2, "0")}</span>
        <div>
          <span class="prep-question-category">${escapeHtml(formatPrepCategory(item.category))}</span>
          <h3 class="prep-question-title">${escapeHtml(item.question)}</h3>
        </div>
      </header>
      <p class="prep-question-meta">
        ${escapeHtml(item.source.replaceAll("_", " "))}
        ${item.based_on_role ? ` · From ${escapeHtml(item.based_on_role)}` : ""}
        ${item.original_question ? ` · <span class="prep-bank-line">Bank: ${escapeHtml(item.original_question)}</span>` : ""}
      </p>
    `;
    container.appendChild(article);
  });

  prepResultsSection.classList.remove("hidden");
  prepResultsSection.scrollIntoView({ behavior: "smooth", block: "start" });
}

function showPrepError(message) {
  prepErrorPanel.textContent = message;
  prepErrorPanel.classList.remove("hidden");
}

function clearPrepError() {
  prepErrorPanel.textContent = "";
  prepErrorPanel.classList.add("hidden");
}

function hidePrepResults() {
  prepResultsSection.classList.add("hidden");
}

function setPrepLoading(isLoading) {
  prepSubmitBtn.disabled = isLoading;
  prepSubmitBtn.querySelector(".prep-submit-label").textContent = isLoading
    ? "Generating practice questions..."
    : "Generate practice questions";
}

function formatStepLabel(step) {
  return String(step || "").replaceAll("_", " ");
}
