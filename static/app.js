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
        const grid = document.getElementById("ticketGrid");
        if (!grid) return;

        const search = document.getElementById("ticketSearch");
        const owner = document.getElementById("ownerFilter");
        const chips = Array.from(document.querySelectorAll("#statusFilters .chip"));
        let activeStatus = "";

        const filterCards = () => {
            const searchValue = (search?.value || "").trim().toLowerCase();
            const ownerValue = owner?.value || "";

            grid.querySelectorAll(".ticket-card").forEach((card) => {
                const matchesSearch = !searchValue || card.dataset.ticketSearch.includes(searchValue);
                const matchesOwner = !ownerValue || card.dataset.assigned === ownerValue;
                const matchesStatus = !activeStatus || card.dataset.status === activeStatus;
                card.hidden = !(matchesSearch && matchesOwner && matchesStatus);
            });
        };

        search?.addEventListener("input", filterCards);
        owner?.addEventListener("change", filterCards);
        chips.forEach((chip) => {
            chip.addEventListener("click", () => {
                chips.forEach((item) => item.classList.remove("is-active"));
                chip.classList.add("is-active");
                activeStatus = chip.dataset.status;
                filterCards();
            });
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
        const grid = document.getElementById("closedGrid");
        const search = document.getElementById("closedSearch");
        if (!grid || !search) return;

        search.addEventListener("input", () => {
            const value = search.value.trim().toLowerCase();
            grid.querySelectorAll(".archive-card").forEach((card) => {
                card.hidden = value && !card.dataset.ticketSearch.includes(value);
            });
        });

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
