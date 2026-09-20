"use strict";

const $ = (id) => document.getElementById(id);

async function api(path, { method = "GET", body } = {}) {
  const res = await fetch(path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (res.status === 204) return null;
  let data = null;
  try {
    data = await res.json();
  } catch (_) {
    // 본문이 JSON 이 아니면 아래 오류 메시지를 쓴다.
  }
  if (!res.ok) {
    const message = typeof data?.detail === "string" ? data.detail : "입력을 확인해 주세요.";
    const err = new Error(message);
    err.status = res.status;
    throw err;
  }
  return data;
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function showView(loggedIn) {
  $("join-view").hidden = loggedIn;
  $("main-view").hidden = !loggedIn;
}

function parseSqliteUtc(sqliteUtc) {
  // SQLite 의 "YYYY-MM-DD HH:MM:SS" (UTC) 를 Date 로 바꾼다.
  return new Date(sqliteUtc.replace(" ", "T") + "Z");
}

function formatTime(sqliteUtc) {
  const d = parseSqliteUtc(sqliteUtc);
  return Number.isNaN(d.getTime()) ? sqliteUtc : d.toLocaleString("ko-KR");
}

function formatClock(sqliteUtc) {
  const d = parseSqliteUtc(sqliteUtc);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleTimeString("ko-KR", { hour: "numeric", minute: "2-digit" });
}

/* ------------------------------------------------------------------ */
/* 상태                                                                 */
/* ------------------------------------------------------------------ */

const POLL_MS = 5000;

const state = {
  me: null, // {nickname, class_name}
  status: null, // GET /api/status
  thread: null, // 화면에 보이는 스레드 (없으면 새 코드 작성 화면)
  threads: [], // 내가 고친 코드 목록
  pinnedThreadId: null, // 방금 끝낸 코드나 지난 코드를 보는 중이면 그 스레드
  selectedId: null, // 힌트를 보여 줄 제출(버전)
  draft: null, // 아직 제출하지 않은 다음 버전 초안
  draftFor: null, // 그 초안이 속한 스레드
  scrolledFor: undefined, // 스트림을 끝까지 넘겨 둔 스레드
  pollTimer: null,
  chat: { sessionId: null, ws: null, ended: false, messages: [] },
};

// 값이 그대로면 다시 그리지 않는다. 입력 중인 칸이 5초마다 지워지지 않게 하려는 것이다.
const signatures = {};
function changed(key, value) {
  const next = JSON.stringify(value);
  if (signatures[key] === next) return false;
  signatures[key] = next;
  return true;
}

function viewKind() {
  if (state.status.helping) return "helping"; // 친구의 코드를 보며 도와주는 중
  if (!state.thread) return "fresh"; // 새 코드를 처음 제출하는 화면
  return state.thread.status === "open" ? "own" : "closed"; // 고치는 중 / 끝난 코드 보기
}

function selectedSubmission() {
  return state.thread?.submissions.find((s) => s.id === state.selectedId) ?? null;
}

async function loadThread(id) {
  return api(`/api/threads/${id}`);
}

let refreshSeq = 0;

async function refresh(retry = true) {
  // 폴링과 실시간 이벤트가 겹쳐 새로고침이 동시에 돌 때, 오래된 응답이 새 응답을 덮어쓰지 않게 한다.
  const seq = ++refreshSeq;
  const status = await api("/api/status");
  let threadId;
  if (status.helping) threadId = status.helping.thread_id;
  else threadId = state.pinnedThreadId ?? status.open_thread_id;

  let thread = null;
  if (threadId) {
    try {
      thread = await loadThread(threadId);
    } catch (err) {
      // 도움이 막 끝나서 더 이상 볼 수 없는 코드라면 처음부터 다시 판단한다.
      if (err.status === 404 && retry) {
        state.pinnedThreadId = null;
        return refresh(false);
      }
      throw err;
    }
  }
  const threads = await api("/api/threads");
  if (seq !== refreshSeq) return;
  state.status = status;
  state.thread = thread;
  state.threads = threads;

  const ids = thread ? thread.submissions.map((s) => s.id) : [];
  if (!ids.includes(state.selectedId)) state.selectedId = ids.length ? ids[ids.length - 1] : null;
  const threadKey = thread ? thread.id : null;
  if (state.draftFor !== threadKey) {
    state.draft = null;
    state.draftFor = threadKey;
  }

  render();
  syncChat();
}

function safeRefresh() {
  refresh().catch((err) => {
    if (err.status === 401) leaveToJoin("로그인이 끝났어요. 다시 입장해 주세요.");
  });
}

/* ------------------------------------------------------------------ */
/* 화면 그리기                                                          */
/* ------------------------------------------------------------------ */

function render() {
  renderSummary();
  renderStream();
  renderHints();
  renderHistory();
}

const MODES = {
  fresh: () => ({
    pill: "정답 대신 단계별 힌트",
    title: "내 코드 점검하기",
    desc: "코드를 제출하면 확인해볼 부분을 힌트로 알려 줘요. 고칠 때마다 새 버전이 옆에 차곡차곡 쌓여요.",
  }),
  own: (t) => ({
    pill: "고치는 중",
    title: `코드 고치기 · ${t.submissions.length}번째 제출`,
    desc: "하나를 고쳤다면 오른쪽 끝 칸에서 새 버전을 제출하세요. 원래 코드는 그대로 남아서 나란히 비교할 수 있어요.",
  }),
  closedFixed: () => ({
    pill: "완성",
    title: "모든 오류를 고쳤어요! 🎉",
    desc: "다른 친구를 도와주러 가거나, 새 코드를 시작할 수 있어요.",
  }),
  closedDropped: () => ({
    pill: "지난 코드",
    title: "그만둔 코드",
    desc: "이 코드는 새 코드를 시작하면서 그만두었어요. 제출한 버전과 힌트는 그대로 볼 수 있어요.",
  }),
  helping: (t) => ({
    pill: "도와주는 중",
    title: `${t.owner} 친구를 도와주는 중`,
    desc: "코드와 힌트를 함께 보면서 채팅으로 설명해 주세요. 정답 코드를 그대로 보내면 막혀요.",
  }),
};

function renderSummary() {
  const kind = viewKind();
  const t = state.thread;
  let mode;
  if (kind === "closed") mode = MODES[t.status === "fixed" ? "closedFixed" : "closedDropped"](t);
  else mode = MODES[kind](t);

  $("mode-pill").textContent = mode.pill;
  $("mode-title").textContent = mode.title;
  $("mode-desc").textContent = mode.desc;
  $("attempt-count").textContent = `${t ? t.submissions.length : 0}회`;

  $("finish-thread").hidden = kind !== "own";
  $("start-help").hidden = !(state.status.can_help && kind !== "helping");
  $("end-help").hidden = kind !== "helping";
  $("new-code").hidden = kind !== "closed";
  $("new-code").textContent = state.status.open_thread_id ? "고치는 코드로 돌아가기" : "새 코드 시작하기";
  $("history-card").hidden = kind === "helping";
}

function diffCounts(lines) {
  return {
    added: lines.filter((l) => l.op === "add").length,
    removed: lines.filter((l) => l.op === "remove").length,
  };
}

// 코드는 textContent 로만 넣는다 (HTML 로 해석하지 않음).
function codeView(lines) {
  const scroll = el("div", "code-scroll");
  const view = el("div", "code-view");
  for (const line of lines) {
    const row = el("div", `code-line ${line.op}`);
    row.append(el("span", "sign", line.op === "add" ? "+" : line.op === "remove" ? "−" : ""));
    row.append(el("span", "text", line.text === "" ? " " : line.text));
    view.append(row);
  }
  scroll.append(view);
  return scroll;
}

function plainLines(code) {
  return code.replace(/\n$/, "").split("\n").map((text) => ({ op: "equal", text }));
}

function renderVersion(submission, number, isLatest) {
  const card = el("article", "version");
  card.dataset.id = String(submission.id);
  card.tabIndex = 0;
  card.classList.toggle("selected", submission.id === state.selectedId);

  const head = el("div", "version-head");
  const title = el("div", "version-title");
  title.append(el("span", "num", `시도 #${number}`), el("span", "time", formatClock(submission.created_at)));
  head.append(title);
  head.append(el("span", `tag ${isLatest ? "latest" : ""}`, number === 1 ? "처음 제출" : isLatest ? "최신" : "이전 수정본"));
  card.append(head);

  const tags = el("div", "version-tags");
  if (submission.diff) {
    const { added, removed } = diffCounts(submission.diff);
    const tag = el("span", "tag");
    tag.append(el("span", null, "이전 시도와 비교 "), el("span", "plus", `+${added}`), el("span", null, " "), el("span", "minus", `−${removed}`));
    tags.append(tag);
  }
  const n = submission.findings.length;
  tags.append(el("span", `tag ${n ? "warn" : "ok"}`, n ? `확인할 부분 ${n}개` : "확인할 부분 없음 🎉"));
  card.append(tags);

  card.append(codeView(submission.diff ?? plainLines(submission.code)));

  card.addEventListener("click", () => selectVersion(submission.id));
  card.addEventListener("keydown", (event) => {
    if (event.target === card && (event.key === "Enter" || event.key === " ")) {
      event.preventDefault();
      selectVersion(submission.id);
    }
  });
  return card;
}

// 파이썬은 들여쓰기가 중요하므로 Tab 키로 공백 4칸을 넣는다.
// Esc 를 누르면 Tab 이 다시 포커스 이동으로 동작한다 (키보드 사용자 배려).
function bindTabIndent(textarea) {
  let tabIndents = true;
  textarea.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      tabIndents = false;
    } else if (event.key === "Tab" && tabIndents && !event.shiftKey) {
      event.preventDefault();
      textarea.setRangeText("    ", textarea.selectionStart, textarea.selectionEnd, "end");
      textarea.dispatchEvent(new Event("input"));
    }
  });
  textarea.addEventListener("blur", () => { tabIndents = true; });
}

function renderCompose(thread) {
  const card = el("article", "version compose");
  const number = thread ? thread.submissions.length + 1 : 1;
  const last = thread ? thread.submissions[thread.submissions.length - 1].code : "";

  const head = el("div", "version-head");
  const title = el("div", "version-title");
  title.append(el("span", "num", `시도 #${number}`));
  title.append(el("strong", null, thread ? "다음 시도 작성" : "첫 코드 제출"));
  head.append(title, el("span", "tag", "python"));
  card.append(head);

  if (thread) {
    card.append(el("p", "muted small", "고친 코드를 제출하면 지금 코드는 왼쪽에 그대로 남고 새 버전이 옆에 쌓여요."));
  }

  const label = el("label", "sr-only", "파이썬 코드");
  label.htmlFor = "code";
  const textarea = el("textarea");
  textarea.id = "code";
  textarea.spellcheck = false;
  textarea.autocomplete = "off";
  textarea.placeholder = "여기에 코드를 붙여넣거나 입력하세요.";
  textarea.value = state.draft ?? last;
  textarea.addEventListener("input", () => { state.draft = textarea.value; });
  bindTabIndent(textarea);
  card.append(label, textarea);

  card.append(el("p", "hint-line", "Tab: 들여쓰기 4칸 · Esc를 누른 뒤 Tab: 다음 칸으로 이동"));

  const button = el("button", "btn btn-primary", thread ? "새 버전 제출하고 분석하기 🚀" : "제출하고 분석하기 🚀");
  button.id = "submit-code";
  button.type = "button";
  button.addEventListener("click", submitCode);
  const error = el("p", "error");
  error.id = "submit-error";
  error.setAttribute("role", "alert");
  card.append(button, error);
  return card;
}

function renderStream() {
  const kind = viewKind();
  const t = state.thread;
  const key = [kind, t?.id ?? null, t?.status ?? null, t ? t.submissions.map((s) => [s.id, s.findings.length]) : []];
  if (!changed("stream", key)) return;

  const stream = $("stream");
  const composeHadFocus = document.activeElement?.id === "code";
  stream.replaceChildren();
  if (t) {
    t.submissions.forEach((s, i) => stream.append(renderVersion(s, i + 1, i === t.submissions.length - 1)));
  }
  if (kind === "fresh") stream.append(renderCompose(null));
  else if (kind === "own") stream.append(renderCompose(t));
  if (composeHadFocus && $("code")) $("code").focus();

  $("stream-sub").textContent =
    kind === "helping"
      ? `${t.owner} 친구가 제출한 코드예요. 새 버전이 올라오면 바로 나타나요.`
      : kind === "own"
        ? "카드를 누르면 그 버전의 힌트를 볼 수 있어요."
        : kind === "closed"
          ? "끝난 코드예요. 카드를 눌러 그때의 힌트를 볼 수 있어요."
          : "코드를 제출하면 여기에 버전이 쌓여요.";

  // 처음 열거나 다른 코드로 바뀌면 가장 최근 버전(작성 칸)이 보이도록 끝으로 넘긴다.
  const threadKey = t ? t.id : null;
  if (state.scrolledFor !== threadKey) {
    state.scrolledFor = threadKey;
    stream.style.scrollBehavior = "auto";
    stream.scrollLeft = stream.scrollWidth;
    stream.style.scrollBehavior = "";
  }
}

function updateSelection() {
  for (const card of $("stream").querySelectorAll(".version[data-id]")) {
    card.classList.toggle("selected", card.dataset.id === String(state.selectedId));
  }
}

function selectVersion(id) {
  if (state.selectedId === id) return;
  state.selectedId = id;
  updateSelection();
  renderHints();
}

/* ------------------------------------------------------------------ */
/* 힌트                                                                 */
/* ------------------------------------------------------------------ */

// 힌트 카드의 한 단계. 공개 전 단계는 본문·라벨 없이 번호와 잠금 표시만 그린다 (서버가 내려주지 않음).
function renderStep(number, stepState, step) {
  const item = el("li", `step ${stepState}`);
  item.append(el("span", "step-num", String(number)));

  const body = el("div");
  const title = el("div", "step-title");
  if (stepState === "revealed") {
    title.append(el("span", null, step.label), el("span", "step-state", "공개됨"));
    body.append(title, el("p", "step-text", step.text));
  } else if (stepState === "next") {
    title.append(el("span", null, `힌트 ${number}`), el("span", "step-state", "열 수 있어요"));
    body.append(title);
  } else {
    title.append(el("span", null, `힌트 ${number}`), el("span", "step-state", "🔒 잠김"));
    body.append(title);
  }
  item.append(body);
  return { item, body };
}

// 학생 코드와 힌트 문구는 모두 textContent 로만 넣는다 (HTML 로 해석하지 않음).
function renderFinding(finding, number) {
  const card = el("section", "finding");
  const revealedCount = finding.revealed.length;

  const head = el("div", "finding-head");
  head.append(
    el("h3", null, `확인할 부분 ${number}`),
    el("span", "progress", `${revealedCount}/${finding.total_steps} 단계`),
  );
  card.append(head);

  const list = el("ol", "steps");
  for (let i = 0; i < finding.total_steps; i++) {
    if (i < revealedCount) {
      list.append(renderStep(i + 1, "revealed", finding.revealed[i]).item);
    } else if (i === revealedCount) {
      const { item, body } = renderStep(i + 1, "next");
      const button = el("button", "btn btn-amber", revealedCount === 0 ? "첫 힌트 보기" : "다음 힌트 보기");
      button.type = "button";
      button.addEventListener("click", async () => {
        button.disabled = true;
        $("hint-error").textContent = "";
        try {
          const updated = await api(`/api/findings/${finding.id}/reveal`, { method: "POST" });
          finding.revealed = updated.revealed; // 화면에 들고 있는 상태도 맞춰 둔다
          card.replaceWith(renderFinding(updated, number));
          changed("hints", hintsKey()); // 방금 그린 것이 최신이므로 다음 새로고침에서 다시 그리지 않는다
        } catch (err) {
          if (err.status === 401) leaveToJoin("로그인이 끝났어요. 다시 입장해 주세요.");
          else $("hint-error").textContent = err.message;
          button.disabled = false;
        }
      });
      body.append(button);
      list.append(item);
    } else {
      list.append(renderStep(i + 1, "locked").item);
    }
  }
  card.append(list);

  if (revealedCount >= finding.total_steps) {
    card.append(el("p", "done-note", "✅ 모든 힌트를 확인했어요"));
  }
  return card;
}

function hintsKey() {
  const sub = selectedSubmission();
  return [sub?.id ?? null, sub ? sub.findings.map((f) => [f.id, f.revealed.length, f.total_steps]) : []];
}

function renderHints() {
  if (!changed("hints", hintsKey())) return;
  const sub = selectedSubmission();
  const box = $("findings");
  box.replaceChildren();
  $("hint-error").textContent = "";

  if (!sub) {
    $("hint-empty").hidden = false;
    box.hidden = true;
    $("hint-sub").textContent = "힌트는 한 단계씩 열어 볼 수 있어요.";
    return;
  }

  const number = state.thread.submissions.indexOf(sub) + 1;
  $("hint-sub").textContent = `시도 #${number} 기준 · 한 단계씩, 친구와 함께 열어 볼 수 있어요.`;
  $("hint-empty").hidden = true;
  box.hidden = false;
  if (sub.findings.length === 0) {
    const empty = el("div", "empty-state");
    empty.append(el("span", "emoji", "🎉"), el("p", null, "확인할 부분이 발견되지 않았어요."));
    box.append(empty);
    return;
  }
  sub.findings.forEach((f, i) => box.append(renderFinding(f, i + 1)));
}

/* ------------------------------------------------------------------ */
/* 내가 고친 코드 목록                                                  */
/* ------------------------------------------------------------------ */

const STATUS_LABEL = { open: "고치는 중", fixed: "완성", dropped: "그만둠" };

function renderHistory() {
  if (!changed("history", [state.threads, state.status.open_thread_id, state.pinnedThreadId])) return;
  const list = $("history");
  list.replaceChildren();
  $("history-empty").hidden = state.threads.length > 0;
  for (const item of state.threads) {
    const li = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.append(
      el("span", `attempt ${item.status}`, STATUS_LABEL[item.status]),
      el("span", "preview", item.preview),
      el("span", "when", `${item.submission_count}번 제출 · ${formatTime(item.created_at)}`),
    );
    button.addEventListener("click", () => {
      state.pinnedThreadId = item.id === state.status.open_thread_id ? null : item.id;
      state.draft = null;
      safeRefresh();
      window.scrollTo({ top: 0, behavior: "smooth" });
    });
    li.append(button);
    list.append(li);
  }
}

/* ------------------------------------------------------------------ */
/* 동작                                                                 */
/* ------------------------------------------------------------------ */

function actionError(message) {
  $("action-error").textContent = message;
}

function handleError(err, target) {
  if (err.status === 401) {
    leaveToJoin("로그인이 끝났어요. 다시 입장해 주세요.");
    return;
  }
  target.textContent = err.message;
}

async function submitCode() {
  const button = $("submit-code");
  const error = $("submit-error");
  error.textContent = "";
  button.disabled = true;
  try {
    const body = { code: $("code").value };
    if (state.thread && state.thread.status === "open" && state.thread.is_owner) body.thread_id = state.thread.id;
    const result = await api("/api/submissions", { method: "POST", body });
    state.draft = null;
    state.pinnedThreadId = result.thread_status === "open" ? null : result.thread_id; // 다 고쳐졌다면 그 코드를 계속 보여 준다
    state.selectedId = result.id;
    await refresh();
    const stream = $("stream");
    stream.scrollTo({ left: stream.scrollWidth, behavior: "smooth" });
  } catch (err) {
    handleError(err, $("submit-error") ?? $("action-error"));
  } finally {
    const again = $("submit-code");
    if (again) again.disabled = false;
  }
}

// 되돌리기 어려운 동작은 같은 버튼을 한 번 더 눌러야 실행된다.
function confirmTwice(button, armedLabel, action) {
  const original = button.textContent;
  let timer = null;
  button.addEventListener("click", async () => {
    if (timer === null) {
      button.textContent = armedLabel;
      timer = setTimeout(() => {
        button.textContent = original;
        timer = null;
      }, 4000);
      return;
    }
    clearTimeout(timer);
    timer = null;
    button.textContent = original;
    await action();
  });
}

confirmTwice($("finish-thread"), "정말 끝낼까요? 한 번 더 누르세요", async () => {
  actionError("");
  try {
    const thread = await api(`/api/threads/${state.thread.id}/finish`, { method: "POST" });
    state.pinnedThreadId = thread.id;
    await refresh();
  } catch (err) {
    handleError(err, $("action-error"));
  }
});

$("start-help").addEventListener("click", async () => {
  actionError("");
  $("start-help").disabled = true;
  try {
    await api("/api/help/start", { method: "POST" });
    state.pinnedThreadId = null;
    await refresh();
    window.scrollTo({ top: 0, behavior: "smooth" });
  } catch (err) {
    handleError(err, $("action-error"));
  } finally {
    $("start-help").disabled = false;
  }
});

$("end-help").addEventListener("click", async () => {
  actionError("");
  try {
    await api(`/api/help/${state.status.helping.session_id}/end`, { method: "POST" });
    await refresh();
  } catch (err) {
    handleError(err, $("action-error"));
  }
});

$("new-code").addEventListener("click", () => {
  actionError("");
  state.pinnedThreadId = null;
  state.draft = null;
  safeRefresh();
});

$("stream-prev").addEventListener("click", () => $("stream").scrollBy({ left: -360, behavior: "smooth" }));
$("stream-next").addEventListener("click", () => $("stream").scrollBy({ left: 360, behavior: "smooth" }));

/* ------------------------------------------------------------------ */
/* 채팅                                                                 */
/* ------------------------------------------------------------------ */

function chatSession() {
  const s = state.status;
  if (s.helping) return { id: s.helping.session_id, peer: s.helping.nickname, role: "helper" };
  if (s.helped_by) return { id: s.helped_by.session_id, peer: s.helped_by.nickname, role: "owner" };
  return null;
}

function closeChat() {
  const { ws } = state.chat;
  state.chat.ws = null;
  if (ws) ws.close();
}

function openChat(sessionId) {
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${scheme}://${location.host}/api/help/${sessionId}/ws`);
  state.chat.ws = ws;
  ws.onmessage = (event) => handleChatEvent(JSON.parse(event.data));
  ws.onclose = () => {
    if (state.chat.ws !== ws) return; // 우리가 직접 닫은 연결이다
    state.chat.ws = null;
    // 끊겼다면 잠시 뒤 상태를 다시 확인하고, 짝이 아직 살아 있으면 다시 연결한다.
    setTimeout(() => { if (state.me) safeRefresh(); }, 2000);
  };
}

function appendBubble(message) {
  const mine = message.sender.toLowerCase() === state.me.nickname.toLowerCase();
  const bubble = el("div", `bubble ${mine ? "mine" : "theirs"}`);
  const meta = el("span", "meta", mine ? formatClock(message.created_at) : `${message.sender} · ${formatClock(message.created_at)}`);
  bubble.append(meta);
  // `백틱`으로 감싼 부분만 코드 모양으로 보여 준다. 모두 textContent 로 넣는다.
  for (const part of message.body.split(/(`[^`\n]+`)/)) {
    if (part.length > 2 && part.startsWith("`") && part.endsWith("`")) bubble.append(el("code", null, part.slice(1, -1)));
    else if (part) bubble.append(document.createTextNode(part));
  }
  $("chat-list").append(bubble);
}

function appendSystemNote(text) {
  $("chat-list").append(el("div", "system-note", text));
}

function scrollChatToEnd() {
  const list = $("chat-list");
  list.scrollTop = list.scrollHeight;
}

function handleChatEvent(event) {
  switch (event.type) {
    case "history":
      $("chat-list").replaceChildren();
      state.chat.messages = event.messages;
      event.messages.forEach(appendBubble);
      scrollChatToEnd();
      break;
    case "message":
      state.chat.messages.push(event);
      appendBubble(event);
      scrollChatToEnd();
      break;
    case "blocked":
      $("chat-error").textContent = event.reason;
      break;
    case "hint":
    case "submission":
      safeRefresh(); // 짝이 힌트를 열었거나 새 버전을 올렸다
      break;
    case "ended":
      state.chat.ended = true;
      appendSystemNote(event.reason === "fixed" ? "친구가 코드를 다 고쳤어요. 대화가 끝났어요. 🎉" : "대화가 끝났어요.");
      scrollChatToEnd();
      $("chat-input").disabled = true;
      $("chat-send").disabled = true;
      safeRefresh();
      break;
  }
}

function syncChat() {
  const session = chatSession();
  const chat = state.chat;

  if (session && session.id !== chat.sessionId) {
    closeChat();
    chat.sessionId = session.id;
    chat.ended = false;
    chat.messages = [];
    $("chat-list").replaceChildren();
    $("chat-error").textContent = "";
    $("chat-input").disabled = false;
    $("chat-send").disabled = false;
    openChat(session.id);
  } else if (session && !chat.ws && !chat.ended) {
    openChat(session.id); // 연결이 끊겼던 경우
  }

  // 끝난 대화는 다음 짝이 생길 때까지 읽기 전용으로 남겨 둔다.
  const showRoom = Boolean(session) || chat.ended;
  $("chat-room").hidden = !showRoom;
  $("chat-empty").hidden = showRoom;

  if (session) {
    $("chat-heading").textContent = session.role === "helper" ? `${session.peer} 친구와 대화` : `${session.peer} 친구가 도와주는 중`;
    $("chat-sub").textContent = session.role === "helper" ? "코드와 힌트를 보며 설명해 주세요." : "막히는 부분을 편하게 물어보세요.";
  } else if (chat.ended) {
    $("chat-heading").textContent = "지난 대화";
    $("chat-sub").textContent = "대화가 끝났어요.";
  } else {
    $("chat-heading").textContent = "도와주는 친구";
    $("chat-sub").textContent = "";
    $("chat-empty-text").textContent =
      viewKind() === "own"
        ? "코드를 완성한 친구가 도와주러 오면 여기서 대화할 수 있어요."
        : "도와주는 친구가 생기면 여기서 대화할 수 있어요.";
  }
}

function sendChat() {
  const input = $("chat-input");
  const text = input.value.trim();
  if (!text) return;
  const ws = state.chat.ws;
  if (!ws || ws.readyState !== WebSocket.OPEN) {
    $("chat-error").textContent = "연결하는 중이에요. 잠시 뒤에 다시 보내 주세요.";
    return;
  }
  $("chat-error").textContent = "";
  ws.send(JSON.stringify({ body: text }));
  input.value = "";
}

$("chat-form").addEventListener("submit", (event) => {
  event.preventDefault();
  sendChat();
});

// Enter 는 보내기, Shift+Enter 는 줄바꿈. 한글을 조합하는 중의 Enter 는 글자 확정이므로 무시한다.
$("chat-input").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    sendChat();
  }
});

/* ------------------------------------------------------------------ */
/* 입장 / 퇴장                                                          */
/* ------------------------------------------------------------------ */

function startPolling() {
  stopPolling();
  state.pollTimer = setInterval(() => {
    if (!document.hidden) safeRefresh();
  }, POLL_MS);
}

function stopPolling() {
  if (state.pollTimer) clearInterval(state.pollTimer);
  state.pollTimer = null;
}

function resetState() {
  stopPolling();
  closeChat();
  Object.assign(state, {
    me: null, status: null, thread: null, threads: [], pinnedThreadId: null, selectedId: null,
    draft: null, draftFor: null, scrolledFor: undefined,
  });
  Object.assign(state.chat, { sessionId: null, ended: false, messages: [] });
  for (const key of Object.keys(signatures)) delete signatures[key];
  $("chat-list").replaceChildren();
  $("stream").replaceChildren();
}

function leaveToJoin(message) {
  resetState();
  showView(false);
  if (message) $("join-error").textContent = message;
}

async function enterMain(profile) {
  resetState();
  state.me = profile;
  $("who").textContent = `${profile.class_name} · ${profile.nickname}`;
  actionError("");
  showView(true);
  await refresh();
  startPolling();
}

$("join-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("join-error").textContent = "";
  try {
    const profile = await api("/api/join", {
      method: "POST",
      body: { join_code: $("join-code").value, nickname: $("nickname").value },
    });
    await enterMain(profile);
  } catch (err) {
    $("join-error").textContent = err.message;
  }
});

$("logout").addEventListener("click", async () => {
  try {
    await api("/api/logout", { method: "POST" });
  } finally {
    leaveToJoin("");
  }
});

document.addEventListener("visibilitychange", () => {
  if (!document.hidden && state.me) safeRefresh();
});

(async function boot() {
  try {
    await enterMain(await api("/api/me"));
  } catch (_) {
    leaveToJoin("");
  }
})();
