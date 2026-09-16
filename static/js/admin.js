// static/js/admin.js - Admin-specific JavaScript
"use strict";

// Admin dashboard specific functions
function toggleAdminPanel(panelId) {
    const panel = document.getElementById(panelId);
    if (panel) {
        panel.classList.toggle('collapsed');
    }
}

function confirmAction(message, callback) {
    if (confirm(message)) {
        callback();
    }
}

// Admin table sorting
function sortAdminTable(tableId, columnIndex) {
    const table = document.getElementById(tableId);
    if (!table) return;
    
    const tbody = table.querySelector('tbody');
    const rows = Array.from(tbody.querySelectorAll('tr'));
    const isAscending = table.dataset.sortAsc !== 'true';
    
    rows.sort((a, b) => {
        const aVal = a.children[columnIndex].textContent.trim();
        const bVal = b.children[columnIndex].textContent.trim();
        const aNum = parseFloat(aVal);
        const bNum = parseFloat(bVal);
        
        if (!isNaN(aNum) && !isNaN(bNum)) {
            return isAscending ? aNum - bNum : bNum - aNum;
        }
        return isAscending ? aVal.localeCompare(bVal) : bVal.localeCompare(aVal);
    });
    
    rows.forEach(row => tbody.appendChild(row));
    table.dataset.sortAsc = isAscending;
}

// Export functions for global use
window.toggleAdminPanel = toggleAdminPanel;
window.confirmAction = confirmAction;
window.sortAdminTable = sortAdminTable;