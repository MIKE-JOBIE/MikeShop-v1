"use strict";

/* =========================================================
   MIKE SHOP – CONSOLIDATED JAVASCRIPT
   (Cleaned – removed online store, product page filters)
   ========================================================= */

/* =========================================================
   CSRF PROTECTION
   - Reads the token from <meta name="csrf-token">
   - csrfFetch() wraps fetch() and adds X-CSRFToken for
     POST / PUT / PATCH / DELETE
   - Injects a hidden input into every POST form on load
========================================================= */

const CSRF_TOKEN = (function () {
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute('content') : '';
})();

function csrfFetch(url, options = {}) {
    const method = (options.method || 'GET').toUpperCase();
    const needsToken = ['POST', 'PUT', 'PATCH', 'DELETE'].includes(method);

    if (needsToken && CSRF_TOKEN) {
        options.headers = Object.assign({}, options.headers, {
            'X-CSRFToken': CSRF_TOKEN
        });
    }
    return fetch(url, options);
}

function injectCsrfIntoForms() {
    if (!CSRF_TOKEN) return;
    document.querySelectorAll('form').forEach(function (form) {
        const method = (form.getAttribute('method') || 'GET').toUpperCase();
        if (method !== 'POST') return;
        if (form.querySelector('input[name="csrf_token"]')) return;

        const input = document.createElement('input');
        input.type  = 'hidden';
        input.name  = 'csrf_token';
        input.value = CSRF_TOKEN;
        form.appendChild(input);
    });
}

/* =========================================================
   SHARED FORMATTERS
========================================================= */

function formatUSD(value) {
    const number = Number(value || 0);
    return "$" + number.toLocaleString("en-US", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2
    });
}

function formatSLL(value) {
    const number = Number(value || 0);
    return "SLL " + number.toLocaleString("en-US", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2
    });
}

/* =========================================================
   DASHBOARD DATA
========================================================= */

let dailySalesDataOriginal = {};
let bestSellersDataOriginal = {};
let unreadCount = 0;
let dailySalesChart = null;
let bestSellersChart = null;

/* =========================================================
   KPI PERIOD LABELS
========================================================= */

const kpiPeriodLabels = {
    daily: "Today's business performance",
    monthly: "This month's business performance",
    quarterly: "This quarter's business performance",
    yearly: "This year's business performance"
};

const kpiShortLabels = {
    daily: "Today's",
    monthly: "This month's",
    quarterly: "This quarter's",
    yearly: "This year's"
};

/* =========================================================
   LOAD DASHBOARD DATA
========================================================= */

function loadDashboardData() {
    if (!document.body) return;
    const dailySales = document.body.dataset.dailySales;
    const bestSellers = document.body.dataset.bestSellers;
    const unreadAlerts = document.body.dataset.unreadAlerts;

    if (dailySales) {
        try {
            dailySalesDataOriginal = JSON.parse(dailySales);
        } catch (error) {
            console.error("Unable to parse dashboard sales data:", error);
            dailySalesDataOriginal = {};
        }
    }
    if (bestSellers) {
        try {
            bestSellersDataOriginal = JSON.parse(bestSellers);
        } catch (error) {
            console.error("Unable to parse dashboard best-seller data:", error);
            bestSellersDataOriginal = {};
        }
    }
    if (typeof unreadAlerts !== "undefined") {
        unreadCount = Number(unreadAlerts || 0);
    }
}

/* =========================================================
   KPI PERIOD TEXT
========================================================= */

function updateKpiPeriodText(range) {
    const label = kpiPeriodLabels[range] || kpiPeriodLabels.daily;
    const shortLabel = kpiShortLabels[range] || kpiShortLabels.daily;

    const periodLabel = document.getElementById("kpi-period-label");
    if (periodLabel) periodLabel.innerText = label;

    const salesNote = document.getElementById("sales-period-note");
    const profitNote = document.getElementById("profit-period-note");
    const expenseNote = document.getElementById("expense-period-note");
    const netProfitNote = document.getElementById("net-profit-period-note");

    if (salesNote) salesNote.innerText = `${shortLabel} revenue`;
    if (profitNote) profitNote.innerText = `${shortLabel} gross profit`;
    if (expenseNote) expenseNote.innerText = `${shortLabel} expenses`;
    if (netProfitNote) netProfitNote.innerText = `${shortLabel} net profit`;
}

/* =========================================================
   KPI LOADING (UPDATED – USD + SLL)
========================================================= */

function getSllRate() {
    const attr = document.body && document.body.dataset.usdToSll;
    const parsed = Number(attr);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : 23000;
}

async function loadKpi(range) {
    try {
        localStorage.setItem('kpiPeriod', range);
        const response = await fetch(`/kpi-data?range=${encodeURIComponent(range)}`);
        if (!response.ok) throw new Error("Unable to load KPI data.");
        const data = await response.json();

        const sllRate = getSllRate();
        const formatSLL = (usd) => "SLL " + (usd * sllRate).toLocaleString("en-US", {minimumFractionDigits: 2, maximumFractionDigits: 2});

        // Sales
        const sales = document.getElementById("totalSales");
        const salesSLL = document.getElementById("salesSLL");
        if (sales) sales.innerText = formatUSD(data.revenue);
        if (salesSLL) salesSLL.innerText = formatSLL(data.revenue);

        // Profit
        const profit = document.getElementById("totalProfit");
        const profitSLL = document.getElementById("profitSLL");
        if (profit) profit.innerText = formatUSD(data.profit);
        if (profitSLL) profitSLL.innerText = formatSLL(data.profit);

        // Expenses
        const expenses = document.getElementById("totalExpenses");
        const expensesSLL = document.getElementById("expenseSLL");
        if (expenses) expenses.innerText = formatUSD(data.expenses);
        if (expensesSLL) expensesSLL.innerText = formatSLL(data.expenses);

        // Net Profit
        const netProfit = document.getElementById("totalNetProfit");
        const netProfitSLL = document.getElementById("netProfitSLL");
        const calculatedNetProfit = Number(data.profit || 0) - Number(data.expenses || 0);
        if (netProfit) netProfit.innerText = formatUSD(calculatedNetProfit);
        if (netProfitSLL) netProfitSLL.innerText = formatSLL(calculatedNetProfit);

        updateKpiPeriodText(range);
    } catch (error) {
        console.error("KPI loading error:", error);
    }
}

/* =========================================================
   KPI BUTTONS – PERSISTENT STATE
========================================================= */

function initializeKpiButtons() {
    const buttons = document.querySelectorAll(".kpi-btn");
    const savedRange = localStorage.getItem('kpiRange') || 'daily';

    buttons.forEach(button => {
        if (button.dataset.range === savedRange) {
            button.classList.add('active');
        } else {
            button.classList.remove('active');
        }
        button.addEventListener("click", function() {
            buttons.forEach(btn => btn.classList.remove("active"));
            this.classList.add("active");
            const range = this.dataset.range;
            localStorage.setItem('kpiRange', range);
            loadKpi(range);
        });
    });

    if (buttons.length > 0) {
        loadKpi(savedRange);
        updateKpiPeriodText(savedRange);
    }
}

/* =========================================================
   DASHBOARD INVENTORY SEARCH
========================================================= */

function searchInventory() {
    const input = document.getElementById("inventorySearch");
    const table = document.getElementById("inventoryTable");
    if (!input || !table) return;

    const query = input.value.toLowerCase().trim();
    const rows = table.querySelectorAll("tbody tr");
    rows.forEach(row => {
        const text = row.innerText.toLowerCase();
        row.style.display = text.includes(query) ? "" : "none";
    });
}

/* =========================================================
   DASHBOARD CHARTS
========================================================= */

function initializeDashboardCharts() {
    const dailySalesCanvas = document.getElementById("dailySalesChart");
    const bestSellersCanvas = document.getElementById("bestSellersChart");

    if (!dailySalesCanvas && !bestSellersCanvas) return;

    // Daily Sales
    if (dailySalesCanvas && typeof Chart !== "undefined") {
        const ctx = dailySalesCanvas.getContext("2d");
        dailySalesChart = new Chart(ctx, {
            type: "line",
            data: {
                labels: Object.keys(dailySalesDataOriginal),
                datasets: [{
                    label: "Sales Revenue (USD)",
                    data: Object.values(dailySalesDataOriginal),
                    borderColor: "rgb(75, 192, 192)",
                    backgroundColor: "rgba(75, 192, 192, 0.10)",
                    tension: 0.3,
                    fill: true,
                    pointRadius: 3,
                    pointHoverRadius: 6
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: { intersect: false, mode: "index" },
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        callbacks: {
                            label: function(context) {
                                return formatUSD(context.raw);
                            }
                        }
                    }
                },
                scales: {
                    y: {
                        beginAtZero: true,
                        ticks: {
                            callback: function(value) {
                                return "$" + Number(value).toLocaleString();
                            }
                        }
                    }
                }
            }
        });
    }

    // Best Sellers
    const initialBestSellers = {};
    for (const [day, shoes] of Object.entries(bestSellersDataOriginal)) {
        for (const [shoe, qty] of Object.entries(shoes)) {
            if (!initialBestSellers[shoe]) initialBestSellers[shoe] = 0;
            initialBestSellers[shoe] += qty;
        }
    }

    if (bestSellersCanvas && typeof Chart !== "undefined") {
        const ctx = bestSellersCanvas.getContext("2d");
        bestSellersChart = new Chart(ctx, {
            type: "bar",
            data: {
                labels: Object.keys(initialBestSellers),
                datasets: [{
                    label: "Units Sold",
                    data: Object.values(initialBestSellers),
                    backgroundColor: "rgba(255, 99, 132, 0.70)",
                    borderRadius: 6
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        callbacks: {
                            label: function(context) {
                                return `${context.raw} units`;
                            }
                        }
                    }
                },
                scales: {
                    y: { beginAtZero: true, ticks: { precision: 0 } }
                }
            }
        });
    }
}

/* =========================================================
   DATE PARSER & CHART FILTERS
========================================================= */

function parseLocalDate(dateString, endOfDay = false) {
    if (!dateString) return null;
    const parts = dateString.split("-").map(Number);
    if (parts.length !== 3) return null;
    const [year, month, day] = parts;
    if (endOfDay) {
        return new Date(year, month - 1, day, 23, 59, 59, 999);
    }
    return new Date(year, month - 1, day);
}

function updateCharts() {
    if (!dailySalesChart || !bestSellersChart) return;

    const startInput = document.getElementById("startDate")?.value || "";
    const endInput = document.getElementById("endDate")?.value || "";
    const start = parseLocalDate(startInput);
    const end = parseLocalDate(endInput, true);

    // Sales
    const filteredSalesLabels = [];
    const filteredSalesData = [];
    for (const [day, value] of Object.entries(dailySalesDataOriginal)) {
        const date = parseLocalDate(day);
        if ((!start || date >= start) && (!end || date <= end)) {
            filteredSalesLabels.push(day);
            filteredSalesData.push(value);
        }
    }
    dailySalesChart.data.labels = filteredSalesLabels;
    dailySalesChart.data.datasets[0].data = filteredSalesData;
    dailySalesChart.update();

    // Best Sellers
    const filteredBestSellers = {};
    for (const [day, shoes] of Object.entries(bestSellersDataOriginal)) {
        const date = parseLocalDate(day);
        if ((!start || date >= start) && (!end || date <= end)) {
            for (const [shoe, qty] of Object.entries(shoes)) {
                if (!filteredBestSellers[shoe]) filteredBestSellers[shoe] = 0;
                filteredBestSellers[shoe] += qty;
            }
        }
    }
    bestSellersChart.data.labels = Object.keys(filteredBestSellers);
    bestSellersChart.data.datasets[0].data = Object.values(filteredBestSellers);
    bestSellersChart.update();
}

function clearChartFilters() {
    const start = document.getElementById("startDate");
    const end = document.getElementById("endDate");
    if (start) start.value = "";
    if (end) end.value = "";
    updateCharts();
}

/* =========================================================
   NOTIFICATIONS
========================================================= */

function updateBadge() {
    const button = document.querySelector(".alert-btn");
    if (!button) return;
    let badge = button.querySelector(".alert-dot");
    if (unreadCount > 0) {
        if (!badge) {
            badge = document.createElement("span");
            badge.className = "alert-dot";
            button.appendChild(badge);
        }
        badge.innerText = unreadCount;
    } else if (badge) {
        badge.remove();
    }
}

function toggleNotifications() {
    const panel = document.getElementById("notificationPanel");
    if (!panel) return;
    panel.classList.toggle("show");
}

function playNotificationSound() {
    if (document.hidden) return;
    try {
        const audio = new Audio("https://assets.mixkit.co/sfx/preview/mixkit-software-interface-start-2574.mp3");
        audio.volume = 0.35;
        audio.play().catch(() => {});
    } catch (error) {
        console.debug("Notification sound unavailable.");
    }
}

function escapeHtml(value) {
    const div = document.createElement("div");
    div.textContent = String(value);
    return div.innerHTML;
}

function initializeNotifications() {
    const panel = document.getElementById("notificationPanel");
    if (!panel || typeof io === "undefined") return;

    const socket = io();
    socket.on("new_notification", function(data) {
        const notificationPanel = document.getElementById("notificationPanel");
        if (!notificationPanel) return;

        const item = document.createElement("div");
        item.className = "alert-item unread fade-in";
        item.innerHTML = `
            <div class="alert-message">${escapeHtml(data.message || "New notification")}</div>
            <div class="alert-time">Just now</div>
        `;
        notificationPanel.prepend(item);

        if (typeof data.unread_count !== "undefined") {
            unreadCount = Number(data.unread_count);
        } else {
            unreadCount++;
        }
        updateBadge();
        playNotificationSound();
    });
}

async function markAllRead() {
    try {
        const response = await csrfFetch("/mark_all_read", { method: "POST" });
        const data = await response.json();
        if (response.ok && data.status === "success") {
            document.querySelectorAll(".alert-item.unread").forEach(el => el.classList.remove("unread"));
            unreadCount = 0;
            updateBadge();
        }
    } catch (error) {
        console.error("Unable to mark notifications read:", error);
    }
}

async function markSingleRead(element) {
    if (!element || !element.classList.contains("unread")) return;
    const notifId = element.dataset.id;
    if (!notifId) return;

    try {
        const response = await csrfFetch(`/mark_notification_read/${notifId}`, { method: "POST" });
        const data = await response.json();
        if (response.ok && data.status === "success") {
            element.classList.remove("unread");
            unreadCount = Number(data.unread_count || 0);
            updateBadge();
        }
    } catch (error) {
        console.error("Unable to mark notification read:", error);
    }
}

/* =========================================================
   STAFF MANAGEMENT
========================================================= */

async function updateRole(userId, role) {
    try {
        const response = await csrfFetch(`/update_role/${userId}`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ role: role })
        });
        const data = await response.json();
        if (response.ok && data.status === "success") {
            alert(data.message || "Role updated successfully.");
        } else {
            alert(data.message || "Unable to update role.");
            location.reload();
        }
    } catch (error) {
        console.error("Role update error:", error);
        alert("Unable to update role.");
        location.reload();
    }
}

async function resetPassword(userId) {
    const newPass = prompt("Enter the new password (minimum 6 characters):");
    if (!newPass) return;
    if (newPass.length < 6) {
        alert("Password must be at least 6 characters.");
        return;
    }
    try {
        const response = await csrfFetch(`/reset_password/${userId}`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ password: newPass })
        });
        const data = await response.json();
        if (response.ok && data.status === "success") {
            alert(data.message || "Password reset successfully.");
        } else {
            alert(data.message || "Unable to reset password.");
        }
    } catch (error) {
        console.error("Password reset error:", error);
        alert("Unable to reset password.");
    }
}

async function toggleStaffActive(userId) {
    try {
        const response = await csrfFetch(`/toggle_staff_active/${userId}`, { method: "POST" });
        const data = await response.json();
        if (response.ok && data.status === "success") {
            location.reload();
        } else {
            alert(data.message || "Unable to update account status.");
        }
    } catch (error) {
        console.error("Staff status toggle error:", error);
        alert("Unable to update account status.");
    }
}

async function deleteStaff(userId) {
    if (!confirm("Delete this staff member? This action cannot be undone.")) return;
    try {
        const response = await csrfFetch(`/delete_staff/${userId}`, { method: "POST" });
        const data = await response.json();
        if (response.ok && data.status === "success") {
            location.reload();
        } else {
            alert(data.message || "Unable to delete staff member.");
        }
    } catch (error) {
        console.error("Staff deletion error:", error);
        alert("Unable to delete staff member.");
    }
}

/* =========================================================
   SALES HISTORY CHARTS
========================================================= */

function initializeSalesHistoryCharts() {
    const trendCanvas = document.getElementById("salesTrendChart");
    const staffCanvas = document.getElementById("staffChart");
    if (!trendCanvas && !staffCanvas) return;
    if (typeof Chart === "undefined") {
        console.error("Chart.js is not loaded.");
        return;
    }

    const dataElement = document.getElementById("salesHistoryData");
    if (!dataElement) {
        console.error("Sales history chart data element is missing.");
        return;
    }

    let trendLabels = [], trendData = [], staffLabels = [], staffData = [];
    try {
        trendLabels = JSON.parse(dataElement.dataset.trendLabels || "[]");
        trendData = JSON.parse(dataElement.dataset.trendData || "[]");
        staffLabels = JSON.parse(dataElement.dataset.staffLabels || "[]");
        staffData = JSON.parse(dataElement.dataset.staffData || "[]");
    } catch (error) {
        console.error("Unable to parse Sales History chart data:", error);
        return;
    }

    if (trendCanvas) {
        new Chart(trendCanvas, {
            type: "line",
            data: {
                labels: trendLabels,
                datasets: [{
                    label: "Revenue ($)",
                    data: trendData,
                    borderColor: "#2fd1b5",
                    backgroundColor: "rgba(47,209,181,0.15)",
                    fill: true,
                    tension: 0.3
                }]
            },
            options: { responsive: true, plugins: { legend: { display: true } } }
        });
    }
    if (staffCanvas) {
        new Chart(staffCanvas, {
            type: "bar",
            data: {
                labels: staffLabels,
                datasets: [{
                    label: "Sales ($)",
                    data: staffData,
                    backgroundColor: "#2fd1b5"
                }]
            },
            options: { responsive: true, plugins: { legend: { display: false } } }
        });
    }
}

/* =========================================================
   OUTSIDE CLICK – CLOSE NOTIFICATIONS
========================================================= */

function initializeNotificationOutsideClick() {
    window.addEventListener("click", function(event) {
        const wrapper = document.querySelector(".notification-wrapper");
        const panel = document.getElementById("notificationPanel");
        if (wrapper && panel && !wrapper.contains(event.target)) {
            panel.classList.remove("show");
        }
    });
}

/* =========================================================
   V2 – CUSTOMER MANAGEMENT
========================================================= */

function searchCustomers() {
    const input = document.getElementById('customerSearch');
    if (!input) return;
    const query = input.value.toLowerCase().trim();
    const cards = document.querySelectorAll('.customer-card');
    cards.forEach(card => {
        const text = card.innerText.toLowerCase();
        card.style.display = text.includes(query) ? '' : 'none';
    });
}



/* =========================================================
   PAGE INITIALIZATION
========================================================= */

document.addEventListener("DOMContentLoaded", function() {
    injectCsrfIntoForms();
    loadDashboardData();
    initializeKpiButtons();
    initializeDashboardCharts();
    initializeNotifications();
    initializeSalesHistoryCharts();
    updateBadge();
    initializeNotificationOutsideClick();
});
