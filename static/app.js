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

    async function updateTicket(ticketId, field, value) {
        try {
            const response = await fetch("/update", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                },
                body: JSON.stringify({ id: ticketId, field, value }),
            });
            const data = await response.json();
            if (!data.success) {
                throw new Error(data.error || "Update failed");
            }
            showToast("Ticket synced");
        } catch (error) {
            console.error(error);
            showToast(error.message || "Unable to update ticket", "error");
        }
    }

    function initAutosize() {
        document.querySelectorAll("[data-autosize]").forEach((textarea) => {
            autosize(textarea);
            textarea.addEventListener("input", () => autosize(textarea));
        });
    }

    function initDashboardFilters() {
        const form = document.getElementById("dashboardFilters");
        if (!form) return;

        initFilterForm(form, {
            searchSelector: "#ticketSearch",
            selectSelector: "#ownerFilter",
            statusInputSelector: "#statusFilter",
            chipSelector: "[data-status-filter]",
        });
    }

    function bindPreview(formId) {
        const form = document.getElementById(formId);
        if (!form) return;

        form.querySelectorAll("input, textarea, select").forEach((field) => {
            field.addEventListener("input", () => {
                document.querySelectorAll(`[data-preview="${field.id}"]`).forEach((target) => {
                    target.textContent = field.value || target.dataset.fallback || "Pending";
                });
            });
        });
    }

    function initClosedFilters() {
        const form = document.getElementById("closedFilters");
        if (form) {
            initFilterForm(form, {
                searchSelector: "#closedSearch",
                selectSelector: "#closedOwnerFilter",
            });
        }

        document.querySelectorAll("[data-reopen-form]").forEach((form) => {
            form.addEventListener("submit", async (event) => {
                event.preventDefault();
                if (!window.confirm("Reopen this ticket?")) return;

                try {
                    const response = await fetch(form.action, {
                        method: "POST",
                        headers: { "X-Requested-With": "fetch" },
                    });
                    const data = await response.json();
                    if (!data.success) throw new Error(data.error || "Unable to reopen");
                    form.closest(".archive-card")?.remove();
                    showToast("Ticket reopened");
                } catch (error) {
                    showToast(error.message || "Unable to reopen ticket", "error");
                }
            });
        });
    }

    function initFilterForm(form, options = {}) {
        const search = options.searchSelector ? form.querySelector(options.searchSelector) : null;
        const select = options.selectSelector ? form.querySelector(options.selectSelector) : null;
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
            searchTimer = window.setTimeout(submitFilters, 280);
        });

        select?.addEventListener("change", submitFilters);

        chips.forEach((chip) => {
            chip.addEventListener("click", () => {
                if (statusInput) {
                    statusInput.value = chip.dataset.statusFilter || "";
                }
                submitFilters();
            });
        });
    }

    function init() {
        initAutosize();
        initDashboardFilters();
        bindPreview("createTicketForm");
        bindPreview("submitTicketForm");
        initClosedFilters();
    }

    return { init, updateTicket };
})();

window.ticketApp = ticketApp;
window.addEventListener("DOMContentLoaded", ticketApp.init);
