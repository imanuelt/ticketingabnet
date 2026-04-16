const ticketApp = (() => {
    const toastStack = document.getElementById("toastStack");

    function showToast(message, type = "success") {
        if (!toastStack) return;
        const toast = document.createElement("div");
        toast.className = `toast ${type}`;
        toast.textContent = message;
        toastStack.appendChild(toast);
        window.setTimeout(() => toast.remove(), 3200);
    }

    function autosize(textarea) {
        textarea.style.height = "auto";
        textarea.style.height = `${textarea.scrollHeight}px`;
    }

    async function postUpdate(payload) {
        const response = await fetch("/update", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
            },
            body: JSON.stringify(payload),
        });
        const data = await response.json();
        if (!data.success) {
            throw new Error(data.error || "Update failed");
        }
        return data.ticket;
    }

    async function updateTicket(ticketId, field, value) {
        try {
            await postUpdate({ id: ticketId, field, value });
            showToast("Ticket synced");
        } catch (error) {
            console.error(error);
            showToast(error.message || "Unable to update ticket", "error");
        }
    }

    async function updateAssignment(ticketId, select) {
        const option = select.options[select.selectedIndex];
        try {
            await postUpdate({
                id: ticketId,
                field: "assignment",
                value: {
                    assigned_to_id: select.value,
                    assigned_to: option?.dataset.name || "",
                },
            });
            showToast("Assignment updated");
        } catch (error) {
            console.error(error);
            showToast(error.message || "Unable to update assignment", "error");
        }
    }

    async function quickSetStatus(ticketId, status) {
        await updateTicket(ticketId, "status", status);
        window.setTimeout(() => window.location.reload(), 280);
    }

    function initAutosize() {
        document.querySelectorAll("[data-autosize]").forEach((textarea) => {
            autosize(textarea);
            textarea.addEventListener("input", () => autosize(textarea));
        });
    }

    function applyToneClass(element, prefix, value) {
        if (!element) return;
        element.className = element.className
            .split(" ")
            .filter((className) => !className.startsWith(`${prefix}-`))
            .join(" ");
        element.classList.add(`${prefix}-${(value || "").toLowerCase().replace(/\s+/g, "-")}`);
    }

    function syncPreviewField(field) {
        const previewValue = field.dataset.previewValue || field.value;
        document.querySelectorAll(`[data-preview="${field.id}"]`).forEach((target) => {
            target.textContent = previewValue || target.dataset.fallback || "Pending";
        });

        if (field.id === "priority") {
            document.querySelectorAll('[data-badge-preview="priority"]').forEach((target) => {
                target.textContent = previewValue || "Medium";
                applyToneClass(target, "priority", previewValue || "Medium");
            });
        }
    }

    function wireAssigneePickers(scope = document) {
        scope.querySelectorAll("[data-assignee-picker]").forEach((select) => {
            const hiddenTarget = document.getElementById(select.dataset.assigneeTarget);
            const syncSelection = () => {
                const option = select.options[select.selectedIndex];
                const displayName = option?.dataset.name || "";
                if (hiddenTarget) {
                    hiddenTarget.value = displayName;
                    hiddenTarget.dataset.previewValue = displayName;
                    syncPreviewField(hiddenTarget);
                }
            };
            syncSelection();
            select.addEventListener("change", syncSelection);
        });
    }

    function bindPreview(formId) {
        const form = document.getElementById(formId);
        if (!form) return;

        wireAssigneePickers(form);

        form.querySelectorAll("input, textarea, select").forEach((field) => {
            syncPreviewField(field);
            field.addEventListener("input", () => syncPreviewField(field));
            field.addEventListener("change", () => syncPreviewField(field));
        });
    }

    function initFilterForm(form, options = {}) {
        const search = options.searchSelector ? form.querySelector(options.searchSelector) : null;
        const selects = options.selectSelectors
            ? options.selectSelectors.flatMap((selector) => Array.from(form.querySelectorAll(selector)))
            : [];
        const statusInput = options.statusInputSelector ? form.querySelector(options.statusInputSelector) : null;
        const chips = options.chipSelector ? Array.from(form.querySelectorAll(options.chipSelector)) : [];
        const pageInput = form.querySelector('input[name="page"]');
        let searchTimer;

        const submitFilters = () => {
            if (pageInput) {
                pageInput.value = "1";
            }
            form.requestSubmit();
        };

        search?.addEventListener("input", () => {
            window.clearTimeout(searchTimer);
            searchTimer = window.setTimeout(submitFilters, 260);
        });

        selects.forEach((select) => {
            select.addEventListener("change", submitFilters);
        });

        chips.forEach((chip) => {
            chip.addEventListener("click", () => {
                if (statusInput) {
                    statusInput.value = chip.dataset.statusFilter || "";
                }
                submitFilters();
            });
        });
    }

    function initDashboardFilters() {
        const form = document.getElementById("dashboardFilters");
        if (!form) return;

        initFilterForm(form, {
            searchSelector: "#ticketSearch",
            selectSelectors: ["#ownerFilter", "#priorityFilter", "#categoryFilter", "#serviceFilter", "#requesterFilter", "#sortFilter"],
            statusInputSelector: "#statusFilter",
            chipSelector: "[data-status-filter]",
        });
    }

    function initClosedFilters() {
        const form = document.getElementById("closedFilters");
        if (form) {
            initFilterForm(form, {
                searchSelector: "#closedSearch",
                selectSelectors: ["#closedOwnerFilter", "#closedPriorityFilter", "#closedCategoryFilter", "#closedServiceFilter", "#closedRequesterFilter", "#closedSortFilter"],
            });
        }

        document.querySelectorAll("[data-reopen-form]").forEach((reopenForm) => {
            reopenForm.addEventListener("submit", async (event) => {
                event.preventDefault();
                if (!window.confirm("Reopen this ticket?")) return;

                try {
                    const response = await fetch(reopenForm.action, {
                        method: "POST",
                        headers: { "X-Requested-With": "fetch" },
                    });
                    const data = await response.json();
                    if (!data.success) throw new Error(data.error || "Unable to reopen");
                    reopenForm.closest(".archive-card")?.remove();
                    showToast("Ticket reopened");
                } catch (error) {
                    showToast(error.message || "Unable to reopen ticket", "error");
                }
            });
        });
    }

    function initTicketEditors() {
        document.querySelectorAll("[data-ticket-editor-toggle]").forEach((button) => {
            button.addEventListener("click", () => {
                const card = button.closest(".ticket-card");
                const editor = card?.querySelector(".ticket-editor");
                if (!editor) return;
                const willOpen = editor.hasAttribute("hidden");
                editor.toggleAttribute("hidden", !willOpen);
                card.classList.toggle("is-editing", willOpen);
                card.querySelectorAll("[data-ticket-editor-toggle]").forEach((toggle) => {
                    toggle.setAttribute("aria-expanded", willOpen ? "true" : "false");
                });
            });
        });
    }

    function init() {
        initAutosize();
        initDashboardFilters();
        initClosedFilters();
        initTicketEditors();
        bindPreview("createTicketForm");
        bindPreview("submitTicketForm");
    }

    return { init, updateTicket, updateAssignment, quickSetStatus };
})();

window.ticketApp = ticketApp;
window.addEventListener("DOMContentLoaded", ticketApp.init);
