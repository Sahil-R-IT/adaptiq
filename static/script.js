// ============================================================
// AdaptIQ — frontend logic
// Supports: topic, website, youtube, image, document modes
// ============================================================

const state = {
  questions: [],
  currentTopic: "",
  currentPrompt: "",
  sessionMeta: null,
  lastIncorrectItems: [],
  selectedFile: null,
  currentSourceType: "topic",
  currentSourceInfo: null,
};

const $ = id => document.getElementById(id);

// Elements
const topicInput = $("topicInput");
const generateBtn = $("generateBtn");
const topicError = $("topicError");
const adaptiveBadge = $("adaptiveBadge");

const loadingSection = $("loadingSection");
const quizSection = $("quizSection");
const resultsSection = $("resultsSection");
const loaderText = $("loaderText");

const quizTopicLabel = $("quizTopicLabel");
const quizSubmeta = $("quizSubmeta");
const quizRequestTags = $("quizRequestTags");
const quizSourceStrip = $("quizSourceStrip");
const quizSourceIcon = $("quizSourceIcon");
const quizSourceTitle = $("quizSourceTitle");
const weakConceptsPanel = $("weakConceptsPanel");
const weakConceptTags = $("weakConceptTags");
const questionsContainer = $("questionsContainer");
const submitBtn = $("submitBtn");

const ringFill = $("ringFill");
const scorePct = $("scorePct");
const scoreSummary = $("scoreSummary");
const scoreMessage = $("scoreMessage");
const xpBadge = $("xpBadge");
const primaryLevelBadge = $("primaryLevelBadge");
const mixBadge = $("mixBadge");
const modeBadge = $("modeBadge");
const sourceModeBadge = $("sourceModeBadge");
const resultsContainer = $("resultsContainer");
const weakAreasSummary = $("weakAreasSummary");
const weakSummaryList = $("weakSummaryList");
const retryBtn = $("retryBtn");
const newTopicBtn = $("newTopicBtn");

const dashboardBtn = $("dashboardBtn");
const dashboardOverlay = $("dashboardOverlay");
const closeDashboardBtn = $("closeDashboardBtn");
const refreshAnalyticsBtn = $("refreshAnalyticsBtn");
const clearHistoryBtn = $("clearHistoryBtn");

const totalXpStat = $("totalXpStat");
const accuracyStat = $("accuracyStat");
const streakStat = $("streakStat");
const quizzesStat = $("quizzesStat");
const strongTopicList = $("strongTopicList");
const weakTopicList = $("weakTopicList");

// Source UI elements
const sourceTabs = document.querySelectorAll(".source-tab");
const dropZone = $("dropZone");
const fileInput = $("fileInput");
const filePreview = $("filePreview");
const fileName = $("fileName");
const fileIcon = $("fileIcon");
const removeFileBtn = $("removeFileBtn");
const sourceBadge = $("sourceBadge");
const sourceIcon = $("sourceIcon");
const sourceLabel = $("sourceLabel");
const promptHelper = $("promptHelper");

// ============================================================
// Source tabs
// ============================================================

const SOURCE_META = {
  topic: {
    icon: "✏️",
    label: "Topic",
    helper: "Default = 10 questions · No type = mixed · No level = easy first, adaptive on retry",
    placeholder: "Try: maths quiz\nor: give me 10 mcq on photosynthesis\nor: Python basics with hard questions",
  },
  website: {
    icon: "🌐",
    label: "Website",
    helper: "Paste an article or web page URL. AdaptIQ will extract the content and build a quiz.",
    placeholder: "https://en.wikipedia.org/wiki/Photosynthesis\nor: make 5 hard questions from https://example.com/article",
  },
  youtube: {
    icon: "▶️",
    label: "YouTube",
    helper: "Paste a YouTube link. AdaptIQ will fetch the transcript and quiz you on it.",
    placeholder: "https://youtu.be/VIDEO_ID\nor: 10 mcq from https://www.youtube.com/watch?v=VIDEO_ID",
  },
  file: {
    icon: "📎",
    label: "File",
    helper: "Upload a PDF, TXT, DOCX, or image. AdaptIQ extracts the content and quizzes you.",
    placeholder: "Optional: add a prompt like '5 hard mcq' or 'short answer only'",
  },
};

function setSourceTab(type, options = {}) {
  const { clearInput = false, clearFileState = false } = options;
  const prevType = state.currentSourceType;

  state.currentSourceType = type;

  sourceTabs.forEach(tab => {
    tab.classList.toggle("active", tab.dataset.source === type);
  });

  const meta = SOURCE_META[type] || SOURCE_META.topic;
  promptHelper.textContent = meta.helper;
  topicInput.placeholder = meta.placeholder;

  if (type === "file") {
    dropZone.classList.remove("hidden");
  } else {
    dropZone.classList.add("hidden");
  }

  if (type !== "topic") {
    sourceBadge.classList.remove("hidden");
    sourceIcon.textContent = meta.icon;
    sourceLabel.textContent = `${meta.label} mode`;
  } else {
    sourceBadge.classList.add("hidden");
  }

  if (clearInput) {
    topicInput.value = "";
    topicError.classList.add("hidden");
  }

  if (clearFileState && prevType === "file" && type !== "file") {
    clearFile();
  }
}

sourceTabs.forEach(tab => {
  tab.addEventListener("click", () => {
    const nextType = tab.dataset.source;

    setSourceTab(nextType, {
      clearInput: true,
      clearFileState: nextType !== "file",
    });
  });
});

// Auto-detect source from pasted text
topicInput.addEventListener("input", () => {
  const val = topicInput.value.trim();
  const youtubePattern = /(?:https?:\/\/)?(?:www\.)?(?:youtube\.com\/watch\?v=|youtu\.be\/|youtube\.com\/shorts\/)([\w\-]+)/i;
  const urlPattern = /https?:\/\/[^\s]+/i;

  if (!val) return;

  if (youtubePattern.test(val)) {
    if (state.currentSourceType !== "youtube") {
      setSourceTab("youtube");
    }
  } else if (urlPattern.test(val)) {
    if (state.currentSourceType !== "website") {
      setSourceTab("website");
    }
  }
});

// ============================================================
// File upload & drag-and-drop
// ============================================================

const IMAGE_EXTS = new Set(["png", "jpg", "jpeg", "webp", "gif"]);
const DOC_EXTS = new Set(["pdf", "txt", "docx", "doc", "md"]);

function getFileIcon(ext) {
  if (IMAGE_EXTS.has(ext)) return "🖼️";
  if (ext === "pdf") return "📕";
  if (ext === "docx" || ext === "doc") return "📝";
  if (ext === "txt" || ext === "md") return "📄";
  return "📎";
}

function setSelectedFile(file) {
  if (!file) {
    clearFile();
    return;
  }

  const MAX_MB = 20;
  if (file.size > MAX_MB * 1024 * 1024) {
    alert(`File too large. Maximum size is ${MAX_MB} MB.`);
    return;
  }

  const ext = (file.name.split(".").pop() || "").toLowerCase();
  const allowed = new Set([...IMAGE_EXTS, ...DOC_EXTS]);
  if (!allowed.has(ext)) {
    alert(`File type .${ext} is not supported.\nAllowed: PDF, TXT, DOCX, PNG, JPG, WEBP`);
    return;
  }

  state.selectedFile = file;
  fileName.textContent = file.name;
  fileIcon.textContent = getFileIcon(ext);
  filePreview.classList.remove("hidden");
  dropZone.classList.add("has-file");
  setSourceTab("file");
}

function clearFile() {
  state.selectedFile = null;
  fileInput.value = "";
  filePreview.classList.add("hidden");
  dropZone.classList.remove("has-file");
}

fileInput.addEventListener("change", () => {
  if (fileInput.files && fileInput.files[0]) {
    setSelectedFile(fileInput.files[0]);
  }
});

removeFileBtn.addEventListener("click", clearFile);

// Drag-and-drop on drop zone
dropZone.addEventListener("dragover", e => {
  e.preventDefault();
  dropZone.classList.add("drag-over");
});

dropZone.addEventListener("dragleave", () => {
  dropZone.classList.remove("drag-over");
});

dropZone.addEventListener("drop", e => {
  e.preventDefault();
  dropZone.classList.remove("drag-over");
  const files = e.dataTransfer?.files;
  if (files && files[0]) {
    setSelectedFile(files[0]);
  }
});

// Also support drag-and-drop anywhere on the page body
document.body.addEventListener("dragover", e => {
  if (state.currentSourceType === "file") {
    e.preventDefault();
  }
});

document.body.addEventListener("drop", e => {
  if (state.currentSourceType === "file") {
    e.preventDefault();
    const files = e.dataTransfer?.files;
    if (files && files[0]) {
      setSelectedFile(files[0]);
    }
  }
});

// ============================================================
// Sections & routing
// ============================================================

function showSection(name) {
  [loadingSection, quizSection, resultsSection].forEach(el => el.classList.add("hidden"));
  const map = { loading: loadingSection, quiz: quizSection, results: resultsSection };
  const target = map[name];
  if (target) {
    target.classList.remove("hidden");
    target.scrollIntoView({ behavior: "smooth", block: "start" });
  }
}

async function apiPost(url, body = {}) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.error || `HTTP ${res.status}`);
  }
  return res.json();
}

async function apiPostForm(url, formData) {
  const res = await fetch(url, { method: "POST", body: formData });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.error || `HTTP ${res.status}`);
  }
  return res.json();
}

async function apiGet(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

// ============================================================
// Quiz generation
// ============================================================

async function handleGenerate({ adaptive = false } = {}) {
  const prompt = topicInput.value.trim();
  const hasFile = !!state.selectedFile;

  if (!prompt && !hasFile) {
    topicError.classList.remove("hidden");
    topicInput.focus();
    return;
  }

  topicError.classList.add("hidden");
  generateBtn.disabled = true;
  state.currentPrompt = prompt;

  // Determine loader message
  const sourceType = state.currentSourceType;
  const loaderMessages = {
    topic: adaptive ? "Building adaptive quiz from your mistakes..." : "Reading your prompt and generating quiz...",
    website: "Fetching page content and building quiz...",
    youtube: "Fetching YouTube transcript and building quiz...",
    image: "Analysing image and generating quiz...",
    document: "Extracting document content and generating quiz...",
    file: hasFile ? "Processing your file and generating quiz..." : "Generating quiz...",
  };

  loaderText.textContent = loaderMessages[hasFile ? "file" : sourceType] || "Generating your quiz...";
  showSection("loading");

  try {
    let data;

    if (hasFile || state.selectedFile) {
      // Multipart form submission
      const formData = new FormData();
      formData.append("prompt", prompt || "generate quiz");
      formData.append("adaptive", adaptive ? "true" : "false");
      formData.append("focus_context", JSON.stringify(adaptive ? state.lastIncorrectItems : []));
      if (state.selectedFile) {
        formData.append("file", state.selectedFile);
      }
      data = await apiPostForm("/generate-quiz", formData);
    } else {
      // JSON submission
      data = await apiPost("/generate-quiz", {
        prompt,
        adaptive,
        focus_context: adaptive ? state.lastIncorrectItems : [],
      });
    }

    state.questions = data.questions || [];
    state.currentTopic = data.request?.topic || prompt;
    state.currentSourceInfo = data.source || null;

    state.sessionMeta = {
      session_id: data.session_id,
      topic: data.request?.topic || prompt,
      source_prompt: data.request?.source_prompt || prompt,
      primary_level: data.request?.primary_level || "easy",
      difficulty_plan: data.request?.difficulty_plan || [],
      question_types: data.request?.question_types || [],
      quiz_size: data.request?.quiz_size || state.questions.length,
      is_adaptive: !!adaptive,
      // Source metadata for DB
      source_type: data.source?.type || "topic",
      source_title: data.source?.title || "",
      source_url: data.source?.url || "",
      source_file_name: data.source?.file_name || "",
      source_excerpt: data.source?.excerpt || "",
    };

    if (!state.questions.length) {
      throw new Error("No questions returned.");
    }

    adaptiveBadge.classList.toggle("hidden", !data.is_adaptive);
    renderQuiz(data.request, state.questions, data.weak_concepts || [], data.source || null);
    showSection("quiz");
  } catch (err) {
    loadingSection.classList.add("hidden");
    alert("Quiz generation failed: " + err.message);
  } finally {
    generateBtn.disabled = false;
  }
}

// ============================================================
// Quiz rendering
// ============================================================

const SOURCE_ICONS = {
  topic: "✏️",
  website: "🌐",
  youtube: "▶️",
  image: "🖼️",
  document: "📄",
};

function renderQuiz(requestMeta, questions, weakConcepts, sourceInfo) {
  quizTopicLabel.textContent = requestMeta.topic;

  // Source strip
  if (sourceInfo && sourceInfo.type && sourceInfo.type !== "topic") {
    quizSourceStrip.classList.remove("hidden");
    quizSourceIcon.textContent = SOURCE_ICONS[sourceInfo.type] || "📌";
    const label = sourceInfo.title || sourceInfo.url || sourceInfo.file_name || capitalize(sourceInfo.type);
    quizSourceTitle.textContent = label.length > 80 ? label.slice(0, 77) + "…" : label;
  } else {
    quizSourceStrip.classList.add("hidden");
  }

  const typeLabel = describeQuestionTypes(requestMeta.question_types || []);
  const mixLabel = describeDifficultyMix(requestMeta.difficulty_plan || []);
  quizSubmeta.textContent = `${questions.length} questions · ${capitalize(requestMeta.primary_level)} primary · ${typeLabel} · ${mixLabel}`;

  quizRequestTags.innerHTML = `
    <span class="request-tag">${escape(capitalize(requestMeta.primary_level))} primary</span>
    <span class="request-tag">${escape(typeLabel)}</span>
    <span class="request-tag">${escape(mixLabel)}</span>
    <span class="request-tag">${escape(String(questions.length))} Qs</span>
    ${sourceInfo && sourceInfo.type && sourceInfo.type !== "topic"
      ? `<span class="request-tag source-mode-tag">${escape(SOURCE_ICONS[sourceInfo.type] || "")} ${escape(capitalize(sourceInfo.type))}</span>`
      : ""}
  `;

  if (weakConcepts.length) {
    weakConceptsPanel.classList.remove("hidden");
    weakConceptTags.innerHTML = weakConcepts
      .slice(0, 4)
      .map(item => `<span class="weak-tag">${escape(item.concept)} · ${escape(String(item.error_rate))}%</span>`)
      .join("");
  } else {
    weakConceptsPanel.classList.add("hidden");
    weakConceptTags.innerHTML = "";
  }

  questionsContainer.innerHTML = "";
  questions.forEach((question, index) => {
    questionsContainer.appendChild(buildQuestionCard(question, index));
  });
}

function buildQuestionCard(question, index) {
  const card = document.createElement("div");
  card.className = "question-card";
  card.dataset.num = `${index + 1} / ${state.questions.length}`;

  const typeMap = {
    mcq: "Multiple Choice",
    fill_blank: "Fill Blank",
    multi_select: "Multi Select",
    short_answer: "Short Answer",
  };

  card.innerHTML = `
    <span class="question-type-badge type-${question.question_type}">${escape(typeMap[question.question_type] || question.question_type)}</span>
    <div class="question-meta-line">
      <p class="question-concept">Concept: ${escape(question.concept || "General")}</p>
      <span class="mini-difficulty">${escape(capitalize(question.difficulty || "easy"))}</span>
    </div>
    <p class="question-text">${escape(question.question)}</p>
    ${renderAnswerWidget(question, index)}
  `;

  return card;
}

function renderAnswerWidget(question, index) {
  switch (question.question_type) {
    case "mcq":
      return `
        <ul class="options-list">
          ${(question.options || []).map((opt, oi) => {
            const { key, text } = splitOption(opt);
            return `
              <li class="option-item">
                <input class="option-radio" type="radio" name="q${index}" id="q${index}_${oi}" value="${escape(key)}" />
                <label class="option-label" for="q${index}_${oi}">
                  <span class="option-key">${escape(key)}</span>
                  <span>${escape(text)}</span>
                </label>
              </li>`;
          }).join("")}
        </ul>`;

    case "multi_select":
      return `
        <p class="question-hint">Select all that apply.</p>
        <ul class="options-list">
          ${(question.options || []).map((opt, oi) => {
            const { key, text } = splitOption(opt);
            return `
              <li class="option-item">
                <input class="option-checkbox" type="checkbox" name="q${index}" id="q${index}_${oi}" value="${escape(key)}" />
                <label class="option-label" for="q${index}_${oi}">
                  <span class="option-key">${escape(key)}</span>
                  <span>${escape(text)}</span>
                </label>
              </li>`;
          }).join("")}
        </ul>`;

    case "fill_blank":
      return `<input class="fill-input" type="text" id="q${index}_fill" placeholder="Your answer..." autocomplete="off" spellcheck="false" />`;

    case "short_answer":
      return `<textarea class="short-textarea" id="q${index}_short" rows="4" placeholder="Write your answer here..."></textarea>`;

    default:
      return `<input class="fill-input" type="text" id="q${index}_default" placeholder="Your answer..." />`;
  }
}

function collectAnswers() {
  return state.questions.map((question, index) => {
    let userAnswer = "";
    switch (question.question_type) {
      case "mcq": {
        const checked = document.querySelector(`input[name="q${index}"]:checked`);
        userAnswer = checked ? checked.value : "";
        break;
      }
      case "multi_select": {
        const checked = [...document.querySelectorAll(`input[name="q${index}"]:checked`)];
        userAnswer = checked.map(n => n.value).sort().join(",");
        break;
      }
      case "fill_blank":
        userAnswer = $(`q${index}_fill`)?.value.trim() || "";
        break;
      case "short_answer":
        userAnswer = $(`q${index}_short`)?.value.trim() || "";
        break;
      default:
        userAnswer = $(`q${index}_default`)?.value.trim() || "";
    }
    return { ...question, user_answer: userAnswer };
  });
}

// ============================================================
// Submit
// ============================================================

submitBtn.addEventListener("click", async () => {
  const answers = collectAnswers();
  const unanswered = answers.filter(item => !item.user_answer).length;

  if (unanswered) {
    const proceed = confirm(`${unanswered} question(s) are unanswered. Submit anyway?`);
    if (!proceed) return;
  }

  submitBtn.disabled = true;
  loaderText.textContent = "Checking answers and updating analytics...";
  showSection("loading");

  try {
    const data = await apiPost("/submit-answers", {
      answers,
      session_meta: state.sessionMeta,
    });

    state.lastIncorrectItems = data.incorrect_items || [];
    renderResults(data);
    renderAnalytics(data.analytics);

    if (state.currentTopic) {
      const weakData = await apiGet(`/weak-areas?topic=${encodeURIComponent(state.currentTopic)}`);
      renderWeakSummary(weakData.weak_concepts || []);
    }

    showSection("results");
  } catch (err) {
    alert("Submit failed: " + err.message);
    showSection("quiz");
  } finally {
    submitBtn.disabled = false;
  }
});

// ============================================================
// Results rendering
// ============================================================

function renderResults(data) {
  const { results, score, xp_earned, session } = data;
  const pct = score?.pct || 0;
  const circumference = 326.7;
  const offset = circumference - (pct / 100) * circumference;

  scorePct.textContent = `${Math.round(pct)}%`;
  const color = pct >= 70 ? "var(--green)" : pct >= 40 ? "var(--amber)" : "var(--red)";
  ringFill.style.stroke = color;
  scorePct.style.color = color;

  requestAnimationFrame(() => requestAnimationFrame(() => {
    ringFill.style.strokeDashoffset = offset;
  }));

  scoreSummary.textContent = `${score.correct} / ${score.total} correct`;
  scoreMessage.textContent =
    pct === 100 ? "Perfect. That round was clean." :
    pct >= 80 ? "Strong. You are close to locking this topic down." :
    pct >= 60 ? "Decent, but weak spots still exist." :
    pct >= 40 ? "Average at best. Retry and fix the misses." :
    "Rough. Use adaptive retry instead of pretending this is fine.";

  xpBadge.textContent = `+${xp_earned || 0} XP`;
  primaryLevelBadge.textContent = `${capitalize(session?.primary_level || state.sessionMeta?.primary_level || "easy")} primary`;
  mixBadge.textContent = describeDifficultyMix(session?.difficulty_plan || state.sessionMeta?.difficulty_plan || []);
  modeBadge.textContent = session?.is_adaptive ? "Adaptive" : "Standard";

  // Source mode badge
  const sType = state.sessionMeta?.source_type;
  if (sType && sType !== "topic") {
    sourceModeBadge.classList.remove("hidden");
    sourceModeBadge.textContent = `${SOURCE_ICONS[sType] || ""} ${capitalize(sType)}`;
  } else {
    sourceModeBadge.classList.add("hidden");
  }

  resultsContainer.innerHTML = "";
  results.forEach((result, index) => {
    const card = document.createElement("div");
    card.className = `result-card ${result.is_correct ? "correct" : "incorrect"}`;
    card.style.animationDelay = `${index * 0.05}s`;

    card.innerHTML = `
      <div class="result-card-header">
        <span class="result-status-icon">${result.is_correct ? "✓" : "✗"}</span>
        <span class="result-question-text">${escape(result.question)}</span>
      </div>
      <div class="result-card-body">
        <div class="result-meta-row">
          <span class="result-mini-tag">${escape(result.concept || "General")}</span>
          <span class="result-mini-tag">${escape(capitalize(result.difficulty || "easy"))}</span>
          <span class="result-mini-tag">${escape(describeQuestionTypes([result.question_type]))}</span>
        </div>
        <div class="result-answers">
          <div class="answer-block yours">
            <div class="answer-block-label">Your answer</div>
            <div class="answer-block-value">${escape(result.user_answer || "—")}</div>
          </div>
          ${!result.is_correct ? `
            <div class="answer-block correct-ans">
              <div class="answer-block-label">Correct answer</div>
              <div class="answer-block-value">${escape(result.correct_answer || "—")}</div>
            </div>` : ""}
        </div>
        ${result.explanation ? `
          <div class="result-explanation">
            <strong>Explanation</strong>
            ${escape(result.explanation)}
          </div>` : ""}
      </div>
    `;

    resultsContainer.appendChild(card);
  });
}

function renderWeakSummary(weakConcepts) {
  if (!weakConcepts.length) {
    weakAreasSummary.classList.add("hidden");
    weakSummaryList.innerHTML = "";
    return;
  }
  weakAreasSummary.classList.remove("hidden");
  weakSummaryList.innerHTML = weakConcepts.slice(0, 5).map(item => `
    <div class="weak-item">
      <span class="weak-concept-name">${escape(item.concept)}</span>
      <div class="weak-bar-wrap">
        <div class="weak-bar" style="width:${escape(String(item.error_rate))}%"></div>
      </div>
      <span class="weak-pct">${escape(String(item.error_rate))}%</span>
    </div>
  `).join("");
}

function renderAnalytics(data) {
  const summary = data?.summary || {};
  const strongTopics = data?.strong_topics || [];
  const weakTopics = data?.weak_topics || [];

  totalXpStat.textContent = summary.total_xp ?? 0;
  accuracyStat.textContent = `${Math.round(summary.accuracy || 0)}%`;
  streakStat.textContent = `${summary.streak ?? 0}`;
  quizzesStat.textContent = `${summary.quizzes ?? 0}`;

  strongTopicList.classList.toggle("empty-state", !strongTopics.length);
  strongTopicList.innerHTML = strongTopics.length
    ? strongTopics.map(item => `
        <div class="analytics-row">
          <div>
            <strong>${escape(item.topic)}</strong>
            <p>${escape(item.mastery)} · ${escape(String(item.total_questions))} questions · ${escape(String(item.correct_answers))} correct</p>
          </div>
          <span class="analytics-pill good">${escape(String(item.accuracy))}%</span>
        </div>`)
      .join("")
    : "No strong areas yet.";

  weakTopicList.classList.toggle("empty-state", !weakTopics.length);
  weakTopicList.innerHTML = weakTopics.length
    ? weakTopics.map(item => `
        <div class="analytics-row analytics-row-stack">
          <div class="analytics-row-main">
            <div class="analytics-row-topline">
              <strong>${escape(item.topic)}</strong>
              <span class="analytics-pill bad">${escape(String(item.error_rate))}% error</span>
            </div>
            <p>${escape(String(item.wrong))} wrong out of ${escape(String(item.total))} attempts</p>
            <div class="concept-chip-row">
              ${(item.top_concepts || []).map(c => `
                <span class="concept-chip">${escape(c.concept)} · ${escape(String(c.error_rate))}%</span>
              `).join("")}
            </div>
          </div>
        </div>`)
      .join("")
    : "No weak areas yet.";
}

// ============================================================
// Dashboard
// ============================================================

function openDashboard() {
  dashboardOverlay.classList.remove("hidden");
  document.body.classList.add("modal-open");
}

function closeDashboard() {
  dashboardOverlay.classList.add("hidden");
  document.body.classList.remove("modal-open");
}

async function fetchAnalytics(openAfter = false) {
  try {
    const data = await apiGet("/analytics");
    renderAnalytics(data);
    if (openAfter) openDashboard();
  } catch (err) {
    alert("Failed to load analytics: " + err.message);
  }
}

async function handleClearHistory() {
  const confirmed = confirm(
    "This will permanently delete all quiz history, analytics, XP, streak and weak-topic data. Continue?"
  );
  if (!confirmed) return;

  clearHistoryBtn.disabled = true;
  try {
    await apiPost("/clear-history", {});
    state.lastIncorrectItems = [];
    renderAnalytics({
      summary: { total_xp: 0, accuracy: 0, streak: 0, quizzes: 0 },
      strong_topics: [],
      weak_topics: [],
    });
    weakAreasSummary.classList.add("hidden");
    weakSummaryList.innerHTML = "";
    alert("History cleared.");
  } catch (err) {
    alert("Clear history failed: " + err.message);
  } finally {
    clearHistoryBtn.disabled = false;
  }
}

// ============================================================
// Retry / new topic
// ============================================================

retryBtn.addEventListener("click", () => {
  if (!state.currentPrompt && !state.selectedFile) {
    topicInput.focus();
    return;
  }
  handleGenerate({ adaptive: true });
});

newTopicBtn.addEventListener("click", () => {
  state.questions = [];
  state.sessionMeta = null;
  state.lastIncorrectItems = [];
  state.currentTopic = "";
  state.currentPrompt = "";
  state.currentSourceInfo = null;
  topicInput.value = "";
  clearFile();
  setSourceTab("topic");
  adaptiveBadge.classList.add("hidden");
  weakAreasSummary.classList.add("hidden");
  weakConceptsPanel.classList.add("hidden");
  topicInput.focus();
  window.scrollTo({ top: 0, behavior: "smooth" });
});

// ============================================================
// Event wiring
// ============================================================

generateBtn.addEventListener("click", () => handleGenerate());
dashboardBtn.addEventListener("click", () => fetchAnalytics(true));
closeDashboardBtn.addEventListener("click", closeDashboard);
refreshAnalyticsBtn.addEventListener("click", () => fetchAnalytics(false));
clearHistoryBtn.addEventListener("click", handleClearHistory);

dashboardOverlay.addEventListener("click", event => {
  if (event.target === dashboardOverlay) closeDashboard();
});

topicInput.addEventListener("keydown", event => {
  if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
    handleGenerate();
  }
});

// ============================================================
// Utility functions
// ============================================================

function describeQuestionTypes(types) {
  const list = types || [];
  if (!list.length || sameSet(list, ["mcq", "fill_blank", "multi_select", "short_answer"])) {
    return "Mixed styles";
  }
  const map = { mcq: "MCQ", fill_blank: "Fill Blank", multi_select: "Multi Select", short_answer: "Short Answer" };
  return list.map(t => map[t] || t).join(" + ");
}

function describeDifficultyMix(plan) {
  const list = Array.isArray(plan) ? plan : [];
  if (!list.length) return "Easy only";
  const counts = { easy: 0, medium: 0, hard: 0 };
  list.forEach(item => { if (counts[item] !== undefined) counts[item]++; });
  const parts = [];
  if (counts.easy) parts.push(`${counts.easy} easy`);
  if (counts.medium) parts.push(`${counts.medium} medium`);
  if (counts.hard) parts.push(`${counts.hard} hard`);
  return parts.join(" · ");
}

function sameSet(a, b) {
  return a.length === b.length && [...a].sort().join("|") === [...b].sort().join("|");
}

function splitOption(option) {
  const parts = String(option).split(".");
  if (parts.length > 1) return { key: parts[0].trim(), text: parts.slice(1).join(".").trim() };
  return { key: option.trim(), text: option.trim() };
}

function escape(value) {
  if (value === null || value === undefined) return "";
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function capitalize(value) {
  const text = String(value || "").trim();
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : "";
}

// ============================================================
// Init
// ============================================================

setSourceTab("topic");
topicInput.focus();
