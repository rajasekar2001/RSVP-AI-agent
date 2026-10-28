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
        voice_tone: $("#voiceTone")?.value || "normal",
        voice_speed: Number($("#voiceSpeed")?.value || 1)
    };
}

function syncVoicePrefs() {
    if ($("#voiceLanguage")) $("#voiceLanguage").value = state.agent?.voice_language || "";
    if ($("#voiceTone")) $("#voiceTone").value = state.agent?.voice_tone || "normal";
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
                const failure = String(contact.failure_reason || "");
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
                            ${attemptDetail}
                        </td>
                        <td>${escapeHtml(contact.rsvp || "—")}</td>
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
function chooseBrowserVoice(locale) {
    const voices = window.speechSynthesis?.getVoices() || [];
    const exact = voices.find(v => v.lang.toLowerCase() === locale.toLowerCase());
    if (exact) return exact;
    const base = locale.split("-")[0].toLowerCase();
    return voices.find(v => v.lang.split("-")[0].toLowerCase() === base) || null;
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
            info.textContent = `${call.name || "Guest"} — ${call.status || "Unknown"} — ${call.duration == null ? "—" : Number(call.duration).toFixed(1)+"s"}`;
            row.appendChild(info);

            const transcript = document.createElement("button");
            transcript.className = "ghost";
            transcript.textContent = "📝 Transcript";

            transcript.title = call.has_transcript
                ? "View transcript"
                : "Check transcript availability";

            transcript.onclick = () => {
                if (!call.has_transcript) {
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
        const rows = data.transcript;
        node.textContent = rows.length
            ? rows.map(item => `${item.role === "agent" ? "AI" : "Guest"}: ${item.indic_text || item.en_text || item.text || ""}`).join("\n\n")
            : "Transcript not available for this call.";
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
