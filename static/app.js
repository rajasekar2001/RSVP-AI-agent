const $ = (s, e = document) => e.querySelector(s);
const $$ = (s, e = document) => [...e.querySelectorAll(s)];

let state = window.INIT_STATE || {};
let session = null;
let recognition = null;
let listening = false;
let agentSpeaking = false;
let voiceAutoListen = false;
let callFinished = false;
let testMode = "voice";
let currentContactName = null;
let movingToNextContact = false;
let uploadingContacts = false;
let startingRealCampaign = false;
let refreshingCalls = false;

const wait = ms => new Promise(resolve => setTimeout(resolve, ms));

function toast(message, duration = 3500) {
    const el = $("#toast");
    if (!el) return;
    el.textContent = message;
    el.classList.add("show");
    clearTimeout(window.tt);
    window.tt = setTimeout(() => el.classList.remove("show"), duration);
}

function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, ch => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;"
    }[ch]));
}

async function api(url, options = {}, timeoutMs = 15000) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);

    try {
        const response = await fetch(url, {
            ...options,
            signal: controller.signal,
            cache: "no-store"
        });

        const type = response.headers.get("content-type") || "";
        const data = type.includes("application/json")
            ? await response.json()
            : { detail: await response.text() };

        if (!response.ok) {
            let message = data.detail || data.error || "Request failed";
            if (typeof message !== "string") {
                message = JSON.stringify(message);
            }
            throw new Error(message);
        }

        return data;
    } catch (error) {
        if (error.name === "AbortError") {
            throw new Error("Request timed out. Check the FastAPI server.");
        }
        throw error;
    } finally {
        clearTimeout(timer);
    }
}

async function refreshState() {
    state = await api("/api/state", {}, 10000);
    render();
    syncVoicePrefs();
    return state;
}

// Voice preferences available to all UI handlers.
function voicePrefs() {
    return {
        voice_language: $("#voiceLanguage")?.value || "",
        voice_gender: $("#voiceGender") ? $("#voiceGender").value : undefined,
        female_app_id: $("#femaleAppId") ? $("#femaleAppId").value.trim() : undefined,
        female_app_version: $("#femaleAppVersion") ? $("#femaleAppVersion").value.trim() : undefined,
        male_app_id: $("#maleAppId") ? $("#maleAppId").value.trim() : undefined,
        male_app_version: $("#maleAppVersion") ? $("#maleAppVersion").value.trim() : undefined,
        voice_tone: $("#voiceTone")?.value || "normal",
        voice_speed: Number($("#voiceSpeed")?.value || 1),
        greeting: $("#greetingBox") ? $("#greetingBox").value : undefined,
        inbound_greeting: $("#inboundGreetingBox") ? $("#inboundGreetingBox").value : undefined,
        inbound_prompt: $("#inboundPromptBox") ? $("#inboundPromptBox").value : undefined,
        max_call_seconds: $("#maxCallSeconds") ? Number($("#maxCallSeconds").value || 50) : undefined,
        retry_enabled: $("#retryEnabled") ? $("#retryEnabled").checked : undefined,
        retry_interval_minutes: $("#retryMinutes") ? Number($("#retryMinutes").value || 60) : undefined,
        retry_max_attempts: $("#retryMax") ? Number($("#retryMax").value || 5) : undefined,
        retry_window_start: $("#retryFrom") ? $("#retryFrom").value || "09:00" : undefined,
        retry_window_end: $("#retryTo") ? $("#retryTo").value || "20:00" : undefined
    };
}

function syncVoicePrefs() {
    if ($("#voiceLanguage")) $("#voiceLanguage").value = state.agent?.voice_language || "";
    if ($("#voiceGender")) $("#voiceGender").value = state.agent?.voice_gender || "";
    if ($("#femaleAppId")) $("#femaleAppId").value = state.agent?.female_app_id || "";
    if ($("#femaleAppVersion")) $("#femaleAppVersion").value = state.agent?.female_app_version || "";
    if ($("#maleAppId")) $("#maleAppId").value = state.agent?.male_app_id || "";
    if ($("#maleAppVersion")) $("#maleAppVersion").value = state.agent?.male_app_version || "";
    if ($("#voiceTone")) $("#voiceTone").value = state.agent?.voice_tone || "normal";
    if ($("#greetingBox")) $("#greetingBox").value = state.agent?.greeting || "";
    if ($("#inboundGreetingBox")) $("#inboundGreetingBox").value = state.agent?.inbound_greeting || "";
    if ($("#inboundPromptBox")) $("#inboundPromptBox").value = state.agent?.inbound_prompt || "";
    if ($("#maxCallSeconds")) $("#maxCallSeconds").value = state.agent?.max_call_seconds || 50;
    if ($("#retryEnabled")) $("#retryEnabled").checked = state.agent?.retry_enabled !== false;
    if ($("#retryMinutes")) $("#retryMinutes").value = state.agent?.retry_interval_minutes || 60;
    if ($("#retryMax")) $("#retryMax").value = state.agent?.retry_max_attempts || 5;
    if ($("#retryFrom")) $("#retryFrom").value = state.agent?.retry_window_start || "09:00";
    if ($("#retryTo")) $("#retryTo").value = state.agent?.retry_window_end || "20:00";
    if ($("#voiceSpeed")) $("#voiceSpeed").value = state.agent?.voice_speed ?? 1;
    if ($("#voiceSpeedLabel")) $("#voiceSpeedLabel").textContent = `${Number($("#voiceSpeed")?.value || 1).toFixed(1)}×`;
}

$("#voiceSpeed")?.addEventListener("input", () => {
    const label = $("#voiceSpeedLabel");
    if (label) label.textContent = `${Number($("#voiceSpeed").value).toFixed(1)}×`;
});

syncVoicePrefs();

if ("speechSynthesis" in window) {
    speechSynthesis.getVoices();
}

function showTab(id) {
    $$(".tab,.node[data-tab]").forEach(el => {
        el.classList.toggle("active", el.dataset.tab === id);
    });
    $$(".panel").forEach(el => {
        el.classList.toggle("active", el.id === id);
    });
}

$$("[data-tab]").forEach(el => {
    el.addEventListener("click", () => showTab(el.dataset.tab));
});

function render() {
    const contacts = Array.isArray(state.contacts) ? state.contacts : [];
    const tests = Array.isArray(state.tests) ? state.tests : [];
    const knowledge = Array.isArray(state.knowledge) ? state.knowledge : [];
    const completed = contacts.filter(c => c.status === "Completed").length;

    if ($("#contactsTop")) {
        $("#contactsTop").textContent = contacts.length;
    }

    if ($("#contactCount")) {
        $("#contactCount").textContent =
            `${completed}/${contacts.length} completed`;
    }

    if ($("#testsList")) {
        $("#testsList").innerHTML = tests.map(t => `
            <div class="test-row">
                <span class="flask">♙</span>
                <div>
                    <b>${escapeHtml(t.title)}</b>
                    <p>${escapeHtml(t.description)}</p>
                </div>
                <span class="status ${escapeHtml(t.status || "ready")}">
                    ${t.status === "passed" ? "✓ Passed" :
                      t.status === "failed" ? "× Failed" : "Ready"}
                </span>
                <span class="muted">${escapeHtml(t.time || "")}</span>
            </div>
        `).join("");
    }

    // Display only uploaded contacts.
    if ($("#contactsBody")) {
        if (contacts.length === 0) {
            $("#contactsBody").innerHTML = `
                <tr>
                    <td colspan="5"
                        style="text-align:center;padding:32px;color:#888">
                        No contacts uploaded yet.
                        Upload a CSV or Excel file to begin.
                    </td>
                </tr>`;
        } else {
            $("#contactsBody").innerHTML = contacts.map(contact => {
                const status = String(contact.status || "Ready");
                const callStatus = String(contact.call_status || "");
                const failure = String(contact.failure_reason || "").replace(/^NO_FAILURE_REASON$/i, "");
                const attemptId = String(contact.attempt_id || "");

                const callDetail =
                    callStatus &&
                    callStatus.toLowerCase() !== status.toLowerCase()
                    ? `<div style="font-size:12px;color:#64748b;margin-top:4px">
                         Call: ${escapeHtml(callStatus)}
                       </div>`
                    : "";

                const failureDetail = failure
                    ? `<div style="font-size:12px;color:#dc2626;margin-top:5px;overflow-wrap:anywhere">
                         ${escapeHtml(failure)}
                       </div>`
                    : status.toLowerCase() === "failed"
                    ? `<div style="font-size:12px;color:#b45309;margin-top:5px">
                         No failure reason saved.
                         Check Sarvam call logs.
                       </div>`
                    : "";

                const limit = Number(state.agent?.max_call_seconds || 50);
                const dur = Number(contact.duration);
                const durationDetail = Number.isFinite(dur) && contact.duration !== null && contact.duration !== ""
                    ? `<div style="font-size:11px;color:${dur > limit ? "#b45309" : "#64748b"};margin-top:4px">
                         Duration: ${dur.toFixed(1)}s${dur > limit ? ` ⚠ over ${limit}s limit` : ""}
                       </div>`
                    : "";
                const retryDetail = contact.retry_note
                    ? `<div style="font-size:11px;color:#6d28d9;margin-top:4px">🔁 ${contact.retry_note}${contact.attempts ? ` (calls made: ${contact.attempts})` : ""}</div>`
                    : "";
                const inboundDetail = contact.last_inbound_at
                    ? `<div style="font-size:11px;color:#0f766e;margin-top:4px">☎ Called back (inbound)</div>`
                    : "";

                const attemptDetail = attemptId
                    ? `<div style="font-size:11px;color:#64748b;margin-top:4px;overflow-wrap:anywhere">
                         Attempt ID: ${escapeHtml(attemptId)}
                       </div>`
                    : "";

                return `
                    <tr>
                        <td>${escapeHtml(contact.name)}</td>
                        <td>${escapeHtml(contact.phone)}</td>
                        <td>
                            <strong>${escapeHtml(status)}</strong>
                            ${callDetail}
                            ${failureDetail}
                            ${durationDetail}
                            ${inboundDetail}
                            ${retryDetail}
                            ${attemptDetail}
                        </td>
                        <td>${escapeHtml(contact.rsvp || (status === "Call ended" ? "Awaiting RSVP result" : "—"))}</td>
                        <td>${escapeHtml(contact.guests || "—")}</td>
                    </tr>
                `;
            }).join("");
        }
    }

    if ($("#knowledgeList")) {
        $("#knowledgeList").innerHTML = knowledge.map((k, i) => `
            <div class="knowledge-item">
                <span>
                    <b>${escapeHtml(k.name)}</b>
                    <small class="muted">
                        Added ${escapeHtml(k.added || "")}
                    </small>
                </span>
                <button class="ghost small" onclick="deleteKnowledge(${i})">
                    Remove
                </button>
            </div>
        `).join("") || '<p class="muted">No event brief added yet.</p>';
    }

    if ($("#webhookUrl") && document.activeElement !== $("#webhookUrl")) {
        $("#webhookUrl").value = state.webhook?.url || "";
    }
}

render();

function setStatus(message) {
    if ($("#callStatus")) $("#callStatus").textContent = message;
}

function stopListening() {
    voiceAutoListen = false;
    if (recognition && listening) {
        try { recognition.stop(); } catch (e) {}
    }
    listening = false;
    if ($("#micBtn")) $("#micBtn").textContent = "🎙";
}

function startListening() {
    if (testMode !== "voice" || !recognition || !session ||
        callFinished || agentSpeaking || listening ||
        movingToNextContact) return;

    try {
        voiceAutoListen = true;
        listening = true;
        $("#micBtn").textContent = "◉";
        setStatus(`Listening to ${currentContactName}…`);
        recognition.lang = voicePrefs().voice_language || "en-IN";
        recognition.start();
    } catch (e) {
        listening = false;
        $("#micBtn").textContent = "🎙";
    }
}

// Pick a speech voice matching the chosen language. Never intentionally
// fall back to Hindi when English (or another language) is selected.
// Browsers do not expose a voice's gender, so match well-known voice names.
const FEMALE_VOICE_HINT = /female|woman|zira|heera|hazel|susan|samantha|victoria|karen|moira|tessa|veena|raveena|priya|neerja|swara|aria|jenny|sonia|natasha|kalpana|lekha|shruti|pallavi|ananya|sapna|kavya|fiona/i;
const MALE_VOICE_HINT = /\bmale\b|david|mark|george|james|richard|ravi|hemant|prabhat|guy|ryan|christopher|eric|alex|daniel|thomas|rishi|kalpesh|valluvar|madhur|prabhat|mohan|rajesh|abhijit/i;

function chooseBrowserVoice(locale) {
    const voices = window.speechSynthesis?.getVoices() || [];
    const base = locale.split("-")[0].toLowerCase();
    const matching = [
        ...voices.filter(v => v.lang.toLowerCase() === locale.toLowerCase()),
        ...voices.filter(v => v.lang.toLowerCase() !== locale.toLowerCase() && v.lang.split("-")[0].toLowerCase() === base)
    ];
    const gender = voicePrefs().voice_gender || "";
    if (gender && matching.length) {
        const hint = gender === "female" ? FEMALE_VOICE_HINT : MALE_VOICE_HINT;
        const other = gender === "female" ? MALE_VOICE_HINT : FEMALE_VOICE_HINT;
        const wanted = matching.find(v => hint.test(v.name)) || matching.find(v => !other.test(v.name));
        if (wanted) return wanted;
    }
    return matching[0] || null;
}

function speak(text, listenAfter = true, onFinished = null) {
    if (!("speechSynthesis" in window)) {
        toast("Browser speech synthesis is unavailable; use Chat Test.");
        if (onFinished) onFinished();
        return;
    }
    speechSynthesis.cancel();
    const prefs = voicePrefs();
    const locale = prefs.voice_language || "en-IN";
    const selected = chooseBrowserVoice(locale);

    // On browsers that expose a voice list, don't let the OS choose a
    // mismatched Hindi default when the requested locale is English.
    const available = speechSynthesis.getVoices();
    if (!selected && available.length) {
        const label = ({"en-IN":"English","ta-IN":"Tamil","hi-IN":"Hindi", "te-IN":"Telugu","ml-IN":"Malayalam","kn-IN":"Kannada"})[locale] || locale;
        toast(`No ${label} browser voice installed. Install it in Windows speech settings, or use Chat Test.`, 7000);
        setStatus(`No compatible browser voice for ${label}`);
        if (onFinished) onFinished();
        return;
    }

    const u = new SpeechSynthesisUtterance(text);
    u.lang = selected?.lang || locale;
    if (selected) u.voice = selected;
    u.rate = Math.min(1.5, Math.max(0.7, Number(prefs.voice_speed) || 1));
    u.pitch = ({normal:1, friendly:1.1, professional:0.95, calm:0.9})[prefs.voice_tone] ?? 1;
    agentSpeaking = true;
    let finished = false;
    const finish = () => {
        if (finished) return;
        finished = true;
        agentSpeaking = false;
        if (onFinished) { onFinished(); return; }
        if (listenAfter && session && !callFinished && testMode === "voice") {
            setTimeout(startListening, 300);
        }
    };
    u.onstart = () => setStatus(`Agent speaking (${u.lang}) to ${currentContactName || ""}…`);
    u.onend = finish;
    u.onerror = e => { toast(`Speech error: ${e.error || "unknown"}`, 5000); finish(); };
    speechSynthesis.speak(u);
}

function setTestMode(mode) {
    testMode = mode;

    $$("#modeTabs button").forEach(b => {
        b.classList.toggle("active", b.dataset.mode === mode);
    });

    $("#phoneSetup")?.classList.toggle("hidden", mode !== "phone");
    $("#voiceControls")?.classList.toggle("hidden", mode !== "voice");
    $("#chatControls")?.classList.toggle("hidden", mode === "voice");

    if (mode !== "voice") stopListening();

    if ($("#startCall")) {
        $("#startCall").textContent = mode === "chat"
            ? "💬 Start Chat Test"
            : mode === "phone"
            ? "☎ Simulate Phone Call"
            : "☎ Start Voice Test";
    }

    setStatus(session ? `${mode}: ${currentContactName}` :
        `Ready for ${mode} test`);
}

$$("#modeTabs button").forEach(b => {
    b.onclick = () => setTestMode(b.dataset.mode);
});
setTestMode("voice");

function addLine(who, text) {
    const box = $("#miniTranscript");
    if (!box) return;
    const div = document.createElement("div");
    div.className = who === "Agent" ? "a" : "";
    div.textContent = `${who}: ${text}`;
    box.appendChild(div);
    box.scrollTop = box.scrollHeight;
}

async function startCall(name = null, preserveTranscript = false) {
    try {
        window.speechSynthesis?.cancel();
        stopListening();

        if (!(state.contacts || []).length) {
            toast("Upload contacts before starting a test.");
            return;
        }

        callFinished = false;
        movingToNextContact = false;

        const body = name ? { name } : {};
        const d = await api("/api/session/start", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body)
        });

        if (d.all_done) {
            session = null;
            currentContactName = null;
            callFinished = true;
            addLine("System", "All contacts completed.");
            setStatus("Campaign completed");
            await refreshState();
            return;
        }

        session = d.session;
        currentContactName = d.contact?.name || session.name;

        if (!preserveTranscript && $("#miniTranscript")) {
            $("#miniTranscript").innerHTML = "";
        }

        addLine("Agent", d.reply);
        await refreshState();

        if (testMode === "chat") {
            setStatus(`Chatting with ${currentContactName}`);
        } else {
            speak(d.reply, testMode === "voice");
        }

        toast(`Started with ${currentContactName}`);
    } catch (e) {
        toast(e.message, 5000);
    }
}

async function moveToNextContact(contact) {
    if (!contact || movingToNextContact) return;
    movingToNextContact = true;
    stopListening();
    session = null;
    addLine("System", `Moving to next contact: ${contact.name}`);
    await wait(900);
    await startCall(contact.name, true);
}

async function finishCampaign() {
    stopListening();
    session = null;
    currentContactName = null;
    callFinished = true;
    addLine("System", "All contacts completed.");
    setStatus("Campaign completed");
    await refreshState();
    toast("Campaign completed");
}

async function sendMessage(text) {
    text = String(text || "").trim();
    if (!text) return;

    if (!session) return startCall();

    stopListening();
    addLine("You", text);
    setStatus("Thinking…");

    try {
        const d = await api("/api/session/message", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ session, message: text })
        });

        session = d.session;
        addLine("Agent", d.reply);

        if (d.done) {
            callFinished = true;
            await refreshState();

            const next = d.next_contact;
            const continueCampaign = async () => {
                if (next) await moveToNextContact(next);
                else await finishCampaign();
            };

            if (testMode === "chat") {
                await continueCampaign();
            } else {
                speak(d.reply, false, continueCampaign);
            }
            return;
        }

        if (testMode === "chat") {
            setStatus(`Chatting with ${currentContactName}`);
        } else {
            speak(d.reply, testMode === "voice");
        }
    } catch (e) {
        toast(e.message, 5000);
        if (testMode === "voice" && session && !callFinished) {
            setTimeout(startListening, 500);
        }
    }
}

if ($("#startCall")) $("#startCall").onclick = () => startCall();

if ($("#sendChat")) {
    $("#sendChat").onclick = () => {
        const input = $("#chatInput");
        if (!input) return;
        const value = input.value;
        input.value = "";
        sendMessage(value);
    };
}

if ($("#chatInput")) {
    $("#chatInput").addEventListener("keydown", e => {
        if (e.key === "Enter") {
            e.preventDefault();
            $("#sendChat")?.click();
        }
    });
}

const SR = window.SpeechRecognition || window.webkitSpeechRecognition;

if (SR) {
    recognition = new SR();
    recognition.lang = "en-IN";
    recognition.interimResults = false;
    recognition.continuous = false;
    recognition.maxAlternatives = 1;

    recognition.onstart = () => {
        listening = true;
        if ($("#micBtn")) $("#micBtn").textContent = "◉";
        setStatus(`Listening to ${currentContactName || ""}…`);
    };

    recognition.onresult = e => {
        const text = e.results[0][0].transcript.trim();
        if (!text) return;
        listening = false;
        if ($("#micBtn")) $("#micBtn").textContent = "🎙";
        sendMessage(text);
    };

    recognition.onend = () => {
        listening = false;
        if ($("#micBtn")) $("#micBtn").textContent = "🎙";
        if (testMode === "voice" && voiceAutoListen && session &&
            !callFinished && !agentSpeaking && !movingToNextContact) {
            setTimeout(startListening, 600);
        }
    };

    recognition.onerror = e => {
        listening = false;
        if ($("#micBtn")) $("#micBtn").textContent = "🎙";

        if (e.error === "not-allowed" ||
            e.error === "service-not-allowed") {
            voiceAutoListen = false;
            toast("Microphone blocked. Allow permission in Chrome.");
            return;
        }

        if (e.error === "no-speech") {
            if (session && !callFinished && !movingToNextContact) {
                setTimeout(startListening, 500);
            }
            return;
        }

        toast(`Voice error: ${e.error}`);
    };

    if ($("#micBtn")) {
        $("#micBtn").onclick = async () => {
            if (!session) return startCall();
            if (callFinished || movingToNextContact) return;

            if (listening) {
                voiceAutoListen = false;
                recognition.stop();
            } else {
                voiceAutoListen = true;
                startListening();
            }
        };
    }
} else if ($("#micBtn")) {
    $("#micBtn").onclick = () =>
        toast("Use Chrome or Edge for microphone testing.");
}

if ($("#newTestBtn")) {
    $("#newTestBtn").onclick = () => {
        window.speechSynthesis?.cancel();
        stopListening();
        session = null;
        currentContactName = null;
        callFinished = false;
        movingToNextContact = false;
        if ($("#miniTranscript")) $("#miniTranscript").innerHTML = "";
        setTestMode(testMode);
        toast("New test session ready");
    };
}

if ($("#historyBtn")) {
    $("#historyBtn").onclick = () =>
        $("#testAgentCard")?.classList.toggle("transcript-hidden");
}

if ($("#expandBtn")) {
    $("#expandBtn").onclick = () => {
        const card = $("#testAgentCard");
        card?.classList.toggle("expanded");
        $("#expandBtn").textContent =
            card?.classList.contains("expanded") ? "▣" : "◫";
    };
}

if ($("#testAgentTitle")) {
    $("#testAgentTitle").onclick = () =>
        $("#testAgentCard")?.classList.toggle("collapsed");
}

if ($("#rsvpSheetNode")) {
    $("#rsvpSheetNode").onclick = () => {
        showTab("campaigns");
        $(".workspace")?.scrollIntoView({
            behavior: "smooth",
            block: "start"
        });
    };
}

if ($("#hangBtn")) {
    $("#hangBtn").onclick = () => {
        window.speechSynthesis?.cancel();
        stopListening();
        session = null;
        currentContactName = null;
        callFinished = true;
        movingToNextContact = false;
        setStatus("Call ended");
    };
}

if ($("#bars")) {
    for (let i = 0; i < 48; i++) {
        const bar = document.createElement("i");
        bar.style.animationDelay = `${i * 0.03}s`;
        $("#bars").appendChild(bar);
    }
}

if ($("#buildBtn")) {
    $("#buildBtn").onclick = async () => {
        try {
            const prompt = $("#prompt")?.value.trim() || "";
            const d = await api("/api/build", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ prompt, ...voicePrefs() })
            });
            state.agent = d.agent;
            syncVoicePrefs();
            if ($("#instructionBox")) $("#instructionBox").value = prompt;
            showTab("build");
            toast("Agent built. Test it now.");
        } catch (e) {
            toast(e.message, 5000);
        }
    };
}

if ($("#saveInstructions")) {
    $("#saveInstructions").onclick = async () => {
        try {
            const prompt = $("#instructionBox")?.value || "";
            const d = await api("/api/build", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ prompt, ...voicePrefs() })
            });
            state.agent = d.agent;
            syncVoicePrefs();
            if ($("#prompt")) $("#prompt").value = prompt;
            toast("Instructions saved");
        } catch (e) {
            toast(e.message, 5000);
        }
    };
}

if ($("#knowledgeFile")) {
    $("#knowledgeFile").onchange = async e => {
        const file = e.target.files?.[0];
        if (!file) return;

        const fd = new FormData();
        fd.append("file", file);

        try {
            await api("/api/knowledge", {
                method: "POST",
                body: fd
            }, 30000);
            await refreshState();
            toast("Knowledge added");
        } catch (err) {
            toast(err.message, 5000);
        } finally {
            e.target.value = "";
        }
    };
}

window.deleteKnowledge = async index => {
    try {
        await api(`/api/knowledge/${index}`, { method: "DELETE" });
        await refreshState();
    } catch (e) {
        toast(e.message, 5000);
    }
};

if ($("#runAll")) {
    $("#runAll").onclick = async () => {
        try {
            const d = await api("/api/tests/run", { method: "POST" });
            state.tests = d.tests;
            render();
            $("#suggestion")?.classList.toggle("hidden", !d.suggestion);
            toast(d.suggestion ? "1 scenario needs a fix" : "All tests passed");
        } catch (e) {
            toast(e.message, 5000);
        }
    };
}

if ($("#fixBtn")) {
    $("#fixBtn").onclick = async () => {
        try {
            const d = await api("/api/fix", { method: "POST" });
            state.tests = d.tests;
            state.agent = state.agent || {};
            state.agent.prompt = d.prompt;
            if ($("#instructionBox")) $("#instructionBox").value = d.prompt;
            if ($("#prompt")) $("#prompt").value = d.prompt;
            $("#suggestion")?.classList.add("hidden");
            render();
            toast("Fixed");
        } catch (e) {
            toast(e.message, 5000);
        }
    };
}

if ($("#dismissBtn")) {
    $("#dismissBtn").onclick = () =>
        $("#suggestion")?.classList.add("hidden");
}

function openWebhookModal() {
    $("#webhookModal")?.classList.add("open");
}

if ($("#webhookBtn")) $("#webhookBtn").onclick = openWebhookModal;
if ($("#webhookNode")) $("#webhookNode").onclick = openWebhookModal;

if ($(".modal .x")) {
    $(".modal .x").onclick = () =>
        $("#webhookModal")?.classList.remove("open");
}

if ($("#saveWebhook")) {
    $("#saveWebhook").onclick = async () => {
        try {
            const url = $("#webhookUrl")?.value.trim() || "";
            const d = await api("/api/webhook", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ url })
            });
            state.webhook = d.webhook;
            $("#webhookModal")?.classList.remove("open");
            toast(url ? "Webhook saved" : "Webhook disabled");
        } catch (e) {
            toast(e.message, 5000);
        }
    };
}

// CSV / XLSX upload
async function uploadContacts(file) {
    if (!file || uploadingContacts) return;

    if (!/\.(csv|xlsx)$/i.test(file.name)) {
        toast("Upload a CSV or XLSX file.");
        return;
    }

    if (file.size > 8 * 1024 * 1024) {
        toast("Maximum file size is 8 MB.");
        return;
    }

    uploadingContacts = true;
    const input = $("#contactsFile");
    if (input) input.disabled = true;

    const fd = new FormData();
    fd.append("file", file);

    try {
        toast("Uploading contacts...");
        const d = await api("/api/contacts", {
            method: "POST",
            body: fd
        }, 15000);

        await refreshState();
        window.speechSynthesis?.cancel();
        stopListening();
        session = null;
        currentContactName = null;
        callFinished = false;

        toast(`${d.count} contacts imported successfully`);
    } catch (e) {
        toast(`Upload failed: ${e.message}`, 6000);
    } finally {
        uploadingContacts = false;
        if (input) {
            input.disabled = false;
            input.value = "";
        }
    }
}

if ($("#contactsFile")) {
    $("#contactsFile").accept = ".csv,.xlsx";
    $("#contactsFile").onchange = async e => {
        await uploadContacts(e.target.files?.[0]);
    };
}

if ($("#runDemoCampaign")) {
    $("#runDemoCampaign").onclick = async () => {
        try {
            if (!(state.contacts || []).length) {
                toast("Please upload contacts first.");
                return;
            }

            const d = await api("/api/campaign/run", { method: "POST" });
            await refreshState();
            toast(`${d.queued} contacts ready for browser demo`);
            showTab("build");
            await startCall();
        } catch (e) {
            toast(e.message, 5000);
        }
    };
}

// Real Sarvam calling

if ($("#runCampaign")) {
    $("#runCampaign").onclick = async () => {
        if (startingRealCampaign) return;

        if (!(state.contacts || []).length) {
            toast("Upload contacts before starting calls.");
            return;
        }

        const button = $("#runCampaign");
        startingRealCampaign = true;
        button.disabled = true;

        try {
            toast("Starting Sarvam RSVP campaign...");

            const data = await api(
                "/api/campaign/real/run",
                { method: "POST" },
                45000
            );

            await refreshState();

            if (data.finished) {
                toast("No Ready contacts remaining.");
            } else if (data.already_running) {
                toast(
                    `Call already running: ${
                        data.contact || "contact"
                    }. Waiting for completion.`,
                    5000
                );
            } else {
                toast(
                    `Calling ${
                        data.contact || "next contact"
                    }...`,
                    4000
                );
            }

        } catch (error) {
            console.error("Campaign error:", error);
            toast(error.message, 6000);

            try {
                await refreshState();
            } catch (refreshError) {
                console.warn(refreshError);
            }

        } finally {
            startingRealCampaign = false;
            button.disabled = false;
        }
    };
}

// Refresh status without placing another call.
setInterval(async () => {
    if (document.hidden || refreshingCalls || uploadingContacts) return;

    const active = (state.contacts || []).some(c =>
        ["calling", "ringing", "answered", "in progress", "queued"].includes(
            String(c.status || "").toLowerCase()
        ) && Boolean(c.attempt_id)
    );

    if (!active) return;

    refreshingCalls = true;
    try {
        await refreshState();
    } catch (e) {
        console.warn("Status refresh error:", e.message);
    } finally {
        refreshingCalls = false;
    }
}, 8000);

// Sarvam configuration check does NOT block uploads.
async function loadSarvamStatus() {
    const el = $("#sarvamStatus");
    if (!el) return;

    el.textContent = "Checking Sarvam configuration…";

    try {
        const d = await api("/api/sarvam/config", {}, 5000);

        if (d.configured) {
            el.textContent = d.agent_phone_number
                ? `✓ Sarvam configured • ${d.agent_phone_number}`
                : "✓ Sarvam configured";
            el.classList.add("ready");
        } else {
            el.textContent =
                "⚠ Sarvam not configured. Browser Demo is available.";
            el.classList.remove("ready");
        }
    } catch (e) {
        el.textContent =
            "⚠ Sarvam status unavailable. CSV upload still works.";
        el.classList.remove("ready");
    }
}

loadSarvamStatus();

if ($("#resetBtn")) {
    $("#resetBtn").onclick = async () => {
        try {
            await api("/api/reset", { method: "POST" });
            window.speechSynthesis?.cancel();
            stopListening();
            session = null;
            currentContactName = null;
            callFinished = false;
            await refreshState();
            toast("Dashboard reset");
        } catch (e) {
            toast(e.message, 5000);
        }
    };
}


// ============================================================
// Protected Sarvam call recordings and transcript viewer
// ============================================================
let recordingsAccessToken = "";
let activeRecordingBlobUrl = "";

function callHistoryMessage(message) {
    const target = document.querySelector("#callHistory");
    if (target) target.textContent = message;
}

async function recordingsRequest(url, responseType = "json") {
    if (!recordingsAccessToken) throw new Error("Unlock call history first");
    const result = await fetch(url, {
        headers: {"X-Recordings-Token": recordingsAccessToken},
        cache: "no-store"
    });
    if (!result.ok) {
        let reason = `HTTP ${result.status}`;
        try {
            const json = await result.json();
            reason = json.detail || reason;
        } catch (_) {}
        throw new Error(reason);
    }
    return responseType === "blob" ? result.blob() : result.json();
}

async function refreshCallHistory() {
    try {
        const data = await recordingsRequest("/api/calls");
        const host = document.querySelector("#callHistory");
        if (!host) return;
        host.replaceChildren();
        if (!data.calls.length) {
            host.textContent = "No real call attempts have been saved yet.";
            return;
        }
        data.calls.forEach(call => {
            const row = document.createElement("div");
            row.style.cssText = "display:flex;flex-wrap:wrap;gap:8px;align-items:center;padding:10px 0;border-bottom:1px solid #eee";
            const info = document.createElement("span");
            info.style.flex = "1 1 240px";
            info.textContent = `${call.direction === "Inbound" ? "⬅ Inbound" : "➡ Outbound"} · ${call.name || "Guest"} — ${call.status || "Unknown"} — ${call.duration == null ? "—" : Number(call.duration).toFixed(1)+"s"}${call.over_limit ? " ⚠ over limit" : ""}`;
            row.appendChild(info);

            const transcript = document.createElement("button");
            transcript.className = "ghost";
            transcript.textContent = "📝 Transcript";

            transcript.title = call.has_transcript
                ? "View transcript"
                : "Check transcript availability";

            transcript.onclick = () => {
                // A connected call can always be fetched from Sarvam on demand.
                if (!call.has_transcript && !call.has_interaction) {
                    const message =
                        String(call.status || "").toLowerCase() === "calling"
                            ? "Call still marked Calling. Waiting for the completion webhook and transcript."
                            : "No transcript stored for this call. Check the completion webhook.";

                    const viewer = document.querySelector("#callTranscript");

                    if (viewer) {
                        viewer.textContent = message;
                    }

                    toast(message, 6500);
                    return;
                }

                showCallTranscript(call.attempt_id);
            };

            row.appendChild(transcript);

            const play = document.createElement("button");
            play.className = "ghost";
            play.textContent = "▶ Recording";

            play.title = call.has_recording
                ? "Play recording"
                : "Check recording availability";

            play.onclick = () => {
                if (!call.has_recording) {
                    const message =
                        String(call.status || "").toLowerCase() === "calling"
                            ? "Call still marked Calling. Recording may become available after completion."
                            : "No recording is indicated for this call. Check Sarvam call logs and interaction ID.";

                    callHistoryMessage(message);
                    toast(message, 6500);
                    return;
                }

                playCallRecording(call.attempt_id);
            };

            row.appendChild(play);

            host.appendChild(row);
        });
    } catch (err) { callHistoryMessage(`Cannot load calls: ${err.message}`); }
}

async function showCallTranscript(attemptId) {
    const node = document.querySelector("#callTranscript");
    if (!node) return;
    node.textContent = "Loading transcript…";
    try {
        const data = await recordingsRequest(`/api/calls/${encodeURIComponent(attemptId)}/transcript`);
        const rows = data.transcript || [];
        node.textContent = rows.length
            ? rows.map(item => `${item.role === "agent" ? "AI" : "Guest"}: ${item.indic_text || item.en_text || item.text || ""}`).join("\n\n")
            : (data.note || "Transcript not available for this call.");
        // The server may have just read RSVP + guests from this transcript: refresh the table.
        if (data.rsvp) { try { await refreshState(); } catch (_) {} }
    } catch (err) { node.textContent = `Transcript error: ${err.message}`; }
}

async function playCallRecording(attemptId) {
    const player = document.querySelector("#callRecordingPlayer");
    if (!player) return;
    try {
        callHistoryMessage("Fetching recording securely from Sarvam…");
        const blob = await recordingsRequest(`/api/calls/${encodeURIComponent(attemptId)}/recording`, "blob");
        player.pause();
        if (activeRecordingBlobUrl) URL.revokeObjectURL(activeRecordingBlobUrl);
        activeRecordingBlobUrl = URL.createObjectURL(blob);
        player.src = activeRecordingBlobUrl;
        player.style.display = "block";
        await player.play();
        await refreshCallHistory();
    } catch (err) { callHistoryMessage(`Recording error: ${err.message}`); }
}

const unlockCallsButton = document.querySelector("#unlockCalls");
if (unlockCallsButton) {
    unlockCallsButton.onclick = async () => {
        const token = window.prompt("Enter your private RSVP_RECORDINGS_ACCESS_TOKEN (not your Sarvam API key):");
        if (!token) return;
        recordingsAccessToken = token.trim();
        await refreshCallHistory();
    };
}
document.querySelector("#reloadCalls")?.addEventListener("click", refreshCallHistory);

// Manually reconcile completed Sarvam attempts when a webhook was missed.
document.querySelector("#syncSarvamCalls")?.addEventListener("click", async () => {
    if (!recordingsAccessToken) {
        toast("Unlock call history first.", 5000);
        return;
    }
    try {
        const response = await fetch("/api/campaign/reconcile", {
            method: "POST",
            headers: {"X-Recordings-Token": recordingsAccessToken},
            cache: "no-store"
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
        const updated = (data.results || []).filter(x => x.result === "updated").length;
        toast(updated ? `${updated} call(s) synchronized.` : "No verified finished call found yet.", 6000);
        await refreshState();
        await refreshCallHistory();
    } catch (error) { toast(`Sync failed: ${error.message}`, 6500); }
});


$("#previewCall")?.addEventListener("click", async e => {
    e.preventDefault();
    try {
        const d = await api("/api/call-preview?name=Raja");
        const box = $("#callPreview");
        box.style.display = "block";
        box.textContent = `Language: ${d.language}\n\nGREETING:\n${d.opening_message}\n\nINSTRUCTIONS:\n${d.call_instructions}`;
    } catch (err) { toast(err.message, 5000); }
});


// ---------------- inbound preview ----------------
$$(".previewInbound").forEach(link => link.addEventListener("click", async e => {
    e.preventDefault();
    try {
        const scenario = link.dataset.scenario || "missed";
        const d = await api(`/api/call-preview?name=Raja&direction=inbound&scenario=${scenario}`);
        const box = $("#callPreview");
        if (!box) return;
        box.style.display = "block";
        box.textContent = `INCOMING CALL (${scenario})\nLanguage: ${d.language}\n\nGREETING:\n${d.opening_message}\n\nINSTRUCTIONS:\n${d.call_instructions}`;
        box.scrollIntoView({behavior: "smooth", block: "nearest"});
    } catch (err) { toast(err.message, 5000); }
}));

$("#checkInbound")?.addEventListener("click", async () => {
    const box = $("#inboundHealth");
    try {
        const d = await api("/api/inbound/health");
        const esc = s => String(s).replace(/[&<>]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;"}[c]));
        box.innerHTML = d.checks.map(c =>
            `<div>${c.ok ? "✅" : "❌"} ${esc(c.label)}${c.hint ? `<br><span style="color:#b45309;margin-left:22px">${esc(c.hint)}</span>` : ""}</div>`).join("")
            + (d.sarvam_defaults ? `<div style="margin-top:10px"><b>Safety defaults to paste into the Sarvam INBOUND agent</b> (as the default value of the input variables, so the AI is never silent even if the hook fails):
                 <br><small>opening_message</small><textarea readonly rows="3" style="width:100%;font-size:12px">${esc(d.sarvam_defaults.opening_message)}</textarea>
                 <small>call_instructions</small><textarea readonly rows="6" style="width:100%;font-size:12px">${esc(d.sarvam_defaults.call_instructions)}</textarea></div>` : "")
            + (d.hook_url ? `<div style="margin-top:8px"><b>Sarvam on-start hook URL:</b><br><code style="word-break:break-all">${esc(d.hook_url)}</code><br><b>Inbound webhook URL:</b><br><code style="word-break:break-all">${esc(d.webhook_url)}</code></div>` : "");
    } catch (err) { box.textContent = err.message; }
});

// ---------------- report ----------------
const REPORT_COLUMNS = [
  ["name","Name"],["phone","Phone"],["direction","Direction"],["status","Status"],["call_status","Call result"],
  ["rsvp","RSVP"],["guests","Guests"],["guest_count","Guest count"],["duration","Duration (s)"],
  ["over_limit","Over time limit"],["call_time","Call time"],["rsvp_source","RSVP source"],
  ["failure_reason","Failure reason"],["attempt_id","Attempt ID"],["interaction_id","Interaction ID"],["transcript","Transcript"]
];
const REPORT_DEFAULTS = ["name","phone","direction","status","rsvp","guests","guest_count","duration","call_time"];

function selected(sel) { return $$(`${sel} input:checked`).map(o => o.value); }

function reportBody(format) {
    const filters = {
        search: $("#rpSearch").value.trim(),
        direction: selected("#rpDirection"),
        status: selected("#rpStatus"),
        rsvp: selected("#rpRsvp"),
        date_from: $("#rpFrom").value, date_to: $("#rpTo").value,
        min_duration: $("#rpMin").value, max_duration: $("#rpMax").value,
        over_limit_only: $("#rpOver").checked
    };
    const g = $("#rpGuests").value;
    if (g) filters.has_guests = g === "yes";
    const columns = $$("#rpColumns input:checked").map(i => i.value);
    return { filters, columns, format };
}

async function reportRequest(format) {
    const body = reportBody(format);
    const headers = {"Content-Type": "application/json"};
    if (body.columns.includes("transcript")) {
        if (!recordingsAccessToken) throw new Error("Unlock call history first to include transcripts");
        headers["X-Recordings-Token"] = recordingsAccessToken;
    }
    const res = await fetch("/api/report", {method: "POST", headers, body: JSON.stringify(body)});
    if (!res.ok) {
        let reason = `HTTP ${res.status}`;
        try { reason = (await res.json()).detail || reason; } catch (_) {}
        throw new Error(reason);
    }
    return res;
}

let reportMatches = null;

async function updateReportCount() {
    try {
        const d = await (await reportRequest("preview")).json();
        reportMatches = d.count;
        $("#rpNote").textContent = d.count
            ? `${d.count} of ${d.total} rows match your filters.`
            : `0 of ${d.total} rows match your filters - click "Clear filters" to include everything.`;
        $("#rpNote").style.color = d.count ? "" : "#b45309";
    } catch (e) { reportMatches = null; $("#rpNote").textContent = e.message; }
}

function clearReportFilters() {
    $$("#reportModal #rpDirection input, #reportModal #rpStatus input, #reportModal #rpRsvp input, #reportModal #rpOver").forEach(i => i.checked = false);
    ["#rpSearch", "#rpFrom", "#rpTo", "#rpMin", "#rpMax"].forEach(s => { $(s).value = ""; });
    $("#rpGuests").value = "";
    updateReportCount();
}

function openReport() {
    const host = $("#rpColumns");
    if (!host.children.length) {
        host.innerHTML = REPORT_COLUMNS.map(([k, l]) =>
            `<label style="display:flex;gap:6px;align-items:center;margin:0;color:#333"><input type="checkbox" style="width:auto" value="${k}" ${REPORT_DEFAULTS.includes(k) ? "checked" : ""}> ${l}</label>`).join("");
        $("#reportModal").addEventListener("change", updateReportCount);
        $("#rpSearch").addEventListener("input", updateReportCount);
    }
    $("#reportModal").classList.add("open");
    updateReportCount();
}

$("#reportBtn")?.addEventListener("click", openReport);
$("#reportClose")?.addEventListener("click", () => $("#reportModal").classList.remove("open"));
$("#rpClear")?.addEventListener("click", clearReportFilters);
$("#rpDownload")?.addEventListener("click", async () => {
    try {
        await updateReportCount();
        if (reportMatches === 0) { toast("No rows match the filters. Click Clear filters first.", 6000); return; }
        const format = $("#rpFormat").value;
        const res = await reportRequest(format);
        const blob = await res.blob();
        const cd = res.headers.get("Content-Disposition") || "";
        const name = (cd.match(/filename="([^"]+)"/) || [])[1] || `rsvp_report.${format}`;
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url; a.download = name;
        document.body.appendChild(a); a.click(); a.remove();
        setTimeout(() => URL.revokeObjectURL(url), 2000);
        toast(`Downloaded ${name}`);
    } catch (e) { toast(e.message, 6000); }
});
