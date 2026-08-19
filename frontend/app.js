const API_BASE = "";

let sessionId = null;
let isLoading = false;

const chat = document.getElementById("chat");
const emptyState = document.getElementById("empty-state");
const input = document.getElementById("message-input");
const sendButton = document.getElementById("send-button");
const newChatButton = document.getElementById("new-chat-button");
const statusDot = document.getElementById("status-dot");
const statusText = document.getElementById("status-text");


document.addEventListener("DOMContentLoaded", () => {
    checkHealth();
    setupSuggestions();
    setupInput();
    setupNewChat();
});


async function checkHealth() {
    try {
        const response = await fetch(`${API_BASE}/api/health`);

        if (!response.ok) {
            throw new Error("Backend unavailable");
        }

        statusDot.classList.add("online");
        statusText.textContent = "Online";

    } catch (error) {
        statusDot.classList.add("offline");
        statusText.textContent = "Offline";
        console.error(error);
    }
}


function setupSuggestions() {
    document.querySelectorAll(".suggestion").forEach(button => {
        button.addEventListener("click", () => {
            sendMessage(button.dataset.message);
        });
    });
}


function setupInput() {
    input.addEventListener("input", () => {
        input.style.height = "auto";
        input.style.height =
            Math.min(input.scrollHeight, 150) + "px";
    });

    input.addEventListener("keydown", event => {
        if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            sendMessage();
        }
    });

    sendButton.addEventListener("click", () => sendMessage());
}


function setupNewChat() {
    newChatButton.addEventListener("click", newChat);
}


function newChat() {
    sessionId = null;

    chat.innerHTML = "";
    chat.appendChild(emptyState);

    emptyState.style.display = "block";

    input.value = "";
    input.style.height = "auto";
}


async function sendMessage(message = null) {

    if (isLoading) {
        return;
    }

    if (message === null) {
        message = input.value.trim();
    }

    if (!message) {
        return;
    }

    hideEmptyState();

    addMessage("user", message);

    if (message === input.value.trim()) {
        input.value = "";
        input.style.height = "auto";
    }

    setLoading(true);

    try {

        const response = await fetch(
            `${API_BASE}/api/chat`,
            {
                method: "POST",
                headers: {
                    "Content-Type": "application/json"
                },
                body: JSON.stringify({
                    message: message,
                    session_id: sessionId,
                    request_id: crypto.randomUUID()
                })
            }
        );

        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }

        const data = await response.json();

        handleAgentResponse(data);

    } catch (error) {

        console.error(error);

        addMessage(
            "assistant",
            "Sorry, I couldn't connect to Nebulous AI."
        );

    } finally {
        setLoading(false);
    }
}


function handleAgentResponse(data) {

    // =====================================================
    // NORMALIZE RESPONSE
    // =====================================================
    //
    // Normally /api/chat returns:
    //
    // {
    //     "type": "clarification",
    //     "session_id": "...",
    //     "message": "What department..."
    // }
    //
    // This also handles the response if the backend wraps it
    // inside "data", "result", or "response".
    //

    let response = data;

    if (
        response &&
        typeof response === "object"
    ) {

        if (
            response.type === undefined &&
            response.data &&
            typeof response.data === "object"
        ) {
            response = response.data;
        }

        else if (
            response.type === undefined &&
            response.result &&
            typeof response.result === "object"
        ) {
            response = response.result;
        }

        else if (
            response.type === undefined &&
            response.response &&
            typeof response.response === "object"
        ) {
            response = response.response;
        }
    }

    // =====================================================
    // SAVE SESSION
    // =====================================================

    if (
        response &&
        response.session_id
    ) {

        sessionId = response.session_id;

    }


    // =====================================================
    // CLARIFICATION
    // =====================================================

    if (
        response &&
        response.type === "clarification"
    ) {

        const question =
            response.message ||
            response.question ||
            "I need some additional information.";

        addMessage(
            "assistant",
            question
        );

        // IMPORTANT:
        // Your actual input ID is "message-input",
        // NOT "messageInput".

        if (input) {

            input.disabled = false;
            input.focus();

        }

        if (sendButton) {

            sendButton.disabled = false;

        }

        return;
    }


    // =====================================================
    // APPROVAL REQUIRED
    // =====================================================

    if (
        response &&
        response.type === "approval_required"
    ) {

        showApprovalCard(response);

        return;
    }


    // =====================================================
    // FINAL
    // =====================================================

    if (
        response &&
        response.type === "final"
    ) {

        addMessage(
            "assistant",
            response.message ||
            "Done."
        );

        return;
    }


    // =====================================================
    // ERROR
    // =====================================================

    if (
        response &&
        response.type === "error"
    ) {

        addMessage(
            "assistant",
            response.message ||
            "Something went wrong."
        );

        return;
    }


    // =====================================================
    // CANCELLED
    // =====================================================

    if (
        response &&
        response.type === "cancelled"
    ) {

        addMessage(
            "assistant",
            response.message ||
            "Action cancelled."
        );

        return;
    }


    // =====================================================
    // UNKNOWN RESPONSE
    // =====================================================

    addMessage(
        "assistant",
        "I received an unexpected response."
    );
}

/* =========================================================
   APPROVAL UI
========================================================= */

function showApprovalCard(data) {

    const card = document.createElement("div");
    card.className = "approval-card";

    const info = getApprovalInfo(data);


    /* HEADER */

    const header = document.createElement("div");
    header.className = "approval-header";

    const icon = document.createElement("div");
    icon.className = `approval-icon ${info.style}`;
    icon.textContent = info.icon;

    const heading = document.createElement("div");
    heading.className = "approval-heading";

    const label = document.createElement("div");
    label.className = "approval-label";
    label.textContent = "Confirmation required";

    const title = document.createElement("div");
    title.className = "approval-title";
    title.textContent = info.title;

    heading.appendChild(label);
    heading.appendChild(title);

    header.appendChild(icon);
    header.appendChild(heading);


    /* DESCRIPTION */

    const description = document.createElement("div");
    description.className = "approval-description";
    description.textContent = info.description;


    /* TARGET */

    let target = null;

    if (info.target) {

        target = document.createElement("div");
        target.className = "approval-target";

        const targetLabel = document.createElement("div");
        targetLabel.className = "approval-target-label";
        targetLabel.textContent =
            info.targetLabel || "User";

        const targetName = document.createElement("div");
        targetName.className = "approval-target-name";
        targetName.textContent = info.target;

        target.appendChild(targetLabel);
        target.appendChild(targetName);
    }


    /* EXTRA DETAIL */

    let detail = null;

    if (info.detail) {

        detail = document.createElement("div");
        detail.className = "approval-detail";

        const detailLabel = document.createElement("div");
        detailLabel.className = "approval-detail-label";
        detailLabel.textContent = info.detailLabel;

        const detailValue = document.createElement("div");
        detailValue.className = "approval-detail-value";
        detailValue.textContent = info.detail;

        detail.appendChild(detailLabel);
        detail.appendChild(detailValue);
    }


    /* BUTTONS */

    const actions = document.createElement("div");
    actions.className = "approval-actions";

    const cancel = document.createElement("button");
    cancel.className =
        "approval-button approval-cancel";
    cancel.textContent = "Cancel";

    const approve = document.createElement("button");
    approve.className =
        `approval-button approval-confirm ${info.buttonStyle}`;
    approve.textContent = info.buttonText;

    cancel.addEventListener("click", () => {
        rejectApproval(data.approval_id, card);
    });

    approve.addEventListener("click", () => {
        approveAction(
            data.approval_id,
            card,
            approve,
            cancel
        );
    });

    actions.appendChild(cancel);
    actions.appendChild(approve);


    /* BUILD */

    card.appendChild(header);
    card.appendChild(description);

    if (target) {
        card.appendChild(target);
    }

    if (detail) {
        card.appendChild(detail);
    }

    card.appendChild(actions);

    chat.appendChild(card);

    scrollToBottom();
}


/* =========================================================
   APPROVAL INFORMATION
========================================================= */

function getApprovalInfo(data) {

    const tool = data.tool;
    const parameters = data.parameters || {};

    const user =
    parameters.user ||
    parameters.user_id ||
    null;
    const license = parameters.license || null;


    switch (tool) {

        case "disable_user":

            return {
                icon: "⚠",
                style: "warning",
                title: "Disable user",
                description:
                    "This will prevent the account from signing in to Microsoft 365 until it is enabled again.",
                target: user,
                targetLabel: "User",
                buttonText: "Disable user",
                buttonStyle: "danger"
            };


        case "enable_user":

            return {
                icon: "✓",
                style: "normal",
                title: "Enable user",
                description:
                    "This will allow the account to sign in to Microsoft 365 again.",
                target: user,
                targetLabel: "User",
                buttonText: "Enable user",
                buttonStyle: ""
            };


        case "delete_user":

            return {
                icon: "×",
                style: "danger",
                title: "Delete user",
                description:
                    "This will permanently delete the Microsoft 365 account. This action should only be performed when the account is no longer required.",
                target: user,
                targetLabel: "User",
                buttonText: "Delete user",
                buttonStyle: "danger"
            };


        case "reset_password":

            return {
                icon: "🔑",
                style: "warning",
                title: "Reset password",
                description:
                    "A new temporary password will be assigned to this account and the user will be required to change it when they sign in.",
                target: user,
                targetLabel: "User",
                buttonText: "Reset password",
                buttonStyle: ""
            };


        case "revoke_sessions":

            return {
                icon: "↻",
                style: "warning",
                title: "Revoke sign-in sessions",
                description:
                    "This will sign the user out of their active Microsoft 365 sessions.",
                target: user,
                targetLabel: "User",
                buttonText: "Revoke sessions",
                buttonStyle: "danger"
            };


        case "assign_license":

            return {
                icon: "▣",
                style: "normal",
                title: "Assign license",
                description:
                    "This will assign the selected Microsoft 365 license to the user.",
                target: user,
                targetLabel: "User",
                detailLabel: "License",
                detail: license,
                buttonText: "Assign license",
                buttonStyle: ""
            };


        case "remove_license":

            return {
                icon: "▣",
                style: "warning",
                title: "Remove license",
                description:
                    "This will remove the selected Microsoft 365 license from the user.",
                target: user,
                targetLabel: "User",
                detailLabel: "License",
                detail: license,
                buttonText: "Remove license",
                buttonStyle: "danger"
            };


        case "send_email":

            return {
                icon: "✉",
                style: "normal",
                title: "Send email",
                description:
                    "The following email will be sent from the configured Nebulous Design mailbox.",
                target: parameters.recipient,
                targetLabel: "Recipient",
                detailLabel: "Subject",
                detail: parameters.subject || "(No subject)",
                buttonText: "Send email",
                buttonStyle: ""
            };


        case "draft_email": {

            const preview = data.preview || {};
            const subject = preview.subject || parameters.subject || "(No subject)";
            const body = preview.body || "";

            return {
                icon: "✎",
                style: "normal",
                title: "Create email draft",
                description:
                    "Review this proposed email. Approving will create an Outlook draft only; it will not send the email.",
                target: preview.recipient || parameters.recipient,
                targetLabel: "Recipient",
                detailLabel: `Draft preview — ${subject}`,
                detail: body,
                buttonText: "Create draft",
                buttonStyle: ""
            };
        }


        case "reply_email":

            return {
                icon: "↩",
                style: "normal",
                title: "Send reply",
                description:
                    "This reply will be sent to the sender of the selected email.",
                target: "Original email",
                targetLabel: "Reply",
                detailLabel: "Message",
                detail: parameters.body || "",
                buttonText: "Send reply",
                buttonStyle: ""
            };


        case "create_user":

            return {
                icon: "＋",
                style: "normal",
                title: "Create user",
                description:
                    "A new Microsoft 365 account will be created for this employee.",
                target:
                    `${parameters.first_name || ""} ${parameters.last_name || ""}`.trim(),
                targetLabel: "New user",
                detailLabel: "Department",
                detail: parameters.department || "Not specified",
                buttonText: "Create user",
                buttonStyle: ""
            };


        default:

            return {
                icon: "⚠",
                style: "warning",
                title: "Confirm action",
                description:
                    "Nebulous AI is requesting permission to perform this action.",
                target: user,
                targetLabel: "Target",
                buttonText: "Approve",
                buttonStyle: ""
            };
    }
}


/* =========================================================
   APPROVE
========================================================= */

async function approveAction(
    approvalId,
    card,
    approveButton,
    cancelButton
) {

    approveButton.disabled = true;
    cancelButton.disabled = true;

    approveButton.textContent = "Approving...";

    try {

        const response = await fetch(
            `${API_BASE}/api/approve`,
            {
                method: "POST",
                headers: {
                    "Content-Type": "application/json"
                },
                body: JSON.stringify({
                    approval_id: approvalId
                })
            }
        );

        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }

        const data = await response.json();

        card.remove();

        handleAgentResponse(data);

    } catch (error) {

        console.error(error);

        approveButton.disabled = false;
        cancelButton.disabled = false;

        approveButton.textContent = "Approve";

        addMessage(
            "assistant",
            "I couldn't process the approval."
        );
    }
}


/* =========================================================
   REJECT
========================================================= */

async function rejectApproval(
    approvalId,
    card
) {

    try {

        const response = await fetch(
            `${API_BASE}/api/reject`,
            {
                method: "POST",
                headers: {
                    "Content-Type": "application/json"
                },
                body: JSON.stringify({
                    approval_id: approvalId
                })
            }
        );

        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }

        const data = await response.json();

        card.remove();

        handleAgentResponse(data);

    } catch (error) {

        console.error(error);

        addMessage(
            "assistant",
            "I couldn't cancel the action."
        );
    }
}


/* =========================================================
   MESSAGES
========================================================= */

function addMessage(role, text) {

    const row = document.createElement("div");
    row.className = `message-row ${role}`;

    const avatar = document.createElement("div");
    avatar.className = "message-avatar";
    avatar.textContent = role === "user" ? "You" : "N";

    const content = document.createElement("div");
    content.className = "message-content";
    content.textContent = text;

    row.appendChild(avatar);
    row.appendChild(content);

    chat.appendChild(row);

    scrollToBottom();
}


/* =========================================================
   LOADING
========================================================= */

function setLoading(loading) {

    isLoading = loading;

    sendButton.disabled = loading;

    if (loading) {
        showTypingIndicator();
    } else {
        removeTypingIndicator();
    }
}


function showTypingIndicator() {

    removeTypingIndicator();

    const row = document.createElement("div");
    row.id = "typing-indicator";
    row.className = "message-row assistant";

    const avatar = document.createElement("div");
    avatar.className = "message-avatar";
    avatar.textContent = "N";

    const typing = document.createElement("div");
    typing.className = "typing";

    typing.innerHTML = `
        <span></span>
        <span></span>
        <span></span>
    `;

    row.appendChild(avatar);
    row.appendChild(typing);

    chat.appendChild(row);

    scrollToBottom();
}


function removeTypingIndicator() {

    const existing =
        document.getElementById("typing-indicator");

    if (existing) {
        existing.remove();
    }
}


/* =========================================================
   EMPTY STATE
========================================================= */

function hideEmptyState() {

    if (emptyState) {
        emptyState.style.display = "none";
    }
}


/* =========================================================
   SCROLL
========================================================= */

function scrollToBottom() {

    chat.scrollTo({
        top: chat.scrollHeight,
        behavior: "smooth"
    });
}
