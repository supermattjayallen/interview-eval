const form = document.getElementById("analysis-form");
const submitBtn = document.getElementById("submit-btn");
const statusPanel = document.getElementById("status-panel");
const statusLabel = document.getElementById("status-label");
const statusMessage = document.getElementById("status-message");
const progressBar = document.getElementById("progress-bar");
const errorPanel = document.getElementById("error-panel");
const resultsSection = document.getElementById("results");

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

function renderQaList(qaPairs, evaluationSkipped = false) {
  const container = document.getElementById("qa-list");
  container.innerHTML = "";

  qaPairs.forEach((qa, index) => {
    const scoreLabel = evaluationSkipped ? "—" : `${qa.score}/10`;
    const item = document.createElement("article");
    item.className = "qa-item";
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
          : `<div class="qa-block qa-block-ideal">
        <h3>Better answer</h3>
        <p class="qa-ideal-answer">${escapeHtml(qa.ideal_answer || "No suggested answer generated.")}</p>
      </div>
      <div class="qa-columns">
        <div>
          <h3>Strengths</h3>
          <ul>${renderListItems(qa.strengths)}</ul>
        </div>
        <div>
          <h3>Gaps</h3>
          <ul>${renderListItems(qa.gaps)}</ul>
        </div>
        <div>
          <h3>Key points to include</h3>
          <ul>${renderListItems(qa.ideal_answer_points)}</ul>
        </div>
      </div>`
      }
    `;
    container.appendChild(item);
  });
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
const savedJobSelect = document.getElementById("saved-job");

tabs.forEach((tab) => {
  tab.addEventListener("click", () => {
    tabs.forEach((item) => item.classList.remove("active"));
    tab.classList.add("active");
    const isAnalyze = tab.dataset.tab === "analyze";
    analyzePanel.classList.toggle("active", isAnalyze);
    preparePanel.classList.toggle("active", !isAnalyze);
  });
});

loadSavedJobs();
loadInterviewSteps();

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

savedJobSelect.addEventListener("change", () => {
  const option = savedJobSelect.selectedOptions[0];
  if (!option || !option.dataset.title) {
    return;
  }
  document.getElementById("prep-role-title").value = option.dataset.title || "";
  document.getElementById("prep-company").value = option.dataset.company || "";
  document.getElementById("prep-role-description").value = option.dataset.description || "";
});

prepForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  clearPrepError();
  hidePrepResults();
  setPrepLoading(true);

  const formData = new FormData(prepForm);
  const payload = {
    role_title: formData.get("role_title"),
    role_description: formData.get("role_description"),
    interview_step: formData.get("interview_step"),
    company: formData.get("company") || null,
    save_job_description: formData.get("save_job_description") === "on",
  };

  const jobId = formData.get("job_id");
  if (jobId) {
    payload.job_id = jobId;
  }

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
    await loadSavedJobs(result.saved_job_id);
  } catch (error) {
    showPrepError(error.message || "Something went wrong.");
  } finally {
    setPrepLoading(false);
  }
});

async function loadSavedJobs(selectJobId = "") {
  try {
    const response = await fetch("/jobs");
    if (!response.ok) {
      return;
    }

    const jobs = await response.json();
    savedJobSelect.innerHTML = '<option value="">New job description</option>';

    for (const job of jobs) {
      const option = document.createElement("option");
      option.value = job.job_id;
      option.textContent = job.company
        ? `${job.role_title} — ${job.company}`
        : job.role_title;
      option.dataset.title = job.role_title;
      option.dataset.company = job.company || "";
      option.dataset.description = job.role_description;
      if (job.job_id === selectJobId) {
        option.selected = true;
      }
      savedJobSelect.appendChild(option);
    }

    if (selectJobId) {
      savedJobSelect.dispatchEvent(new Event("change"));
    }
  } catch {
    // Ignore job list failures in the UI.
  }
}

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
  document.getElementById("prep-step-label").textContent =
    `Preparing for: ${formatStepLabel(result.interview_step)}`;
  renderChips("prep-focus-areas", result.focus_areas);

  const container = document.getElementById("prep-question-list");
  container.innerHTML = "";

  result.predicted_questions.forEach((item, index) => {
    const article = document.createElement("article");
    article.className = "qa-item";
    article.innerHTML = `
      <div class="qa-top">
        <h3 class="qa-question">Q${index + 1}. ${escapeHtml(item.question)}</h3>
        <span class="score-pill">${escapeHtml(item.category)}</span>
      </div>
      <p class="qa-meta">
        Source: ${escapeHtml(item.source.replaceAll("_", " "))}
        ${item.based_on_role ? ` · Based on: ${escapeHtml(item.based_on_role)}` : ""}
      </p>
      <div class="qa-block">
        <h3>Why this question is likely</h3>
        <p class="qa-answer">${escapeHtml(item.why_likely)}</p>
      </div>
      <div class="qa-block qa-block-ideal">
        <h3>Strong answer outline</h3>
        <p class="qa-ideal-answer">${escapeHtml(item.strong_answer_outline || "No outline generated.")}</p>
      </div>
      <div class="qa-columns">
        <div>
          <h3>Preparation tips</h3>
          <ul>${renderListItems(item.preparation_tips)}</ul>
        </div>
      </div>
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
  prepSubmitBtn.textContent = isLoading ? "Preparing..." : "Get possible questions";
}

function formatStepLabel(step) {
  return String(step || "").replaceAll("_", " ");
}
